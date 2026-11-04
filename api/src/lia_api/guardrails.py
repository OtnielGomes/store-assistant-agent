"""Deterministic input and output checks. No classifier model.

Customer messages longer than ``MAX_CUSTOMER_MESSAGE_LENGTH`` characters are
refused before the chat model is called. CPF, card numbers, full phone
numbers, and e-mails are replaced before the message is stored, traced, or
sent to the model. Four isolated digits are left unchanged.

Every money amount (R$) and calendar date in a reply must match a value in
the tool results of that same turn.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

MAX_CUSTOMER_MESSAGE_LENGTH = 2000

TOO_LONG_REPLY = (
    "Sua mensagem passou do limite de 2000 caracteres. Envie um trecho menor, por favor."
)
SAFE_REPLY = (
    "Não consegui confirmar um valor ou uma data desta resposta. "
    "Vou pedir para um atendente continuar o atendimento."
)
OUTPUT_CHECK_SUMMARY = "A resposta citou preço ou data sem respaldo das ferramentas."

_CPF_FORMATTED = re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")
_ELEVEN_DIGITS = re.compile(r"(?<!\d)\d{11}(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PHONE = re.compile(
    r"(?<!\d)"
    r"(?:\+?55[\s-]*)?"
    r"(?:\(?\d{2}\)?[\s-]*)"
    r"(?:9\d{4}|\d{4})[\s-]?\d{4}"
    r"(?!\d)"
)
_REPLY_MONEY = re.compile(
    r"R\$\s*(\d{1,3}(?:\.\d{3})+(?:,\d{2})?|\d+(?:[.,]\d{2})?)",
    re.IGNORECASE,
)
_EVIDENCE_MONEY = re.compile(r"\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}|\d+\.\d{2}")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SLASH_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
_LONG_DATE = re.compile(
    r"\b(\d{1,2}) de (janeiro|fevereiro|março|marco|abril|maio|junho|julho|agosto|"
    r"setembro|outubro|novembro|dezembro) de (\d{4})\b",
    re.IGNORECASE,
)
_MONTHS = {
    "janeiro": 1,
    "fevereiro": 2,
    "março": 3,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

_CPF_FORMATTED = re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)")
_ELEVEN_DIGITS = re.compile(r"(?<!\d)\d{11}(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PHONE = re.compile(
    r"(?<!\d)"
    r"(?:\+?55[\s-]*)?"
    r"(?:\(?\d{2}\)?[\s-]*)"
    r"(?:9\d{4}|\d{4})[\s-]?\d{4}"
    r"(?!\d)"
)


def exceeds_message_length(text: str) -> bool:
    return len(text) > MAX_CUSTOMER_MESSAGE_LENGTH


def mask_personal_data(text: str) -> str:
    """Replace personal data. Safe to run more than once on the same text."""
    spans: list[tuple[int, int, str, int]] = []

    def collect(pattern: re.Pattern[str], token: str, priority: int) -> None:
        for match in pattern.finditer(text):
            spans.append((match.start(), match.end(), token, priority))

    collect(_EMAIL, "[e-mail]", 4)
    collect(_CPF_FORMATTED, "[CPF]", 3)
    for match in _ELEVEN_DIGITS.finditer(text):
        if _cpf_digits_valid(match.group()):
            spans.append((match.start(), match.end(), "[CPF]", 3))
    collect(_CARD, "[cartão]", 2)
    collect(_PHONE, "[telefone]", 1)

    spans.sort(key=lambda item: (item[0], -item[3], -(item[1] - item[0])))
    chosen: list[tuple[int, int, str]] = []
    cursor = 0
    for start, end, token, _priority in spans:
        if start < cursor:
            continue
        chosen.append((start, end, token))
        cursor = end

    parts: list[str] = []
    last = 0
    for start, end, token in chosen:
        parts.append(text[last:start])
        parts.append(token)
        last = end
    parts.append(text[last:])
    return "".join(parts)


def _cpf_digits_valid(digits: str) -> bool:
    if len(digits) != 11 or digits == digits[0] * 11:
        return False

    def check(size: int) -> bool:
        total = sum(int(digits[index]) * (size + 1 - index) for index in range(size))
        rest = (total * 10) % 11
        if rest == 10:
            rest = 0
        return rest == int(digits[size])

    return check(9) and check(10)


def reply_is_grounded(reply: str, evidence: str) -> bool:
    """True when every R$ amount and calendar date in the reply is in evidence."""
    amounts = _reply_amounts(reply)
    dates = _dates(reply, strict=True)
    if amounts is None or dates is None:
        return False
    if not amounts and not dates:
        return True
    known_amounts = _evidence_amounts(evidence)
    known_dates = _dates(evidence, strict=False) or set()
    return amounts <= known_amounts and dates <= known_dates


def _reply_amounts(reply: str) -> set[Decimal] | None:
    amounts: set[Decimal] = set()
    for match in _REPLY_MONEY.finditer(reply):
        parsed = _parse_money(match.group(1))
        if parsed is None:
            return None
        amounts.add(parsed)
    return amounts


def _evidence_amounts(evidence: str) -> set[Decimal]:
    amounts: set[Decimal] = set()
    for match in _EVIDENCE_MONEY.finditer(evidence):
        parsed = _parse_money(match.group())
        if parsed is not None:
            amounts.add(parsed)
    return amounts


def _parse_money(raw: str) -> Decimal | None:
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+,\d{2}", raw):
        normalized = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", raw):
        normalized = raw.replace(".", "")
    elif re.fullmatch(r"\d+,\d{2}", raw):
        normalized = raw.replace(",", ".")
    else:
        normalized = raw
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _dates(text: str, *, strict: bool) -> set[date] | None:
    found: set[date] = set()
    for match in _ISO_DATE.finditer(text):
        parsed = _safe_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if parsed is None:
            if strict:
                return None
            continue
        found.add(parsed)
    for match in _SLASH_DATE.finditer(text):
        year = int(match.group(3))
        if year < 100:
            year += 2000
        parsed = _safe_date(year, int(match.group(2)), int(match.group(1)))
        if parsed is None:
            if strict:
                return None
            continue
        found.add(parsed)
    for match in _LONG_DATE.finditer(text):
        month = _MONTHS[match.group(2).casefold()]
        parsed = _safe_date(int(match.group(3)), month, int(match.group(1)))
        if parsed is None:
            if strict:
                return None
            continue
        found.add(parsed)
    return found


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
