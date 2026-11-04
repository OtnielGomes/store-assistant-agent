import os
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from lia_api.clock import FrozenClock, SystemClock
from lia_api.handoff import HandoffStore, record_handoff

ZONE = ZoneInfo("America/Campo_Grande")
TUESDAY_MORNING = datetime(2026, 11, 3, 10, 0, tzinfo=ZONE)

DURING_HOURS = "A equipe continua o atendimento em instantes."
COMPLAINT_TUESDAY = (
    "O retorno é em até 24 horas úteis, até quinta-feira, 05/11/2026, às 16:00."
)
COMPLAINT_SUNDAY = (
    "O retorno é em até 24 horas úteis, até quarta-feira, 11/11/2026, às 15:00."
)
NEXT_MONDAY = (
    "Fora do expediente. O próximo atendimento é na segunda-feira, às 09:00, exceto feriados."
)
NEXT_WEDNESDAY = (
    "Fora do expediente. O próximo atendimento é na quarta-feira, às 09:00, exceto feriados."
)
NEXT_SATURDAY = (
    "Fora do expediente. O próximo atendimento é no sábado, às 09:00, exceto feriados."
)

PUBLIC_FIELDS = {
    "id",
    "conversation_id",
    "reason",
    "summary",
    "order_id",
    "created_at",
}


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.fail("DATABASE_URL must point at a real PostgreSQL database")
    return database_url


def _store() -> HandoffStore:
    store = HandoffStore(_database_url())
    store.ensure_schema()
    return store


def test_handoff_tool_records_the_request_without_phone_or_email() -> None:
    store = _store()
    message = record_handoff(
        store,
        FrozenClock(TUESDAY_MORNING),
        conversation_id="conv-human",
        reason="customer_request",
        summary="Cliente pediu um atendente humano",
        order_id=14,
    )

    assert message == DURING_HOURS
    recorded = next(
        item
        for item in store.list_handoffs()
        if item.conversation_id == "conv-human" and item.summary.startswith("Cliente pediu")
    )
    payload = recorded.as_dict()
    assert set(payload) == PUBLIC_FIELDS
    assert payload["reason"] == "customer_request"
    assert payload["order_id"] == 14
    assert payload["created_at"].astimezone(ZONE) == TUESDAY_MORNING
    assert "phone" not in payload
    assert "email" not in payload
    assert "telefone" not in payload


def test_complaint_during_business_hours_promises_24_business_hours() -> None:
    store = _store()
    message = record_handoff(
        store,
        FrozenClock(TUESDAY_MORNING),
        conversation_id="conv-complaint",
        reason="complaint",
        summary="Cliente reclamou do atraso",
    )

    assert message == COMPLAINT_TUESDAY


def test_complaint_on_sunday_counts_24_business_hours_from_monday_opening() -> None:
    store = _store()
    message = record_handoff(
        store,
        FrozenClock(datetime(2026, 11, 8, 12, 0, tzinfo=ZONE)),
        conversation_id="conv-complaint-sunday",
        reason="complaint",
        summary="Reclamação no domingo",
    )

    assert message == COMPLAINT_SUNDAY


def test_saturday_afternoon_promises_monday_opening_except_holidays() -> None:
    store = _store()
    message = record_handoff(
        store,
        FrozenClock(datetime(2026, 11, 7, 15, 30, tzinfo=ZONE)),
        conversation_id="conv-saturday",
        reason="customer_request",
        summary="Pedido no sábado à tarde",
    )

    assert message == NEXT_MONDAY


def test_sunday_promises_monday_opening_except_holidays() -> None:
    store = _store()
    message = record_handoff(
        store,
        FrozenClock(datetime(2026, 11, 8, 11, 0, tzinfo=ZONE)),
        conversation_id="conv-sunday",
        reason="policy_gap",
        summary="Pergunta sem resposta no manual",
    )

    assert message == NEXT_MONDAY


def test_weekday_after_closing_promises_the_next_opening_except_holidays() -> None:
    store = _store()
    tuesday_evening = record_handoff(
        store,
        FrozenClock(datetime(2026, 11, 3, 19, 0, tzinfo=ZONE)),
        conversation_id="conv-evening",
        reason="data_deletion_request",
        summary="Pedido de exclusão à noite",
    )
    friday_evening = record_handoff(
        store,
        FrozenClock(datetime(2026, 11, 6, 18, 30, tzinfo=ZONE)),
        conversation_id="conv-friday",
        reason="customer_request",
        summary="Pedido na sexta à noite",
    )

    assert tuesday_evening == NEXT_WEDNESDAY
    assert friday_evening == NEXT_SATURDAY


def test_handoffs_are_listed_newest_first() -> None:
    store = _store()
    token = uuid4().hex
    older = f"primeiro relato {token}"
    newer = f"segundo relato {token}"
    record_handoff(
        store,
        FrozenClock(datetime(2026, 11, 3, 10, 0, tzinfo=ZONE)),
        conversation_id=f"conv-order-{token}",
        reason="customer_request",
        summary=older,
    )
    record_handoff(
        store,
        FrozenClock(datetime(2026, 11, 3, 11, 0, tzinfo=ZONE)),
        conversation_id=f"conv-order-{token}",
        reason="complaint",
        summary=newer,
    )

    listed = [item.summary for item in store.list_handoffs() if token in item.summary]
    assert listed == [newer, older]


def test_default_clock_reads_real_campo_grande_time() -> None:
    store = _store()
    clock = SystemClock()
    record_handoff(
        store,
        clock,
        conversation_id="conv-real-clock",
        reason="customer_request",
        summary="relato no horário real",
    )

    recorded = next(
        item
        for item in store.list_handoffs()
        if item.summary == "relato no horário real"
    )
    created = recorded.created_at.astimezone(ZONE)
    assert created.utcoffset() == timedelta(hours=-4)
    drift = abs((datetime.now(ZONE) - created).total_seconds())
    assert drift < 60
