"""ElectroS WiFi charger protocol: status parsing, command builders, timer logic.

Pure module: standard library only (no aiohttp, no Home Assistant), so it can be
tested on bare Python. ``docs/protocol.md`` is the source of truth for the
field indexes and command formats used here.

Security: status fields 0, 1 (router Wi-Fi SSID/password), 2, 3 (charger access
point name/password) and 28, 29 (product number / cloud password) are never
read, returned or logged by this module.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, time
from typing import Any, Optional, Tuple, Union

# --------------------------------------------------------------------------- #
# Status field indexes (line numbers of the status message)
# --------------------------------------------------------------------------- #

FIELD_CONFIGURED_CURRENT = 7  # A
FIELD_STATUS = 8  # status code
FIELD_MEASURED_CURRENT = 9  # x0.1 A
FIELD_VOLTAGE = 10  # V, current mains voltage
FIELD_ENERGY = 11  # kWh x 1e6, since the last "Reset kWh"
FIELD_POWER = 12  # kW x 1e4
FIELD_DEVICE_CLOCK = 15  # d.mm.yyyyThh:mm:ss
FIELD_VOLTAGE_MAX = 16  # V
FIELD_VOLTAGE_MIN = 17  # V
FIELD_VOLTAGE_AVG = 20  # V
FIELD_TIMER_START = 26  # HH:MM
FIELD_TIMER_END = 27  # HH:MM
FIELD_EVENTS = 30  # "HH:MM:SS code" pairs
FIELD_MONTHS = 31  # "<current month> <12 values Wh> ..."
FIELD_HISTORY = 32  # "dd.mm HH:MM:SS <kWh x 1e6>" triples

#: Fields that hold secrets (Wi-Fi credentials, cloud password). Never exposed.
SECRET_FIELDS = frozenset({0, 1, 2, 3, 28, 29})

#: Minimum number of lines for a message to be treated as a status message.
MIN_STATUS_FIELDS = FIELD_STATUS + 1

HISTORY_MAX = 50
EVENTS_MAX = 50

# --------------------------------------------------------------------------- #
# Status codes
# --------------------------------------------------------------------------- #

STATUS_UNKNOWN = "unknown"
STATUS_CHARGING = "charging"
STATUS_CHARGING_BLOCKED = "charging_blocked"
STATUS_OFFLINE = "offline"

#: Raw status code (field 8) -> enum key used by sensor.status.
STATUS_KEYS: dict[str, str] = {
    "0": "waiting_for_vehicle",
    "1": "vehicle_connected_idle",
    "2": STATUS_CHARGING,
    "3": "charging_fault",
    "4": STATUS_CHARGING_BLOCKED,
    "5": "mains_voltage_low",
    "6": "charger_communication_fault",
    "7": STATUS_OFFLINE,
    "16": "leakage_current_fault",
    "32": "overcurrent_fault",
    "77": STATUS_OFFLINE,
}

#: All possible values of sensor.status (enum options), in a stable order.
STATUS_OPTIONS: list[str] = [
    "waiting_for_vehicle",
    "vehicle_connected_idle",
    STATUS_CHARGING,
    "charging_fault",
    STATUS_CHARGING_BLOCKED,
    "mains_voltage_low",
    "charger_communication_fault",
    STATUS_OFFLINE,
    "leakage_current_fault",
    "overcurrent_fault",
    STATUS_UNKNOWN,
]

OFFLINE_STATUS_CODES = frozenset({"7", "77"})

# --------------------------------------------------------------------------- #
# Charging current
# --------------------------------------------------------------------------- #

MIN_AMPS = 6
MAX_AMPS = 32
AMPS_STEP = 2

#: Charging current (A) -> index used by the ``start`` command (0=32 A ... 13=6 A).
AMP_TO_INDEX: dict[int, int] = {
    amps: (MAX_AMPS - amps) // AMPS_STEP
    for amps in range(MAX_AMPS, MIN_AMPS - 1, -AMPS_STEP)
}
SUPPORTED_AMPS: list[int] = sorted(AMP_TO_INDEX)


def normalize_amps(amps: Union[int, float, None]) -> int:
    """Round a current down to the nearest supported value (6..32 step 2).

    Returns 0 for values below 6 A (meaning "cannot charge"), 32 for anything above.
    """
    if amps is None:
        return 0
    try:
        value = int(amps)
    except (TypeError, ValueError):
        return 0
    if value < MIN_AMPS:
        return 0
    if value >= MAX_AMPS:
        return MAX_AMPS
    return max(a for a in SUPPORTED_AMPS if a <= value)


def amps_to_index(amps: int) -> int:
    """Return the ``start`` command index for a current, normalizing it first."""
    normalized = normalize_amps(amps)
    if normalized <= 0:
        raise ValueError(f"unsupported charging current: {amps}")
    return AMP_TO_INDEX[normalized]


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #

Window = Tuple[str, str]
TimeLike = Union[str, time, None]

_HHMM_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$")
_CLOCK_RE = re.compile(
    r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})T(\d{1,2}):(\d{2})(?::(\d{2}))?\s*$"
)
_DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})$")
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})$")


def parse_hhmm(value: TimeLike) -> str:
    """Normalize ``"7:00"``, ``"07:00"``, ``"07:00:00"`` or a ``time`` to ``"07:00"``.

    Raises ValueError for anything else.
    """
    if isinstance(value, time):
        return f"{value.hour:02d}:{value.minute:02d}"
    if not isinstance(value, str):
        raise ValueError(f"not a time: {value!r}")
    match = _HHMM_RE.match(value)
    if not match:
        raise ValueError(f"not a HH:MM time: {value!r}")
    hour, minute = int(match.group(1)), int(match.group(2))
    second = int(match.group(3) or 0)
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError(f"time out of range: {value!r}")
    return f"{hour:02d}:{minute:02d}"


def hhmm_to_time(value: str) -> time:
    """Convert ``"HH:MM"`` to ``datetime.time``."""
    hour, minute = parse_hhmm(value).split(":")
    return time(int(hour), int(minute))


def format_device_datetime(moment: datetime) -> str:
    """Format a datetime exactly like the electros web page does.

    The day is NOT zero-padded: 5 Mar 2026 08:07:09 -> ``"5.03.2026T08:07:09"``.
    """
    d = moment
    return (
        f"{d.day}.{d.month:02d}.{d.year}"
        f"T{d.hour:02d}:{d.minute:02d}:{d.second:02d}"
    )


def parse_device_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse the charger clock (field 15), e.g. ``"28.10.2026T20:15:03"`` (naive)."""
    if not value:
        return None
    match = _CLOCK_RE.match(value)
    if not match:
        return None
    day, month, year, hour, minute = (int(match.group(i)) for i in range(1, 6))
    second = int(match.group(6) or 0)
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# Timer window
# --------------------------------------------------------------------------- #

