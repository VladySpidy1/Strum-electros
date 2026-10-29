"""Energy boosts ("charge N kWh"), Q8 timed expiry, ∞ and re-timing boosts.

Time is frozen in Europe/Kyiv. The fake charger starts at 25.8 kWh with its
timer at 23:00-07:00, a car connected (status 1) and 3634 W reported power.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .common import (
    STORE_KEY,
    FakeCharger,
    async_call,
    async_fire_at,
    async_poll,
    async_press,
    async_set_number,
    async_setup_entry,
    async_switch,
    boost_state,
    entity_id,
    local_dt,
    make_entry,
    state,
    storage_payload,
    stored,
)

WED_18H = (2026, 10, 28, 18, 0)  # outside the 23:00-07:00 window
WED_2245 = (2026, 10, 28, 22, 45)
WED_2330 = (2026, 10, 28, 23, 30)  # inside the window
START_KWH_RAW = 25_800_000


def _kwh(charger: FakeCharger, kwh: float) -> None:
    """Set the charger's energy counter to ``kwh``."""
    charger.energy_raw = round(kwh * 1_000_000)


def _deadline(start, kwh: float, amps: int):
    return start + timedelta(hours=kwh / (amps * 0.23) * 1.5, minutes=30)


def _seeded(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "night_enabled": True,
        "timer_start": "23:00",
        "timer_end": "07:00",
        "weekend_free": False,
        "boost_minutes": 30,
        "charging_amps": None,
        "boost": None,
        "last_written": "23:00-07:00",
        "seeded": True,
    }
    data.update(overrides)
    return data


# --------------------------------------------------------------------------- #
# Energy boost lifecycle
# --------------------------------------------------------------------------- #


async def test_charge_energy_lifecycle(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """Timer off, start, count the energy, stop at the target, restore the timer."""
    start = local_dt(*WED_18H)
    freezer.move_to(start)
    entry = await async_setup_entry(hass, charger)
    coordinator = entry.runtime_data
    assert coordinator.update_interval == timedelta(seconds=60)

    await async_call(hass, "charge_energy", kwh=2, amps=16)
    assert charger.take_calls() == [("disable_timer",), ("start", 16)]
    value, attrs = boost_state(hass)
    assert attrs["active"] is True
    assert attrs["mode"] == "energy"
    assert attrs["unlimited"] is False
    assert attrs["minutes"] == 0
    assert attrs["target_kwh"] == 2.0
    assert attrs["delivered_kwh"] == 0.0
    assert dt_util.parse_datetime(attrs["deadline"]) == _deadline(start, 2, 16)
    # Estimated end: 2 kWh at the reported 3634 W.
    estimate = dt_util.parse_datetime(value)
    assert abs(estimate - (start + timedelta(hours=2 / 3.634))) < timedelta(seconds=2)
    assert state(hass, "switch", "charging").state == "on"
    assert stored(hass_storage)["boost"]["target_kwh"] == 2.0

    # Polling every 20 s while the energy boost runs.
    assert coordinator.update_interval == timedelta(seconds=20)
    await async_fire_at(hass, freezer, start + timedelta(seconds=11))  # follow-up refresh
    polls = charger.polls
    await async_fire_at(hass, freezer, start + timedelta(seconds=32))
    assert charger.polls == polls + 1
    assert charger.take_calls() == []

    _kwh(charger, 26.8)
    await async_poll(hass, entry)
    assert charger.take_calls() == []
    value, attrs = boost_state(hass)
    assert attrs["delivered_kwh"] == 1.0
    assert abs(
        dt_util.parse_datetime(value) - (dt_util.utcnow() + timedelta(hours=1 / 3.634))
    ) < timedelta(seconds=2)

    charger.power_raw = 0  # paused: no estimate
    await async_poll(hass, entry)
    assert boost_state(hass)[0] == STATE_UNKNOWN
    charger.power_raw = 36340

    _kwh(charger, 27.9)
    await async_poll(hass, entry)
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]
    value, attrs = boost_state(hass)
    assert value == STATE_UNKNOWN
    assert attrs["active"] is False
    assert attrs["mode"] is None
    assert attrs["target_kwh"] is None
    assert attrs["delivered_kwh"] is None
    assert attrs["deadline"] is None
    assert attrs["last_error"] is None
    assert state(hass, "switch", "charging").state == "off"
    assert stored(hass_storage)["boost"] is None
    assert coordinator.update_interval == timedelta(seconds=60)  # configured again

    # Nothing else happens later, also not at the old deadline.
    await async_fire_at(hass, freezer, _deadline(start, 2, 16) + timedelta(minutes=1))
    assert charger.take_calls() == []


