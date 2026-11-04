import json
import os
from uuid import uuid4

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


def _ask(monkeypatch: pytest.MonkeyPatch, message: str, tool_args: dict[str, object], reply: str):
    _prepare_env(monkeypatch)
    model = ScriptedChatModel(
        responses=[
            ScriptedToolCall(name="search_catalog", args=tool_args),
            reply,
        ]
    )
    from lia_api.main import create_app

    with TestClient(create_app(model=model)) as client:
        response = client.post(
            "/chat",
            json={"conversation_id": f"catalog-{uuid4()}", "message": message},
        )
    assert response.status_code == 200
    assert response.json()["reply"] == reply
    assert response.json()["handoff"] is None
    tool_messages = [item for item in model.calls[1] if item.type == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0].name == "search_catalog"
    content = tool_messages[0].content
    assert isinstance(content, str)
    return json.loads(content)


def test_chat_repeats_the_customers_price_range_without_a_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = (
        "Estas são as opções de violões disponíveis por até R$ 1.000:\n"
        "- Tagima Memphis AC-39 Nylon Natural: R$ 429,90"
    )
    result = _ask(
        monkeypatch,
        "Quais opções de violões disponíveis custando até R$ 1000?",
        {"category": "Violões", "maximum_price": "1000"},
        reply,
    )

    assert result["price_range"] == {"minimum_price": None, "maximum_price": "1000.00"}
    names = {product["name"] for product in result["products"]}
    assert "Tagima Memphis AC-39 Nylon Natural" in names
    assert "Takamine GD20 Dreadnought Natural" not in names


def test_chat_answers_price_and_active_promotion(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = (
        "O Taylor 110e Dreadnought Natural custa R$ 5999.00, "
        "com 8% de desconto, saindo a R$ 5519.08."
    )
    result = _ask(
        monkeypatch,
        "Quanto custa o Taylor 110e?",
        {"name": "Taylor 110e"},
        reply,
    )

    product = result["products"][0]
    assert product["price"] == "5999.00"
    assert product["availability"] == "available"
    assert product["active_promotion"] == {
        "discount_percent": "8",
        "promotional_price": "5519.08",
    }


def test_chat_answers_temporarily_unavailable_with_alternatives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = (
        "O Giannini GF-3D está temporariamente indisponível. "
        "Posso sugerir o Yamaha C40 Nylon Natural."
    )
    result = _ask(
        monkeypatch,
        "O Giannini GF-3D está disponível?",
        {"name": "Giannini GF-3D"},
        reply,
    )

    product = result["products"][0]
    assert product["availability"] == "temporarily_unavailable"
    assert "stock_quantity" not in product
    names = {item["name"] for item in product["alternatives"]}
    assert "Yamaha C40 Nylon Natural" in names
    assert "Giannini GF-3D Dreadnought Sunburst" not in names


def test_chat_answers_discontinued_product(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = "O Ibanez Bass 2X Electric Bass foi descontinuado. Temos outros baixos disponíveis."
    result = _ask(
        monkeypatch,
        "Vocês ainda têm o Ibanez Bass 2X?",
        {"name": "Ibanez Bass 2X"},
        reply,
    )

    product = result["products"][0]
    assert product["status"] == "discontinued"
    assert "availability" not in product
    assert "13" not in json.dumps(product)
    assert product["alternatives"]


def test_chat_answers_coming_soon_without_a_price(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = "O Kala KA-ABP-CTG Barítono Cedar ainda não está à venda, então não informo preço."
    result = _ask(
        monkeypatch,
        "Qual o preço do Kala KA-ABP-CTG?",
        {"name": "Kala KA-ABP-CTG"},
        reply,
    )

    product = result["products"][0]
    assert product["status"] == "coming_soon"
    assert "price" not in product
    assert "1749" not in json.dumps(result)


def test_chat_answers_unstocked_category(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = "Cordas orquestrais estão temporariamente sem produtos, não saíram do catálogo."
    result = _ask(
        monkeypatch,
        "Vocês têm violino?",
        {"category": "Cordas Orquestrais"},
        reply,
    )

    assert result["products"] == []
    assert result["unstocked_categories"] == ["Cordas Orquestrais"]
    assert "discontinued" not in json.dumps(result)

    from lia_api.prompt import STORE_SYSTEM_PROMPT

    guidance = STORE_SYSTEM_PROMPT.casefold()
    assert "ofereça um encaminhamento" in guidance
    assert "quando o estoque chegar" in guidance


def test_chat_does_not_offer_an_expired_promotion(monkeypatch: pytest.MonkeyPatch) -> None:
    reply = (
        "A Black Friday do Crafter CE-15 não está mais em vigor. "
        "O preço atual é R$ 3899.00."
    )
    result = _ask(
        monkeypatch,
        "O Crafter CE-15 está na Black Friday?",
        {"name": "Crafter CE-15"},
        reply,
    )

    product = result["products"][0]
    assert product["price"] == "3899.00"
    assert product["active_promotion"] is None
    assert product["inactive_promotions"] == ["Black Friday"]
    assert "3509.10" not in json.dumps(product)
