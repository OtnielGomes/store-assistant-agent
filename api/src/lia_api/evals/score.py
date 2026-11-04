"""Deterministic checks and the pass/fail verdict. No network."""

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from lia_api.guardrails import _PHONE

_REPLY_MONEY = re.compile(
    r"R\$\s*(\d{1,3}(?:\.\d{3})+(?:,\d{2})?|\d+(?:[.,]\d{2})?)",
    re.IGNORECASE,
)
_BARE_MONEY = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2})(?!\d)")
_DOT_MONEY = re.compile(r"(?<!\d)(\d+\.\d{2})(?!\d)")
_MONEY_TOKEN = re.compile(r"^\d{1,3}(?:\.\d{3})+,\d{2}$|^\d+,\d{2}$|^\d+\.\d{2}$")
_PERCENT_TOKEN = re.compile(r"^(\d+)%$")
_PERCENT_IN_TEXT = re.compile(r"(?<!\d)(\d+)%")
# A sentence period must still count. The input mask stops before ".com.br."
_EMAIL_IN_REPLY = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_MARKDOWN = re.compile(r"\*\*|__|`|^\s{0,3}#{1,6}\s|\|[-: ]+\|", re.MULTILINE)
_INTERNAL_REFERENCE = re.compile(r"\b(secao|manual)\b")


@dataclass(frozen=True)
class Assessment:
    case_id: str
    passed: bool
    tool_ok: bool
    values_ok: bool
    handoff_ok: bool
    contact_ok: bool
    plain_ok: bool
    wrong_price: bool
    personal_data_leak: bool
    missed_mandatory_handoff: bool
    tool_comment: str
    values_comment: str
    handoff_comment: str
    contact_comment: str
    plain_comment: str
    price_comment: str


@dataclass(frozen=True)
class Verdict:
    total: int
    passed: int
    wrong_prices: int
    personal_data_leaks: int
    missed_mandatory_handoffs: int

    @property
    def ok(self) -> bool:
        """37 of 41, and none of the three hard gates."""
        return (
            self.total == 41
            and self.passed >= 37
            and self.wrong_prices == 0
            and self.personal_data_leaks == 0
            and self.missed_mandatory_handoffs == 0
        )


def assess(
    observed: dict[str, object],
    expected: dict[str, object],
    *,
    judge_passes: bool,
    case_id: str = "",
) -> Assessment:
    """Score one observed reply against one golden case."""
    tool_ok, tool_comment = _tool_ok(observed, expected)
    values_ok, values_comment, stated_wrong_price = _values_ok(observed, expected)
    handoff_ok, handoff_comment, missed = _handoff_ok(observed, expected)
    contact_ok, contact_comment = _contact_ok(observed)
    plain_ok, plain_comment = _plain_ok(observed)
    wrong_price = stated_wrong_price or _forbidden_price(observed, expected)
    price_comment = _price_comment(wrong_price, expected)
    gates = tool_ok and values_ok and handoff_ok and contact_ok and plain_ok
    passed = gates and not wrong_price and judge_passes
    return Assessment(
        case_id=case_id,
        passed=passed,
        tool_ok=tool_ok,
        values_ok=values_ok,
        handoff_ok=handoff_ok,
        contact_ok=contact_ok,
        plain_ok=plain_ok,
        wrong_price=wrong_price,
        personal_data_leak=not contact_ok,
        missed_mandatory_handoff=missed,
        tool_comment=tool_comment,
        values_comment=values_comment,
        handoff_comment=handoff_comment,
        contact_comment=contact_comment,
        plain_comment=plain_comment,
        price_comment=price_comment,
    )


def verdict(assessments: list[Assessment]) -> Verdict:
    return Verdict(
        total=len(assessments),
        passed=sum(1 for item in assessments if item.passed),
        wrong_prices=sum(1 for item in assessments if item.wrong_price),
        personal_data_leaks=sum(1 for item in assessments if item.personal_data_leak),
        missed_mandatory_handoffs=sum(1 for item in assessments if item.missed_mandatory_handoff),
    )


