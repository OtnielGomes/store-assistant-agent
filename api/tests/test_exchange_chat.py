import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, BaseMessage

from lia_api.ingest import load_store
from tests.order_examples import ORDERS, STORE_CLOCK, assert_no_customer_contact
from tests.policy_embedder import embed_for_tests
from tests.scripted_model import ScriptedChatModel, ScriptedToolCall

DURING_HOURS = "A equipe continua o atendimento em instantes."
FORBIDDEN_PHONE = "(67) 3341-4444"


def _prepare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.fail("DATABASE_URL must point at a real PostgreSQL database")
    load_store(database_url)
    from lia_api.policies import ingest_policies

    ingest_policies(database_url, embed_for_tests)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "none")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.delenv("OPENAI_TEMPERATURE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _client(monkeypatch: pytest.MonkeyPatch, responses: list):
    _prepare_env(monkeypatch)
    model = ScriptedChatModel(responses=responses)
    from lia_api.main import create_app

    return model, TestClient(create_app(model=model, clock=STORE_CLOCK, embedder=embed_for_tests))


def _system_text(messages: list[BaseMessage]) -> str:
    system = next(message for message in messages if message.type == "system")
    assert isinstance(system.content, str)
    return system.content


def _tool_message(messages: list[BaseMessage], name: str) -> str:
    found = [message for message in messages if message.type == "tool" and message.name == name]
    assert found, name
    content = found[-1].content
    assert isinstance(content, str)
    return content


def _policy_sections(text: str, prefixes: tuple[str, ...]) -> str:
    blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
    kept: list[str] = []
    keeping = False
    for block in blocks:
        if block.startswith("Seção "):
            number = block.removeprefix("Seção ").split(" ", 1)[0].split("—", 1)[0].strip()
            keeping = any(
                number == prefix or number.startswith(f"{prefix}.") for prefix in prefixes
            )
        if keeping:
            kept.append(block)
    return "\n\n".join(kept)


def _window_lines(payload: dict[str, object]) -> str:
    deadlines = payload["deadlines"]
    assert isinstance(deadlines, dict)
    labels = (
        ("withdrawal_and_preference_exchange", 7),
        ("defect_exchange", 30),
        ("legal_warranty", 90),
    )
    lines: list[str] = []
    for key, days in labels:
        item = deadlines[key]
        assert isinstance(item, dict)
        when = item["deadline_day_month"]
        if item["open"]:
            lines.append(f"Pelo sistema, o prazo de {days} dias segue aberto até {when}.")
        else:
            lines.append(f"Pelo sistema, o prazo de {days} dias terminou em {when}.")
    return "\n".join(lines)


def _claim_reply(messages: list[BaseMessage]) -> AIMessage:
    policy = _policy_sections(_tool_message(messages, "search_policies"), ("4", "8"))
    window = json.loads(_tool_message(messages, "check_return_window"))
    handoff = _tool_message(messages, "record_handoff")
    if not policy or not isinstance(window, dict) or "deadlines" not in window:
        return AIMessage(content="ferramentas de troca incompletas")
    return AIMessage(content=f"{policy}\n{_window_lines(window)}\n{handoff}")


def _lookup_reply(example: dict[str, object]) -> str:
    return (
        f"{example['customer_first_name']}, o total registrado do pedido "
        f"é R$ {example['order_total']}."
    )


def _overdue_reply(messages: list[BaseMessage]) -> AIMessage:
    lookup = json.loads(_tool_message(messages, "lookup_order"))
    handoff = _tool_message(messages, "record_handoff")
    order = lookup["order"]
    estimated = order["estimated_delivery"]
    assert isinstance(estimated, str)
    year, month, day = estimated.split("-")
    return AIMessage(
        content=(
            f"A previsão de entrega {day}/{month}/{year} já passou. {handoff}"
        )
    )


def _coming_soon_reply(messages: list[BaseMessage]) -> AIMessage:
    lookup = json.loads(_tool_message(messages, "lookup_order"))
    handoff = _tool_message(messages, "record_handoff")
    items = lookup["order"]["items"]
    names = [item["name"] for item in items if item["status"] == "coming_soon"]
    if not names:
        return AIMessage(content="item em breve ausente")
    return AIMessage(
        content=(
            f"O pedido aberto inclui um item em breve: {names[0]}. {handoff}"
        )
    )


def _value_reply(messages: list[BaseMessage]) -> AIMessage:
    policy = _policy_sections(_tool_message(messages, "search_policies"), ("3", "6"))
    handoff = _tool_message(messages, "record_handoff")
    lookup = json.loads(_tool_message(messages, "lookup_order"))
    total = lookup["order"]["order_total"]
    rule = "5% de desconto sobre o preço de tabela"
    if rule not in policy:
        return AIMessage(content="regra de PIX ausente")
    return AIMessage(
        content=(
            f"O total registrado é R$ {total}. {policy} "
            f"Não confirmo um erro e não prometo reembolso. {handoff}"
        )
    )


def _assert_store_identity(prompt: str) -> None:
    assert "assistente virtual" in prompt.lower()
    assert "(67) 3321-4500" in prompt
    assert "contato@emporiodamusica.com.br" in prompt
    assert FORBIDDEN_PHONE not in prompt


def _assert_handoff_rules(prompt: str) -> None:
    folded = prompt.lower()
    assert "não aprove nem recuse" in folded
    assert "exchange_return_refund_warranty" in prompt
    assert "esteja o prazo aberto ou encerrado" in folded
    assert "como funciona" in folded
    assert "não abra encaminhamento" in folded
    assert "overdue_delivery" in prompt
    assert "coming_soon_item" in prompt
    assert "value_question" in prompt
    assert "total_differs_from_items" in prompt
    assert "pix_without_discount" in prompt
    assert "não confirme erro" in folded or "não confirme um erro" in folded
    assert "não prometa reembolso" in folded


@pytest.mark.parametrize(
    ("message", "order_number", "phone_last_four", "example", "query", "section"),
    [
        (
            "Quero trocar o instrumento do pedido 7.",
            7,
            "0123",
            ORDERS[6],
            "trocas por preferencia defeito arrependimento",
            "Seção 4.3",
        ),
        (
            "Quero devolver o pedido 7.",
            7,
            "0123",
            ORDERS[6],
            "trocas por preferencia defeito arrependimento",
            "Seção 4.1",
        ),
        (
            "Quero o reembolso do pedido 7.",
            7,
            "0123",
            ORDERS[6],
            "reembolso da devolução",
            "Seção 4.1",
        ),
        (
            "Quero acionar a garantia do pedido 7.",
            7,
            "0123",
            ORDERS[6],
            "garantia legal de 90 dias",
            "Seção 8.1",
        ),
        (
            "Quero acionar a garantia do pedido 4.",
            4,
            "3456",
            ORDERS[3],
            "garantia legal de 90 dias",
            "Seção 8.1",
        ),
    ],
)
def test_executing_a_claim_explains_the_policy_reports_deadlines_and_hands_off(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    order_number: int,
    phone_last_four: str,
    example: dict[str, object],
    query: str,
    section: str,
) -> None:
    model, client = _client(
        monkeypatch,
        [
            ScriptedToolCall(name="search_policies", args={"query": query}),
            ScriptedToolCall(
                name="check_return_window",
                args={"order_number": order_number, "phone_last_four": phone_last_four},
            ),
            ScriptedToolCall(
                name="record_handoff",
                args={
                    "reason": "exchange_return_refund_warranty",
                    "summary": f"Cliente pediu providência no pedido {order_number}.",
                    "order_id": order_number,
                },
            ),
            _claim_reply,
        ],
    )
    conversation_id = f"claim-{order_number}-{uuid4()}"
    with client:
        response = client.post(
            "/chat",
            json={"conversation_id": conversation_id, "message": message},
        )
        listed = client.get("/handoffs").json()

    assert response.status_code == 200
    body = response.json()
    reply = body["reply"]
    assert body["handoff"]["reason"] == "exchange_return_refund_warranty"
    assert section in reply
    assert DURING_HOURS in reply
    assert "aprovada" not in reply.lower()
    assert "negada" not in reply.lower()
    assert FORBIDDEN_PHONE not in reply
    assert_no_customer_contact(reply, example)
    if order_number == 7:
        assert "Pelo sistema, o prazo de 7 dias terminou em 06/10." in reply
        assert "Pelo sistema, o prazo de 30 dias terminou em 29/10." in reply
        assert "Pelo sistema, o prazo de 90 dias segue aberto até 28/12." in reply
    else:
        assert "Pelo sistema, o prazo de 90 dias terminou em 01/11." in reply

    prompt = _system_text(model.calls[0])
    _assert_store_identity(prompt)
    _assert_handoff_rules(prompt)
    window = json.loads(_tool_message(model.calls[2], "check_return_window"))
    assert window["receipt_date"] == example["estimated_delivery"]
    policy = _tool_message(model.calls[1], "search_policies")
    assert section in policy
    recorded = next(item for item in listed if item["id"] == body["handoff"]["id"])
    assert recorded["reason"] == "exchange_return_refund_warranty"
    assert recorded["order_id"] == order_number
    assert_no_customer_contact(json.dumps(recorded, ensure_ascii=False), example)


def test_asking_how_exchanges_work_explains_the_policy_without_a_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explain(messages: list[BaseMessage]) -> AIMessage:
        policy = _policy_sections(
            _tool_message(messages, "search_policies"),
            ("4",),
        )
        if "Seção 4.1" not in policy or "Seção 4.3" not in policy:
            return AIMessage(content="política de troca ausente")
        return AIMessage(content=policy)

    model, client = _client(
        monkeypatch,
        [
            ScriptedToolCall(
                name="search_policies",
                args={"query": "trocas por preferencia defeito arrependimento"},
            ),
            explain,
        ],
    )
    with client:
        response = client.post(
            "/chat",
            json={
                "conversation_id": f"how-exchange-{uuid4()}",
                "message": "Como funcionam as trocas?",
            },
        )

    body = response.json()
    assert body["handoff"] is None
    assert "Seção 4.1" in body["reply"]
    assert "Seção 4.3" in body["reply"]
    assert "30 (trinta) dias" in body["reply"]
    assert "record_handoff" not in [message.name for call in model.calls for message in call]
    prompt = _system_text(model.calls[0])
    assert "como funciona" in prompt.lower()
    assert "não abra encaminhamento" in prompt.lower()


def test_looking_up_order_8_mentions_the_overdue_delivery_and_hands_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example = ORDERS[7]
    model, client = _client(
        monkeypatch,
        [
            ScriptedToolCall(
                name="lookup_order",
                args={"order_number": 8, "phone_last_four": "7821"},
            ),
            ScriptedToolCall(
                name="record_handoff",
                args={
                    "reason": "overdue_delivery",
                    "summary": "A previsão de entrega do pedido 8 já passou.",
                    "order_id": 8,
                },
            ),
            _overdue_reply,
        ],
    )
    with client:
        response = client.post(
            "/chat",
            json={
                "conversation_id": f"overdue-{uuid4()}",
                "message": "Consulta o pedido 8. Os quatro dígitos são 7821.",
            },
        )

    body = response.json()
    assert body["handoff"]["reason"] == "overdue_delivery"
    assert "10/10/2026" in body["reply"]
    assert "já passou" in body["reply"]
    assert DURING_HOURS in body["reply"]
    assert_no_customer_contact(body["reply"], example)
    lookup = json.loads(_tool_message(model.calls[1], "lookup_order"))
    assert lookup["order"]["anomaly_flags"] == ["overdue_delivery"]
    assert "overdue_delivery" in _system_text(model.calls[0])


def test_looking_up_order_14_mentions_the_coming_soon_item_and_hands_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example = ORDERS[13]
    model, client = _client(
        monkeypatch,
        [
            ScriptedToolCall(
                name="lookup_order",
                args={"order_number": 14, "phone_last_four": "9876"},
            ),
            ScriptedToolCall(
                name="record_handoff",
                args={
                    "reason": "coming_soon_item",
                    "summary": "O pedido 14 tem um item em breve.",
                    "order_id": 14,
                },
            ),
            _coming_soon_reply,
        ],
    )
    with client:
        response = client.post(
            "/chat",
            json={
                "conversation_id": f"soon-{uuid4()}",
                "message": "Consulta o pedido 14. Os quatro dígitos são 9876.",
            },
        )

    body = response.json()
    assert body["handoff"]["reason"] == "coming_soon_item"
    assert "Kala KA-ABP-CTG Barítono Cedar" in body["reply"]
    assert "em breve" in body["reply"]
    assert DURING_HOURS in body["reply"]
    assert_no_customer_contact(body["reply"], example)
    lookup = json.loads(_tool_message(model.calls[1], "lookup_order"))
    assert "coming_soon_item_in_open_order" in lookup["order"]["anomaly_flags"]


@pytest.mark.parametrize(
    ("example", "question"),
    [
        (ORDERS[2], "Esse total está certo? Os itens não fecham."),
        (ORDERS[19], "O valor registrado está certo? A soma dos itens é outra."),
        (ORDERS[0], "Por que o PIX não teve o desconto?"),
    ],
    ids=["order-3", "order-20", "pix-order-1"],
)
def test_value_divergence_is_silent_until_the_customer_questions_the_amount(
    monkeypatch: pytest.MonkeyPatch,
    example: dict[str, object],
    question: str,
) -> None:
    neutral = _lookup_reply(example)
    model, client = _client(
        monkeypatch,
        [
            ScriptedToolCall(
                name="lookup_order",
                args={
                    "order_number": example["order_number"],
                    "phone_last_four": example["phone_last_four"],
                },
            ),
            neutral,
            ScriptedToolCall(
                name="search_policies",
                args={"query": "desconto PIX preco de tabela"},
            ),
            ScriptedToolCall(
                name="record_handoff",
                args={
                    "reason": "value_question",
                    "summary": f"Cliente questionou o valor do pedido {example['order_number']}.",
                    "order_id": example["order_number"],
                },
            ),
            _value_reply,
        ],
    )
    conversation_id = f"value-{example['order_number']}-{uuid4()}"
    with client:
        opened = client.post(
            "/chat",
            json={
                "conversation_id": conversation_id,
                "message": f"Consulta o pedido {example['order_number']}.",
            },
        )
        questioned = client.post(
            "/chat",
            json={"conversation_id": conversation_id, "message": question},
        )

    first = opened.json()
    second = questioned.json()
    assert first["handoff"] is None
    assert first["reply"] == neutral
    assert "diverg" not in first["reply"].lower()
    assert "desconto" not in first["reply"].lower()
    assert "erro" not in first["reply"].lower()
    lookup = json.loads(_tool_message(model.calls[1], "lookup_order"))
    assert example["anomaly_flags"] == lookup["order"]["anomaly_flags"]
    assert example["anomaly_flags"]
    assert second["handoff"]["reason"] == "value_question"
    assert example["order_total"] in second["reply"]
    assert "5% de desconto sobre o preço de tabela" in second["reply"]
    assert "Não confirmo um erro" in second["reply"]
    assert "não prometo reembolso" in second["reply"]
    assert "há um erro" not in second["reply"].lower()
    assert "vou reembolsar" not in second["reply"].lower()
    assert DURING_HOURS in second["reply"]
    assert FORBIDDEN_PHONE not in second["reply"]
    assert_no_customer_contact(second["reply"], example)
    prompt = _system_text(model.calls[0])
    assert "pix_without_discount" in prompt
    assert "total_differs_from_items" in prompt
    assert "value_question" in prompt
