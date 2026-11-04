from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from lia_api.calculator import build_calculator_tool
from lia_api.catalog import build_catalog_search_tool
from lia_api.prompt import STORE_SYSTEM_PROMPT


def build_graph(
    model: BaseChatModel,
    checkpointer: object | None = None,
    database_url: str | None = None,
) -> CompiledStateGraph:
    tools = (
        [build_catalog_search_tool(database_url), build_calculator_tool(database_url)]
        if database_url
        else []
    )
    runnable = model.bind_tools(tools) if tools else model

    def respond(state: MessagesState) -> dict[str, list[BaseMessage]]:
        reply = runnable.invoke([SystemMessage(content=STORE_SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [reply]}

    graph = StateGraph(MessagesState)
    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    if tools:
        graph.add_node("tools", ToolNode(tools))
        graph.add_conditional_edges("respond", tools_condition)
        graph.add_edge("tools", "respond")
    else:
        graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer)