async def test_energy_boost_deadline(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """Target not reached (car full, slow charge): stop at the deadline, set last_error."""
    start = local_dt(*WED_18H)
    freezer.move_to(start)
    await async_setup_entry(hass, charger)
    await async_call(hass, "charge_energy", kwh=2, amps=6)
    charger.take_calls()
    deadline = _deadline(start, 2, 6)
    assert timedelta(hours=2, minutes=40) < deadline - start < timedelta(hours=2, minutes=41)

    await async_fire_at(hass, freezer, deadline - timedelta(minutes=1))
    assert charger.take_calls() == []
    assert boost_state(hass)[1]["active"] is True

    await async_fire_at(hass, freezer, deadline + timedelta(seconds=1))
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]
    _, attrs = boost_state(hass)
    assert attrs["active"] is False
    assert attrs["last_error"] == "energy_deadline"

    # The next start clears the error.
    await async_call(hass, "charge_for", minutes=10)
    assert boost_state(hass)[1]["last_error"] is None


async def test_counter_reset_does_not_end_the_boost(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """Positive deltas are accumulated: "Reset kWh" during the boost changes nothing."""
    freezer.move_to(local_dt(*WED_18H))
    entry = await async_setup_entry(hass, charger)
    await async_call(hass, "charge_energy", kwh=3)
    charger.take_calls()

    _kwh(charger, 27.8)
    await async_poll(hass, entry)
    assert boost_state(hass)[1]["delivered_kwh"] == 2.0

    await async_press(hass, "reset_energy")  # counter -> 0
    assert charger.take_calls() == [("resetkwt",)]
    assert boost_state(hass)[1]["delivered_kwh"] == 2.0

    _kwh(charger, 0.5)
    await async_poll(hass, entry)
    assert charger.take_calls() == []
    _, attrs = boost_state(hass)
    assert attrs["active"] is True
    assert attrs["delivered_kwh"] == 2.5

    _kwh(charger, 1.0)
    await async_poll(hass, entry)
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]


async def test_extend_energy_boost_by_kwh(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    start = local_dt(*WED_18H)
    freezer.move_to(start)
    entry = await async_setup_entry(hass, charger)
    await async_call(hass, "charge_energy", kwh=2)
    charger.take_calls()

    _kwh(charger, 27.3)
    await async_poll(hass, entry)
    await async_call(hass, "extend_charge", kwh=1)
    assert charger.take_calls() == []  # only HA's target moves
    _, attrs = boost_state(hass)
    assert attrs["target_kwh"] == 3.0
    assert attrs["delivered_kwh"] == 1.5
    assert dt_util.parse_datetime(attrs["deadline"]) == _deadline(start, 3, 16)

    _kwh(charger, 28.3)  # 2.5 kWh: the old target would have ended it
    await async_poll(hass, entry)
    assert charger.take_calls() == []
    _kwh(charger, 28.9)
    await async_poll(hass, entry)
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]


async def test_extend_by_kwh_without_energy_boost_starts_one(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local_dt(*WED_18H))
    entry = await async_setup_entry(hass, charger)
    await async_call(hass, "extend_charge", kwh=5)
    assert charger.take_calls() == [("disable_timer",), ("start", 16)]
    _, attrs = boost_state(hass)
    assert attrs["mode"] == "energy"
    assert attrs["target_kwh"] == 5.0

    # A timed boost is replaced by an energy boost, and the other way round.
    await async_call(hass, "charge_for", minutes=30)
    assert charger.take_calls() == [("start", 16)]
    assert entry.runtime_data.update_interval == timedelta(seconds=60)
    await async_call(hass, "extend_charge", kwh=4)
    assert charger.take_calls() == [("start", 16)]
    _, attrs = boost_state(hass)
    assert (attrs["mode"], attrs["target_kwh"], attrs["delivered_kwh"]) == ("energy", 4.0, 0.0)
    await async_call(hass, "extend_charge", minutes=20)
    assert charger.take_calls() == [("start", 16)]
    _, attrs = boost_state(hass)
    assert (attrs["mode"], attrs["minutes"]) == ("time", 20)


