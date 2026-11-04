import os

import pytest
from fastapi.testclient import TestClient

from tests.scripted_model import ScriptedChatModel

REPLY = "Olá! Eu sou a Lia, assistente virtual do Empório da Música."


def _prepare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.fail("DATABASE_URL must point at a real PostgreSQL database")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "none")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.delenv("OPENAI_TEMPERATURE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_chat_returns_scripted_reply_and_gives_the_model_store_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_env(monkeypatch)
    model = ScriptedChatModel(responses=[REPLY])

    from lia_api.main import create_app

    with TestClient(create_app(model=model)) as client:
        response = client.post(
            "/chat",
            json={"conversation_id": "conv-greeting", "message": "Oi"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == REPLY
    assert body["handoff"] is None

    system_messages = [message for message in model.calls[0] if message.type == "system"]
    assert len(system_messages) == 1
    content = system_messages[0].content
    assert isinstance(content, str)
    assert "Empório da Música" in content
    assert "Rua 14 de Maio, 3200" in content
    assert "(67) 3321-4500" in content
    assert "contato@emporiodamusica.com.br" in content
    assert "assistente virtual" in content.lower()
    assert "(67) 3341-4444" not in content
