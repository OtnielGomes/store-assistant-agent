import json
import os

import pytest
from fastapi.testclient import TestClient

from lia_api.ingest import load_store
from tests.scripted_model import ScriptedChatModel, ScriptedToolCall


def _prepare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.fail("DATABASE_URL must point at a real PostgreSQL database")
    load_store(database_url)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "none")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.delenv("OPENAI_TEMPERATURE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _system_text(model: ScriptedChatModel) -> str:
    system_messages = [message for message in model.calls[0] if message.type == "system"]
    assert len(system_messages) == 1
    content = system_messages[0].content
    assert isinstance(content, str)
    return content


def test_follow_up_pix_uses_the_product_from_earlier_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_env(monkeypatch)
    price_reply = "O Shelby SU-21S Soprano Sunburst custa R$ 159.90."
    pix_reply = "No PIX, o Shelby SU-21S sai a R$ 151.91."
    model = ScriptedChatModel(
        responses=[
            ScriptedToolCall(name="search_catalog", args={"name": "Shelby SU-21S"}),
            price_reply,
            ScriptedToolCall(name="calculate_price", args={"name": "Shelby SU-21S"}),
            pix_reply,
            "Pode me dizer qual produto?",
        ]
    )

    from lia_api.main import create_app

    with TestClient(create_app(model=model)) as client:
        first = client.post(
            "/chat",
            json={
                "conversation_id": "calc-pix-follow-up",
                "message": "Quanto custa o Shelby SU-21S?",
            },
        )
        second = client.post(
            "/chat",
            json={"conversation_id": "calc-pix-follow-up", "message": "e no PIX?"},
        )
        fresh = client.post(
            "/chat",
            json={"conversation_id": "calc-pix-fresh", "message": "e no PIX?"},
        )

    assert first.status_code == 200
    assert first.json()["reply"] == price_reply
    assert first.json()["handoff"] is None
    assert second.status_code == 200
    assert second.json()["reply"] == pix_reply
    assert fresh.json()["reply"] == "Pode me dizer qual produto?"

    earlier_turn = [
        message.content
        for message in model.calls[2]
        if message.type == "human" and isinstance(message.content, str)
    ]
    assert earlier_turn == ["Quanto custa o Shelby SU-21S?", "e no PIX?"]

    tool_messages = [
        message
        for message in model.calls[3]
        if message.type == "tool" and message.name == "calculate_price"
    ]
    assert len(tool_messages) == 1
    quote = json.loads(tool_messages[0].content)
    assert quote["name"] == "Shelby SU-21S Soprano Sunburst"
    assert quote["list_price"] == "159.90"
    assert quote["active_promotion"] is None
    assert quote["pix_price"] == "151.91"

    fresh_text = " ".join(
        message.content
        for message in model.calls[4]
        if message.type != "system" and isinstance(message.content, str)
    )
    assert "Shelby" not in fresh_text


def test_shipping_without_a_city_asks_which_city(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_env(monkeypatch)
    reply = "Para qual cidade devo calcular o frete do Shelby SU-21S?"
    model = ScriptedChatModel(
        responses=[
            ScriptedToolCall(name="calculate_price", args={"name": "Shelby SU-21S"}),
            reply,
        ]
    )

    from lia_api.main import create_app

    with TestClient(create_app(model=model)) as client:
        response = client.post(
            "/chat",
            json={
                "conversation_id": "calc-shipping-city",
                "message": "Quanto fica o frete do Shelby SU-21S?",
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == reply
    prompt = _system_text(model)
    assert "Empório da Música" in prompt
    assert "(67) 3321-4500" in prompt
    assert "contato@emporiodamusica.com.br" in prompt
    assert "assistente virtual" in prompt.lower()
    assert "(67) 3341-4444" not in prompt
    assert "pergunte a cidade" in prompt.lower()

    tool_messages = [message for message in model.calls[1] if message.type == "tool"]
    quote = json.loads(tool_messages[0].content)
    assert quote["shipping"]["city_required"] is True
    assert "cost" not in quote["shipping"]


def test_chat_quotes_campo_grande_and_other_city_carriers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_env(monkeypatch)
    campo_reply = "O frete do Shelby SU-21S para Campo Grande é R$ 35.00, em 1 a 3 dias úteis."
    other_reply = (
        "Para Dourados o valor do frete é calculado pelo CEP na finalização. "
        "PAC leva 5 a 12 dias úteis, SEDEX 2 a 5 dias úteis e Jadlog 3 a 8 dias úteis."
    )
    model = ScriptedChatModel(
        responses=[
            ScriptedToolCall(
                name="calculate_price",
                args={"name": "Shelby SU-21S", "city": "Campo Grande"},
            ),
            campo_reply,
            ScriptedToolCall(
                name="calculate_price",
                args={"name": "Yamaha C40", "city": "Dourados"},
            ),
            other_reply,
        ]
    )

    from lia_api.main import create_app

    with TestClient(create_app(model=model)) as client:
        campo = client.post(
            "/chat",
            json={
                "conversation_id": "calc-campo-grande",
                "message": "Quanto fica o frete do Shelby SU-21S para Campo Grande?",
            },
        )
        other = client.post(
            "/chat",
            json={
                "conversation_id": "calc-dourados",
                "message": "E o frete do Yamaha C40 para Dourados?",
            },
        )

    assert campo.json()["reply"] == campo_reply
    assert other.json()["reply"] == other_reply

    campo_tool = [message for message in model.calls[1] if message.type == "tool"]
    campo_quote = json.loads(campo_tool[0].content)
    assert campo_quote["shipping"]["cost"] == "35.00"
    assert campo_quote["shipping"]["free"] is False
    assert campo_quote["shipping"]["business_days"] == "1 a 3"

    other_tool = [message for message in model.calls[3] if message.type == "tool"]
    other_quote = json.loads(other_tool[0].content)
    assert other_quote["shipping"]["carriers"] == [
        {"name": "PAC (Correios)", "timeframe": "5 a 12 dias úteis"},
        {"name": "SEDEX (Correios)", "timeframe": "2 a 5 dias úteis"},
        {"name": "Jadlog (.package)", "timeframe": "3 a 8 dias úteis"},
    ]
    assert "CEP" in other_quote["shipping"]["message"]
