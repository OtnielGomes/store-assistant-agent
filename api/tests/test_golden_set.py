"""Golden set composition, deterministic evaluators, and the verdict. No network."""

import json
import re
from collections import Counter
from types import SimpleNamespace

import pytest
from langsmith.utils import LangSmithNotFoundError

from lia_api.evals.cases import DEFAULT_FROZEN_AT, GOLDEN_SET_PATH, REQUIRED_COMPOSITION, load_cases
from lia_api.evals.dataset import sync_dataset
from lia_api.evals.evaluators import (
    expected_tool,
    handoff,
    make_portuguese_judge,
    no_contact,
    required_values,
    wrong_price,
)
from lia_api.evals.score import Assessment, value_present, verdict

_ORDER_NUMBERS = (1, 3, 5, 8, 10, 13, 14, 17, 20)


def _example(expected: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        outputs=expected,
        inputs={"messages": ["Oi"], "frozen_at": DEFAULT_FROZEN_AT},
        metadata={"id": "fixture"},
    )


def _run(reply: str, tools: list[str], handoff_created: bool) -> SimpleNamespace:
    return SimpleNamespace(
        outputs={"reply": reply, "tools": tools, "handoff_created": handoff_created}
    )


def _passing_assessment(case_id: str, *, passed: bool = True, **overrides: object) -> Assessment:
    values: dict[str, object] = {
        "case_id": case_id,
        "passed": passed,
        "tool_ok": True,
        "values_ok": True,
        "handoff_ok": True,
        "contact_ok": True,
        "plain_ok": True,
        "wrong_price": False,
        "personal_data_leak": False,
        "missed_mandatory_handoff": False,
        "tool_comment": "",
        "values_comment": "",
        "handoff_comment": "",
        "contact_comment": "",
        "plain_comment": "",
        "price_comment": "",
    }
    values.update(overrides)
    return Assessment(**values)  # type: ignore[arg-type]


def test_plain_text_rejects_markdown_and_manual_citations() -> None:
    from lia_api.evals.evaluators import plain_text

    example = _example(_outcomes())
    bold = plain_text(_run("disponível por **R$ 599,90**.", ["search_catalog"], False), example)
    cited = plain_text(_run("Pela seção 4.1 do manual, são 7 dias.", [], False), example)
    plain = plain_text(
        _run("- Yamaha C40 Nylon Natural: R$ 599,90", ["search_catalog"], False),
        example,
    )

    assert bold["score"] == 0
    assert cited["score"] == 0
    assert plain["score"] == 1