async def test_lower_current_moves_the_deadline(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    start = local_dt(*WED_18H)
    freezer.move_to(start)
    await async_setup_entry(hass, charger)
    await async_call(hass, "charge_energy", kwh=10, amps=32)
    assert dt_util.parse_datetime(boost_state(hass)[1]["deadline"]) == _deadline(start, 10, 32)
    charger.take_calls()

    await async_set_number(hass, "charging_amps", 6)
    assert charger.take_calls() == [("start", 6)]
    assert dt_util.parse_datetime(boost_state(hass)[1]["deadline"]) == _deadline(start, 10, 6)

    await async_fire_at(hass, freezer, _deadline(start, 10, 32) + timedelta(minutes=1))
    assert charger.take_calls() == []  # the old deadline no longer ends it


@pytest.mark.parametrize(
    "data",
    [
        {"kwh": 0.2},
        {"kwh": 150},
        {"kwh": "nan"},
        {"kwh": 5, "amps": 40},
        {},
    ],
)
async def test_charge_energy_validation(
    hass: HomeAssistant, charger: FakeCharger, data: dict[str, Any]
) -> None:
    await async_setup_entry(hass, charger)
    with pytest.raises(ServiceValidationError):
        await async_call(hass, "charge_energy", **data)
    assert charger.calls == []


@pytest.mark.parametrize("data", [{}, {"minutes": 10, "kwh": 2}, {"kwh": 0}, {"kwh": 101}])
async def test_extend_charge_needs_exactly_one_of_minutes_or_kwh(
    hass: HomeAssistant, charger: FakeCharger, data: dict[str, Any]
) -> None:
    await async_setup_entry(hass, charger)
    with pytest.raises(ServiceValidationError):
        await async_call(hass, "extend_charge", **data)
    assert charger.calls == []


async def test_boost_energy_button_and_boost_kwh(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    freezer.move_to(local_dt(*WED_18H))
    await async_setup_entry(hass, charger)
    number = state(hass, "number", "boost_kwh")
    assert number.state == "10.0"
    assert (number.attributes["min"], number.attributes["max"], number.attributes["step"]) == (
        1,
        80,
        1,
    )
    assert number.attributes["unit_of_measurement"] == "kWh"
    # A primary control, not a configuration entity.
    assert er.async_get(hass).async_get(entity_id(hass, "number", "boost_kwh")).entity_category is None

    await async_set_number(hass, "boost_kwh", 15)
    assert state(hass, "number", "boost_kwh").state == "15.0"
    assert stored(hass_storage)["boost_kwh"] == 15
    assert charger.calls == []
    with pytest.raises(ServiceValidationError):
        await async_set_number(hass, "boost_kwh", 90)

    await async_press(hass, "boost_energy")
    assert charger.take_calls() == [("disable_timer",), ("start", 16)]
    _, attrs = boost_state(hass)
    assert (attrs["mode"], attrs["target_kwh"]) == ("energy", 15.0)


async def test_restart_continues_energy_boost(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """Energy charged while HA was down counts; polling is fast again at once."""
    now = local_dt(*WED_18H)
    freezer.move_to(now)
    started = now - timedelta(hours=1)
    hass_storage[STORE_KEY] = storage_payload(
        _seeded(
            last_written="",
            boost={
                "end": None,
                "restore": True,
                "started_at": started.isoformat(),
                "target_kwh": 5,
                "delivered_kwh": 2,
                "deadline": (now + timedelta(hours=1)).isoformat(),
                "amps": 16,
            },
            tariff={
                "last_energy": 25.8,
                "last_sample_at": (now - timedelta(minutes=5)).isoformat(),
            },
        ),
        version=2,
    )
    charger.status = "2"
    charger.timer = None
    _kwh(charger, 26.8)
    entry = await async_setup_entry(hass, charger, make_entry(hass))
    assert charger.take_calls() == []
    _, attrs = boost_state(hass)
    assert (attrs["mode"], attrs["target_kwh"], attrs["delivered_kwh"]) == ("energy", 5.0, 3.0)
    assert entry.runtime_data.update_interval == timedelta(seconds=20)

    _kwh(charger, 28.8)
    await async_poll(hass, entry)
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]
    assert entry.runtime_data.update_interval == timedelta(seconds=60)


async def test_restart_after_energy_deadline(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    now = local_dt(*WED_18H)
    freezer.move_to(now)
    hass_storage[STORE_KEY] = storage_payload(
        _seeded(
            last_written="",
            boost={
                "restore": True,
                "started_at": (now - timedelta(hours=5)).isoformat(),
                "target_kwh": 5,
                "delivered_kwh": 1,
                "deadline": (now - timedelta(minutes=1)).isoformat(),
                "amps": 16,
            },
        ),
        version=2,
    )
    charger.status = "2"
    charger.timer = None
    await async_setup_entry(hass, charger, make_entry(hass))
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]
    _, attrs = boost_state(hass)
    assert attrs["active"] is False
    assert attrs["last_error"] == "energy_deadline"


# --------------------------------------------------------------------------- #
# Q8: a timed boost ending inside the night window does not stop
# --------------------------------------------------------------------------- #


async def test_timed_boost_ending_inside_night_window_keeps_charging(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    start = local_dt(*WED_2245)
    freezer.move_to(start)
    await async_setup_entry(hass, charger)
    await async_call(hass, "charge_for", minutes=30)
    assert charger.take_calls() == [("disable_timer",), ("start", 16)]

    await async_fire_at(hass, freezer, start + timedelta(minutes=30, seconds=1))  # 23:15
    assert charger.take_calls() == [("set_timer", "23:00", "07:00")]  # no stop
    assert boost_state(hass)[1]["active"] is False
    assert state(hass, "switch", "charging").state == "on"


async def test_timed_boost_ending_outside_window_on_weekend_free_stops(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """No window is restored on a free weekend: nothing continues, so it stops."""
    start = local_dt(2026, 10, 31, 23, 0)  # Saturday, the timer is off all day
    freezer.move_to(start)
    await async_setup_entry(hass, charger)
    await async_switch(hass, "weekend_free", True)
    assert charger.take_calls() == [("disable_timer",)]
    await async_call(hass, "charge_for", minutes=20)
    assert charger.take_calls() == [("start", 16)]
    await async_fire_at(hass, freezer, start + timedelta(minutes=20, seconds=1))
    assert charger.take_calls() == [("stop",)]


async def test_restart_with_boost_expired_inside_night_window(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    freezer.move_to(local_dt(*WED_2330))
    end = local_dt(*WED_2330) - timedelta(minutes=10)
    hass_storage[STORE_KEY] = storage_payload(
        _seeded(last_written="", boost={"end": end.isoformat(), "minutes": 30, "restore": True})
    )
    charger.status = "2"
    charger.timer = None
    await async_setup_entry(hass, charger, make_entry(hass))
    assert charger.take_calls() == [("set_timer", "23:00", "07:00")]
    assert boost_state(hass)[1]["active"] is False


async def test_energy_boost_inside_night_window_stops_at_target(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local_dt(*WED_2330))
    entry = await async_setup_entry(hass, charger)
    await async_call(hass, "charge_energy", kwh=1)
    assert charger.take_calls() == [("disable_timer",), ("start", 16)]
    _kwh(charger, 26.8)
    await async_poll(hass, entry)
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]


# --------------------------------------------------------------------------- #
# ∞ and re-timing while charging
# --------------------------------------------------------------------------- #


async def test_infinity_turns_a_timed_boost_into_a_bypass(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    start = local_dt(*WED_18H)
    freezer.move_to(start)
    entry = await async_setup_entry(hass, charger)
    await async_call(hass, "charge_for", minutes=30)
    charger.take_calls()

    await async_switch(hass, "charging", True)
    assert charger.take_calls() == [("start", 16)]  # the timer is already off
    value, attrs = boost_state(hass)
    assert value == STATE_UNKNOWN
    assert (attrs["active"], attrs["mode"], attrs["unlimited"]) == (True, "unlimited", True)

    await async_fire_at(hass, freezer, start + timedelta(minutes=30, seconds=1))
    assert charger.take_calls() == []  # the old end is gone

    charger.status = "1"  # car full
    await async_poll(hass, entry)
    assert charger.take_calls() == [("set_timer", "23:00", "07:00")]
    assert boost_state(hass)[1]["active"] is False


async def test_infinity_turns_an_energy_boost_into_a_bypass(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local_dt(*WED_18H))
    entry = await async_setup_entry(hass, charger)
    await async_call(hass, "charge_energy", kwh=1)
    charger.take_calls()
    assert entry.runtime_data.update_interval == timedelta(seconds=20)

    await async_switch(hass, "charging", True)
    assert charger.take_calls() == [("start", 16)]
    assert boost_state(hass)[1]["mode"] == "unlimited"
    assert entry.runtime_data.update_interval == timedelta(seconds=60)

    _kwh(charger, 30.0)  # far past the old target: no stop
    await async_poll(hass, entry)
    assert charger.take_calls() == []
    assert boost_state(hass)[1]["mode"] == "unlimited"


async def test_charge_for_during_bypass_replaces_it(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """A chip tapped while charging (∞) re-times the session."""
    start = local_dt(*WED_18H)
    freezer.move_to(start)
    await async_setup_entry(hass, charger)
    await async_switch(hass, "charging", True)
    assert charger.take_calls() == [("disable_timer",), ("start", 16)]

    await async_call(hass, "charge_for", minutes=30)
    assert charger.take_calls() == [("start", 16)]
    value, attrs = boost_state(hass)
    assert (attrs["mode"], attrs["minutes"]) == ("time", 30)
    assert dt_util.parse_datetime(value) == start + timedelta(minutes=30)

    await async_fire_at(hass, freezer, start + timedelta(minutes=30, seconds=1))
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]


async def test_chip_while_the_night_window_charges(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """Charging by schedule (no boost) and a chip is tapped: a timed boost takes over;
    at its end inside the window the schedule just continues (Q8)."""
    start = local_dt(*WED_2330)
    freezer.move_to(start)
    charger.status = "2"
    await async_setup_entry(hass, charger)
    assert boost_state(hass)[1]["active"] is False

    await async_call(hass, "charge_for", minutes=30)
    assert charger.take_calls() == [("disable_timer",), ("start", 16)]
    await async_fire_at(hass, freezer, start + timedelta(minutes=30, seconds=1))
    assert charger.take_calls() == [("set_timer", "23:00", "07:00")]
    assert state(hass, "switch", "charging").state == "on"


async def test_restart_keeps_energy_settings_and_prices(
    hass: HomeAssistant, charger: FakeCharger, hass_storage: dict[str, Any]
) -> None:
    entry = await async_setup_entry(hass, charger)
    await async_set_number(hass, "boost_kwh", 25)
    await async_set_number(hass, "price_day", 5.5)
    await async_set_number(hass, "price_night", 1.75)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert (stored(hass_storage)["boost_kwh"], stored(hass_storage)["price_night"]) == (25, 1.75)

    await async_setup_entry(hass, charger, entry)
    assert state(hass, "number", "boost_kwh").state == "25.0"
    assert state(hass, "number", "price_day").state == "5.5"
    assert state(hass, "number", "price_night").state == "1.75"
    assert charger.calls == []


async def test_charge_for_during_timed_boost_re_times_it(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    start = local_dt(*WED_18H)
    freezer.move_to(start)
    await async_setup_entry(hass, charger)
    await async_call(hass, "charge_for", minutes=60)
    charger.take_calls()

    freezer.move_to(start + timedelta(minutes=5))
    await async_call(hass, "charge_for", minutes=15)
    assert charger.take_calls() == [("start", 16)]
    _, attrs = boost_state(hass)
    assert attrs["minutes"] == 15

    await async_fire_at(hass, freezer, start + timedelta(minutes=20, seconds=1))
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]
    await async_fire_at(hass, freezer, start + timedelta(minutes=61))
    assert charger.take_calls() == []


# --------------------------------------------------------------------------- #
# More energy boost cases (night timer off, weekend, stop, failures, offline)
# --------------------------------------------------------------------------- #


async def test_charge_energy_with_night_timer_off(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """No timer on the charger: only start and stop; the service current is stored."""
    charger.timer = None
    start = local_dt(*WED_18H)
    freezer.move_to(start)
    entry = await async_setup_entry(hass, charger)
    assert state(hass, "switch", "night_timer").state == "off"

    await async_call(hass, "charge_energy", kwh=1.5, amps=10)
    assert charger.take_calls() == [("start", 10)]
    assert state(hass, "number", "charging_amps").state == "10.0"
    assert stored(hass_storage)["charging_amps"] == 10
    _, attrs = boost_state(hass)
    assert attrs["target_kwh"] == 1.5
    assert dt_util.parse_datetime(attrs["deadline"]) == _deadline(start, 1.5, 10)

    _kwh(charger, 27.3)
    await async_poll(hass, entry)
    assert charger.take_calls() == [("stop",)]  # nothing to restore
    assert boost_state(hass)[1]["active"] is False


async def test_energy_boost_on_a_free_weekend(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """weekend_free keeps the timer off on Saturday: the boost writes no timer at all."""
    freezer.move_to(local_dt(2026, 10, 31, 12, 0))  # Saturday
    entry = await async_setup_entry(hass, charger)
    await async_switch(hass, "weekend_free", True)
    assert charger.take_calls() == [("disable_timer",)]

    await async_call(hass, "charge_energy", kwh=1)
    assert charger.take_calls() == [("start", 16)]
    _kwh(charger, 26.8)
    await async_poll(hass, entry)
    assert charger.take_calls() == [("stop",)]


async def test_stop_during_energy_boost(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """Stop cancels the target and the deadline; polling goes back to the configured interval."""
    start = local_dt(*WED_18H)
    freezer.move_to(start)
    entry = await async_setup_entry(hass, charger)
    await async_call(hass, "charge_energy", kwh=2, amps=16)
    charger.take_calls()
    assert entry.runtime_data.update_interval == timedelta(seconds=20)

    await async_switch(hass, "charging", False)
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]
    _, attrs = boost_state(hass)
    assert (attrs["active"], attrs["mode"], attrs["last_error"]) == (False, None, None)
    assert entry.runtime_data.update_interval == timedelta(seconds=60)

    _kwh(charger, 30.0)  # energy after the stop ends nothing
    await async_poll(hass, entry)
    await async_fire_at(hass, freezer, _deadline(start, 2, 16) + timedelta(minutes=1))
    assert charger.take_calls() == []
    assert boost_state(hass)[1]["last_error"] is None


async def test_failed_energy_start_restores_the_timer(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local_dt(*WED_18H))
    entry = await async_setup_entry(hass, charger)
    charger.fail.add("start")
    with pytest.raises(HomeAssistantError) as err:
        await async_call(hass, "charge_energy", kwh=5)
    assert err.value.translation_key == "command_failed"
    assert charger.take_calls() == [
        ("disable_timer",),
        ("start", 16),
        ("set_timer", "23:00", "07:00"),
    ]
    assert boost_state(hass)[1]["active"] is False
    assert entry.runtime_data.update_interval == timedelta(seconds=60)


async def test_energy_deadline_inside_the_night_window_still_stops(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """Q8 is only for timed boosts: an energy boost stops at its deadline even at night."""
    start = local_dt(*WED_2330)
    freezer.move_to(start)
    await async_setup_entry(hass, charger)
    await async_call(hass, "charge_energy", kwh=2, amps=6)
    charger.take_calls()
    deadline = _deadline(start, 2, 6)  # about 02:10, inside 23:00-07:00
    await async_fire_at(hass, freezer, deadline + timedelta(seconds=1))
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]
    assert boost_state(hass)[1]["last_error"] == "energy_deadline"


async def test_failed_stop_at_the_energy_target_is_retried_even_at_night(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """An energy boost always stops at its target, also when that stop has to be
    retried inside the night window."""
    freezer.move_to(local_dt(*WED_2330))
    entry = await async_setup_entry(hass, charger)
    await async_call(hass, "charge_energy", kwh=1)
    charger.take_calls()

    charger.fail.add("stop")
    _kwh(charger, 26.8)
    await async_poll(hass, entry)
    calls = charger.take_calls()
    assert ("set_timer", "23:00", "07:00") in calls
    assert {call for call in calls if call[0] != "set_timer"} == {("stop",)}
    _, attrs = boost_state(hass)
    assert (attrs["active"], attrs["last_error"]) == (False, "stop_failed")
    assert stored(hass_storage)["stop_pending"] == "always"
    assert entry.runtime_data.update_interval == timedelta(seconds=60)

    charger.fail.clear()
    await async_poll(hass, entry)
    assert charger.take_calls() == [("stop",)]
    assert state(hass, "switch", "charging").state == "off"
    assert boost_state(hass)[1]["last_error"] is None
    assert stored(hass_storage)["stop_pending"] is None


async def test_energy_deadline_while_offline_stops_when_back(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    start = local_dt(*WED_18H)
    freezer.move_to(start)
    entry = await async_setup_entry(hass, charger)
    await async_call(hass, "charge_energy", kwh=2, amps=16)
    charger.take_calls()

    charger.status = "77"
    await async_poll(hass, entry)
    await async_fire_at(hass, freezer, _deadline(start, 2, 16) + timedelta(seconds=1))
    assert charger.take_calls() == []
    _, attrs = boost_state(hass)
    assert (attrs["active"], attrs["last_error"]) == (False, "stop_failed")

    charger.status = "2"
    await async_poll(hass, entry)
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]
    assert boost_state(hass)[1]["last_error"] is None


async def test_energy_boost_while_the_charger_is_unreachable(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """No estimate without fresh data; offline polls count nothing; the boost survives."""
    freezer.move_to(local_dt(*WED_18H))
    entry = await async_setup_entry(hass, charger)
    await async_call(hass, "charge_energy", kwh=2)
    charger.take_calls()
    assert boost_state(hass)[0] != STATE_UNKNOWN

    charger.unreachable = True
    await async_poll(hass, entry)
    value, attrs = boost_state(hass)
    assert value == STATE_UNKNOWN  # the estimate needs a fresh power reading
    assert (attrs["active"], attrs["mode"], attrs["target_kwh"]) == (True, "energy", 2.0)

    charger.unreachable = False
    charger.status = "77"  # the cloud reports the charger offline
    _kwh(charger, 40.0)
    await async_poll(hass, entry)
    assert boost_state(hass)[1]["delivered_kwh"] == 0.0
    assert charger.take_calls() == []

    charger.status = "2"
    _kwh(charger, 26.3)  # online again: the delta since the last online sample
    await async_poll(hass, entry)
    _, attrs = boost_state(hass)
    assert (attrs["active"], attrs["delivered_kwh"]) == (True, 0.5)


async def test_extending_late_gives_the_remaining_energy_time(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """Extend by kWh: deadline = max(start + f(new target), now + f(remaining))."""
    start = local_dt(*WED_18H)
    freezer.move_to(start)
    entry = await async_setup_entry(hass, charger)
    await async_call(hass, "charge_energy", kwh=2, amps=16)
    charger.take_calls()

    now = start + timedelta(hours=1)
    freezer.move_to(now)
    _kwh(charger, 26.3)  # only 0.5 kWh in an hour (the car limits the current)
    await async_poll(hass, entry)
    await async_call(hass, "extend_charge", kwh=1)
    _, attrs = boost_state(hass)
    assert (attrs["target_kwh"], attrs["delivered_kwh"]) == (3.0, 0.5)
    assert dt_util.parse_datetime(attrs["deadline"]) == _deadline(now, 2.5, 16)
    assert _deadline(now, 2.5, 16) > _deadline(start, 3, 16)


async def test_boost_button_during_energy_boost_switches_to_time(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    start = local_dt(*WED_18H)
    freezer.move_to(start)
    entry = await async_setup_entry(hass, charger)
    await async_press(hass, "boost_energy")
    assert charger.take_calls() == [("disable_timer",), ("start", 16)]

    await async_press(hass, "boost")
    assert charger.take_calls() == [("start", 16)]
    value, attrs = boost_state(hass)
    assert (attrs["mode"], attrs["minutes"], attrs["target_kwh"]) == ("time", 30, None)
    assert dt_util.parse_datetime(value) == start + timedelta(minutes=30)
    assert entry.runtime_data.update_interval == timedelta(seconds=60)


# --------------------------------------------------------------------------- #
# Q13: the car stops taking current before the energy target
# --------------------------------------------------------------------------- #


async def test_energy_boost_ends_when_the_car_takes_no_current(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """Battery full at 1 of 10 kWh: after 10 min without current the boost ends and
    the night timer comes back instead of waiting for the safety deadline."""
    start = local_dt(*WED_18H)
    freezer.move_to(start)
    entry = await async_setup_entry(hass, charger)
    await async_call(hass, "charge_energy", kwh=10)
    assert charger.take_calls() == [("disable_timer",), ("start", 16)]

    _kwh(charger, 26.8)
    freezer.move_to(start + timedelta(minutes=5))
    await async_poll(hass, entry)
    assert boost_state(hass)[1]["delivered_kwh"] == 1.0

    charger.status = "1"  # the car stopped taking current
    freezer.move_to(start + timedelta(minutes=6))
    await async_poll(hass, entry)
    freezer.move_to(start + timedelta(minutes=15))
    await async_poll(hass, entry)
    assert charger.take_calls() == []
    assert boost_state(hass)[1]["active"] is True

    freezer.move_to(start + timedelta(minutes=16, seconds=1))
    await async_poll(hass, entry)
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]
    _, attrs = boost_state(hass)
    assert attrs["active"] is False
    assert attrs["last_error"] is None
    assert entry.runtime_data.update_interval == timedelta(seconds=60)


async def test_energy_boost_idle_clock_restarts_when_the_car_draws_again(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """A pause (status 2 at 0 W counts as idle) shorter than 10 min does not end it."""
    start = local_dt(*WED_18H)
    freezer.move_to(start)
    entry = await async_setup_entry(hass, charger)
    await async_call(hass, "charge_energy", kwh=10)
    charger.take_calls()

    charger.power_raw = 0  # status 2, but the car draws nothing
    freezer.move_to(start + timedelta(minutes=1))
    await async_poll(hass, entry)
    charger.power_raw = 36340  # drawing again after 8 min
    freezer.move_to(start + timedelta(minutes=9))
    await async_poll(hass, entry)
    charger.power_raw = 0
    freezer.move_to(start + timedelta(minutes=12))
    await async_poll(hass, entry)
    freezer.move_to(start + timedelta(minutes=20))
    await async_poll(hass, entry)
    assert charger.take_calls() == []
    assert boost_state(hass)[1]["active"] is True

    freezer.move_to(start + timedelta(minutes=22, seconds=1))
    await async_poll(hass, entry)
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]


async def test_energy_boost_idle_end_inside_the_night_window_does_not_stop(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """Like Q8: inside the restored window the schedule takes over, no stop is sent."""
    start = local_dt(*WED_2330)
    freezer.move_to(start)
    entry = await async_setup_entry(hass, charger)
    await async_call(hass, "charge_energy", kwh=10)
    charger.take_calls()

    charger.status = "1"
    freezer.move_to(start + timedelta(minutes=1))
    await async_poll(hass, entry)
    freezer.move_to(start + timedelta(minutes=11, seconds=1))
    await async_poll(hass, entry)
    assert charger.take_calls() == [("set_timer", "23:00", "07:00")]
    assert boost_state(hass)[1]["active"] is False


async def test_infinity_while_the_schedule_charges_bypasses_the_window_end(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """∞ while charging inside the night window: the timer is disabled so 07:00 does
    not stop the car; it comes back when charging stops."""
    start = local_dt(*WED_2330)
    freezer.move_to(start)
    charger.status = "2"
    entry = await async_setup_entry(hass, charger)

    await async_switch(hass, "charging", True)
    assert charger.take_calls() == [("disable_timer",), ("start", 16)]
    _, attrs = boost_state(hass)
    assert attrs["mode"] == "unlimited"

    freezer.move_to(local_dt(2026, 10, 29, 7, 30))
    await async_poll(hass, entry)
    assert charger.take_calls() == []
    assert state(hass, "switch", "charging").state == "on"

    charger.status = "1"  # done
    await async_poll(hass, entry)
    assert charger.take_calls() == [("set_timer", "23:00", "07:00")]
    assert boost_state(hass)[1]["active"] is False
