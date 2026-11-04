from collections.abc import Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from lia_api.prompt import STORE_SYSTEM_PROMPT


def build_graph(
    model: BaseChatModel,
    checkpointer: object | None = None,
    tools: Sequence[BaseTool] | None = None,
) -> CompiledStateGraph:
    tool_list = list(tools or [])
    responding_model = model.bind_tools(tool_list) if tool_list else model

    def respond(state: MessagesState) -> dict[str, list[BaseMessage]]:
        reply = responding_model.invoke(
            [SystemMessage(content=STORE_SYSTEM_PROMPT), *state["messages"]]
        )
        return {"messages": [reply]}

    graph = StateGraph(MessagesState)
    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    if not tool_list:
        graph.add_edge("respond", END)
        return graph.compile(checkpointer=checkpointer)

    graph.add_node("tools", ToolNode(tool_list, handle_tool_errors=True))
    graph.add_conditional_edges("respond", tools_condition, {"tools": "tools", "__end__": END})
    graph.add_edge("tools", "respond")
    return graph.compile(checkpointer=checkpointer)
