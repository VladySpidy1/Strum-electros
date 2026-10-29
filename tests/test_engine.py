"""Night timer / boost engine: what is written to the charger and when.

Time is frozen in Europe/Kyiv. The fake charger starts with its timer at
23:00-07:00, a car connected (status 1) and reacts to commands like the device.
"""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.electros_ev.const import DOMAIN

from .common import (
    STORE_KEY,
    FakeCharger,
    async_poll,
    async_settle,
    async_setup_entry,
    device_id,
    entity_id,
    local_dt,
    make_entry,
    patch_api,
    state,
    storage_payload,
    stored,
)

WED_18H = (2026, 10, 28, 18, 0)  # outside the 23:00-07:00 window: timer blocks charging
WED_2330 = (2026, 10, 28, 23, 30)  # inside the window


async def _fire(hass: HomeAssistant, freezer: FrozenDateTimeFactory, moment) -> None:
    freezer.move_to(moment)
    async_fire_time_changed(hass, moment)
    await async_settle(hass)


async def _call(hass: HomeAssistant, service: str, **data: Any) -> None:
    await hass.services.async_call(
        DOMAIN, service, {"device_id": device_id(hass), **data}, blocking=True
    )
    await async_settle(hass)


async def _switch(hass: HomeAssistant, key: str, on: bool) -> None:
    await hass.services.async_call(
        "switch",
        "turn_on" if on else "turn_off",
        {"entity_id": entity_id(hass, "switch", key)},
        blocking=True,
    )
    await async_settle(hass)


def _boost(hass: HomeAssistant) -> tuple[str, dict[str, Any]]:
    current = state(hass, "sensor", "boost_end")
    return current.state, dict(current.attributes)


# --------------------------------------------------------------------------- #
# Timed boost ("charge for N minutes")
# --------------------------------------------------------------------------- #


async def test_charge_for_lifecycle(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """Timer disabled before start; stop at the end; then the window is restored."""
    freezer.move_to(local_dt(*WED_18H))
    await async_setup_entry(hass, charger)

    await _call(hass, "charge_for", minutes=30, amps=10)
    assert charger.take_calls() == [("disable_timer",), ("start", 10)]
    end = local_dt(*WED_18H) + timedelta(minutes=30)
    value, attrs = _boost(hass)
    assert dt_util.parse_datetime(value) == end
    assert attrs["active"] is True
    assert attrs["unlimited"] is False
    assert attrs["minutes"] == 30
    assert state(hass, "switch", "charging").state == "on"
    assert state(hass, "number", "charging_amps").state == "10.0"
    assert state(hass, "switch", "night_timer").state == "on"  # the setting is unchanged
    assert stored(hass_storage)["boost"]["end"] == end.isoformat()

    # Polls during the boost write nothing.
    await _fire(hass, freezer, end - timedelta(minutes=1))
    assert charger.take_calls() == []
    assert charger.timer is None

    await _fire(hass, freezer, end + timedelta(seconds=1))
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]
    value, attrs = _boost(hass)
    assert value == STATE_UNKNOWN
    assert attrs["active"] is False
    assert state(hass, "switch", "charging").state == "off"
    assert stored(hass_storage)["boost"] is None

    # Nothing else happens later.
    await _fire(hass, freezer, end + timedelta(hours=1))
    assert charger.take_calls() == []


async def test_boost_button_uses_boost_minutes(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local_dt(*WED_18H))
    await async_setup_entry(hass, charger)
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id(hass, "number", "boost_minutes"), "value": 45},
        blocking=True,
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id(hass, "button", "boost")}, blocking=True
    )
    await async_settle(hass)
    assert charger.take_calls() == [("disable_timer",), ("start", 16)]
    value, attrs = _boost(hass)
    assert attrs["minutes"] == 45
    assert dt_util.parse_datetime(value) == local_dt(*WED_18H) + timedelta(minutes=45)


