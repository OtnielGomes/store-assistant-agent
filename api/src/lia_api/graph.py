from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from lia_api.prompt import STORE_SYSTEM_PROMPT


def build_graph(
    model: BaseChatModel,
    checkpointer: object | None = None,
    tools: list[BaseTool] | None = None,
) -> CompiledStateGraph:
    bound_model = model.bind_tools(tools or [])

    def respond(state: MessagesState) -> dict[str, list[BaseMessage]]:
        reply = bound_model.invoke(
            [SystemMessage(content=STORE_SYSTEM_PROMPT), *state["messages"]]
        )
        return {"messages": [reply]}

    graph = StateGraph(MessagesState)
    graph.add_node("respond", respond)
    graph.add_node("tools", ToolNode(tools or []))
    graph.add_edge(START, "respond")
    graph.add_conditional_edges("respond", tools_condition)
    graph.add_edge("tools", "respond")
    return graph.compile(checkpointer=checkpointer)
