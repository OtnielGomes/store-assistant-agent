from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from lia_api.calculator import build_calculator_tool
from lia_api.catalog import build_catalog_search_tool
from lia_api.policies import Embedder, build_policy_search_tool
from lia_api.prompt import STORE_SYSTEM_PROMPT


def build_graph(
    model: BaseChatModel,
    checkpointer: object | None = None,
    database_url: str | None = None,
    embedder: Embedder | None = None,
) -> CompiledStateGraph:
    tools: list[BaseTool] = []
    if database_url:
        tools.append(build_catalog_search_tool(database_url))
        tools.append(build_calculator_tool(database_url))
        if embedder is not None:
            tools.append(build_policy_search_tool(database_url, embedder))
    runnable = model.bind_tools(tools) if tools else model

    def respond(state: MessagesState) -> dict[str, list[BaseMessage]]:
        reply = runnable.invoke([SystemMessage(content=STORE_SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [reply]}

    graph = StateGraph(MessagesState)
    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    if tools:
        graph.add_node("tools", ToolNode(tools, handle_tool_errors=True))
        graph.add_conditional_edges("respond", tools_condition)
        graph.add_edge("tools", "respond")
    else:
        graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer)
