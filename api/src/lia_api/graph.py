from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from lia_api.calculator import build_calculator_tool
from lia_api.catalog import build_catalog_search_tool
from lia_api.guardrails import (
    OUTPUT_CHECK_SUMMARY,
    SAFE_REPLY,
    TOO_LONG_REPLY,
    exceeds_message_length,
    mask_personal_data,
    reply_is_grounded,
)
from lia_api.handoff import HandoffStore, record_handoff
from lia_api.policies import Embedder, build_policy_search_tool
from lia_api.prompt import STORE_SYSTEM_PROMPT


class LiaState(MessagesState):
    blocked: bool
    output_failures: int
    regenerate: bool


def _latest_human(messages: list[BaseMessage]) -> HumanMessage | None:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message
    return None


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return ""


def _input_guardrail(state: LiaState) -> dict[str, object]:
    human = _latest_human(state["messages"])
    text = _message_text(human) if human is not None else ""
    cleared = {"blocked": False, "output_failures": 0, "regenerate": False}
    if exceeds_message_length(text):
        return {
            "messages": [AIMessage(content=TOO_LONG_REPLY)],
            "blocked": True,
            "output_failures": 0,
            "regenerate": False,
        }
    masked = mask_personal_data(text)
    if human is not None and masked != text and human.id:
        return {
            "messages": [HumanMessage(content=masked, id=human.id)],
            **cleared,
        }
    return cleared


def _route_after_input(state: LiaState) -> Literal["respond", "__end__"]:
    if state.get("blocked"):
        return END
    return "respond"


def _route_after_respond(state: LiaState) -> Literal["tools", "output_check"]:
    if tools_condition(state) == "tools":
        return "tools"
    return "output_check"


def _route_after_output(state: LiaState) -> Literal["respond", "__end__"]:
    if state.get("regenerate"):
        return "respond"
    return END


def _turn_start(messages: list[BaseMessage]) -> int:
    start = 0
    for index, message in enumerate(messages):
        if message.type == "human":
            start = index + 1
    return start


def _candidate_reply(messages: list[BaseMessage]) -> AIMessage | None:
    for message in reversed(messages[_turn_start(messages) :]):
        if not isinstance(message, AIMessage):
            continue
        if message.tool_calls:
            continue
        if _message_text(message).strip():
            return message
    return None


def _tool_evidence(messages: list[BaseMessage]) -> str:
    parts = [
        _message_text(message)
        for message in messages[_turn_start(messages) :]
        if message.type == "tool"
    ]
    return "\n".join(parts)


def _output_check_node(store: HandoffStore | None):
    def output_check(state: LiaState, config: RunnableConfig) -> dict[str, object]:
        reply = _candidate_reply(state["messages"])
        if reply is None or reply.id is None:
            return {"regenerate": False}
        if reply_is_grounded(_message_text(reply), _tool_evidence(state["messages"])):
            return {"regenerate": False, "output_failures": 0}
        failures = int(state.get("output_failures") or 0)
        if failures < 1:
            return {
                "messages": [RemoveMessage(id=reply.id)],
                "output_failures": 1,
                "regenerate": True,
            }
        _record_output_check_failure(store, config)
        return {
            "messages": [AIMessage(content=SAFE_REPLY, id=reply.id)],
            "output_failures": failures + 1,
            "regenerate": False,
        }

    return output_check


def _record_output_check_failure(store: HandoffStore | None, config: RunnableConfig) -> None:
    configurable = config.get("configurable") or {}
    conversation_id = configurable.get("thread_id")
    clock = configurable.get("clock")
    missing_store = store is None or clock is None
    missing_conversation = not isinstance(conversation_id, str) or not conversation_id
    if missing_store or missing_conversation:
        raise RuntimeError("Output check requires a conversation, clock, and handoff store.")
    record_handoff(
        store,
        clock,
        conversation_id=conversation_id,
        reason="output_check_failure",
        summary=OUTPUT_CHECK_SUMMARY,
    )


def build_graph(
    model: BaseChatModel,
    checkpointer: object | None = None,
    tools: list[BaseTool] | None = None,
    database_url: str | None = None,
    embedder: Embedder | None = None,
    handoff_store: HandoffStore | None = None,
) -> CompiledStateGraph:
    selected = list(tools or [])
    if database_url:
        selected.append(build_catalog_search_tool(database_url))
        selected.append(build_calculator_tool(database_url))
        if embedder is not None:
            selected.append(build_policy_search_tool(database_url, embedder))
    bound_model = model.bind_tools(selected)

    def respond(state: LiaState) -> dict[str, list[BaseMessage]]:
        reply = bound_model.invoke(
            [SystemMessage(content=STORE_SYSTEM_PROMPT), *state["messages"]]
        )
        return {"messages": [reply]}

    graph = StateGraph(LiaState)
    graph.add_node("input_guardrail", _input_guardrail)
    graph.add_node("respond", respond)
    graph.add_node("tools", ToolNode(selected, handle_tool_errors=True))
    graph.add_node("output_check", _output_check_node(handoff_store))
    graph.add_edge(START, "input_guardrail")
    graph.add_conditional_edges("input_guardrail", _route_after_input)
    graph.add_conditional_edges("respond", _route_after_respond)
    graph.add_edge("tools", "respond")
    graph.add_conditional_edges("output_check", _route_after_output)
    return graph.compile(checkpointer=checkpointer)