async def test_extend_charge_moves_the_end(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local_dt(*WED_18H))
    await async_setup_entry(hass, charger)
    await _call(hass, "charge_for", minutes=30)
    charger.take_calls()

    await _call(hass, "extend_charge", minutes=15)
    assert charger.take_calls() == []  # no command: only HA's end time moves
    value, attrs = _boost(hass)
    new_end = local_dt(*WED_18H) + timedelta(minutes=45)
    assert dt_util.parse_datetime(value) == new_end
    assert attrs["minutes"] == 45

    await _fire(hass, freezer, local_dt(*WED_18H) + timedelta(minutes=31))
    assert charger.take_calls() == []  # the old end passes without a stop
    await _fire(hass, freezer, new_end + timedelta(seconds=1))
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]


async def test_extend_without_boost_starts_one(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local_dt(*WED_18H))
    await async_setup_entry(hass, charger)
    await _call(hass, "extend_charge", minutes=20)
    assert charger.take_calls() == [("disable_timer",), ("start", 16)]
    value, attrs = _boost(hass)
    assert attrs["minutes"] == 20
    assert dt_util.parse_datetime(value) == local_dt(*WED_18H) + timedelta(minutes=20)


@pytest.mark.parametrize("how", ["service", "switch"])
async def test_stop_during_boost_restores_timer(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory, how: str
) -> None:
    freezer.move_to(local_dt(*WED_18H))
    await async_setup_entry(hass, charger)
    await _call(hass, "charge_for", minutes=60)
    charger.take_calls()

    if how == "service":
        await _call(hass, "stop_charging")
    else:
        await _switch(hass, "charging", False)
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]
    assert _boost(hass)[1]["active"] is False

    await _fire(hass, freezer, local_dt(*WED_18H) + timedelta(minutes=61))
    assert charger.take_calls() == []  # the cancelled expiry never fires


async def test_charge_for_with_night_timer_off(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """No timer on the charger: nothing to disable or restore."""
    charger.timer = None
    freezer.move_to(local_dt(*WED_18H))
    await async_setup_entry(hass, charger)
    assert state(hass, "switch", "night_timer").state == "off"

    await _call(hass, "charge_for", minutes=10)
    assert charger.take_calls() == [("start", 16)]
    await _fire(hass, freezer, local_dt(*WED_18H) + timedelta(minutes=10, seconds=1))
    assert charger.take_calls() == [("stop",)]


async def test_failed_start_restores_timer(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local_dt(*WED_18H))
    await async_setup_entry(hass, charger)
    charger.fail.add("start")
    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "charge_for", minutes=30)
    assert err.value.translation_key == "command_failed"
    assert charger.take_calls() == [
        ("disable_timer",),
        ("start", 16),
        ("set_timer", "23:00", "07:00"),
    ]
    assert _boost(hass)[1]["active"] is False


async def test_failed_timer_write_blocks_the_start(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local_dt(*WED_18H))
    await async_setup_entry(hass, charger)
    charger.fail.add("disable_timer")
    with pytest.raises(HomeAssistantError):
        await _call(hass, "charge_for", minutes=30)
    assert charger.take_calls() == [("disable_timer",)]
    assert _boost(hass)[1]["active"] is False


# --------------------------------------------------------------------------- #
# switch.charging: unlimited bypass
# --------------------------------------------------------------------------- #


async def test_manual_start_while_timer_blocks_is_unlimited_bypass(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local_dt(*WED_18H))
    entry = await async_setup_entry(hass, charger)

    await _switch(hass, "charging", True)
    assert charger.take_calls() == [("disable_timer",), ("start", 16)]
    value, attrs = _boost(hass)
    assert value == STATE_UNKNOWN  # no end time
    assert attrs["active"] is True
    assert attrs["unlimited"] is True
    assert state(hass, "switch", "charging").state == "on"

    # Hours later it is still charging: no timer, no stop.
    await _fire(hass, freezer, local_dt(2026, 10, 28, 21, 0))
    assert charger.take_calls() == []

    # The car is full: the charger reports "connected, not charging".
    charger.status = "1"
    await async_poll(hass, entry)
    assert charger.take_calls() == [("set_timer", "23:00", "07:00")]  # no stop needed
    assert _boost(hass)[1]["active"] is False