DISABLED_TIME = "00:00"


def parse_window(start_raw: TimeLike, end_raw: TimeLike) -> Optional[Window]:
    """Parse the timer window from fields 26/27.

    Returns ``("HH:MM", "HH:MM")`` or None when the timer is disabled: both empty,
    ``"00:00"``/``"00:00"`` (what the charger reports when the timer is off), any
    other zero-length window, or values that are not times.
    """
    if start_raw in (None, "") or end_raw in (None, ""):
        return None
    try:
        start = parse_hhmm(start_raw)
        end = parse_hhmm(end_raw)
    except ValueError:
        return None
    if start == end:
        return None
    return (start, end)


def window_to_str(window: Optional[Window]) -> str:
    """Serialize a window: ``"23:00-07:00"``; ``""`` for disabled."""
    if window is None:
        return ""
    return f"{window[0]}-{window[1]}"


def window_from_str(value: Optional[str]) -> Optional[Window]:
    """Inverse of :func:`window_to_str`. Empty/invalid -> None (disabled)."""
    if not value or "-" not in value:
        return None
    start, _, end = value.partition("-")
    return parse_window(start, end)


def _minutes(value: Union[time, datetime]) -> int:
    return value.hour * 60 + value.minute


def window_allows(window: Optional[Window], moment: Union[time, datetime]) -> bool:
    """True if the device timer allows charging at ``moment`` (local wall time).

    ``None`` (timer disabled) always allows. Windows may cross midnight
    (``23:00-07:00``). The start is inclusive, the end exclusive.
    """
    if window is None:
        return True
    start = _minutes(hhmm_to_time(window[0]))
    end = _minutes(hhmm_to_time(window[1]))
    now = _minutes(moment)
    if start == end:
        return True
    if start < end:
        return start <= now < end
    return now >= start or now < end


def is_weekend(local_now: datetime) -> bool:
    """Saturday or Sunday."""
    return local_now.weekday() >= 5


