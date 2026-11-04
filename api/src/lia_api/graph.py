from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph

from lia_api.prompt import STORE_SYSTEM_PROMPT


def build_graph(model: BaseChatModel, checkpointer: object | None = None) -> CompiledStateGraph:
    def respond(state: MessagesState) -> dict[str, list[BaseMessage]]:
        reply = model.invoke([SystemMessage(content=STORE_SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [reply]}

    graph = StateGraph(MessagesState)
    graph.add_node("respond", respond)
    graph.add_edge(START, "respond")
    graph.add_edge("respond", END)
    return graph.compile(checkpointer=checkpointer)
