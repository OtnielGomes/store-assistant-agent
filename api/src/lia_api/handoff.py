from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import psycopg
from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import ToolRuntime
from psycopg.rows import class_row

from lia_api.clock import FrozenClock, SystemClock, expected_return_message

HandoffReason = Literal[
    "customer_request",
    "exchange_return_refund_warranty",
    "complaint",
    "value_question",
    "overdue_delivery",
    "coming_soon_item",
    "data_deletion_request",
    "policy_gap",
    "verification_locked",
    "output_check_failure",
]

HANDOFF_REASONS: frozenset[str] = frozenset(
    (
        "customer_request",
        "exchange_return_refund_warranty",
        "complaint",
        "value_question",
        "overdue_delivery",
        "coming_soon_item",
        "data_deletion_request",
        "policy_gap",
        "verification_locked",
        "output_check_failure",
    )
)

_REASON_LIST = ", ".join(repr(reason) for reason in sorted(HANDOFF_REASONS))

_CREATE_TABLE = f"""
CREATE TABLE IF NOT EXISTS handoffs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id text NOT NULL,
    reason text NOT NULL,
    summary text NOT NULL,
    order_id bigint,
    created_at timestamptz NOT NULL,
    CONSTRAINT handoffs_reason_check CHECK (reason IN ({_REASON_LIST}))
)
"""


@dataclass
class Handoff:
    id: int
    conversation_id: str
    reason: str
    summary: str
    order_id: int | None
    created_at: datetime

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "reason": self.reason,
            "summary": self.summary,
            "order_id": self.order_id,
            "created_at": self.created_at,
        }


class HandoffStore:
    """PostgreSQL store for handoff records. No phone or e-mail columns."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def ensure_schema(self) -> None:
        with psycopg.connect(self._database_url) as connection:
            connection.execute(_CREATE_TABLE)

    def list_handoffs(self) -> list[Handoff]:
        with psycopg.connect(self._database_url, row_factory=class_row(Handoff)) as connection:
            rows = connection.execute(
                """
                SELECT id, conversation_id, reason, summary, order_id, created_at
                FROM handoffs
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
        return list(rows)

    def latest_id(self) -> int:
        with psycopg.connect(self._database_url) as connection:
            row = connection.execute("SELECT COALESCE(MAX(id), 0) FROM handoffs").fetchone()
        if row is None:
            return 0
        return int(row[0])

    def insert(
        self,
        *,
        conversation_id: str,
        reason: str,
        summary: str,
        order_id: int | None,
        created_at: datetime,
    ) -> None:
        with psycopg.connect(self._database_url) as connection:
            connection.execute(
                """
                INSERT INTO handoffs (conversation_id, reason, summary, order_id, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (conversation_id, reason, summary, order_id, created_at),
            )


def record_handoff(
    store: HandoffStore,
    clock: SystemClock | FrozenClock,
    *,
    conversation_id: str,
    reason: HandoffReason,
    summary: str,
    order_id: int | None = None,
) -> str:
    """Record a handoff and return the customer-facing expected return message."""
    if reason not in HANDOFF_REASONS:
        raise ValueError(f"Unknown handoff reason: {reason}")
    text = summary.strip()
    if not text:
        raise ValueError("Handoff summary is required.")
    created_at = clock.now()
    message = expected_return_message(created_at, reason)
    store.insert(
        conversation_id=conversation_id,
        reason=reason,
        summary=text,
        order_id=order_id,
        created_at=created_at,
    )
    return message


def build_record_handoff_tool(
    store: HandoffStore,
    fallback_clock: SystemClock | FrozenClock,
) -> BaseTool:
    """LangChain tool. Conversation id and clock come from the graph config."""

    @tool("record_handoff")
    def record_handoff_tool(
        reason: HandoffReason,
        summary: str,
        runtime: ToolRuntime,
        order_id: int | None = None,
    ) -> str:
        """Registra um encaminhamento para um atendente e devolve a mensagem de retorno.

        Use quando o cliente pedir uma pessoa, reclamar, pedir exclusão de dados
        pessoais ou perguntar algo que o manual não responde. Não grave telefone
        nem e-mail. Repasse ao cliente, sem alterar, o texto devolvido aqui.

        reason: customer_request, complaint, data_deletion_request, policy_gap,
        exchange_return_refund_warranty, value_question, overdue_delivery,
        coming_soon_item, verification_locked ou output_check_failure.
        summary: resumo curto do pedido, sem telefone e sem e-mail.
        order_id: número do pedido, somente se o cliente informou.
        """
        configurable = runtime.config.get("configurable") or {}
        conversation_id = configurable.get("thread_id")
        if not isinstance(conversation_id, str) or not conversation_id:
            raise RuntimeError("Handoff tool requires a conversation id.")
        clock = configurable.get("clock", fallback_clock)
        return record_handoff(
            store,
            clock,
            conversation_id=conversation_id,
            reason=reason,
            summary=summary,
            order_id=order_id,
        )

    return record_handoff_tool