def desired_window(
    night_enabled: bool,
    start: TimeLike,
    end: TimeLike,
    boost_active: bool,
    weekend_free: bool,
    local_now: datetime,
) -> Optional[Window]:
    """Decide which timer window the charger should have right now.

    - night timer off -> None (timer disabled, charging allowed any time);
    - a boost (timed or unlimited manual charge) is active -> None;
    - weekend_free and it is Saturday/Sunday (local time) -> None;
    - otherwise the night window ``(start, end)``; a zero-length or invalid
      window is treated as disabled.
    """
    if not night_enabled or boost_active:
        return None
    if weekend_free and is_weekend(local_now):
        return None
    return parse_window(start, end)


# --------------------------------------------------------------------------- #
# Command builders (client -> charger / cloud)
# --------------------------------------------------------------------------- #


def build_status(login: str) -> str:
    """Status request (sent once a second by the web page)."""
    return f"site {login}"


def build_start(login: str, amps: int) -> str:
    """Start charging with the given current (A)."""
    return f"site {login}\nstart\n{amps_to_index(amps)}\n0\n"


def build_stop(login: str) -> str:
    """Stop charging."""
    return f"site {login}\nstop"


def build_resetkwt(login: str) -> str:
    """Reset the consumed-energy counter (field 11)."""
    return f"site {login}\nresetkwt"


def build_datetime(
    login: str, moment: datetime, window: Optional[Window] = None
) -> str:
    """Set the charger clock and the daily timer window (None disables the timer)."""
    clock = format_device_datetime(moment)
    if window is None:
        return f"site {login}\ndatetime\n{clock}\n\n\n"
    start, end = parse_hhmm(window[0]), parse_hhmm(window[1])
    return f"site {login}\ndatetime\n{clock}\n{start}\n{end}\n"


# --------------------------------------------------------------------------- #
# Status parsing (charger / cloud -> client)
# --------------------------------------------------------------------------- #


def _field(parts: list[str], index: int) -> Optional[str]:
    if index in SECRET_FIELDS:  # pragma: no cover - defensive
        raise KeyError("secret field")
    if len(parts) > index:
        return parts[index].strip()
    return None


def _number(value: Optional[str]) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _int(value: Optional[str]) -> Optional[int]:
    number = _number(value)
    return None if number is None else int(round(number))


def _scaled(value: Optional[str], divisor: float, digits: int) -> Optional[float]:
    number = _number(value)
    if number is None:
        return None
    return round(number / divisor, digits)


def status_key(code: Optional[str]) -> str:
    """Map a raw status code to its enum key ("unknown" if not recognized)."""
    if code is None:
        return STATUS_UNKNOWN
    return STATUS_KEYS.get(code.strip(), STATUS_UNKNOWN)


def parse_events(raw: Optional[str], limit: int = EVENTS_MAX) -> list[dict[str, Any]]:
    """Parse the event log (field 30): ``"HH:MM:SS code"`` pairs, in device order."""
    if not raw:
        return []
    tokens = raw.split()
    events: list[dict[str, Any]] = []
    i = 0
    while i + 1 < len(tokens):
        stamp, code = tokens[i], tokens[i + 1]
        if _TIME_RE.match(stamp) and re.fullmatch(r"-?\d+", code):
            events.append({"time": stamp, "code": int(code)})
            i += 2
        else:
            i += 1
    return events[-limit:] if limit else events


def parse_months(raw: Optional[str]) -> tuple[Optional[int], Optional[list[float]]]:
    """Parse monthly statistics (field 31).

    Format ``"<current month> <12 values in Wh> ..."``; returns
    ``(current_month 1..12 | None, [12 kWh values Jan..Dec] | None)``.
    """
    if not raw:
        return None, None
    tokens = raw.split()
    current: Optional[int] = None
    if tokens:
        value = _int(tokens[0])
        if value is not None and 1 <= value <= 12:
            current = value
    months: Optional[list[float]] = None
    if len(tokens) >= 13:
        values = [_number(token) for token in tokens[1:13]]
        if all(v is not None for v in values):
            months = [round(v / 1000, 3) for v in values]  # type: ignore[operator]
    return current, months


