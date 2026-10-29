"""Tests for custom_components/electros_ev/tariff.py (two-zone split, boost maths).

tariff.py is stdlib-only, so these tests run on bare Python without Home
Assistant: ``python -m pytest tests/test_tariff.py --noconftest``. The module is
loaded by file path so the integration package (which imports homeassistant) is
not imported.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import importlib.util
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pytest

_TARIFF_PATH = (
    Path(__file__).resolve().parents[1] / "custom_components" / "electros_ev" / "tariff.py"
)
_spec = importlib.util.spec_from_file_location("electros_tariff", _TARIFF_PATH)
tariff = importlib.util.module_from_spec(_spec)
sys.modules["electros_tariff"] = tariff
_spec.loader.exec_module(tariff)

KYIV = ZoneInfo("Europe/Kyiv")
GAP = timedelta(minutes=3)  # 3 polls of 60 s
NIGHT = ("23:00", "07:00")


def kyiv(*args: int) -> datetime:
    return datetime(*args, tzinfo=KYIV)


# --------------------------------------------------------------------------- #
# Zones
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("moment", "zone"),
    [
        (time(22, 59, 59), "day"),
        (time(23, 0), "night"),
        (time(0, 0), "night"),
        (time(6, 59, 59), "night"),
        (time(7, 0), "day"),
        (time(12, 0), "day"),
    ],
)
def test_zone_at_night_window_across_midnight(moment: time, zone: str) -> None:
    assert tariff.zone_at(moment, *NIGHT) == zone


def test_zone_at_window_within_a_day_and_datetimes() -> None:
    assert tariff.zone_at(kyiv(2026, 10, 28, 1, 0), "01:00", "05:00") == "night"
    assert tariff.zone_at(kyiv(2026, 10, 28, 5, 0), "01:00", "05:00") == "day"
    assert tariff.zone_at(kyiv(2026, 10, 28, 0, 59), "01:00", "05:00") == "day"
    # The time selector stores HH:MM:SS; time objects work too.
    assert tariff.zone_at(time(23, 30), "23:00:00", time(7, 0)) == "night"


def test_zone_at_without_night_zone() -> None:
    """Equal start and end: no night zone at all."""
    for hour in (0, 7, 23):
        assert tariff.zone_at(time(hour, 0), "07:00", "07:00") == "day"


@pytest.mark.parametrize("bad", ["", "7", "24:00", "07:60", "ab:cd", None, 700])
def test_parse_hm_rejects_garbage(bad: object) -> None:
    with pytest.raises(ValueError):
        tariff.parse_hm(bad)  # type: ignore[arg-type]


def test_zone_seconds_simple_day() -> None:
    day, night = tariff.zone_seconds(kyiv(2026, 10, 28, 22, 0), kyiv(2026, 10, 29, 8, 0), *NIGHT)
    assert day == 2 * 3600  # 22-23 and 07-08
    assert night == 8 * 3600


def test_zone_seconds_inside_one_zone_and_empty() -> None:
    assert tariff.zone_seconds(kyiv(2026, 10, 28, 9, 0), kyiv(2026, 10, 28, 9, 30), *NIGHT) == (
        1800.0,
        0.0,
    )
    assert tariff.zone_seconds(kyiv(2026, 10, 28, 9, 0), kyiv(2026, 10, 28, 9, 0), *NIGHT) == (
        0.0,
        0.0,
    )
    assert tariff.zone_seconds(kyiv(2026, 10, 28, 9, 0), kyiv(2026, 10, 28, 8, 0), *NIGHT) == (
        0.0,
        0.0,
    )


def test_zone_seconds_several_days() -> None:
    day, night = tariff.zone_seconds(kyiv(2026, 10, 26, 12, 0), kyiv(2026, 10, 29, 12, 0), *NIGHT)
    assert night == 3 * 8 * 3600
    assert day == 3 * 16 * 3600


def test_zone_seconds_dst_end_counts_real_time() -> None:
    """25 Oct 2026, Kyiv: 04:00 EEST -> 03:00 EET; that night is 9 real hours long."""
    day, night = tariff.zone_seconds(kyiv(2026, 10, 24, 22, 0), kyiv(2026, 10, 25, 8, 0), *NIGHT)
    assert night == 9 * 3600
    assert day == 2 * 3600


def test_zone_seconds_dst_start_counts_real_time() -> None:
    """29 Mar 2026, Kyiv: 03:00 EET -> 04:00 EEST; that night is 7 real hours long."""
    day, night = tariff.zone_seconds(kyiv(2026, 3, 28, 22, 0), kyiv(2026, 3, 29, 8, 0), *NIGHT)
    assert night == 7 * 3600
    assert day == 2 * 3600


def test_zone_seconds_utc_input_uses_its_wall_clock() -> None:
    """The wall clock is that of ``start``: UTC times are UTC wall-clock times."""
    start = datetime(2026, 10, 28, 22, 0, tzinfo=timezone.utc)
    day, night = tariff.zone_seconds(start, start + timedelta(hours=2), *NIGHT)
    assert (day, night) == (3600.0, 3600.0)


def test_zone_seconds_naive_wall_clock() -> None:
    day, night = tariff.zone_seconds(
        datetime(2026, 10, 28, 6, 30), datetime(2026, 10, 28, 7, 30), *NIGHT
    )
    assert (day, night) == (1800.0, 1800.0)


# --------------------------------------------------------------------------- #
# Counter deltas and the split
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("previous", "current", "delta"),
    [
        (None, 25.8, 0.0),  # first sample: baseline only
        (25.8, None, 0.0),  # no reading
        (25.8, 25.8, 0.0),
        (25.8, 27.0, 1.2),
        (25.8, 0.0, 0.0),  # "Reset kWh"
        (25.8, 0.4, 0.4),  # reset, then 0.4 kWh: counts from 0
        (25.8, -1.0, 0.0),
        (float("nan"), 3.0, 0.0),
    ],
)
def test_counter_delta(previous: float | None, current: float | None, delta: float) -> None:
    assert tariff.counter_delta(previous, current) == pytest.approx(delta)


def test_split_short_gap_goes_to_the_zone_of_the_poll() -> None:
    """2.5 min since the last sample (<= 3 polls): all of it is night at 23:00:30."""
    previous = kyiv(2026, 10, 28, 22, 58)
    now = kyiv(2026, 10, 28, 23, 0, 30)
    assert tariff.split_delta(0.5, previous, now, *NIGHT, GAP) == (0.0, 0.5)
    assert tariff.split_delta(0.5, kyiv(2026, 10, 28, 6, 58), kyiv(2026, 10, 28, 7, 0), *NIGHT, GAP) == (
        0.5,
        0.0,
    )


def test_split_without_previous_sample() -> None:
    assert tariff.split_delta(1.0, None, kyiv(2026, 10, 28, 3, 0), *NIGHT, GAP) == (0.0, 1.0)


def test_split_long_gap_is_proportional() -> None:
    """06:00 -> 08:00 (HA down): one hour night, one hour day."""
    day, night = tariff.split_delta(
        2.0, kyiv(2026, 10, 29, 6, 0), kyiv(2026, 10, 29, 8, 0), *NIGHT, GAP
    )
    assert (day, night) == (pytest.approx(1.0), pytest.approx(1.0))
    assert day + night == pytest.approx(2.0)


def test_split_long_gap_over_days() -> None:
    day, night = tariff.split_delta(
        6.0, kyiv(2026, 10, 26, 12, 0), kyiv(2026, 10, 29, 12, 0), *NIGHT, GAP
    )
    assert night == pytest.approx(2.0)
    assert day == pytest.approx(4.0)


def test_split_gap_exactly_three_polls_is_not_split() -> None:
    previous = kyiv(2026, 10, 28, 22, 59)
    now = previous + GAP  # 23:02, not longer than the limit
    assert tariff.split_delta(0.3, previous, now, *NIGHT, GAP) == (0.0, 0.3)
    assert tariff.split_delta(0.3, previous, now + timedelta(seconds=1), *NIGHT, GAP) != (0.0, 0.3)


def test_split_nothing() -> None:
    assert tariff.split_delta(0.0, None, kyiv(2026, 10, 28, 3, 0), *NIGHT, GAP) == (0.0, 0.0)
    assert tariff.split_delta(-1.0, None, kyiv(2026, 10, 28, 3, 0), *NIGHT, GAP) == (0.0, 0.0)


def test_cost_of() -> None:
    assert tariff.cost_of(1.0, 1.0, 4.32, 2.16) == pytest.approx(6.48)
    assert tariff.cost_of(0.0, 0.0, 4.32, 2.16) == 0.0


# --------------------------------------------------------------------------- #
# Energy boost maths
# --------------------------------------------------------------------------- #


def test_energy_deadline_formula() -> None:
    """start + (target / (amps * 0.23 kW)) * 1.5 + 30 min."""
    start = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)
    hours = 10 / (16 * 0.23) * 1.5
    assert tariff.energy_deadline(start, 10, 16) == start + timedelta(hours=hours, minutes=30)
    # 2 kWh at 6 A: 2.17 h * ... -> about 2 h 40 min after the start.
    deadline = tariff.energy_deadline(start, 2, 6)
    assert deadline - start == timedelta(hours=2 / 1.38 * 1.5, minutes=30)
    assert timedelta(hours=2, minutes=40) < deadline - start < timedelta(hours=2, minutes=41)


@pytest.mark.parametrize("amps", [None, 0, -5])
def test_energy_deadline_without_current_uses_6_a(amps: int | None) -> None:
    start = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)
    assert tariff.energy_deadline(start, 2, amps) == tariff.energy_deadline(start, 2, 6)


def test_energy_deadline_of_nothing_is_the_margin() -> None:
    start = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)
    assert tariff.energy_deadline(start, 0, 16) == start + timedelta(minutes=30)


def test_estimate_end() -> None:
    now = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)
    assert tariff.estimate_end(now, 1.817, 3634.0) == now + timedelta(hours=0.5)
    assert tariff.estimate_end(now, 0.0, 3634.0) == now
    assert tariff.estimate_end(now, 1.0, 0.0) is None
    assert tariff.estimate_end(now, 1.0, None) is None
    assert tariff.estimate_end(now, 1.0, float("nan")) is None
