from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import psycopg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver
from pydantic import BaseModel

from lia_api.graph import build_graph
from lia_api.model import build_chat_model, configure_langsmith
from lia_api.settings import load_settings


class HealthResponse(BaseModel):
    status: Literal["healthy", "unhealthy"]
    database: Literal["connected", "unavailable"]


class ChatRequest(BaseModel):
    conversation_id: str
    message: str


class ChatResponse(BaseModel):
    reply: str
    handoff: None = None


def _reply_text(content: str | list[str | dict[str, object]]) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


def create_app(model: BaseChatModel | None = None) -> FastAPI:
    settings = load_settings()
    configure_langsmith(settings)
    chat_model = model
    if chat_model is None and settings.openai_api_key:
        chat_model = build_chat_model(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if chat_model is None:
            app.state.graph = None
            yield
            return
        with PostgresSaver.from_conn_string(settings.database_url) as checkpointer:
            checkpointer.setup()
            app.state.graph = build_graph(
                chat_model,
                checkpointer,
                database_url=settings.database_url,
            )
            yield

    app = FastAPI(title="Lia", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health")
    def health() -> HealthResponse:
        current = load_settings()
        try:
            with psycopg.connect(current.database_url, connect_timeout=5) as connection:
                connection.execute("SELECT 1")
        except psycopg.Error:
            return HealthResponse(status="unhealthy", database="unavailable")
        return HealthResponse(status="healthy", database="connected")

    @app.post("/chat")
    def chat(request: ChatRequest, http_request: Request) -> ChatResponse:
        graph = http_request.app.state.graph
        if graph is None:
            raise RuntimeError("OPENAI_API_KEY is required before Lia can answer.")
        result = graph.invoke(
            {"messages": [HumanMessage(content=request.message)]},
            {"configurable": {"thread_id": request.conversation_id}},
        )
        reply = _reply_text(result["messages"][-1].content)
        return ChatResponse(reply=reply)

    return app


app = create_app()
