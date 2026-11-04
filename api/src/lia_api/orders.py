"""Order lookup. The model chooses arguments; it never writes SQL."""

import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

import psycopg
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import InjectedToolArg, tool
from psycopg.rows import dict_row

# Ticket #5's injectable clock is not on this branch. Anomaly flags use the
# frozen store date from the spec: 2026-11-03 in America/Campo_Grande.
LOOKUP_TODAY = date(2026, 11, 3)
_LOCK_AFTER_FAILURES = 3
_OPEN_STATUSES = ("pending", "confirmed", "shipped")
_PIX_FACTOR = Decimal("0.95")

_FAILURES_TABLE = """
CREATE TABLE IF NOT EXISTS order_verification_failures (
    conversation_id TEXT PRIMARY KEY,
    failure_count INTEGER NOT NULL CHECK (failure_count >= 0)
)
"""

_ORDER_SQL = """
SELECT
    split_part(c.name, ' ', 1) AS customer_first_name,
    o.status,
    o.order_date,
    o.total,
    o.payment_method,
    o.tracking_code,
    o.estimated_delivery,
    CASE WHEN o.status = 'cancelled' THEN o.notes END AS cancellation_reason,
    (
        SELECT COALESCE(SUM(oi.quantity * p.price), 0)
        FROM order_items AS oi
        JOIN products AS p ON p.product_id = oi.product_id
        WHERE oi.order_id = o.order_id
    ) AS items_total,
    EXISTS (
        SELECT 1
        FROM order_items AS oi
        JOIN products AS p ON p.product_id = oi.product_id
        WHERE oi.order_id = o.order_id
          AND p.status = 'coming_soon'
          AND o.status = ANY(%(open_statuses)s)
    ) AS coming_soon_in_open_order
FROM orders AS o
JOIN customers AS c ON c.customer_id = o.customer_id
WHERE o.order_id = %(order_id)s
  AND %(phone_last_four)s ~ '^[0-9]{4}$'
  AND right(regexp_replace(c.phone, '\\D', '', 'g'), 4) = %(phone_last_four)s
"""

_ITEMS_SQL = """
SELECT p.name, oi.quantity, p.status
FROM order_items AS oi
JOIN products AS p ON p.product_id = oi.product_id
WHERE oi.order_id = %(order_id)s
ORDER BY p.name
"""


def build_order_lookup_tool(database_url: str):
    """Tool the assistant uses to read one verified order. The model never writes SQL."""

    @tool
    def lookup_order(
        order_number: int,
        phone_last_four: str,
        config: Annotated[RunnableConfig, InjectedToolArg],
    ) -> str:
        """Consulta um pedido do Empório da Música.

        Exige order_number e phone_last_four (exatamente quatro dígitos).
        A conferência acontece na consulta. Sem a conferência, não devolve o
        pedido. Telefone e e-mail nunca vêm na resposta. Depois de três falhas
        na mesma conversa, as consultas ficam bloqueadas.
        """
        result = execute_order_lookup(
            database_url,
            order_number=order_number,
            phone_last_four=phone_last_four,
            conversation_id=_conversation_id(config),
        )
        return json.dumps(result, ensure_ascii=False)

    return lookup_order


def execute_order_lookup(
    database_url: str,
    *,
    order_number: int,
    phone_last_four: str,
    conversation_id: str,
    today: date = LOOKUP_TODAY,
) -> dict[str, object]:
    if not conversation_id.strip():
        raise ValueError("A conversation id is required to look up an order.")
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute(_FAILURES_TABLE)
        if _failure_count(connection, conversation_id) >= _LOCK_AFTER_FAILURES:
            connection.commit()
            return _locked()
        row = connection.execute(
            _ORDER_SQL,
            {
                "order_id": order_number,
                "phone_last_four": phone_last_four,
                "open_statuses": list(_OPEN_STATUSES),
            },
        ).fetchone()
        if row is None:
            locked = _record_failure(connection, conversation_id)
            connection.commit()
            return _locked() if locked else _hidden()
        items = connection.execute(_ITEMS_SQL, {"order_id": order_number}).fetchall()
        connection.commit()
    return _present(row, items, today)


def _conversation_id(config: RunnableConfig) -> str:
    configurable = config.get("configurable")
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("A conversation id is required to look up an order.")
    return thread_id


def _failure_count(connection: psycopg.Connection, conversation_id: str) -> int:
    row = connection.execute(
        """
        SELECT failure_count
        FROM order_verification_failures
        WHERE conversation_id = %(conversation_id)s
        """,
        {"conversation_id": conversation_id},
    ).fetchone()
    if row is None:
        return 0
    return int(row["failure_count"])


def _record_failure(connection: psycopg.Connection, conversation_id: str) -> bool:
    row = connection.execute(
        """
        INSERT INTO order_verification_failures (conversation_id, failure_count)
        VALUES (%(conversation_id)s, 1)
        ON CONFLICT (conversation_id) DO UPDATE
        SET failure_count = order_verification_failures.failure_count + 1
        RETURNING failure_count
        """,
        {"conversation_id": conversation_id},
    ).fetchone()
    assert row is not None
    return int(row["failure_count"]) >= _LOCK_AFTER_FAILURES


def _present(
    row: dict[str, object],
    items: list[dict[str, object]],
    today: date,
) -> dict[str, object]:
    estimated = row["estimated_delivery"]
    return {
        "verified": True,
        "verification_locked": False,
        "order": {
            "customer_first_name": row["customer_first_name"],
            "status": row["status"],
            "order_date": _iso_date(row["order_date"]),
            "order_total": _money(row["total"]),
            "payment_method": row["payment_method"],
            "tracking_code": row["tracking_code"],
            "estimated_delivery": _iso_date(estimated) if estimated is not None else None,
            "items": [
                {"name": item["name"], "quantity": int(item["quantity"]), "status": item["status"]}
                for item in items
            ],
            "cancellation_reason": row["cancellation_reason"],
            "anomaly_flags": _anomaly_flags(row, today),
        },
    }


def _anomaly_flags(row: dict[str, object], today: date) -> list[str]:
    items_total = _cents(row["items_total"])
    order_total = _cents(row["total"])
    estimated = row["estimated_delivery"]
    flags: list[str] = []
    if row["status"] in _OPEN_STATUSES and isinstance(estimated, date) and estimated < today:
        flags.append("overdue_delivery")
    if row["coming_soon_in_open_order"]:
        flags.append("coming_soon_item_in_open_order")
    if order_total != items_total:
        flags.append("total_differs_from_items")
    if row["payment_method"] == "pix" and order_total != _cents(items_total * _PIX_FACTOR):
        flags.append("pix_without_discount")
    return sorted(flags)


def _hidden() -> dict[str, object]:
    return {"verified": False, "verification_locked": False, "order": None}


def _locked() -> dict[str, object]:
    return {"verified": False, "verification_locked": True, "order": None}


def _iso_date(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _cents(value: object) -> Decimal:
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _money(value: object) -> str:
    return f"{_cents(value):.2f}"
