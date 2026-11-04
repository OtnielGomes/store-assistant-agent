"""Load the versioned golden set. The JSONL file is the source of truth."""

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

GOLDEN_VERSION = 1
DATASET_NAME = "lia-golden-v1"
DEFAULT_FROZEN_AT = "2026-11-03T10:00:00-04:00"
REQUIRED_COMPOSITION = {
    "catalog": 10,
    "promotion": 5,
    "order": 8,
    "policy": 8,
    "handoff": 5,
    "out_of_scope": 4,
}
GOLDEN_SET_PATH = Path(__file__).resolve().parents[4] / "evals" / "golden" / "v1.jsonl"


@dataclass(frozen=True)
class GoldenCase:
    version: int
    id: str
    category: str
    messages: tuple[str, ...]
    frozen_at: str
    expected_tool: str | None
    required_values: tuple[str, ...]
    forbidden_prices: tuple[str, ...]
    handoff_required: bool
    forbid_phone_and_email: bool

    def as_example(self) -> dict[str, object]:
        """One LangSmith example. Reference outputs are the expected outcomes."""
        return {
            "inputs": {
                "messages": list(self.messages),
                "frozen_at": self.frozen_at,
            },
            "reference_outputs": {
                "expected_tool": self.expected_tool,
                "required_values": list(self.required_values),
                "forbidden_prices": list(self.forbidden_prices),
                "handoff_required": self.handoff_required,
                "forbid_phone_and_email": self.forbid_phone_and_email,
            },
            "metadata": {
                "version": self.version,
                "id": self.id,
                "category": self.category,
            },
        }


def load_cases(path: Path | None = None) -> list[GoldenCase]:
    """Read the golden set and reject a file that is not version 1 with 40 cases."""
    source = path or GOLDEN_SET_PATH
    cases = [
        _parse_case(json.loads(line), index)
        for index, line in enumerate(_lines(source), start=1)
    ]
    _require_composition(cases)
    return cases


def _lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.strip()]


def _parse_case(raw: object, index: int) -> GoldenCase:
    if not isinstance(raw, dict):
        raise ValueError(f"Case {index} must be a JSON object.")
    outputs = raw.get("outputs")
    inputs = raw.get("inputs")
    if not isinstance(inputs, dict) or not isinstance(outputs, dict):
        raise ValueError(f"Case {index} needs inputs and outputs objects.")
    messages = inputs.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError(f"Case {index} needs at least one customer message.")
    if not all(isinstance(item, str) and item.strip() for item in messages):
        raise ValueError(f"Case {index} needs at least one customer message.")
    frozen_at = inputs.get("frozen_at")
    if not isinstance(frozen_at, str):
        raise ValueError(f"Case {index} needs a frozen instant.")
    parsed = datetime.fromisoformat(frozen_at)
    if parsed.tzinfo is None:
        raise ValueError(f"Case {index} frozen instant needs a timezone.")
    expected_tool = outputs.get("expected_tool")
    if expected_tool is not None and not isinstance(expected_tool, str):
        raise ValueError(f"Case {index} expected_tool must be a string or null.")
    required_values = _string_list(outputs.get("required_values"), index, "required_values")
    forbidden_prices = _string_list(outputs.get("forbidden_prices"), index, "forbidden_prices")
    handoff_required = outputs.get("handoff_required")
    if not isinstance(handoff_required, bool):
        raise ValueError(f"Case {index} handoff_required must be true or false.")
    if outputs.get("forbid_phone_and_email") is not True:
        raise ValueError(f"Case {index} must forbid a phone or e-mail in the reply.")
    case_id = raw.get("id")
    category = raw.get("category")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError(f"Case {index} needs an id.")
    if not isinstance(category, str):
        raise ValueError(f"Case {index} needs a category.")
    if raw.get("version") != GOLDEN_VERSION:
        raise ValueError(f"Case {index} must be golden set version {GOLDEN_VERSION}.")
    return GoldenCase(
        version=GOLDEN_VERSION,
        id=case_id,
        category=category,
        messages=tuple(messages),
        frozen_at=frozen_at,
        expected_tool=expected_tool,
        required_values=tuple(required_values),
        forbidden_prices=tuple(forbidden_prices),
        handoff_required=handoff_required,
        forbid_phone_and_email=True,
    )


def _string_list(value: object, index: int, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Case {index} {field} must be a list of strings.")
    return list(value)


def _require_composition(cases: list[GoldenCase]) -> None:
    if len(cases) != sum(REQUIRED_COMPOSITION.values()):
        raise ValueError(f"Golden set must have 40 cases, found {len(cases)}.")
    counts = Counter(case.category for case in cases)
    if dict(counts) != REQUIRED_COMPOSITION:
        raise ValueError(
            f"Golden set composition must be {REQUIRED_COMPOSITION}, found {dict(counts)}."
        )
    ids = [case.id for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("Golden set case ids must be unique.")
