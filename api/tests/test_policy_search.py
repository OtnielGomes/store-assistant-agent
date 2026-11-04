import os

import pytest

from tests.policy_embedder import embed_for_tests


def _database_url() -> str:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        pytest.fail("DATABASE_URL must point at a real PostgreSQL database")
    return database_url


def test_lexical_query_using_manual_wording_retrieves_section_4_1() -> None:
    from lia_api.policies import ingest_policies, search_policies

    database_url = _database_url()
    ingest_policies(database_url, embed_for_tests)

    hits = search_policies(database_url, "direito de arrependimento", embed_for_tests)

    assert hits[0].section_number == "4.1"


def test_paraphrase_without_shared_keywords_retrieves_section_4_1() -> None:
    from lia_api.policies import ingest_policies, search_policies

    database_url = _database_url()
    ingest_policies(database_url, embed_for_tests)

    hits = search_policies(
        database_url,
        "comprei e me arrependi, posso devolver?",
        embed_for_tests,
    )

    assert hits[0].section_number == "4.1"


def test_reloading_the_manual_does_not_duplicate_chunks() -> None:
    from lia_api.policies import ingest_policies, search_policies

    database_url = _database_url()
    first = ingest_policies(database_url, embed_for_tests)
    second = ingest_policies(database_url, embed_for_tests)
    hits = search_policies(
        database_url,
        "direito de arrependimento",
        embed_for_tests,
        limit=20,
    )

    assert first == second == 28
    assert [hit.section_number for hit in hits].count("4.1") == 1


def test_hours_payment_and_shipping_tables_are_retrieved_with_section_numbers() -> None:
    from lia_api.policies import ingest_policies, search_policies

    database_url = _database_url()
    ingest_policies(database_url, embed_for_tests)

    hours = search_policies(database_url, "horário de funcionamento", embed_for_tests)
    payment = search_policies(database_url, "formas de pagamento", embed_for_tests)
    shipping = search_policies(database_url, "PAC SEDEX Jadlog", embed_for_tests)

    assert hours[0].section_number == "2"
    assert "09:00 às 18:00" in hours[0].content
    assert "09:00 às 13:00" in hours[0].content
    assert "Fechado" in hours[0].content

    assert payment[0].section_number == "3"
    assert "PIX" in payment[0].content
    assert "5% de desconto sobre o preço de tabela" in payment[0].content
    assert "Parcela mínima de R$ 100,00" in payment[0].content
    assert "Compensação em até 3 dias úteis" in payment[0].content

    assert shipping[0].section_number == "5.2"
    assert "5 a 12 dias úteis" in shipping[0].content
    assert "2 a 5 dias úteis" in shipping[0].content
    assert "3 a 8 dias úteis" in shipping[0].content
    assert "Incluído" in shipping[0].content
