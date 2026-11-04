from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import psycopg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver
from pydantic import BaseModel

from lia_api.clock import FrozenClock, SystemClock
from lia_api.graph import build_graph
from lia_api.handoff import HandoffStore, build_record_handoff_tool
from lia_api.model import build_chat_model, configure_langsmith
from lia_api.policies import Embedder, openai_embedder
from lia_api.settings import Settings, load_settings


class HealthResponse(BaseModel):
    status: Literal["healthy", "unhealthy"]
    database: Literal["connected", "unavailable"]


class ChatRequest(BaseModel):
    conversation_id: str
    message: str
    simulated_at: datetime | None = None


class HandoffCreated(BaseModel):
    id: int
    reason: str


class ChatResponse(BaseModel):
    reply: str
    handoff: HandoffCreated | None = None


class HandoffResponse(BaseModel):
    id: int
    conversation_id: str
    reason: str
    summary: str
    order_id: int | None
    created_at: datetime


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


def _final_reply(messages: list[object]) -> str:
    for message in reversed(messages):
        if getattr(message, "type", None) == "ai":
            text = _reply_text(message.content).strip()
            if text:
                return text
    for message in reversed(messages):
        if getattr(message, "type", None) == "tool":
            return _reply_text(message.content)
    return ""


def _clock_for_request(
    app_env: str,
    timezone: ZoneInfo,
    app_clock: SystemClock | FrozenClock,
    simulated_at: datetime | None,
) -> SystemClock | FrozenClock:
    if simulated_at is None or app_env != "development":
        return app_clock
    return FrozenClock(simulated_at, timezone)


def _lazy_openai_embedder(settings: Settings) -> Embedder:
    holder: list[Embedder] = []

    def embed(texts: list[str]) -> list[list[float]]:
        if not holder:
            holder.append(openai_embedder(settings))
        return holder[0](texts)

    return embed


def create_app(
    model: BaseChatModel | None = None,
    clock: SystemClock | FrozenClock | None = None,
    embedder: Embedder | None = None,
) -> FastAPI:
    settings = load_settings()
    configure_langsmith(settings)
    chat_model = model
    if chat_model is None and settings.openai_api_key:
        chat_model = build_chat_model(settings)
    timezone = ZoneInfo(settings.app_timezone)
    app_clock = clock or SystemClock(timezone)
    store = HandoffStore(settings.database_url)
    policy_embedder = embedder if embedder is not None else _lazy_openai_embedder(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store.ensure_schema()
        app.state.handoffs = store
        app.state.clock = app_clock
        if chat_model is None:
            app.state.graph = None
            yield
            return
        with PostgresSaver.from_conn_string(settings.database_url) as checkpointer:
            checkpointer.setup()
            handoff_tool = build_record_handoff_tool(store, app_clock)
            app.state.graph = build_graph(
                chat_model,
                checkpointer,
                [handoff_tool],
                database_url=settings.database_url,
                embedder=policy_embedder,
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

    @app.get("/handoffs")
    def list_handoffs(http_request: Request) -> list[HandoffResponse]:
        records = http_request.app.state.handoffs.list_handoffs()
        return [
            HandoffResponse(
                id=record.id,
                conversation_id=record.conversation_id,
                reason=record.reason,
                summary=record.summary,
                order_id=record.order_id,
                created_at=record.created_at,
            )
            for record in records
        ]

    @app.post("/chat")
    def chat(request: ChatRequest, http_request: Request) -> ChatResponse:
        graph = http_request.app.state.graph
        if graph is None:
            raise RuntimeError("OPENAI_API_KEY is required before Lia can answer.")
        handoff_store: HandoffStore = http_request.app.state.handoffs
        before = handoff_store.latest_id()
        request_clock = _clock_for_request(
            settings.app_env,
            timezone,
            http_request.app.state.clock,
            request.simulated_at,
        )
        result = graph.invoke(
            {"messages": [HumanMessage(content=request.message)]},
            {
                "configurable": {
                    "thread_id": request.conversation_id,
                    "clock": request_clock,
                }
            },
        )
        reply = _final_reply(result["messages"])
        created = next(
            (
                item
                for item in handoff_store.list_handoffs()
                if item.id > before and item.conversation_id == request.conversation_id
            ),
            None,
        )
        handoff = None if created is None else HandoffCreated(id=created.id, reason=created.reason)
        return ChatResponse(reply=reply, handoff=handoff)

    return app


app = create_app()
