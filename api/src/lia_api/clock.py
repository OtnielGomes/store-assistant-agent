from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

STORE_TIMEZONE = ZoneInfo("America/Campo_Grande")

# Monday=0 … Saturday=5. Sunday is closed. Closing time is exclusive.
_BUSINESS_HOURS: dict[int, tuple[time, time]] = {
    0: (time(9, 0), time(18, 0)),
    1: (time(9, 0), time(18, 0)),
    2: (time(9, 0), time(18, 0)),
    3: (time(9, 0), time(18, 0)),
    4: (time(9, 0), time(18, 0)),
    5: (time(9, 0), time(13, 0)),
}

_WEEKDAYS_PT = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)

DURING_HOURS_MESSAGE = "A equipe continua o atendimento em instantes."


class SystemClock:
    """Real time in the store timezone."""

    def __init__(self, timezone: ZoneInfo = STORE_TIMEZONE) -> None:
        self._timezone = timezone

    def now(self) -> datetime:
        return datetime.now(self._timezone)


class FrozenClock:
    """A fixed instant, used by tests and by a simulated development request."""

    def __init__(self, instant: datetime, timezone: ZoneInfo = STORE_TIMEZONE) -> None:
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone)
        else:
            instant = instant.astimezone(timezone)
        self._instant = instant

    def now(self) -> datetime:
        return self._instant


def in_store_zone(moment: datetime, timezone: ZoneInfo = STORE_TIMEZONE) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone)
    return moment.astimezone(timezone)


def is_open(moment: datetime) -> bool:
    local = in_store_zone(moment)
    window = _BUSINESS_HOURS.get(local.weekday())
    if window is None:
        return False
    start, end = window
    current = local.time().replace(tzinfo=None)
    return start <= current < end


def next_opening(moment: datetime) -> datetime:
    local = in_store_zone(moment)
    day = local.date()
    for offset in range(8):
        current = day + timedelta(days=offset)
        window = _BUSINESS_HOURS.get(current.weekday())
        if window is None:
            continue
        opening = datetime.combine(current, window[0], tzinfo=STORE_TIMEZONE)
        if opening > local:
            return opening
    raise RuntimeError("No store opening within the next week.")


def add_business_minutes(start: datetime, minutes: int) -> datetime:
    moment = in_store_zone(start)
    if not is_open(moment):
        moment = next_opening(moment)
    remaining = minutes
    while remaining > 0:
        window = _BUSINESS_HOURS[moment.weekday()]
        close = datetime.combine(moment.date(), window[1], tzinfo=STORE_TIMEZONE)
        available = int((close - moment).total_seconds() // 60)
        if remaining <= available:
            return moment + timedelta(minutes=remaining)
        remaining -= available
        moment = next_opening(close)
    return moment


def expected_return_message(moment: datetime, reason: str) -> str:
    local = in_store_zone(moment)
    if reason == "complaint":
        deadline = add_business_minutes(local, 24 * 60)
        weekday = _WEEKDAYS_PT[deadline.weekday()]
        when = deadline.strftime("%d/%m/%Y")
        hour = deadline.strftime("%H:%M")
        return f"O retorno é em até 24 horas úteis, até {weekday}, {when}, às {hour}."
    if is_open(local):
        return DURING_HOURS_MESSAGE
    opening = next_opening(local)
    weekday = _WEEKDAYS_PT[opening.weekday()]
    article = "no" if opening.weekday() >= 5 else "na"
    hour = opening.strftime("%H:%M")
    return (
        "Fora do expediente. O próximo atendimento é "
        f"{article} {weekday}, às {hour}, exceto feriados."
    )
