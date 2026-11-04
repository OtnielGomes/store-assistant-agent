import os

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, BaseMessage

from tests.policy_embedder import embed_for_tests
from tests.scripted_model import ScriptedChatModel


def _prepare_env(monkeypatch: pytest.MonkeyPatch) -> str:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.fail("DATABASE_URL must point at a real PostgreSQL database")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "none")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.delenv("OPENAI_TEMPERATURE", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return database_url


def _system_text(messages: list[BaseMessage]) -> str:
    system = next(message for message in messages if message.type == "system")
    assert isinstance(system.content, str)
    return system.content


def _tool_text(messages: list[BaseMessage]) -> str:
    return "\n".join(str(message.content) for message in messages if message.type == "tool")


def _echo_retrieved_sections(messages: list[BaseMessage]) -> AIMessage:
    retrieved = _tool_text(messages)
    if "Seção" not in retrieved:
        return AIMessage(content="sem política recuperada")
    return AIMessage(content=retrieved)


def _search_call(query: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "search_policies",
                "args": {"query": query},
                "id": "call-policy",
                "type": "tool_call",
            }
        ],
    )


def _ask(monkeypatch: pytest.MonkeyPatch, message: str, reply_from) -> str:
    database_url = _prepare_env(monkeypatch)
    from lia_api.policies import ingest_policies

    ingest_policies(database_url, embed_for_tests)
    model = ScriptedChatModel(responses=[_search_call(message), reply_from])

    from lia_api.main import create_app

    with TestClient(create_app(model=model, embedder=embed_for_tests)) as client:
        response = client.post(
            "/chat",
            json={"conversation_id": f"policy-{abs(hash(message))}", "message": message},
        )
    assert response.status_code == 200
    assert response.json()["handoff"] is None
    return response.json()["reply"]


def test_lia_answers_hours_payment_exchange_shipping_warranty_and_privacy_from_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    questions = {
        "qual o horário de funcionamento?": (
            "Seção 2",
            "09:00 às 18:00",
            "09:00 às 13:00",
            "Fechado",
        ),
        "quais as formas de pagamento?": (
            "Seção 3",
            "PIX",
            "5% de desconto sobre o preço de tabela",
            "Parcela mínima de R$ 100,00",
        ),
        "comprei e me arrependi, posso devolver?": (
            "Seção 4.1",
            "7 (sete) dias corridos",
        ),
        "quanto custa o frete para Campo Grande?": (
            "Seção 5.1",
            "Frete grátis para pedidos acima de R$ 500,00",
            "taxa fixa de R$ 35,00",
        ),
        "qual é a garantia legal?": ("Seção 8.1", "90 (noventa) dias"),
        "como vocês tratam a privacidade dos meus dados?": (
            "Seção 9",
            "Lei Geral de Proteção de Dados",
        ),
    }
    for message, expected in questions.items():
        reply = _ask(monkeypatch, message, _echo_retrieved_sections)
        for snippet in expected:
            assert snippet in reply


def test_black_friday_is_november_and_no_discount_is_invented(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def answer(messages: list[BaseMessage]) -> AIMessage:
        system = _system_text(messages).lower()
        retrieved = _tool_text(messages)
        prompt_forbids_invented_discount = (
            "novembro" in system and "não há desconto ativo" in system
        )
        manual_says_november = "Novembro" in retrieved and "Seção 6.1" in retrieved
        if prompt_forbids_invented_discount and manual_says_november:
            return AIMessage(
                content=(
                    "A Black Friday acontece em novembro. Não há desconto ativo no momento."
                )
            )
        return AIMessage(content=retrieved or "sem política recuperada")

    reply = _ask(monkeypatch, "Tem Black Friday? Tem desconto agora?", answer)

    assert reply == "A Black Friday acontece em novembro. Não há desconto ativo no momento."
    assert "15%" not in reply
    assert "30%" not in reply


def test_accessory_requests_stay_inside_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def answer(messages: list[BaseMessage]) -> AIMessage:
        system = _system_text(messages).lower()
        refuses_accessories = all(
            item in system
            for item in (
                "apenas instrumentos",
                "cordas",
                "cabos",
                "palhetas",
                "pedais",
                "amplificadores",
                "cases",
                "nenhuma outra loja",
            )
        )
        if refuses_accessories:
            return AIMessage(
                content=(
                    "A loja vende apenas instrumentos musicais. "
                    "Não trabalhamos com cordas, cabos, palhetas, pedais, amplificadores ou cases."
                )
            )
        return AIMessage(content="Tente na Loja Parceira do Centro.")

    reply = _ask(
        monkeypatch,
        "Vocês vendem cordas, cabos, palhetas, pedais, amplificadores ou cases?",
        answer,
    )

    assert "apenas instrumentos" in reply
    assert "Loja Parceira" not in reply


def test_contacts_are_only_the_store_whatsapp_and_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def answer(messages: list[BaseMessage]) -> AIMessage:
        system = _system_text(messages)
        identity_only = (
            "(67) 3321-4500" in system
            and "contato@emporiodamusica.com.br" in system
            and "(67) 3341-4444" not in system
            and "informe apenas o WhatsApp" in system
        )
        if identity_only:
            return AIMessage(
                content=(
                    "Nosso WhatsApp é (67) 3321-4500 e o e-mail é contato@emporiodamusica.com.br."
                )
            )
        return AIMessage(content="O telefone é (67) 3341-4444.")

    reply = _ask(monkeypatch, "Qual o telefone e o e-mail da loja?", answer)

    assert "(67) 3321-4500" in reply
    assert "contato@emporiodamusica.com.br" in reply
    assert "(67) 3341-4444" not in reply


def test_off_topic_questions_are_redirected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def answer(messages: list[BaseMessage]) -> AIMessage:
        system = _system_text(messages).lower()
        if "redirecione" in system and "políticas" in system:
            return AIMessage(
                content="Posso ajudar com instrumentos, pedidos ou as políticas da loja."
            )
        return AIMessage(content="Amanhã o tempo fica nublado em Campo Grande.")

    reply = _ask(monkeypatch, "Vai chover amanhã?", answer)

    assert reply == "Posso ajudar com instrumentos, pedidos ou as políticas da loja."
    assert "nublado" not in reply