def parse_history(
    raw: Optional[str],
    reference: Optional[datetime] = None,
    limit: int = HISTORY_MAX,
) -> list[dict[str, Any]]:
    """Parse the session history (field 32), newest first.

    Format: triples ``"dd.mm HH:MM:SS <kWh x 1e6>"``. The year is not reported, so
    entries are ordered by inferring the year relative to ``reference`` (the
    charger clock): a date later than the reference belongs to the previous year.
    """
    if not raw:
        return []
    tokens = raw.split()
    entries: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
    i = 0
    while i + 2 < len(tokens):
        date_m = _DATE_RE.match(tokens[i])
        time_m = _TIME_RE.match(tokens[i + 1])
        energy = _number(tokens[i + 2])
        if not (date_m and time_m and energy is not None):
            i += 1
            continue
        day, month = int(date_m.group(1)), int(date_m.group(2))
        hour, minute, second = (int(time_m.group(k)) for k in (1, 2, 3))
        i += 3
        if not (1 <= day <= 31 and 1 <= month <= 12 and hour < 24 and minute < 60):
            continue  # empty slot such as "00.00 00:00:00 0"
        year = 0
        if reference is not None:
            year = reference.year
            if (month, day, hour, minute, second) > (
                reference.month,
                reference.day,
                reference.hour,
                reference.minute,
                reference.second,
            ):
                year -= 1
        sort_key = (year, month, day, hour * 3600 + minute * 60 + second)
        entries.append(
            (
                sort_key,
                {
                    "date": f"{day:02d}.{month:02d}",
                    "time": f"{hour:02d}:{minute:02d}:{second:02d}",
                    "kwh": round(energy / 1_000_000, 3),
                },
            )
        )
    entries.sort(key=lambda item: item[0], reverse=True)
    result = [entry for _, entry in entries]
    return result[:limit] if limit else result


def parse_status(message: str) -> dict[str, Any]:
    """Parse a status message into a dict with only non-secret values.

    Raises ValueError if the message does not look like a status message.
    """
    if not isinstance(message, str):
        raise ValueError("status message must be text")
    parts = message.replace("\r", "").split("\n")
    if len(parts) < MIN_STATUS_FIELDS:
        raise ValueError("not a status message")

    code = _field(parts, FIELD_STATUS) or None
    key = status_key(code)

    voltage_raw = _field(parts, FIELD_VOLTAGE)
    clock_raw = _field(parts, FIELD_DEVICE_CLOCK) or None
    clock = parse_device_datetime(clock_raw)
    start_raw = _field(parts, FIELD_TIMER_START)
    end_raw = _field(parts, FIELD_TIMER_END)
    timer_known = start_raw is not None and end_raw is not None
    current_month, months = parse_months(_field(parts, FIELD_MONTHS))
    if current_month is None and clock is not None:
        current_month = clock.month

    return {
        "online": code is not None and code not in OFFLINE_STATUS_CODES,
        "status_code": code,
        "status_text": key,
        "current_amps": _int(_field(parts, FIELD_CONFIGURED_CURRENT)),
        "measured_current": _scaled(_field(parts, FIELD_MEASURED_CURRENT), 10, 1),
        # Current mains voltage is field 10 (16/17 are max/min, 20 the average).
        "mains_voltage": _int(voltage_raw),
        "voltage_max": _int(_field(parts, FIELD_VOLTAGE_MAX)),
        "voltage_min": _int(_field(parts, FIELD_VOLTAGE_MIN)),
        "voltage_avg": _int(_field(parts, FIELD_VOLTAGE_AVG)),
        "energy_kwh": _scaled(_field(parts, FIELD_ENERGY), 1_000_000, 3),
        # Field 12 is kW x 1e4, so W = raw / 10.
        "charging_power_w": _scaled(_field(parts, FIELD_POWER), 10, 1),
        "device_time": clock_raw,
        "device_datetime": clock,
        "timer_known": timer_known,
        "timer_start": start_raw if timer_known else None,
        "timer_end": end_raw if timer_known else None,
        "timer_window": parse_window(start_raw, end_raw) if timer_known else None,
        "events": parse_events(_field(parts, FIELD_EVENTS)),
        "current_month": current_month,
        "months": months,
        "history": parse_history(_field(parts, FIELD_HISTORY), reference=clock),
    }


def month_energy(current_month: Optional[int], months: Optional[list[float]]) -> Optional[float]:
    """kWh of the current month from the parsed statistics."""
    if not months or current_month is None or not 1 <= current_month <= len(months):
        return None
    return months[current_month - 1]
