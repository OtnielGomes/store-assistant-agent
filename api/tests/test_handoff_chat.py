import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from lia_api.clock import FrozenClock
from tests.scripted_model import ScriptedChatModel, ScriptedToolCall

ZONE = ZoneInfo("America/Campo_Grande")
TUESDAY_MORNING = datetime(2026, 11, 3, 10, 0, tzinfo=ZONE)

DURING_HOURS = "A equipe continua o atendimento em instantes."
SATURDAY_AFTERNOON = (
    "Fora do expediente. O próximo atendimento é na segunda-feira, às 09:00, exceto feriados."
)
SUNDAY = (
    "Fora do expediente. O próximo atendimento é na segunda-feira, às 09:00, exceto feriados."
)


def _prepare_env(monkeypatch: pytest.MonkeyPatch, app_env: str = "development") -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.fail("DATABASE_URL must point at a real PostgreSQL database")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "none")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.delenv("OPENAI_TEMPERATURE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _handoff_then_echo(reason: str, summary: str) -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            ScriptedToolCall(
                name="record_handoff",
                args={"reason": reason, "summary": summary},
            ),
            "__echo_tool__",
        ]
    )


def test_chat_handoff_during_business_hours_lists_newest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_env(monkeypatch)
    model = _handoff_then_echo("customer_request", "Cliente pediu uma pessoa")

    from lia_api.main import create_app

    with TestClient(create_app(model=model, clock=FrozenClock(TUESDAY_MORNING))) as client:
        older = client.post(
            "/chat",
            json={
                "conversation_id": "conv-hours-older",
                "message": "Quero falar com uma pessoa",
                "simulated_at": "2026-11-03T10:00:00",
            },
        )
    newer_model = _handoff_then_echo("complaint", "Cliente reclamou do pedido 8")
    with TestClient(create_app(model=newer_model, clock=FrozenClock(TUESDAY_MORNING))) as client:
        newer = client.post(
            "/chat",
            json={
                "conversation_id": "conv-hours-newer",
                "message": "Estou insatisfeito com o atendimento",
                "simulated_at": "2026-11-03T11:00:00",
            },
        )
        queue = client.get("/handoffs")

    assert older.status_code == 200
    assert older.json()["reply"] == DURING_HOURS
    assert older.json()["handoff"]["reason"] == "customer_request"
    assert isinstance(older.json()["handoff"]["id"], int)

    assert newer.status_code == 200
    assert newer.json()["reply"].startswith("O retorno é em até 24 horas úteis")
    assert newer.json()["handoff"]["reason"] == "complaint"
    system = next(
        message.content for message in newer_model.calls[0] if message.type == "system"
    )
    assert isinstance(system, str)
    guidance = system.casefold()
    assert "empatia" in guidance
    assert "24 horas úteis" in guidance

    assert queue.status_code == 200
    rows = queue.json()
    summaries = [row["summary"] for row in rows]
    assert summaries.index("Cliente reclamou do pedido 8") < summaries.index(
        "Cliente pediu uma pessoa"
    )
    newest = rows[summaries.index("Cliente reclamou do pedido 8")]
    assert set(newest) == {
        "id",
        "conversation_id",
        "reason",
        "summary",
        "order_id",
        "created_at",
    }
    assert "phone" not in newest
    assert "email" not in newest


def test_chat_handoff_on_saturday_afternoon_names_monday_except_holidays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_env(monkeypatch)
    model = _handoff_then_echo("customer_request", "Cliente pediu ajuda no sábado")

    from lia_api.main import create_app

    with TestClient(create_app(model=model, clock=FrozenClock(TUESDAY_MORNING))) as client:
        response = client.post(
            "/chat",
            json={
                "conversation_id": "conv-saturday",
                "message": "Tem alguém aí?",
                "simulated_at": "2026-11-07T15:00:00",
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == SATURDAY_AFTERNOON
    assert response.json()["handoff"]["reason"] == "customer_request"


def test_chat_handoff_on_sunday_names_monday_except_holidays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_env(monkeypatch)
    model = _handoff_then_echo("policy_gap", "Pergunta que o manual não cobre")

    from lia_api.main import create_app

    with TestClient(create_app(model=model, clock=FrozenClock(TUESDAY_MORNING))) as client:
        response = client.post(
            "/chat",
            json={
                "conversation_id": "conv-sunday",
                "message": "Vocês alugam estúdio?",
                "simulated_at": "2026-11-08T11:00:00",
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == SUNDAY


def test_conversation_continues_after_a_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_env(monkeypatch)
    model = ScriptedChatModel(
        responses=[
            ScriptedToolCall(
                name="record_handoff",
                args={"reason": "customer_request", "summary": "Pediu uma pessoa e seguiu"},
            ),
            "__echo_tool__",
            "O violão ainda está disponível.",
        ]
    )

    from lia_api.main import create_app

    with TestClient(create_app(model=model, clock=FrozenClock(TUESDAY_MORNING))) as client:
        first = client.post(
            "/chat",
            json={"conversation_id": "conv-continue", "message": "Quero um atendente"},
        )
        second = client.post(
            "/chat",
            json={"conversation_id": "conv-continue", "message": "E o violão, tem?"},
        )

    assert first.json()["reply"] == DURING_HOURS
    assert first.json()["handoff"]["reason"] == "customer_request"
    assert second.status_code == 200
    assert second.json()["reply"] == "O violão ainda está disponível."
    assert second.json()["handoff"] is None
    follow_up = [
        (message.type, message.content)
        for message in model.calls[-1]
        if message.type != "system"
    ]
    assert ("human", "Quero um atendente") in follow_up
    assert ("human", "E o violão, tem?") in follow_up


def test_simulated_time_is_ignored_outside_development(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_env(monkeypatch, app_env="production")
    model = _handoff_then_echo("customer_request", "Horário simulado ignorado")

    from lia_api.main import create_app

    with TestClient(create_app(model=model, clock=FrozenClock(TUESDAY_MORNING))) as client:
        response = client.post(
            "/chat",
            json={
                "conversation_id": "conv-prod-clock",
                "message": "Quero um humano",
                "simulated_at": "2026-11-08T11:00:00",
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == DURING_HOURS
