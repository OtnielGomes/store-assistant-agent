import json
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from lia_api.ingest import load_store
from tests.order_examples import (
    ORDERS,
    STORE_CLOCK,
    assert_no_customer_contact,
    verified_payload,
)
from tests.scripted_model import ScriptedChatModel, ScriptedToolCall

LOCKED = {"verified": False, "verification_locked": True, "order": None}


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


def _client(monkeypatch: pytest.MonkeyPatch, responses: list[str | ScriptedToolCall]):
    _prepare_env(monkeypatch)
    model = ScriptedChatModel(responses=responses)
    from lia_api.main import create_app

    return model, TestClient(create_app(model=model, clock=STORE_CLOCK))


def test_chat_verifies_every_order_and_replies_with_the_first_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: list[str | ScriptedToolCall] = []
    replies: list[str] = []
    for example in ORDERS:
        first_name = example["customer_first_name"]
        total = example["order_total"]
        reply = f"{first_name}, o total registrado do pedido é R$ {total}."
        replies.append(reply)
        responses.append(
            ScriptedToolCall(
                name="lookup_order",
                args={
                    "order_number": example["order_number"],
                    "phone_last_four": example["phone_last_four"],
                },
            )
        )
        responses.append(reply)

    model, client = _client(monkeypatch, responses)
    with client:
        for index, example in enumerate(ORDERS):
            conversation_id = f"chat-order-{example['order_number']}-{uuid4()}"
            response = client.post(
                "/chat",
                json={
                    "conversation_id": conversation_id,
                    "message": f"Quero consultar o pedido {example['order_number']}.",
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["reply"] == replies[index]
            assert body["handoff"] is None
            assert example["customer_first_name"] in body["reply"]
            assert_no_customer_contact(body["reply"], example)

            tool_messages = [item for item in model.calls[2 * index + 1] if item.type == "tool"]
            assert len(tool_messages) == 1
            assert tool_messages[0].name == "lookup_order"
            content = tool_messages[0].content
            assert isinstance(content, str)
            assert json.loads(content) == verified_payload(example)
            assert_no_customer_contact(content, example)

            trace = "\n".join(
                str(message.content)
                for message in model.calls[2 * index + 1]
                if message.type != "system"
            )
            assert_no_customer_contact(trace, example)


def test_chat_keeps_the_verified_first_name_for_the_rest_of_the_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    example = ORDERS[7]
    first_reply = "Ana, seu pedido foi enviado."
    follow_up = "Ana, sigo por aqui se precisar de mais alguma coisa."
    model, client = _client(
        monkeypatch,
        [
            ScriptedToolCall(
                name="lookup_order",
                args={"order_number": 8, "phone_last_four": "7821"},
            ),
            first_reply,
            follow_up,
        ],
    )
    conversation_id = f"chat-name-{uuid4()}"
    with client:
        opened = client.post(
            "/chat",
            json={"conversation_id": conversation_id, "message": "Consulta o pedido 8."},
        )
        continued = client.post(
            "/chat",
            json={"conversation_id": conversation_id, "message": "Pode repetir meu nome?"},
        )

    assert opened.json()["reply"] == first_reply
    assert continued.json()["reply"] == follow_up
    assert continued.json()["handoff"] is None

    later_turn = "\n".join(
        str(message.content) for message in model.calls[2] if message.type != "system"
    )
    assert "Ana" in later_turn
    assert "customer_first_name" in later_turn
    assert_no_customer_contact(later_turn, example)
    assert_no_customer_contact(continued.json()["reply"], example)


def test_chat_offers_a_handoff_when_verification_locks(monkeypatch: pytest.MonkeyPatch) -> None:
    example = ORDERS[7]
    during_hours = "A equipe continua o atendimento em instantes."
    model, client = _client(
        monkeypatch,
        [
            ScriptedToolCall(
                name="lookup_order",
                args={"order_number": 8, "phone_last_four": "0000"},
            ),
            "Não localizei esse pedido.",
            ScriptedToolCall(
                name="lookup_order",
                args={"order_number": 8, "phone_last_four": "1111"},
            ),
            "Os dígitos ainda não conferem.",
            ScriptedToolCall(
                name="lookup_order",
                args={"order_number": 8, "phone_last_four": "2222"},
            ),
            ScriptedToolCall(
                name="record_handoff",
                args={
                    "reason": "verification_locked",
                    "summary": "O cliente não conseguiu confirmar o pedido 8.",
                    "order_id": 8,
                },
            ),
            during_hours,
            ScriptedToolCall(
                name="lookup_order",
                args={"order_number": 8, "phone_last_four": "7821"},
            ),
            during_hours,
        ],
    )
    conversation_id = f"chat-lock-{uuid4()}"
    messages = [
        "Os quatro dígitos são 0000.",
        "Tenta 1111.",
        "Agora 2222.",
        "Os quatro dígitos são 7821.",
    ]
    with client:
        responses = [
            client.post(
                "/chat",
                json={"conversation_id": conversation_id, "message": message},
            )
            for message in messages
        ]
        listed = client.get("/handoffs").json()

    assert responses[0].json()["handoff"] is None
    assert responses[1].json()["handoff"] is None
    locked_body = responses[2].json()
    assert locked_body["reply"] == during_hours
    assert locked_body["handoff"]["reason"] == "verification_locked"
    assert isinstance(locked_body["handoff"]["id"], int)
    assert responses[3].json()["reply"] == during_hours
    assert responses[3].json()["handoff"] is None
    assert_no_customer_contact(locked_body["reply"], example)
    recorded = next(item for item in listed if item["id"] == locked_body["handoff"]["id"])
    assert recorded["reason"] == "verification_locked"
    assert recorded["order_id"] == 8
    assert recorded["summary"] == "O cliente não conseguiu confirmar o pedido 8."
    assert_no_customer_contact(json.dumps(recorded, ensure_ascii=False), example)

    lookup_payloads = []
    for call in model.calls:
        for message in call:
            if message.type != "tool":
                continue
            content = message.content
            assert isinstance(content, str)
            assert_no_customer_contact(content, example)
            if message.name == "record_handoff":
                assert content == during_hours
            if message.name == "lookup_order":
                lookup_payloads.append(json.loads(content))
    assert lookup_payloads[-2] == LOCKED
    assert lookup_payloads[-1] == LOCKED
    assert "Ana" not in json.dumps(lookup_payloads[-1])
    assert "349.90" not in json.dumps(lookup_payloads[-1])