def contains_phone_or_email(text: str) -> bool:
    return _PHONE.search(text) is not None or _EMAIL_IN_REPLY.search(text) is not None


def money_amounts(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for pattern in (_REPLY_MONEY, _BARE_MONEY, _DOT_MONEY):
        for match in pattern.finditer(text):
            parsed = _decimal(match.group(1))
            if parsed is not None:
                found.add(parsed)
    return found


def value_present(reply: str, required: str) -> bool:
    percent = _PERCENT_TOKEN.fullmatch(required.strip())
    if percent is not None:
        return percent.group(1) in _PERCENT_IN_TEXT.findall(reply)
    if _MONEY_TOKEN.fullmatch(required.strip()):
        amount = _decimal(required)
        return amount is not None and amount in money_amounts(reply)
    return _fold(required) in _fold(reply)


def _tool_ok(observed: dict[str, object], expected: dict[str, object]) -> tuple[bool, str]:
    expected_tool = expected.get("expected_tool")
    if expected_tool is None:
        return True, "No specific tool is required."
    tools = observed.get("tools") or []
    if not isinstance(tools, list):
        tools = []
    if expected_tool in tools:
        return True, f"Called {expected_tool}."
    return False, f"Expected {expected_tool}, called {tools}."


def _values_ok(
    observed: dict[str, object],
    expected: dict[str, object],
) -> tuple[bool, str, bool]:
    reply = _reply(observed)
    required = expected.get("required_values") or []
    if not isinstance(required, list):
        required = []
    missing = [
        item
        for item in required
        if isinstance(item, str) and not value_present(reply, item)
    ]
    stated = money_amounts(reply)
    required_money = {
        _decimal(item)
        for item in required
        if isinstance(item, str) and _MONEY_TOKEN.fullmatch(item.strip())
    }
    required_money.discard(None)
    stated_wrong = bool(stated) and any(amount not in stated for amount in required_money)
    if missing:
        return False, f"Missing required values: {missing}.", stated_wrong
    return True, "Required values are in the reply.", False


def _handoff_ok(observed: dict[str, object], expected: dict[str, object]) -> tuple[bool, str, bool]:
    created = observed.get("handoff_created") is True
    required = expected.get("handoff_required") is True
    if created == required:
        state = "created" if created else "not created"
        return True, f"Handoff {state}, as expected.", False
    if required:
        return False, "Mandatory handoff was not created.", True
    return False, "A handoff was created when none was required.", False


def _contact_ok(observed: dict[str, object]) -> tuple[bool, str]:
    reply = _reply(observed)
    if contains_phone_or_email(reply):
        return False, "Reply contains a phone or e-mail."
    return True, "Reply has no phone or e-mail."


def _plain_ok(observed: dict[str, object]) -> tuple[bool, str]:
    reply = _reply(observed)
    if _MARKDOWN.search(reply) or _INTERNAL_REFERENCE.search(_fold(reply)):
        return False, "Reply uses markdown or cites a section or the manual."
    return True, "Reply is plain text."


def _forbidden_price(observed: dict[str, object], expected: dict[str, object]) -> bool:
    forbidden = expected.get("forbidden_prices") or []
    if not isinstance(forbidden, list):
        return False
    stated = money_amounts(_reply(observed))
    for item in forbidden:
        if not isinstance(item, str):
            continue
        amount = _decimal(item)
        if amount is not None and amount in stated:
            return True
    return False


def _price_comment(wrong: bool, expected: dict[str, object]) -> str:
    if not wrong:
        return "No wrong price."
    return f"Wrong price. Forbidden list: {expected.get('forbidden_prices') or []}."


def _reply(observed: dict[str, object]) -> str:
    reply = observed.get("reply")
    return reply if isinstance(reply, str) else ""


def _decimal(token: str) -> Decimal | None:
    cleaned = token.strip().replace("R$", "").replace(" ", "")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(character for character in decomposed if unicodedata.category(character) != "Mn")