def test_golden_file_has_the_required_composition() -> None:
    raw = [
        json.loads(line)
        for line in GOLDEN_SET_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(raw) == 41
    assert "catalog-11-violoes-ate-1000" in {item["id"] for item in raw}
    assert Counter(item["category"] for item in raw) == REQUIRED_COMPOSITION
    assert {item["version"] for item in raw} == {1}
    assert all(item["outputs"]["forbid_phone_and_email"] is True for item in raw)
    frozen = {item["inputs"]["frozen_at"] for item in raw}
    assert DEFAULT_FROZEN_AT in frozen
    assert frozen - {DEFAULT_FROZEN_AT} == {"2026-11-08T15:00:00-04:00"}

    order_text = " ".join(
        " ".join(item["inputs"]["messages"]) for item in raw if item["category"] == "order"
    ).casefold()
    for number in _ORDER_NUMBERS:
        assert re.search(rf"pedido {number}(?!\d)", order_text)

    loaded = load_cases()
    assert [item.id for item in loaded] == [item["id"] for item in raw]


def _outcomes(**overrides: object) -> dict[str, object]:
    expected: dict[str, object] = {
        "expected_tool": "search_catalog",
        "required_values": [],
        "forbidden_prices": [],
        "handoff_required": False,
    }
    expected.update(overrides)
    return expected


def test_expected_tool_evaluator_accepts_and_rejects_fixture_traces() -> None:
    expected = _outcomes()
    example = _example(expected)

    accepted = expected_tool(_run("O Yamaha custa R$ 599,90.", ["search_catalog"], False), example)
    rejected = expected_tool(_run("Sem consulta.", ["lookup_order"], False), example)
    skipped = expected_tool(
        _run("Só instrumentos.", [], False),
        _example({**expected, "expected_tool": None}),
    )

    assert accepted == {"key": "expected_tool", "score": 1, "comment": "Called search_catalog."}
    assert rejected["score"] == 0
    assert skipped["score"] == 1


def test_required_values_accept_brazilian_money_and_reject_a_missing_fact() -> None:
    expected = {
        "expected_tool": "search_catalog",
        "required_values": ["Yamaha C40 Nylon Natural", "599.90", "8%"],
        "forbidden_prices": [],
        "handoff_required": False,
    }
    present = required_values(
        _run(
            "O Yamaha C40 Nylon Natural custa R$ 599,90, com 8% em outra linha.",
            ["search_catalog"],
            False,
        ),
        _example(expected),
    )
    missing = required_values(
        _run("O Yamaha C40 Nylon Natural está disponível.", ["search_catalog"], False),
        _example(expected),
    )

    assert present["score"] == 1
    assert missing["score"] == 0
    assert "599.90" in str(missing["comment"])
    assert value_present("desconto de 15% a 30%", "5%") is False
    assert value_present("desconto de 5% no PIX", "5%") is True
    assert value_present("18% de desconto", "8%") is False


def test_handoff_evaluator_accepts_a_match_and_rejects_a_miss() -> None:
    required = _outcomes(expected_tool="record_handoff", handoff_required=True)
    optional = {**required, "handoff_required": False}

    created = handoff(_run("Em instantes.", ["record_handoff"], True), _example(required))
    missing = handoff(_run("Sem fila.", ["record_handoff"], False), _example(required))
    extra = handoff(_run("Abri chamado.", [], True), _example(optional))

    assert created["score"] == 1
    assert missing["score"] == 0
    assert extra["score"] == 0


def test_no_contact_evaluator_rejects_phone_and_email_and_accepts_a_price() -> None:
    example = _example(_outcomes(expected_tool=None))

    leaked = no_contact(
        _run("Escreva para contato@emporiodamusica.com.br.", [], False),
        example,
    )
    assert no_contact(_run("O preço é R$ 11.499,00.", [], False), example)["score"] == 1
    assert no_contact(_run("Chame (67) 3321-4500.", [], False), example)["score"] == 0
    assert leaked["score"] == 0


def test_wrong_price_evaluator_rejects_a_forbidden_amount_and_a_substituted_price() -> None:
    expected = {
        "expected_tool": "lookup_order",
        "required_values": ["3450.00"],
        "forbidden_prices": ["3498.00"],
        "handoff_required": True,
    }
    example = _example(expected)

    right = wrong_price(_run("O total registrado é R$ 3.450,00.", ["lookup_order"], True), example)
    forbidden = wrong_price(
        _run(
            "O total é R$ 3.450,00 e a soma dá R$ 3.498,00.",
            ["lookup_order"],
            True,
        ),
        example,
    )
    substituted = wrong_price(_run("O total é R$ 3.498,00.", ["lookup_order"], True), example)
    silent = wrong_price(_run("Vou encaminhar para um atendente.", ["lookup_order"], True), example)

    assert right["score"] == 1
    assert forbidden["score"] == 0
    assert substituted["score"] == 0
    assert silent["score"] == 1


def test_judge_evaluator_uses_the_injected_judge() -> None:
    calls: list[str] = []

    def judge(reply: str, expected: dict[str, object], messages: list[str]) -> bool:
        calls.append(reply)
        assert messages == ["Oi"]
        assert expected["expected_tool"] == "search_catalog"
        return "599,90" in reply

    evaluator = make_portuguese_judge(judge)
    example = _example(
        _outcomes(required_values=["599.90"]),
    )

    accepted = evaluator(_run("R$ 599,90", ["search_catalog"], False), example)
    rejected = evaluator(
        {"outputs": {"reply": "não sei", "tools": [], "handoff_created": False}},
        example,
    )

    assert accepted["score"] == 1
    assert rejected["score"] == 0
    assert calls == ["R$ 599,90", "não sei"]


def test_verdict_passes_only_at_37_of_41_with_the_three_gates_clear() -> None:
    clean = [_passing_assessment(f"case-{index}") for index in range(37)]
    tone_misses = [_passing_assessment(f"tone-{index}", passed=False) for index in range(4)]
    assert verdict(clean + tone_misses).ok is True

    only_36 = [_passing_assessment(f"case-{index}") for index in range(36)]
    only_36 += [_passing_assessment(f"miss-{index}", passed=False) for index in range(5)]
    assert verdict(only_36).ok is False

    wrong_price = [_passing_assessment(f"case-{index}") for index in range(40)]
    wrong_price.append(_passing_assessment("bad-price", passed=False, wrong_price=True))
    failed = verdict(wrong_price)
    assert failed.passed == 40
    assert failed.wrong_prices == 1
    assert failed.ok is False

    leaked = [_passing_assessment(f"case-{index}") for index in range(40)]
    leaked.append(
        _passing_assessment(
            "leak",
            passed=False,
            contact_ok=False,
            personal_data_leak=True,
        )
    )
    assert verdict(leaked).personal_data_leaks == 1
    assert verdict(leaked).ok is False

    missed = [_passing_assessment(f"case-{index}") for index in range(40)]
    missed.append(
        _passing_assessment("missed", passed=False, handoff_ok=False, missed_mandatory_handoff=True)
    )
    assert verdict(missed).missed_mandatory_handoffs == 1
    assert verdict(missed).ok is False


class _Example:
    def __init__(self, example_id: str) -> None:
        self.id = example_id


class _Dataset:
    def __init__(self, dataset_id: str) -> None:
        self.id = dataset_id


class _Client:
    def __init__(self, *, exists: bool) -> None:
        self.exists = exists
        self.examples = [_Example("old-1"), _Example("old-2")] if exists else []
        self.deleted: list[str] = []
        self.created: list[dict[str, object]] = []
        self.dataset_name = ""

    def read_dataset(self, *, dataset_name: str) -> _Dataset:
        self.dataset_name = dataset_name
        if not self.exists:
            raise LangSmithNotFoundError("missing")
        return _Dataset("ds-existing")

    def create_dataset(
        self,
        dataset_name: str,
        *,
        description: str,
        metadata: dict[str, object],
    ) -> _Dataset:
        self.dataset_name = dataset_name
        self.exists = True
        assert metadata["golden_version"] == 1
        assert description
        return _Dataset("ds-new")

    def list_examples(self, *, dataset_id: str) -> list[_Example]:
        assert dataset_id
        return list(self.examples)

    def delete_examples(self, example_ids: list[str], *, hard_delete: bool) -> None:
        assert hard_delete is True
        self.deleted.extend(example_ids)
        self.examples = []

    def create_examples(self, *, dataset_id: str, examples: list[dict[str, object]]) -> None:
        assert dataset_id
        self.created = examples


def test_sync_replaces_remote_examples_from_the_file() -> None:
    client = _Client(exists=True)
    cases = load_cases()

    dataset_id = sync_dataset(client, cases)

    assert dataset_id == "ds-existing"
    assert client.dataset_name == "lia-golden-v1"
    assert client.deleted == ["old-1", "old-2"]
    assert len(client.created) == 41
    first = client.created[0]
    assert first["inputs"]["messages"]
    assert first["reference_outputs"]["forbid_phone_and_email"] is True


def test_sync_creates_the_dataset_when_it_is_missing() -> None:
    client = _Client(exists=False)

    dataset_id = sync_dataset(client, load_cases())

    assert dataset_id == "ds-new"
    assert client.deleted == []
    assert len(client.created) == 41


def test_dict_shaped_run_is_scored_like_a_trace_object() -> None:
    example = {
        "outputs": {
            "expected_tool": "search_catalog",
            "required_values": ["599.90"],
            "forbidden_prices": [],
            "handoff_required": False,
        }
    }
    observed = {
        "outputs": {
            "reply": "R$ 599,90",
            "tools": ["search_catalog"],
            "handoff_created": False,
        }
    }
    score = required_values(observed, example)
    assert score["score"] == 1


@pytest.mark.parametrize(
    ("reply", "required"),
    [
        ("R$ 1.749,00", "1749.00"),
        ("custa 159,90 reais", "159.90"),
        ("sábado até 13:00", "sábado"),
    ],
)
def test_required_value_formats_from_the_store_data(reply: str, required: str) -> None:
    assert value_present(reply, required) is True