async def test_manual_start_inside_window_is_plain_start(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local_dt(*WED_2330))
    entry = await async_setup_entry(hass, charger)
    await _switch(hass, "charging", True)
    assert charger.take_calls() == [("start", 16)]
    assert _boost(hass)[1]["active"] is False
    charger.status = "1"
    await async_poll(hass, entry)
    assert charger.take_calls() == []


async def test_bypass_ends_if_charging_never_starts(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """Car not plugged in: the bypass gives up after 15 minutes and restores the timer."""
    charger.status = "0"
    charger.start_status = "0"
    freezer.move_to(local_dt(*WED_18H))
    await async_setup_entry(hass, charger)
    await _switch(hass, "charging", True)
    assert charger.take_calls() == [("disable_timer",), ("start", 16)]

    await _fire(hass, freezer, local_dt(*WED_18H) + timedelta(minutes=10))
    assert charger.take_calls() == []
    await _fire(hass, freezer, local_dt(*WED_18H) + timedelta(minutes=17))
    assert charger.take_calls() == [("set_timer", "23:00", "07:00")]
    assert _boost(hass)[1]["active"] is False


async def test_status_4_after_start_sets_last_error(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    charger.start_status = "4"  # the charger still refuses
    freezer.move_to(local_dt(*WED_18H))
    entry = await async_setup_entry(hass, charger)
    await _switch(hass, "charging", True)
    assert state(hass, "sensor", "status").state == "charging_blocked"
    assert _boost(hass)[1]["last_error"] == "charging_blocked"
    assert "charging blocked (status 4) after a start command" in caplog.text

    charger.status = "2"
    await async_poll(hass, entry)
    assert _boost(hass)[1]["last_error"] is None


# --------------------------------------------------------------------------- #
# weekend_free
# --------------------------------------------------------------------------- #


async def test_weekend_free_at_the_weekend_boundary(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local_dt(2026, 10, 30, 12, 0))  # Friday
    entry = await async_setup_entry(hass, charger)
    await _switch(hass, "weekend_free", True)
    assert charger.take_calls() == []  # a weekday: the window stays

    await _fire(hass, freezer, local_dt(2026, 10, 30, 23, 59))
    assert charger.take_calls() == []

    await _fire(hass, freezer, local_dt(2026, 10, 31, 0, 0, 5))  # Saturday 00:00:05
    assert charger.take_calls() == [("disable_timer",)]

    # The charger now reports the timer off; that is HA's own write, not adopted.
    await async_poll(hass, entry)
    assert state(hass, "switch", "night_timer").state == "on"
    assert charger.take_calls() == []

    await _fire(hass, freezer, local_dt(2026, 11, 1, 0, 0, 5))  # Sunday
    assert charger.take_calls() == []

    await _fire(hass, freezer, local_dt(2026, 11, 2, 0, 0, 5))  # Monday
    assert charger.take_calls() == [("set_timer", "23:00", "07:00")]


async def test_weekend_free_switched_on_a_saturday(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local_dt(2026, 10, 31, 15, 0))  # Saturday
    await async_setup_entry(hass, charger)
    await _switch(hass, "weekend_free", True)
    assert charger.take_calls() == [("disable_timer",)]
    await _switch(hass, "weekend_free", False)
    assert charger.take_calls() == [("set_timer", "23:00", "07:00")]


# --------------------------------------------------------------------------- #
# Changes made in the electros web panel
# --------------------------------------------------------------------------- #


async def test_panel_change_is_adopted(hass: HomeAssistant, charger: FakeCharger) -> None:
    entry = await async_setup_entry(hass, charger)

    charger.timer = ("21:00", "05:00")
    await async_poll(hass, entry)
    assert state(hass, "time", "timer_start").state == "21:00:00"
    assert state(hass, "time", "timer_end").state == "05:00:00"
    assert state(hass, "switch", "night_timer").state == "on"

    charger.timer = None  # timer switched off in the panel
    await async_poll(hass, entry)
    assert state(hass, "switch", "night_timer").state == "off"
    assert state(hass, "time", "timer_start").state == "21:00:00"  # times are kept
    assert charger.calls == []  # HA never wrote back


async def test_panel_change_right_after_ha_write_is_ignored(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """Within 120 s of HA's own write a different window is not adopted."""
    freezer.move_to(local_dt(*WED_18H))
    entry = await async_setup_entry(hass, charger)
    await _call(hass, "set_timer", start="22:00")
    assert charger.take_calls() == [("set_timer", "22:00", "07:00")]

    charger.timer = ("20:00", "06:00")
    freezer.tick(60)
    await async_poll(hass, entry)
    assert state(hass, "time", "timer_start").state == "22:00:00"

    freezer.tick(61)
    await async_poll(hass, entry)
    assert state(hass, "time", "timer_start").state == "20:00:00"
    assert state(hass, "time", "timer_end").state == "06:00:00"
    assert charger.take_calls() == []


async def test_panel_change_during_boost_is_not_adopted(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """During a boost HA owns the timer; at the end the HA window is written back."""
    freezer.move_to(local_dt(*WED_18H))
    entry = await async_setup_entry(hass, charger)
    await _call(hass, "charge_for", minutes=30)
    charger.take_calls()

    freezer.tick(300)
    charger.timer = ("21:00", "05:00")
    await async_poll(hass, entry)
    assert state(hass, "time", "timer_start").state == "23:00:00"
    assert state(hass, "switch", "night_timer").state == "on"

    await _fire(hass, freezer, local_dt(*WED_18H) + timedelta(minutes=30, seconds=1))
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]


async def test_polls_never_write(hass: HomeAssistant, charger: FakeCharger) -> None:
    entry = await async_setup_entry(hass, charger)
    for code in ("1", "2", "1", "0", "4", "77", "1"):
        charger.status = code
        await async_poll(hass, entry)
    assert charger.calls == []


async def test_failed_timer_write_is_retried_on_next_poll(
    hass: HomeAssistant, charger: FakeCharger
) -> None:
    entry = await async_setup_entry(hass, charger)
    charger.fail.add("disable_timer")
    with pytest.raises(HomeAssistantError) as err:
        await _switch(hass, "night_timer", False)
    assert err.value.translation_key == "command_failed"
    assert state(hass, "switch", "night_timer").state == "off"  # the setting is kept
    assert charger.take_calls() == [("disable_timer",)]

    charger.unreachable = True  # retried only when the charger answers
    await async_poll(hass, entry)
    assert charger.take_calls() == []

    charger.unreachable = False
    charger.fail.clear()
    await async_poll(hass, entry)
    assert charger.take_calls() == [("disable_timer",)]
    assert charger.timer is None

    await async_poll(hass, entry)
    assert charger.take_calls() == []


# --------------------------------------------------------------------------- #
# Charger offline (status 7/77) and stops that do not reach the charger
# --------------------------------------------------------------------------- #


async def test_offline_boost_expiry_is_written_when_the_charger_is_back(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """The charger loses power during a boost while HA runs on a UPS. Nothing is
    sent to it while offline; when it is back with its old (disabled) timer, HA
    stops it, writes its window and does not adopt the stale one."""
    freezer.move_to(local_dt(*WED_18H))
    entry = await async_setup_entry(hass, charger)
    await _call(hass, "charge_for", minutes=30)
    assert charger.take_calls() == [("disable_timer",), ("start", 16)]
    end = local_dt(*WED_18H) + timedelta(minutes=30)

    charger.status = "77"
    await async_poll(hass, entry)
    assert state(hass, "sensor", "status").state == "offline"

    await _fire(hass, freezer, end + timedelta(seconds=1))
    assert charger.take_calls() == []  # neither stop nor timer: they would not arrive
    _, attrs = _boost(hass)
    assert attrs["active"] is False
    assert attrs["last_error"] == "stop_failed"
    assert state(hass, "switch", "night_timer").state == "on"
    assert stored(hass_storage)["stop_pending"] == "unless_window"
    assert stored(hass_storage)["last_written"] == ""  # what the charger really has

    # Back long after ADOPT_GRACE, still charging, timer still off.
    await _fire(hass, freezer, end + timedelta(minutes=20))
    assert charger.take_calls() == []
    charger.status = "2"
    await async_poll(hass, entry)
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]
    assert state(hass, "switch", "night_timer").state == "on"
    assert state(hass, "switch", "charging").state == "off"
    assert _boost(hass)[1]["last_error"] is None
    data = stored(hass_storage)
    assert (data["last_written"], data["stop_pending"], data["night_enabled"]) == (
        "23:00-07:00",
        None,
        True,
    )

    freezer.tick(timedelta(minutes=5))
    await async_poll(hass, entry)
    assert charger.take_calls() == []
    assert state(hass, "switch", "night_timer").state == "on"


async def test_settings_changed_while_offline_are_written_when_back(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """A change made while the charger is offline is kept and written later,
    not reverted by the window the charger reports when it is back."""
    freezer.move_to(local_dt(*WED_18H))
    entry = await async_setup_entry(hass, charger)
    charger.status = "7"
    await async_poll(hass, entry)

    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "set_timer", start="22:00")
    assert err.value.translation_key == "command_failed"
    with pytest.raises(HomeAssistantError):
        await _switch(hass, "weekend_free", True)
    assert charger.take_calls() == []
    assert state(hass, "time", "timer_start").state == "22:00:00"

    freezer.tick(timedelta(minutes=10))
    charger.status = "1"
    await async_poll(hass, entry)
    assert charger.take_calls() == [("set_timer", "22:00", "07:00")]
    assert state(hass, "time", "timer_start").state == "22:00:00"
    assert state(hass, "switch", "weekend_free").state == "on"
    assert state(hass, "switch", "night_timer").state == "on"

    freezer.tick(timedelta(minutes=5))
    await async_poll(hass, entry)
    assert charger.take_calls() == []


async def test_writes_just_before_the_charger_went_offline_are_redone(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """The boost ends while the charger is still reported online, but it goes
    offline right then and gets neither command: the first online status
    re-sends the stop and rewrites the window instead of adopting the old one."""
    freezer.move_to(local_dt(*WED_18H))
    entry = await async_setup_entry(hass, charger)
    await _call(hass, "charge_for", minutes=30)
    charger.take_calls()

    charger.stop_status = "77"  # the refresh right after the stop sees it offline
    await _fire(hass, freezer, local_dt(*WED_18H) + timedelta(minutes=30, seconds=1))
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]
    assert state(hass, "sensor", "status").state == "offline"

    # Back with what it had before: no window, still charging.
    charger.timer = None
    charger.status = "2"
    charger.stop_status = "1"
    freezer.tick(timedelta(minutes=10))
    await async_poll(hass, entry)
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]
    assert state(hass, "switch", "night_timer").state == "on"


async def test_stop_charging_while_offline_fails_without_sending(
    hass: HomeAssistant, charger: FakeCharger
) -> None:
    entry = await async_setup_entry(hass, charger)
    charger.status = "77"
    await async_poll(hass, entry)
    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "stop_charging")
    assert err.value.translation_placeholders == {"command": "stop"}
    assert charger.calls == []


async def test_failed_stop_at_boost_end_is_retried(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """Night timer off: nothing else would end the charge, so a failed stop is
    retried on online polls while the charger still charges."""
    charger.timer = None
    freezer.move_to(local_dt(*WED_18H))
    entry = await async_setup_entry(hass, charger)
    await _call(hass, "charge_for", minutes=30)
    assert charger.take_calls() == [("start", 16)]

    charger.fail.add("stop")
    await _fire(hass, freezer, local_dt(*WED_18H) + timedelta(minutes=30, seconds=1))
    calls = charger.take_calls()
    assert calls and set(calls) == {("stop",)}  # the stop and its retries failed
    _, attrs = _boost(hass)
    assert (attrs["active"], attrs["last_error"]) == (False, "stop_failed")
    assert state(hass, "switch", "charging").state == "on"
    assert stored(hass_storage)["stop_pending"] == "unless_window"

    charger.unreachable = True  # no status, no retry
    await async_poll(hass, entry)
    assert charger.take_calls() == []

    charger.unreachable = False
    charger.fail.clear()
    await async_poll(hass, entry)
    assert charger.take_calls() == [("stop",)]
    assert state(hass, "switch", "charging").state == "off"
    assert _boost(hass)[1]["last_error"] is None
    assert stored(hass_storage)["stop_pending"] is None

    await async_poll(hass, entry)
    assert charger.take_calls() == []


async def test_failed_timed_stop_is_dropped_once_the_night_window_allows(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """Q8 also for a retried stop: inside the restored night window the
    schedule continues the charge, so the pending stop is not sent."""
    freezer.move_to(local_dt(*WED_18H))
    entry = await async_setup_entry(hass, charger)
    await _call(hass, "charge_for", minutes=30)
    charger.take_calls()

    charger.fail.add("stop")
    await _fire(hass, freezer, local_dt(*WED_18H) + timedelta(minutes=30, seconds=1))
    calls = charger.take_calls()
    assert ("set_timer", "23:00", "07:00") in calls
    assert {call for call in calls if call[0] != "set_timer"} == {("stop",)}

    charger.unreachable = True
    await _fire(hass, freezer, local_dt(*WED_2330))
    charger.unreachable = False
    charger.fail.clear()
    await async_poll(hass, entry)
    assert charger.take_calls() == []
    assert _boost(hass)[1]["last_error"] is None
    assert state(hass, "switch", "charging").state == "on"


async def test_restart_retries_a_pending_stop(
    hass: HomeAssistant, charger: FakeCharger, hass_storage: dict[str, Any]
) -> None:
    hass_storage[STORE_KEY] = storage_payload(_seeded(stop_pending="always"), version=2)
    charger.status = "2"
    await async_setup_entry(hass, charger, make_entry(hass))
    assert charger.take_calls() == [("stop",)]
    assert stored(hass_storage)["stop_pending"] is None
    assert state(hass, "switch", "charging").state == "off"


async def test_restart_drops_a_pending_stop_if_not_charging(
    hass: HomeAssistant, charger: FakeCharger, hass_storage: dict[str, Any]
) -> None:
    hass_storage[STORE_KEY] = storage_payload(_seeded(stop_pending="always"), version=2)
    charger.status = "1"
    entry = await async_setup_entry(hass, charger, make_entry(hass))
    assert charger.calls == []
    assert entry.runtime_data.stored.stop_pending is None
    charger.status = "2"  # charging again later (e.g. the night window) is not stopped
    await async_poll(hass, entry)
    assert charger.calls == []
    assert await hass.config_entries.async_unload(entry.entry_id)
    await async_settle(hass)
    assert stored(hass_storage)["stop_pending"] is None


async def test_a_new_start_cancels_a_pending_stop(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    charger.timer = None
    freezer.move_to(local_dt(*WED_18H))
    entry = await async_setup_entry(hass, charger)
    await _call(hass, "charge_for", minutes=30)
    charger.fail.add("stop")
    await _fire(hass, freezer, local_dt(*WED_18H) + timedelta(minutes=30, seconds=1))
    charger.take_calls()

    charger.fail.clear()
    await _switch(hass, "charging", True)  # the user wants it to charge after all
    assert charger.take_calls() == [("start", 16)]
    assert _boost(hass)[1]["last_error"] is None
    await async_poll(hass, entry)
    assert charger.take_calls() == []
    assert state(hass, "switch", "charging").state == "on"


# --------------------------------------------------------------------------- #
# Restart and unload
# --------------------------------------------------------------------------- #


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


async def test_restart_with_expired_boost(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """The boost ended while HA was down: stop now and restore the timer."""
    freezer.move_to(local_dt(*WED_18H))
    end = local_dt(*WED_18H) - timedelta(minutes=5)
    hass_storage[STORE_KEY] = storage_payload(
        _seeded(
            last_written="",
            boost={"end": end.isoformat(), "minutes": 30, "restore": True},
        )
    )
    charger.status = "2"
    charger.timer = None
    entry = await async_setup_entry(hass, charger, make_entry(hass))
    assert entry.state is ConfigEntryState.LOADED
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]
    assert _boost(hass)[1]["active"] is False
    assert stored(hass_storage)["boost"] is None


async def test_restart_with_future_boost(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """The boost is still running: keep charging and stop at the stored end."""
    freezer.move_to(local_dt(*WED_18H))
    end = local_dt(*WED_18H) + timedelta(minutes=10)
    hass_storage[STORE_KEY] = storage_payload(
        _seeded(
            last_written="",
            boost={"end": end.isoformat(), "minutes": 30, "restore": True},
        )
    )
    charger.status = "2"
    charger.timer = None
    await async_setup_entry(hass, charger, make_entry(hass))
    assert charger.take_calls() == []
    value, attrs = _boost(hass)
    assert dt_util.parse_datetime(value) == end
    assert attrs["active"] is True and attrs["minutes"] == 30

    await _fire(hass, freezer, end + timedelta(seconds=1))
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]


async def test_restart_with_unlimited_bypass(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    freezer.move_to(local_dt(*WED_18H))
    hass_storage[STORE_KEY] = storage_payload(
        _seeded(
            last_written="",
            boost={
                "end": None,
                "unlimited": True,
                "restore": True,
                "started_at": (local_dt(*WED_18H) - timedelta(hours=1)).isoformat(),
                "charging_seen": True,
            },
        )
    )
    charger.status = "2"
    charger.timer = None
    entry = await async_setup_entry(hass, charger, make_entry(hass))
    assert charger.take_calls() == []
    assert _boost(hass)[1]["unlimited"] is True

    charger.status = "1"
    await async_poll(hass, entry)
    assert charger.take_calls() == [("set_timer", "23:00", "07:00")]


async def test_restart_adopts_window_changed_while_stopped(
    hass: HomeAssistant, charger: FakeCharger, hass_storage: dict[str, Any]
) -> None:
    """The device differs from what HA last wrote: changed in the web panel; adopt it."""
    hass_storage[STORE_KEY] = storage_payload(_seeded())
    charger.timer = ("21:00", "05:00")
    entry = await async_setup_entry(hass, charger, make_entry(hass))
    assert charger.take_calls() == []
    assert state(hass, "switch", "night_timer").state == "on"
    assert state(hass, "time", "timer_start").state == "21:00:00"
    assert state(hass, "time", "timer_end").state == "05:00:00"
    assert stored(hass_storage)["last_written"] == "21:00-05:00"
    await async_poll(hass, entry)
    assert charger.take_calls() == []


async def test_restart_adopts_timer_switched_off_while_stopped(
    hass: HomeAssistant, charger: FakeCharger, hass_storage: dict[str, Any]
) -> None:
    hass_storage[STORE_KEY] = storage_payload(_seeded())
    charger.timer = None
    await async_setup_entry(hass, charger, make_entry(hass))
    assert charger.take_calls() == []
    assert state(hass, "switch", "night_timer").state == "off"
    assert state(hass, "time", "timer_start").state == "23:00:00"  # times are kept


async def test_restart_writes_once_if_ha_wants_something_else(
    hass: HomeAssistant, charger: FakeCharger, hass_storage: dict[str, Any]
) -> None:
    """The device still has HA's last write, but HA wants another window: write once."""
    hass_storage[STORE_KEY] = storage_payload(
        _seeded(timer_start="22:00", last_written="23:00-07:00")
    )
    charger.timer = ("23:00", "07:00")
    entry = await async_setup_entry(hass, charger, make_entry(hass))
    assert charger.take_calls() == [("set_timer", "22:00", "07:00")]
    assert state(hass, "time", "timer_start").state == "22:00:00"
    await async_poll(hass, entry)
    assert charger.take_calls() == []


async def test_restart_after_weekend_restores_the_window(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """HA disabled the timer for the weekend and was down until Monday: device ==
    last_written, so this is not a panel change; the window is written back."""
    freezer.move_to(local_dt(2026, 11, 2, 12, 0))  # Monday
    hass_storage[STORE_KEY] = storage_payload(_seeded(weekend_free=True, last_written=""))
    charger.timer = None
    await async_setup_entry(hass, charger, make_entry(hass))
    assert charger.take_calls() == [("set_timer", "23:00", "07:00")]
    assert state(hass, "switch", "night_timer").state == "on"


async def test_restart_during_a_boost_does_not_adopt_the_window(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """During a boost HA owns the timer: a window found at startup is switched off
    again (not adopted), and HA's own window is restored when the boost ends."""
    freezer.move_to(local_dt(*WED_18H))
    end = local_dt(*WED_18H) + timedelta(minutes=20)
    hass_storage[STORE_KEY] = storage_payload(
        _seeded(last_written="", boost={"end": end.isoformat(), "minutes": 30, "restore": True})
    )
    charger.status = "2"
    charger.timer = ("21:00", "05:00")
    await async_setup_entry(hass, charger, make_entry(hass))
    assert charger.take_calls() == [("disable_timer",)]
    assert state(hass, "time", "timer_start").state == "23:00:00"
    assert _boost(hass)[1]["active"] is True

    await _fire(hass, freezer, end + timedelta(seconds=1))
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]


async def test_restart_when_the_charger_is_unreachable(
    hass: HomeAssistant, charger: FakeCharger, hass_storage: dict[str, Any]
) -> None:
    """Setup retries while the charger cannot be reached; nothing is decided blindly."""
    hass_storage[STORE_KEY] = storage_payload(_seeded(timer_start="22:00"))
    charger.unreachable = True
    entry = make_entry(hass)
    with patch_api(charger):
        await hass.config_entries.async_setup(entry.entry_id)
        await async_settle(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert charger.calls == []
    assert stored(hass_storage)["timer_start"] == "22:00"


async def test_restart_without_difference_writes_nothing(
    hass: HomeAssistant, charger: FakeCharger, hass_storage: dict[str, Any]
) -> None:
    hass_storage[STORE_KEY] = storage_payload(_seeded(timer_start="22:00", last_written="22:00-07:00"))
    charger.timer = ("22:00", "07:00")
    await async_setup_entry(hass, charger, make_entry(hass))
    assert charger.calls == []
    assert state(hass, "time", "timer_start").state == "22:00:00"


async def test_unload_cancels_timers(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """After unload nothing fires: no boost expiry, no daily check, no polls."""
    freezer.move_to(local_dt(2026, 10, 30, 18, 0))  # Friday
    entry = await async_setup_entry(hass, charger)
    await _switch(hass, "weekend_free", True)
    await _call(hass, "charge_for", minutes=30)
    charger.take_calls()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await async_settle(hass)
    assert entry.state is ConfigEntryState.NOT_LOADED
    # The boost is saved so that the next start can finish it.
    assert stored(hass_storage)["boost"]["minutes"] == 30
    polls = charger.polls

    await _fire(hass, freezer, local_dt(2026, 10, 30, 18, 31))  # boost end
    await _fire(hass, freezer, local_dt(2026, 10, 31, 0, 0, 5))  # Saturday check
    await _fire(hass, freezer, local_dt(2026, 10, 31, 1, 0))
    assert charger.calls == []
    assert charger.polls == polls


async def test_reload_keeps_the_boost(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """Unload + setup (HA restart) in the middle of a boost: it still ends on time."""
    freezer.move_to(local_dt(*WED_18H))
    entry = await async_setup_entry(hass, charger)
    await _call(hass, "charge_for", minutes=60)
    charger.take_calls()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await async_settle(hass)
    freezer.tick(timedelta(minutes=10))
    await async_setup_entry(hass, charger, entry)
    assert charger.take_calls() == []  # still charging, timer still off
    assert _boost(hass)[1]["active"] is True

    await _fire(hass, freezer, local_dt(*WED_18H) + timedelta(minutes=60, seconds=1))
    assert charger.take_calls() == [("stop",), ("set_timer", "23:00", "07:00")]
