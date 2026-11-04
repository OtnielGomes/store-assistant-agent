import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from lia_api.ingest import load_store
from tests.scripted_model import ScriptedChatModel, ScriptedToolCall

TOO_LONG_REPLY = (
    "Sua mensagem passou do limite de 2000 caracteres. Envie um trecho menor, por favor."
)
SAFE_REPLY = (
    "Não consegui confirmar um valor ou uma data desta resposta. "
    "Vou pedir para um atendente continuar o atendimento."
)


def _prepare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.fail("DATABASE_URL must point at a real PostgreSQL database")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "none")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.delenv("OPENAI_TEMPERATURE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_overlong_message_gets_a_friendly_reply_and_never_reaches_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_env(monkeypatch)
    model = ScriptedChatModel(responses=["não deveria ser chamada"])

    from lia_api.main import create_app

    with TestClient(create_app(model=model)) as client:
        response = client.post(
            "/chat",
            json={
                "conversation_id": "guard-too-long",
                "message": "a" * 2001,
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == TOO_LONG_REPLY
    assert response.json()["handoff"] is None
    assert model.calls == []


def _human_texts(model: ScriptedChatModel) -> list[str]:
    texts: list[str] = []
    for call in model.calls:
        for message in call:
            if message.type == "human" and isinstance(message.content, str):
                texts.append(message.content)
    return texts


def test_cpf_is_masked_before_the_model_sees_the_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_env(monkeypatch)
    reply = "Pode me dizer o número do pedido e os quatro últimos dígitos?"
    model = ScriptedChatModel(responses=[reply])

    from lia_api.main import create_app

    with TestClient(create_app(model=model)) as client:
        response = client.post(
            "/chat",
            json={
                "conversation_id": "guard-cpf-formatted",
                "message": "Meu CPF é 529.982.247-25 e os 4 dígitos são 4821",
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == reply
    assert response.json()["handoff"] is None
    seen = " ".join(_human_texts(model))
    assert "529.982.247-25" not in seen
    assert "52998224725" not in seen
    assert "[CPF]" in seen
    assert "4821" in seen


def test_unformatted_cpf_is_masked_and_four_digits_stay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_env(monkeypatch)
    reply = "Anotei. Quais são os quatro dígitos do telefone do pedido?"
    model = ScriptedChatModel(responses=[reply])

    from lia_api.main import create_app

    with TestClient(create_app(model=model)) as client:
        response = client.post(
            "/chat",
            json={
                "conversation_id": "guard-cpf-digits",
                "message": "CPF 52998224725, verificação 4821",
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == reply
    seen = " ".join(_human_texts(model))
    assert "52998224725" not in seen
    assert "[CPF]" in seen
    assert "4821" in seen


def test_card_number_is_masked_and_four_digits_stay(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_env(monkeypatch)
    reply = "Não preciso do cartão. Posso ajudar com o pedido?"
    model = ScriptedChatModel(responses=[reply])

    from lia_api.main import create_app

    with TestClient(create_app(model=model)) as client:
        response = client.post(
            "/chat",
            json={
                "conversation_id": "guard-card",
                "message": "Paguei no cartão 4532 0151 1283 0366. Final do telefone 4821",
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == reply
    seen = " ".join(_human_texts(model))
    assert "4532" not in seen
    assert "0151" not in seen
    assert "1283" not in seen
    assert "0366" not in seen
    assert "[cartão]" in seen
    assert "4821" in seen


def test_full_phone_numbers_are_masked_and_four_digits_stay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_env(monkeypatch)
    reply = "Usei só os quatro dígitos para localizar o pedido."
    model = ScriptedChatModel(responses=[reply, reply, reply])

    from lia_api.main import create_app

    messages = [
        "(67) 99988-7766 e os 4 dígitos 4821",
        "Meu número é +55 67 3321-4500, verificação 1042",
        "Telefone 67999887766, final 4821",
    ]
    with TestClient(create_app(model=model)) as client:
        responses = [
            client.post(
                "/chat",
                json={"conversation_id": f"guard-phone-{index}", "message": message},
            )
            for index, message in enumerate(messages)
        ]

    assert all(response.status_code == 200 for response in responses)
    assert all(response.json()["reply"] == reply for response in responses)
    seen = _human_texts(model)
    assert "[telefone] e os 4 dígitos 4821" in seen
    assert "Meu número é [telefone], verificação 1042" in seen
    assert "Telefone [telefone], final 4821" in seen
    joined = " ".join(seen)
    assert "99988" not in joined
    assert "7766" not in joined
    assert "3321-4500" not in joined
    assert "67999887766" not in joined


def test_email_is_masked_before_the_model_sees_the_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_env(monkeypatch)
    reply = "Não uso e-mail para consultar o pedido."
    model = ScriptedChatModel(responses=[reply])

    from lia_api.main import create_app

    with TestClient(create_app(model=model)) as client:
        response = client.post(
            "/chat",
            json={
                "conversation_id": "guard-email",
                "message": "Meu e-mail é ana.souza@example.com e os 4 dígitos são 4821",
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == reply
    seen = " ".join(_human_texts(model))
    assert "ana.souza@example.com" not in seen
    assert "[e-mail]" in seen
    assert "4821" in seen


def _prepare_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_env(monkeypatch)
    load_store(os.environ["DATABASE_URL"])


def test_grounded_price_reaches_the_customer_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare_catalog(monkeypatch)
    reply = "O Shelby SU-21S custa R$ 159,90."
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
                "conversation_id": f"guard-grounded-price-{uuid4()}",
                "message": "Quanto custa o Shelby SU-21S?",
            },
        )

    assert response.status_code == 200
    assert response.json()["reply"] == reply
    assert response.json()["handoff"] is None
    assert _human_texts(model) == [
        "Quanto custa o Shelby SU-21S?",
        "Quanto custa o Shelby SU-21S?",
    ]
    assert len(model.calls) == 2


def test_ungrounded_price_is_replaced_by_one_grounded_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_catalog(monkeypatch)
    grounded = "O valor do Shelby SU-21S é R$ 159,90."
    model = ScriptedChatModel(
        responses=[
            ScriptedToolCall(name="calculate_price", args={"name": "Shelby SU-21S"}),
            "Consigo fechar por R$ 10,00.",
            grounded,
        ]
    )

    from lia_api.main import create_app

    with TestClient(create_app(model=model)) as client:
        response = client.post(
            "/chat",
            json={
                "conversation_id": f"guard-retry-price-{uuid4()}",
                "message": "Quanto custa o Shelby SU-21S?",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == grounded
    assert body["handoff"] is None
    assert "R$ 10,00" not in body["reply"]
    assert _human_texts(model) == ["Quanto custa o Shelby SU-21S?"] * 3
    assert len(model.calls) == 3


def test_ungrounded_date_is_replaced_by_one_grounded_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_catalog(monkeypatch)
    grounded = "O Shelby SU-21S custa R$ 159,90."
    model = ScriptedChatModel(
        responses=[
            ScriptedToolCall(name="calculate_price", args={"name": "Shelby SU-21S"}),
            "A entrega chega em 03/11/2026.",
            grounded,
        ]
    )

    from lia_api.main import create_app

    with TestClient(create_app(model=model)) as client:
        response = client.post(
            "/chat",
            json={
                "conversation_id": f"guard-retry-date-{uuid4()}",
                "message": "Quando chega e quanto custa o Shelby SU-21S?",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == grounded
    assert body["handoff"] is None
    assert "03/11/2026" not in body["reply"]
    assert len(model.calls) == 3


def test_price_from_an_earlier_turn_does_not_ground_a_new_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_catalog(monkeypatch)
    grounded = "O Shelby SU-21S custa R$ 159,90."
    repeated = "Continua R$ 159,90."
    model = ScriptedChatModel(
        responses=[
            ScriptedToolCall(name="calculate_price", args={"name": "Shelby SU-21S"}),
            grounded,
            repeated,
            repeated,
        ]
    )

    from lia_api.main import create_app

    conversation_id = f"guard-prior-turn-price-{uuid4()}"
    with TestClient(create_app(model=model)) as client:
        first = client.post(
            "/chat",
            json={
                "conversation_id": conversation_id,
                "message": "Quanto custa o Shelby SU-21S?",
            },
        )
        second = client.post(
            "/chat",
            json={
                "conversation_id": conversation_id,
                "message": "Confirma o preço?",
            },
        )

    assert first.status_code == 200
    assert first.json()["reply"] == grounded
    assert first.json()["handoff"] is None
    assert second.status_code == 200
    assert second.json()["reply"] == SAFE_REPLY
    assert "159,90" not in second.json()["reply"]
    assert second.json()["handoff"]["reason"] == "output_check_failure"


def test_two_ungrounded_replies_become_a_safe_message_and_a_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepare_catalog(monkeypatch)
    model = ScriptedChatModel(
        responses=[
            ScriptedToolCall(name="calculate_price", args={"name": "Shelby SU-21S"}),
            "Posso fazer por R$ 10,00.",
            "Ou então por R$ 20,00 no dia 03/11/2026.",
        ]
    )

    from lia_api.main import create_app

    conversation_id = f"guard-two-failures-{uuid4()}"
    with TestClient(create_app(model=model)) as client:
        response = client.post(
            "/chat",
            json={
                "conversation_id": conversation_id,
                "message": "Quanto custa o Shelby SU-21S?",
            },
        )
        queue = client.get("/handoffs")

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == SAFE_REPLY
    assert "R$ 10,00" not in body["reply"]
    assert "R$ 20,00" not in body["reply"]
    assert "03/11/2026" not in body["reply"]
    assert body["handoff"]["reason"] == "output_check_failure"
    assert isinstance(body["handoff"]["id"], int)

    rows = [
        row
        for row in queue.json()
        if row["conversation_id"] == conversation_id
    ]
    assert len(rows) == 1
    assert rows[0]["reason"] == "output_check_failure"
    assert rows[0]["summary"] == (
        "A resposta citou preço ou data sem respaldo das ferramentas."
    )
    assert rows[0]["order_id"] is None
    assert "phone" not in rows[0]
    assert "email" not in rows[0]
    assert _human_texts(model) == ["Quanto custa o Shelby SU-21S?"] * 3
    assert len(model.calls) == 3
