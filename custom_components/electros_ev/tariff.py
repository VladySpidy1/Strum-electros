"""Two-zone tariff and energy-boost maths.

Pure module: standard library only (like protocol.py), so it can be tested on
bare Python (tests/test_tariff.py). Home Assistant code passes aware local
datetimes (``dt_util.now()`` / ``dt_util.as_local``).

* Zones: the night zone is the daily wall-clock window ``night_start`` (inclusive)
  .. ``night_end`` (exclusive), which may cross midnight; everything else is the
  day zone. Equal start and end means there is no night zone.
* Counter deltas: only positive changes of the charger's energy counter count; a
  decrease means the counter was reset ("Reset kWh"), so the new value counts
  from 0.
* A delta is assigned to the zone of the poll time. If the previous sample is
  older than ``max_gap`` (HA was down, polls failed), the delta is spread across
  the zones in proportion to their share of the gap.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import math
from typing import Any, Optional, Tuple, Union

ZONE_DAY = "day"
ZONE_NIGHT = "night"
ZONES = (ZONE_DAY, ZONE_NIGHT)

#: kW per ampere at the nominal 230 V of a single-phase charger.
KW_PER_AMP = 0.23
DEADLINE_FACTOR = 1.5
DEADLINE_MARGIN = timedelta(minutes=30)
#: Current assumed for the deadline when none is known (the charger minimum).
DEADLINE_MIN_AMPS = 6
#: A gap longer than this many poll intervals is split across zones.
GAP_POLLS = 3

TimeLike = Union[str, time]

_DIGITS = 6  # kWh / money are accumulated with this many decimals


def parse_hm(value: TimeLike) -> Tuple[int, int]:
    """``"23:00"``, ``"7:00"``, ``"23:00:00"`` or a ``time`` -> ``(23, 0)``."""
    if isinstance(value, time):
        return value.hour, value.minute
    if not isinstance(value, str):
        raise ValueError(f"not a time: {value!r}")
    parts = value.strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        raise ValueError(f"not a HH:MM time: {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if hour > 23 or minute > 59 or (len(parts) == 3 and int(parts[2]) > 59):
        raise ValueError(f"time out of range: {value!r}")
    return hour, minute


def zone_at(moment: Union[datetime, time], night_start: TimeLike, night_end: TimeLike) -> str:
    """Tariff zone of a wall-clock moment (minute resolution)."""
    start_h, start_m = parse_hm(night_start)
    end_h, end_m = parse_hm(night_end)
    start = start_h * 60 + start_m
    end = end_h * 60 + end_m
    now = moment.hour * 60 + moment.minute
    if start == end:
        return ZONE_DAY
    if start < end:
        night = start <= now < end
    else:
        night = now >= start or now < end
    return ZONE_NIGHT if night else ZONE_DAY


def _utc(moment: datetime) -> datetime:
    """Aware -> UTC (exact arithmetic across DST); naive stays naive."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(timezone.utc)


def zone_seconds(
    start: datetime, end: datetime, night_start: TimeLike, night_end: TimeLike
) -> Tuple[float, float]:
    """Seconds of ``[start, end)`` in the day and in the night zone.

    Zones are evaluated on the wall clock of ``start``'s time zone; naive
    datetimes are wall-clock times. Elapsed time is real time (UTC), so a DST
    change inside the interval is counted correctly.
    """
    tz = start.tzinfo
    cursor, stop = _utc(start), _utc(end)
    if stop <= cursor:
        return 0.0, 0.0
    boundaries = sorted({parse_hm(night_start), parse_hm(night_end)})
    if len(boundaries) == 1:  # no night zone
        return (stop - cursor).total_seconds(), 0.0
    day = night = 0.0
    while cursor < stop:
        local = cursor.astimezone(tz) if tz is not None else cursor
        zone = zone_at(local, night_start, night_end)
        boundary = _next_boundary(local, cursor, boundaries, tz)
        segment_end = boundary if boundary < stop else stop
        seconds = (segment_end - cursor).total_seconds()
        if zone == ZONE_NIGHT:
            night += seconds
        else:
            day += seconds
        cursor = segment_end
    return day, night


def _next_boundary(
    local: datetime,
    cursor: datetime,
    boundaries: list[Tuple[int, int]],
    tz: Any,
) -> datetime:
    """First zone boundary strictly after ``cursor`` (in ``cursor``'s representation)."""
    for days in (0, 1, 2):
        date = local.date() + timedelta(days=days)
        for hour, minute in boundaries:
            candidate = datetime.combine(date, time(hour, minute))
            if tz is not None:
                candidate = candidate.replace(tzinfo=tz).astimezone(timezone.utc)
            if candidate > cursor:
                return candidate
    return cursor + timedelta(days=1)  # pragma: no cover - defensive


def counter_delta(previous: Optional[float], current: Optional[float]) -> float:
    """Energy (kWh) consumed between two counter readings.

    No previous reading (first sample) or no current reading -> 0. A decrease is
    a counter reset: the new value counts from 0.
    """
    if previous is None or current is None:
        return 0.0
    if not (math.isfinite(previous) and math.isfinite(current)) or current < 0:
        return 0.0
    if current >= previous:
        return round(current - previous, _DIGITS)
    return round(current, _DIGITS)


def split_delta(
    delta_kwh: float,
    previous: Optional[datetime],
    now: datetime,
    night_start: TimeLike,
    night_end: TimeLike,
    max_gap: timedelta,
) -> Tuple[float, float]:
    """``(day_kwh, night_kwh)`` for a counter delta measured at ``now``.

    The delta belongs to the zone of ``now``, unless the previous sample is older
    than ``max_gap``: then it is split in proportion to the zone time in the gap.
    """
    if not delta_kwh or delta_kwh <= 0:
        return 0.0, 0.0
    if previous is not None and _utc(now) - _utc(previous) > max_gap:
        day_s, night_s = zone_seconds(previous, now, night_start, night_end)
        total = day_s + night_s
        if total > 0:
            night = round(delta_kwh * night_s / total, _DIGITS)
            return round(delta_kwh - night, _DIGITS), night
    if zone_at(now, night_start, night_end) == ZONE_NIGHT:
        return 0.0, round(delta_kwh, _DIGITS)
    return round(delta_kwh, _DIGITS), 0.0


def cost_of(day_kwh: float, night_kwh: float, price_day: float, price_night: float) -> float:
    """Cost of a split delta with the zone prices."""
    return round(day_kwh * price_day + night_kwh * price_night, _DIGITS)


def charge_hours(kwh: float, amps: Optional[int]) -> float:
    """Hours to deliver ``kwh`` at ``amps`` (230 V nominal)."""
    current = amps if amps and amps > 0 else DEADLINE_MIN_AMPS
    return max(0.0, kwh) / (current * KW_PER_AMP)


def energy_deadline(start: datetime, target_kwh: float, amps: Optional[int]) -> datetime:
    """Safety deadline of an energy boost.

    ``start + (target_kwh / (amps * 0.23 kW)) * 1.5 + 30 min``.
    """
    return start + timedelta(hours=charge_hours(target_kwh, amps) * DEADLINE_FACTOR) + DEADLINE_MARGIN


def estimate_end(
    now: datetime, remaining_kwh: float, power_w: Optional[float]
) -> Optional[datetime]:
    """When ``remaining_kwh`` is delivered at ``power_w``; None while not charging."""
    if power_w is None or not math.isfinite(power_w) or power_w <= 0:
        return None
    if remaining_kwh <= 0:
        return now
    return now + timedelta(hours=remaining_kwh / (power_w / 1000))
