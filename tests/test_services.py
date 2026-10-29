"""Service schemas and device resolution (charge_for, charge_energy, extend_charge,
stop_charging, set_timer).

Time is frozen in Europe/Kyiv; the fake charger has its timer at 23:00-07:00.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory
import voluptuous as vol
import yaml

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electros_ev.const import DOMAIN

from .common import (
    FakeCharger,
    async_call,
    async_fire_at,
    async_setup_entry,
    boost_state,
    device_id,
    local_dt,
    state,
    stored,
)

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
WED_18H = (2026, 10, 28, 18, 0)  # outside the 23:00-07:00 window


async def test_registered_services_match_services_yaml(
    hass: HomeAssistant, charger: FakeCharger
) -> None:
    await async_setup_entry(hass, charger)
    described = yaml.safe_load((COMPONENT / "services.yaml").read_text(encoding="utf-8"))
    assert set(hass.services.async_services_for_domain(DOMAIN)) == set(described) == {
        "charge_for",
        "charge_energy",
        "extend_charge",
        "stop_charging",
        "set_timer",
    }


# --------------------------------------------------------------------------- #
# Schemas: nothing reaches the charger for bad input
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("service", "data"),
    [
        ("charge_for", {}),
        ("charge_for", {"minutes": 0}),
        ("charge_for", {"minutes": 1441}),
        ("charge_for", {"minutes": "soon"}),
        ("charge_for", {"minutes": 30, "amps": 4}),
        ("charge_for", {"minutes": 30, "amps": 34}),
        ("charge_for", {"minutes": 30, "unknown": 1}),
        ("charge_energy", {"kwh": 0.4}),
        ("charge_energy", {"kwh": 100.5}),
        ("charge_energy", {"kwh": "inf"}),
        ("charge_energy", {"kwh": 5, "amps": 5}),
        ("charge_energy", {"kwh": 5, "minutes": 30}),
        ("extend_charge", {"minutes": 0}),
        ("extend_charge", {"minutes": 1441}),
        ("extend_charge", {"kwh": 0.4}),
        ("set_timer", {}),
        ("set_timer", {"start": "25:00"}),
        ("set_timer", {"end": "7"}),
        ("set_timer", {"enabled": "maybe"}),
        ("stop_charging", {"minutes": 5}),
    ],
)
async def test_invalid_service_data(
    hass: HomeAssistant, charger: FakeCharger, service: str, data: dict[str, Any]
) -> None:
    await async_setup_entry(hass, charger)
    with pytest.raises(ServiceValidationError) as err:
        await async_call(hass, service, **data)
    # Both a validation error for the UI and a voluptuous error for scripts.
    assert isinstance(err.value, vol.Invalid)
    assert err.value.translation_key == "invalid_input"
    assert charger.calls == []


@pytest.mark.parametrize(
    ("service", "data", "commands"),
    [
        ("charge_for", {"minutes": 1}, [("disable_timer",), ("start", 16)]),
        ("charge_for", {"minutes": 1440, "amps": 32}, [("disable_timer",), ("start", 32)]),
        ("charge_for", {"minutes": "45", "amps": "6"}, [("disable_timer",), ("start", 6)]),
        ("charge_energy", {"kwh": 0.5}, [("disable_timer",), ("start", 16)]),
        ("charge_energy", {"kwh": 100, "amps": 32}, [("disable_timer",), ("start", 32)]),
        ("charge_energy", {"kwh": "7.5"}, [("disable_timer",), ("start", 16)]),
    ],
)
async def test_boundary_values_are_accepted(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    service: str,
    data: dict[str, Any],
    commands: list[tuple[Any, ...]],
) -> None:
    freezer.move_to(local_dt(*WED_18H))
    await async_setup_entry(hass, charger)
    await async_call(hass, service, **data)
    assert charger.take_calls() == commands
    assert boost_state(hass)[1]["active"] is True


async def test_odd_current_is_rounded_down_and_stored(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """The charger takes 6..32 A in steps of 2; 7 A becomes 6 A for this and later starts."""
    freezer.move_to(local_dt(*WED_18H))
    await async_setup_entry(hass, charger)
    await async_call(hass, "charge_energy", kwh=3, amps=7)
    assert charger.take_calls() == [("disable_timer",), ("start", 6)]
    assert state(hass, "number", "charging_amps").state == "6.0"
    assert stored(hass_storage)["charging_amps"] == 6
    assert stored(hass_storage)["boost"]["amps"] == 6

    await async_call(hass, "charge_for", minutes=10)  # without amps: the stored current
    assert charger.take_calls() == [("start", 6)]


# --------------------------------------------------------------------------- #
# set_timer and stop_charging
# --------------------------------------------------------------------------- #


async def test_set_timer_combinations(hass: HomeAssistant, charger: FakeCharger) -> None:
    await async_setup_entry(hass, charger)

    # HH:MM:SS from the time selector is accepted.
    await async_call(hass, "set_timer", start="22:30:00", end="06:15")
    assert charger.take_calls() == [("set_timer", "22:30", "06:15")]
    assert state(hass, "time", "timer_start").state == "22:30:00"
    assert state(hass, "time", "timer_end").state == "06:15:00"

    await async_call(hass, "set_timer", enabled=False)
    assert charger.take_calls() == [("disable_timer",)]
    assert state(hass, "switch", "night_timer").state == "off"

    # Disabled: new times are only stored.
    await async_call(hass, "set_timer", start="23:00")
    assert charger.take_calls() == []
    assert state(hass, "time", "timer_start").state == "23:00:00"

    # Enable and change the end in one call: one write.
    await async_call(hass, "set_timer", enabled=True, end="07:00")
    assert charger.take_calls() == [("set_timer", "23:00", "07:00")]
    assert state(hass, "switch", "night_timer").state == "on"

    # Nothing changes: nothing is written.
    await async_call(hass, "set_timer", start="23:00", end="07:00", enabled=True)
    assert charger.take_calls() == []


async def test_set_timer_during_a_boost_is_applied_at_the_end(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """While a boost owns the timer a new window is only stored, then restored."""
    start = local_dt(*WED_18H)
    freezer.move_to(start)
    await async_setup_entry(hass, charger)
    await async_call(hass, "charge_for", minutes=30)
    charger.take_calls()

    await async_call(hass, "set_timer", start="22:00", end="06:00")
    assert charger.take_calls() == []  # the timer stays off during the boost
    await async_fire_at(hass, freezer, start.replace(minute=30, second=1))
    assert charger.take_calls() == [("stop",), ("set_timer", "22:00", "06:00")]


async def test_stop_charging_without_a_boost(hass: HomeAssistant, charger: FakeCharger) -> None:
    await async_setup_entry(hass, charger)
    charger.status = "2"
    await async_call(hass, "stop_charging")
    assert charger.take_calls() == [("stop",)]  # the timer is already right
    assert state(hass, "switch", "charging").state == "off"


async def test_failed_stop_raises(hass: HomeAssistant, charger: FakeCharger) -> None:
    await async_setup_entry(hass, charger)
    charger.fail.add("stop")
    with pytest.raises(HomeAssistantError) as err:
        await async_call(hass, "stop_charging")
    assert err.value.translation_key == "command_failed"
    assert err.value.translation_placeholders == {"command": "stop"}


# --------------------------------------------------------------------------- #
# device_id resolution
# --------------------------------------------------------------------------- #


async def _call_for(hass: HomeAssistant, device: str) -> None:
    await hass.services.async_call(
        DOMAIN, "charge_for", {"device_id": device, "minutes": 10}, blocking=True
    )


async def test_unknown_device(hass: HomeAssistant, charger: FakeCharger) -> None:
    await async_setup_entry(hass, charger)
    with pytest.raises(ServiceValidationError) as err:
        await _call_for(hass, "not-a-device")
    assert err.value.translation_key == "invalid_device"
    assert charger.calls == []


async def test_device_of_another_integration(hass: HomeAssistant, charger: FakeCharger) -> None:
    await async_setup_entry(hass, charger)
    other = MockConfigEntry(domain="other_integration")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("other_integration", "x")}
    )
    with pytest.raises(ServiceValidationError) as err:
        await _call_for(hass, device.id)
    assert err.value.translation_key == "invalid_device"
    assert charger.calls == []


async def test_device_of_an_unloaded_entry(hass: HomeAssistant, charger: FakeCharger) -> None:
    entry = await async_setup_entry(hass, charger)
    dev = device_id(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as err:
        await _call_for(hass, dev)
    assert err.value.translation_key == "entry_not_loaded"
    assert charger.calls == []
