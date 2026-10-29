"""Entity setup, values and the shared contract (device + translation_key)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from freezegun.api import FrozenDateTimeFactory

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.electros_ev import frontend as electros_frontend, protocol
from custom_components.electros_ev.const import DOMAIN

from .common import (
    ENTITY_OBJECT_IDS,
    EXPECTED_ENTITIES,
    LOGIN,
    SECRETS,
    STORE_KEY,
    FakeCharger,
    async_poll,
    async_settle,
    async_setup_entry,
    device_id,
    entity_id,
    local_dt,
    state,
    stored,
)

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN


async def test_entities_follow_the_contract(hass: HomeAssistant, charger: FakeCharger) -> None:
    """One device; every entity found by device_id + translation_key; unique_ids kept."""
    await async_setup_entry(hass, charger)

    devices = dr.async_entries_for_config_entry(
        dr.async_get(hass), hass.config_entries.async_entries(DOMAIN)[0].entry_id
    )
    assert len(devices) == 1
    device = devices[0]
    assert device.identifiers == {(DOMAIN, LOGIN)}
    assert device.name == f"ElectroS {LOGIN}"
    assert device.manufacturer == "ElectroS"

    registry = er.async_get(hass)
    ours = [entry for entry in registry.entities.values() if entry.platform == DOMAIN]
    assert {(entry.domain, entry.translation_key) for entry in ours} == set(EXPECTED_ENTITIES)
    for (domain, key), suffix in EXPECTED_ENTITIES.items():
        entry = registry.async_get(entity_id(hass, domain, key))
        assert entry.unique_id == f"{LOGIN}_{suffix}"
        assert entry.device_id == device.id
        assert entry.has_entity_name is True


async def test_entity_names_come_from_translations(
    hass: HomeAssistant, charger: FakeCharger
) -> None:
    await async_setup_entry(hass, charger)
    english = json.loads((COMPONENT / "translations" / "en.json").read_text(encoding="utf-8"))
    for domain, key in EXPECTED_ENTITIES:
        name = english["entity"][domain][key]["name"]
        assert state(hass, domain, key).name == f"ElectroS {LOGIN} {name}"
    # Entity ids are generated from the English names.
    assert entity_id(hass, "sensor", "status") == "sensor.electros_es123456_status"
    assert entity_id(hass, "switch", "charging") == "switch.electros_es123456_charging"
    assert (
        entity_id(hass, "sensor", "session_energy") == "sensor.electros_es123456_session_energy"
    )


async def test_entity_ids_of_a_new_install(hass: HomeAssistant, charger: FakeCharger) -> None:
    """The ids README.md and examples/ document (``<domain>.electros_<login>_<name>``)."""
    await async_setup_entry(hass, charger)
    assert set(ENTITY_OBJECT_IDS) == set(EXPECTED_ENTITIES)
    for (domain, key), object_id in ENTITY_OBJECT_IDS.items():
        assert entity_id(hass, domain, key) == f"{domain}.electros_{LOGIN.lower()}_{object_id}"


async def test_entity_ids_stay_english_in_a_ukrainian_ui(
    hass: HomeAssistant, charger: FakeCharger
) -> None:
    """Names are translated, entity ids are not (uk and ru are not native-id languages)."""
    hass.config.language = "uk"
    await async_setup_entry(hass, charger)
    assert entity_id(hass, "sensor", "boost_end") == f"sensor.electros_{LOGIN.lower()}_boost_ends"
    assert entity_id(hass, "switch", "weekend_free") == (
        f"switch.electros_{LOGIN.lower()}_free_charging_on_weekends"
    )
    ukrainian = json.loads((COMPONENT / "translations" / "uk.json").read_text(encoding="utf-8"))
    name = ukrainian["entity"]["sensor"]["boost_end"]["name"]
    assert state(hass, "sensor", "boost_end").name == f"ElectroS {LOGIN} {name}"


async def test_sensor_values(hass: HomeAssistant, charger: FakeCharger) -> None:
    await async_setup_entry(hass, charger)

    status = state(hass, "sensor", "status")
    assert status.state == "vehicle_connected_idle"
    assert status.attributes["status_code"] == "1"
    assert status.attributes["device_class"] == "enum"
    assert status.attributes["options"] == protocol.STATUS_OPTIONS

    assert state(hass, "sensor", "configured_current").state == "16"
    assert state(hass, "sensor", "measured_current").state == "15.8"

    voltage = state(hass, "sensor", "voltage")
    assert voltage.state == "229"  # field 10 (current), not 16 (max)
    assert (voltage.attributes["min"], voltage.attributes["max"], voltage.attributes["avg"]) == (
        201,
        247,
        226,
    )
    assert voltage.attributes["unit_of_measurement"] == "V"

    power = state(hass, "sensor", "power")
    assert power.state == "3634.0"
    assert power.attributes["unit_of_measurement"] == "W"

    energy = state(hass, "sensor", "energy")
    assert energy.state == "25.8"
    assert energy.attributes["unit_of_measurement"] == "kWh"
    assert energy.attributes["state_class"] == "total_increasing"
    assert energy.attributes["device_class"] == "energy"

    month = state(hass, "sensor", "month_energy")
    assert month.state == "120.5"  # October (current month 10)
    assert month.attributes["current_month"] == 10
    assert month.attributes["months"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 120.5, 0.0, 0.0]

    session = state(hass, "sensor", "session_energy")
    # HA has not seen a session yet: the newest entry of the charger history.
    assert session.state == "12.346"
    assert session.attributes["started_at"] is None
    assert session.attributes["history"] == [
        {"date": "27.10", "time": "01:10:00", "kwh": 12.346},
        {"date": "25.10", "time": "22:29:57", "kwh": 25.8},
        {"date": "30.12", "time": "10:00:00", "kwh": 5.0},
    ]

    boost_end = state(hass, "sensor", "boost_end")
    assert boost_end.state == STATE_UNKNOWN
    assert boost_end.attributes["device_class"] == "timestamp"
    assert boost_end.attributes["active"] is False
    assert boost_end.attributes["unlimited"] is False
    assert boost_end.attributes["minutes"] == 0
    for key in ("mode", "target_kwh", "delivered_kwh", "deadline", "last_error"):
        assert boost_end.attributes[key] is None, key
    assert session.attributes["cost"] is None  # no session seen by HA yet


async def test_control_entities_initial_state(hass: HomeAssistant, charger: FakeCharger) -> None:
    """First install: night timer taken over from the charger, nothing written."""
    await async_setup_entry(hass, charger)
    assert state(hass, "switch", "charging").state == "off"
    night = state(hass, "switch", "night_timer")
    assert night.state == "on"
    assert night.attributes["device_window"] == "23:00-07:00"
    assert state(hass, "switch", "weekend_free").state == "off"
    amps = state(hass, "number", "charging_amps")
    assert amps.state == "16.0"
    assert (amps.attributes["min"], amps.attributes["max"], amps.attributes["step"]) == (6, 32, 2)
    minutes = state(hass, "number", "boost_minutes")
    assert minutes.state == "30.0"
    assert (minutes.attributes["min"], minutes.attributes["max"], minutes.attributes["step"]) == (
        5,
        480,
        5,
    )
    assert state(hass, "time", "timer_start").state == "23:00:00"
    assert state(hass, "time", "timer_end").state == "07:00:00"
    assert state(hass, "number", "boost_kwh").state == "10.0"
    assert state(hass, "number", "price_day").state == "4.32"
    assert state(hass, "number", "price_night").state == "2.16"
    assert state(hass, "button", "boost").state == STATE_UNKNOWN
    assert state(hass, "button", "boost_energy").state == STATE_UNKNOWN
    assert state(hass, "button", "reset_energy").state == STATE_UNKNOWN
    assert charger.calls == []


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("0", "waiting_for_vehicle"),
        ("1", "vehicle_connected_idle"),
        ("2", "charging"),
        ("3", "charging_fault"),
        ("4", "charging_blocked"),
        ("5", "mains_voltage_low"),
        ("6", "charger_communication_fault"),
        ("7", "offline"),
        ("16", "leakage_current_fault"),
        ("32", "overcurrent_fault"),
        ("77", "offline"),
        ("99", "unknown"),
    ],
)
async def test_status_codes(
    hass: HomeAssistant, charger: FakeCharger, code: str, expected: str
) -> None:
    entry = await async_setup_entry(hass, charger)
    charger.status = code
    await async_poll(hass, entry)
    status = state(hass, "sensor", "status")
    assert status.state == expected
    assert status.attributes["status_code"] == code
    assert state(hass, "switch", "charging").state == ("on" if code == "2" else "off")
    assert charger.calls == []


async def test_secrets_never_reach_states_or_logs(
    hass: HomeAssistant, charger: FakeCharger, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.electros_ev")
    entry = await async_setup_entry(hass, charger)
    charger.status = "2"
    await async_poll(hass, entry)
    dumped = repr([(item.state, dict(item.attributes)) for item in hass.states.async_all()])
    dumped += repr(entry.runtime_data.data)
    for secret in SECRETS.values():
        assert secret not in dumped
        assert secret not in caplog.text


async def test_unreachable_charger(hass: HomeAssistant, charger: FakeCharger) -> None:
    """Live values go unavailable; HA-side settings stay usable."""
    entry = await async_setup_entry(hass, charger)
    charger.unreachable = True
    await async_poll(hass, entry)

    for domain, key in [
        ("sensor", "status"),
        ("sensor", "voltage"),
        ("sensor", "energy"),
        ("switch", "charging"),
        ("button", "boost"),
        ("button", "boost_energy"),
    ]:
        assert state(hass, domain, key).state == STATE_UNAVAILABLE, key
    for domain, key in [
        ("switch", "night_timer"),
        ("switch", "weekend_free"),
        ("number", "charging_amps"),
        ("number", "boost_minutes"),
        ("time", "timer_start"),
        ("time", "timer_end"),
        ("sensor", "boost_end"),
        ("number", "boost_kwh"),
        ("number", "price_day"),
        ("number", "price_night"),
        ("sensor", "energy_day_tariff"),
        ("sensor", "energy_night_tariff"),
        ("sensor", "energy_cost"),
        ("sensor", "current_price"),
    ]:
        assert state(hass, domain, key).state != STATE_UNAVAILABLE, key

    charger.unreachable = False
    await async_poll(hass, entry)
    assert state(hass, "sensor", "status").state == "vehicle_connected_idle"


async def test_charging_amps(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """Idle: only stored. Charging: sent to the charger at once."""
    freezer.move_to(local_dt(2026, 10, 28, 23, 30))  # inside the night window
    entry = await async_setup_entry(hass, charger)
    amps = entity_id(hass, "number", "charging_amps")

    await hass.services.async_call(
        "number", "set_value", {"entity_id": amps, "value": 10}, blocking=True
    )
    await async_settle(hass)
    assert charger.calls == []
    assert state(hass, "number", "charging_amps").state == "10.0"
    assert stored(hass_storage)["charging_amps"] == 10

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": entity_id(hass, "switch", "charging")}, blocking=True
    )
    await async_settle(hass)
    assert charger.take_calls() == [("start", 10)]  # the window allows it: no bypass
    assert state(hass, "switch", "charging").state == "on"

    await hass.services.async_call(
        "number", "set_value", {"entity_id": amps, "value": 20}, blocking=True
    )
    await async_settle(hass)
    assert charger.take_calls() == [("start", 20)]
    assert entry.runtime_data.target_amps == 20

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": amps, "value": 40}, blocking=True
        )


async def test_boost_minutes_persisted(
    hass: HomeAssistant, charger: FakeCharger, hass_storage: dict[str, Any]
) -> None:
    await async_setup_entry(hass, charger)
    minutes = entity_id(hass, "number", "boost_minutes")
    await hass.services.async_call(
        "number", "set_value", {"entity_id": minutes, "value": 45}, blocking=True
    )
    assert state(hass, "number", "boost_minutes").state == "45.0"
    assert stored(hass_storage)["boost_minutes"] == 45
    assert charger.calls == []
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "number", "set_value", {"entity_id": minutes, "value": 500}, blocking=True
        )


async def test_night_window_entities(hass: HomeAssistant, charger: FakeCharger) -> None:
    """time.* and switch.night_timer write the charger timer only when needed."""
    await async_setup_entry(hass, charger)
    start = entity_id(hass, "time", "timer_start")
    end = entity_id(hass, "time", "timer_end")
    night = entity_id(hass, "switch", "night_timer")

    await hass.services.async_call(
        "time", "set_value", {"entity_id": start, "time": "22:00:00"}, blocking=True
    )
    assert charger.take_calls() == [("set_timer", "22:00", "07:00")]
    assert state(hass, "time", "timer_start").state == "22:00:00"

    await hass.services.async_call("switch", "turn_off", {"entity_id": night}, blocking=True)
    assert charger.take_calls() == [("disable_timer",)]
    assert state(hass, "switch", "night_timer").state == "off"

    # Night timer off: changing the time is only stored.
    await hass.services.async_call(
        "time", "set_value", {"entity_id": end, "time": "06:00:00"}, blocking=True
    )
    assert charger.take_calls() == []
    assert state(hass, "time", "timer_end").state == "06:00:00"

    await hass.services.async_call("switch", "turn_on", {"entity_id": night}, blocking=True)
    assert charger.take_calls() == [("set_timer", "22:00", "06:00")]
    assert charger.timer == ("22:00", "06:00")
    # The clock is set in local time with every timer write.
    assert all(moment.tzinfo is not None for moment in charger.clock_writes)
    assert str(charger.clock_writes[-1].tzinfo) == "Europe/Kyiv"


async def test_reset_energy_button(hass: HomeAssistant, charger: FakeCharger) -> None:
    await async_setup_entry(hass, charger)
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id(hass, "button", "reset_energy")}, blocking=True
    )
    await async_settle(hass)
    assert charger.calls == [("resetkwt",)]
    assert state(hass, "sensor", "energy").state == "0.0"


async def test_session_energy(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """Counter minus the value when charging started; kept after the session ends."""
    freezer.move_to(local_dt(2026, 10, 28, 23, 30))
    entry = await async_setup_entry(hass, charger)

    charger.status = "2"
    await async_poll(hass, entry)
    session = state(hass, "sensor", "session_energy")
    assert session.state == "0.0"
    assert session.attributes["started_at"] == local_dt(2026, 10, 28, 23, 30).isoformat()

    charger.energy_raw = 27_000_000
    await async_poll(hass, entry)
    assert state(hass, "sensor", "session_energy").state == "1.2"

    charger.status = "1"
    charger.energy_raw = 27_500_000
    await async_poll(hass, entry)
    assert state(hass, "sensor", "session_energy").state == "1.7"

    charger.energy_raw = 27_600_000  # not charging: no longer counted
    await async_poll(hass, entry)
    assert state(hass, "sensor", "session_energy").state == "1.7"

    freezer.tick(3600)
    charger.status = "2"
    await async_poll(hass, entry)
    assert state(hass, "sensor", "session_energy").state == "0.0"  # a new session


async def test_unload_and_remove(
    hass: HomeAssistant, charger: FakeCharger, hass_storage: dict[str, Any]
) -> None:
    entry = await async_setup_entry(hass, charger)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert state(hass, "sensor", "status").state == STATE_UNAVAILABLE
    assert stored(hass_storage)["seeded"] is True

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert STORE_KEY not in hass_storage
    assert hass.services.has_service(DOMAIN, "charge_for")  # services live for the HA run


async def test_card_is_served_and_registered(hass: HomeAssistant) -> None:
    """/electros_ev/electros-card.js is served without cache and loaded on dashboards."""
    from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL

    hass.http = MagicMock()
    hass.http.async_register_static_paths = AsyncMock()
    hass.data[DATA_EXTRA_MODULE_URL] = set()

    await electros_frontend.async_register_card(hass)
    await electros_frontend.async_register_card(hass)  # once per HA run

    hass.http.async_register_static_paths.assert_awaited_once()
    (config,) = hass.http.async_register_static_paths.call_args.args[0]
    assert config.url_path == "/electros_ev/electros-card.js"
    assert config.cache_headers is False
    card = Path(config.path)
    assert card.resolve() == (COMPONENT / "frontend" / "electros-card.js").resolve()
    assert card.is_file()
    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert hass.data[DATA_EXTRA_MODULE_URL] == {
        f"/electros_ev/electros-card.js?v={manifest['version']}"
    }


async def test_card_registration_without_http(hass: HomeAssistant) -> None:
    """No web server (e.g. a minimal setup): nothing to register, no error."""
    hass.http = None
    await electros_frontend.async_register_card(hass)


async def test_device_id_lookup_matches_card(hass: HomeAssistant, charger: FakeCharger) -> None:
    """The card resolves entities from hass.entities; the registry has what it needs."""
    await async_setup_entry(hass, charger)
    dev = device_id(hass)
    registry = er.async_get(hass)
    by_key = {
        f"{entry.domain}.{entry.translation_key}": entry.entity_id
        for entry in registry.entities.values()
        if entry.device_id == dev
    }
    assert set(by_key) == {f"{domain}.{key}" for domain, key in EXPECTED_ENTITIES}


async def test_session_counts_the_poll_that_starts_it(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    """Energy only flows while charging: the delta of the first charging poll belongs
    to the new session, as it does to the tariff totals."""
    freezer.move_to(local_dt(2026, 10, 28, 23, 30))
    entry = await async_setup_entry(hass, charger)

    charger.status = "2"
    charger.energy_raw = 25_920_000
    await async_poll(hass, entry)
    assert state(hass, "sensor", "session_energy").state == "0.12"
