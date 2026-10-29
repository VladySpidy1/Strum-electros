"""Two-zone tariff: energy split, cost, current price, prices, options, Store migration.

Time is frozen in Europe/Kyiv; the tariff night zone defaults to 23:00-07:00 and
the prices to 4.32 (day) / 2.16 (night) per kWh. The test currency is EUR.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .common import (
    STORE_KEY,
    FakeCharger,
    async_fire_at,
    async_poll,
    async_set_number,
    async_settle,
    async_setup_entry,
    entity_id,
    local_dt,
    make_entry,
    patch_api,
    state,
    storage_payload,
    stored,
)

FLOW_API = "custom_components.electros_ev.config_flow.ElectroSApi"


def _kwh(charger: FakeCharger, kwh: float) -> None:
    charger.energy_raw = round(kwh * 1_000_000)


def _value(hass: HomeAssistant, domain: str, key: str) -> float:
    return float(state(hass, domain, key).state)


def _totals(hass: HomeAssistant) -> tuple[float, float, float]:
    return (
        _value(hass, "sensor", "energy_day_tariff"),
        _value(hass, "sensor", "energy_night_tariff"),
        _value(hass, "sensor", "energy_cost"),
    )


async def _poll_at(hass: HomeAssistant, entry, freezer: FrozenDateTimeFactory, moment) -> None:
    freezer.move_to(moment)
    await async_poll(hass, entry)


async def test_tariff_entities(hass: HomeAssistant, charger: FakeCharger) -> None:
    """Units, classes and initial values (usable in the HA Energy dashboard)."""
    await async_setup_entry(hass, charger)
    for key in ("energy_day_tariff", "energy_night_tariff"):
        current = state(hass, "sensor", key)
        assert current.state == "0.0"
        assert current.attributes["unit_of_measurement"] == "kWh"
        assert current.attributes["device_class"] == "energy"
        assert current.attributes["state_class"] == "total_increasing"
    cost = state(hass, "sensor", "energy_cost")
    assert cost.state == "0.0"
    assert cost.attributes["unit_of_measurement"] == "EUR"
    assert cost.attributes["device_class"] == "monetary"
    assert cost.attributes["state_class"] == "total"
    price = state(hass, "sensor", "current_price")
    assert price.attributes["unit_of_measurement"] == "EUR/kWh"
    assert price.attributes["night_start"] == "23:00"
    assert price.attributes["night_end"] == "07:00"

    registry = er.async_get(hass)
    for zone, default in (("day", "4.32"), ("night", "2.16")):
        number = state(hass, "number", f"price_{zone}")
        assert number.state == default
        assert number.attributes["unit_of_measurement"] == "EUR/kWh"
        assert (number.attributes["min"], number.attributes["max"], number.attributes["step"]) == (
            0,
            100,
            0.01,
        )
        assert registry.async_get(entity_id(hass, "number", f"price_{zone}")).entity_category == "config"
    assert state(hass, "sensor", "session_energy").attributes["cost"] is None


async def test_energy_split_and_cost(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    freezer.move_to(local_dt(2026, 10, 29, 6, 0))  # Thursday, night zone
    entry = await async_setup_entry(hass, charger)  # first sample: baseline only
    price = state(hass, "sensor", "current_price")
    assert (price.state, price.attributes["zone"]) == ("2.16", "night")
    assert _totals(hass) == (0.0, 0.0, 0.0)

    # Two hours without a sample (HA down): split 1 h night (06-07), 1 h day (07-08).
    _kwh(charger, 27.8)
    await _poll_at(hass, entry, freezer, local_dt(2026, 10, 29, 8, 0))
    assert _totals(hass) == (1.0, 1.0, pytest.approx(6.48))
    price = state(hass, "sensor", "current_price")
    assert (price.state, price.attributes["zone"]) == ("4.32", "day")

    # A regular poll: the zone of the poll time.
    _kwh(charger, 28.3)
    await _poll_at(hass, entry, freezer, local_dt(2026, 10, 29, 8, 1))
    assert _totals(hass) == (1.5, 1.0, pytest.approx(8.64))

    # Counter reset ("Reset kWh" in the web panel): the new value counts from 0.
    _kwh(charger, 0.2)
    await _poll_at(hass, entry, freezer, local_dt(2026, 10, 29, 8, 2))
    assert _totals(hass) == (1.7, 1.0, pytest.approx(9.5))

    # A new price applies to energy from now on.
    await async_set_number(hass, "price_day", 5)
    assert state(hass, "sensor", "current_price").state == "5.0"
    _kwh(charger, 0.3)
    await _poll_at(hass, entry, freezer, local_dt(2026, 10, 29, 8, 3))
    assert _totals(hass) == (1.8, 1.0, pytest.approx(10.0))

    # A short gap across 23:00: all of it is night at the poll time.
    await _poll_at(hass, entry, freezer, local_dt(2026, 10, 29, 22, 59))
    _kwh(charger, 1.3)
    await _poll_at(hass, entry, freezer, local_dt(2026, 10, 29, 23, 1))
    assert _totals(hass) == (1.8, 2.0, pytest.approx(12.16))

    # Offline or unreachable: nothing counted.
    charger.status = "77"
    _kwh(charger, 9.0)
    await _poll_at(hass, entry, freezer, local_dt(2026, 10, 29, 23, 2))
    assert _totals(hass) == (1.8, 2.0, pytest.approx(12.16))

    assert await hass.config_entries.async_unload(entry.entry_id)
    await async_settle(hass)
    totals = stored(hass_storage)["tariff"]
    assert totals["day_kwh"] == pytest.approx(1.8)
    assert totals["night_kwh"] == pytest.approx(2.0)
    assert totals["cost"] == pytest.approx(12.164)
    assert totals["last_energy"] == 1.3
    assert stored(hass_storage)["price_day"] == 5.0


async def test_totals_survive_a_restart_and_count_the_gap(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """The last sample is persisted; energy charged while HA was down is split."""
    freezer.move_to(local_dt(2026, 10, 28, 22, 0))
    entry = await async_setup_entry(hass, charger)
    _kwh(charger, 26.8)
    await _poll_at(hass, entry, freezer, local_dt(2026, 10, 28, 22, 1))
    assert _totals(hass) == (1.0, 0.0, pytest.approx(4.32))

    assert await hass.config_entries.async_unload(entry.entry_id)
    await async_settle(hass)
    # HA is down 22:01 -> 00:01 (1 h day, 1 h night) while 2 kWh are charged.
    freezer.move_to(local_dt(2026, 10, 29, 0, 1))
    _kwh(charger, 28.8)
    await async_setup_entry(hass, charger, entry)
    assert _totals(hass) == (
        pytest.approx(1.983, abs=1e-3),
        pytest.approx(1.017, abs=1e-3),
        pytest.approx(4.32 + 0.983 * 4.32 + 1.017 * 2.16, abs=1e-2),
    )


@pytest.mark.parametrize(
    ("down_from", "up_at", "kwh", "day", "night"),
    [
        # Autumn: 04:00 EEST -> 03:00 EET; 22:00 -> 08:00 is 11 real hours,
        # 9 of them in the 23:00-07:00 night zone (03:00-04:00 happens twice).
        ((2026, 10, 24, 22, 0), (2026, 10, 25, 8, 0), 11.0, 2.0, 9.0),
        # Spring: 03:00 EET -> 04:00 EEST; 22:00 -> 08:00 is 9 real hours, 7 at night.
        ((2026, 3, 28, 22, 0), (2026, 3, 29, 8, 0), 9.0, 2.0, 7.0),
    ],
)
async def test_gap_across_a_dst_change_is_split_by_real_time(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    down_from: tuple[int, ...],
    up_at: tuple[int, ...],
    kwh: float,
    day: float,
    night: float,
) -> None:
    """HA was down over the night of a DST change (Europe/Kyiv) while the car charged."""
    freezer.move_to(local_dt(*down_from))
    entry = await async_setup_entry(hass, charger)  # baseline sample
    assert await hass.config_entries.async_unload(entry.entry_id)
    await async_settle(hass)

    freezer.move_to(local_dt(*up_at))
    _kwh(charger, 25.8 + kwh)
    await async_setup_entry(hass, charger, entry)
    assert _totals(hass) == (
        pytest.approx(day, abs=1e-3),
        pytest.approx(night, abs=1e-3),
        pytest.approx(day * 4.32 + night * 2.16, abs=1e-2),
    )


async def test_session_cost(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local_dt(2026, 10, 28, 22, 58))
    entry = await async_setup_entry(hass, charger)
    charger.status = "2"
    await _poll_at(hass, entry, freezer, local_dt(2026, 10, 28, 22, 59))
    session = state(hass, "sensor", "session_energy")
    assert (session.state, session.attributes["cost"]) == ("0.0", 0.0)

    _kwh(charger, 26.8)  # day
    await _poll_at(hass, entry, freezer, local_dt(2026, 10, 28, 22, 59, 50))
    _kwh(charger, 27.8)  # night
    await _poll_at(hass, entry, freezer, local_dt(2026, 10, 28, 23, 1))
    session = state(hass, "sensor", "session_energy")
    assert session.state == "2.0"
    assert session.attributes["cost"] == pytest.approx(6.48)

    charger.status = "1"  # the session ends; its cost stays
    await _poll_at(hass, entry, freezer, local_dt(2026, 10, 28, 23, 2))
    assert state(hass, "sensor", "session_energy").attributes["cost"] == pytest.approx(6.48)
    _kwh(charger, 28.0)  # not charging: counted in the totals, not in the session
    await _poll_at(hass, entry, freezer, local_dt(2026, 10, 28, 23, 3))
    assert state(hass, "sensor", "session_energy").attributes["cost"] == pytest.approx(6.48)
    assert _totals(hass)[1] == pytest.approx(1.2)


async def test_current_price_changes_at_the_boundary_without_polls(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local_dt(2026, 10, 29, 6, 59, 30))
    entry = await async_setup_entry(hass, charger)
    assert state(hass, "sensor", "current_price").attributes["zone"] == "night"
    charger.unreachable = True
    await async_poll(hass, entry)
    assert state(hass, "sensor", "status").state == STATE_UNAVAILABLE

    await async_fire_at(hass, freezer, local_dt(2026, 10, 29, 7, 0, 0))
    price = state(hass, "sensor", "current_price")
    assert (price.state, price.attributes["zone"]) == ("4.32", "day")
    # HA-side totals and prices stay available while the charger is unreachable.
    for domain, key in (
        ("sensor", "energy_day_tariff"),
        ("sensor", "energy_night_tariff"),
        ("sensor", "energy_cost"),
        ("number", "price_day"),
        ("number", "boost_kwh"),
    ):
        assert state(hass, domain, key).state != STATE_UNAVAILABLE, key
    assert state(hass, "button", "boost_energy").state == STATE_UNAVAILABLE


async def test_price_numbers(
    hass: HomeAssistant, charger: FakeCharger, hass_storage: dict[str, Any]
) -> None:
    await async_setup_entry(hass, charger)
    await async_set_number(hass, "price_night", 1.23)
    assert state(hass, "number", "price_night").state == "1.23"
    assert stored(hass_storage)["price_night"] == 1.23
    with pytest.raises(ServiceValidationError):
        await async_set_number(hass, "price_day", 150)
    assert charger.calls == []


# --------------------------------------------------------------------------- #
# Options flow: tariff night zone
# --------------------------------------------------------------------------- #


async def test_options_tariff_zone(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(local_dt(2026, 10, 28, 22, 30))
    entry = await async_setup_entry(hass, charger)
    assert state(hass, "sensor", "current_price").attributes["zone"] == "day"
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = {str(key): key for key in result["data_schema"].schema}
    assert schema["tariff_night_start"].default() == "23:00"
    assert schema["tariff_night_end"].default() == "07:00"

    with patch(FLOW_API, return_value=charger) as flow_api, patch_api(charger):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"tariff_night_start": "22:00:00", "tariff_night_end": "06:00:00"},
        )
        await hass.async_block_till_done(wait_background_tasks=True)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["tariff_night_start"] == "22:00"
    assert entry.options["tariff_night_end"] == "06:00"
    flow_api.assert_not_called()  # the host did not change
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.tariff_window == ("22:00", "06:00")
    price = state(hass, "sensor", "current_price")
    assert (price.attributes["zone"], price.attributes["night_start"]) == ("night", "22:00")
    assert charger.calls == []  # the charger timer is a separate setting

    # The form now shows the saved zone.
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = {str(key): key for key in result["data_schema"].schema}
    assert schema["tariff_night_start"].default() == "22:00"


async def test_options_tariff_zone_must_not_be_empty(
    hass: HomeAssistant, charger: FakeCharger
) -> None:
    entry = await async_setup_entry(hass, charger)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"tariff_night_start": "07:00", "tariff_night_end": "07:00:00"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"tariff_night_end": "tariff_zero_length"}
    assert entry.options == {}


# --------------------------------------------------------------------------- #
# Store migration (schema 1 -> 2)
# --------------------------------------------------------------------------- #


async def test_store_migration_keeps_settings(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_storage: dict[str, Any],
) -> None:
    """A v2.0.0 Store (schema 1) loads with every setting and gets the new defaults."""
    now = local_dt(2026, 10, 28, 18, 0)
    freezer.move_to(now)
    boost_end = local_dt(2026, 10, 28, 18, 20)
    session_start = local_dt(2026, 10, 27, 23, 0)
    hass_storage[STORE_KEY] = storage_payload(
        {
            "night_enabled": True,
            "timer_start": "22:00",
            "timer_end": "06:00",
            "weekend_free": True,
            "boost_minutes": 45,
            "charging_amps": 10,
            "boost": {
                "end": boost_end.isoformat(),
                "unlimited": False,
                "minutes": 45,
                "restore": True,
                "started_at": local_dt(2026, 10, 28, 17, 35).isoformat(),
                "charging_seen": False,
            },
            "last_written": "",
            "seeded": True,
            "session": {
                "active": True,
                "started_at": session_start.isoformat(),
                "kwh": 7.5,
                "last_energy": 25.8,
            },
        },
        version=1,
    )
    charger.status = "2"
    charger.timer = None
    entry = await async_setup_entry(hass, charger, make_entry(hass))
    assert charger.take_calls() == []

    assert hass_storage[STORE_KEY]["version"] == 2
    data = stored(hass_storage)
    assert data["night_enabled"] is True
    assert (data["timer_start"], data["timer_end"]) == ("22:00", "06:00")
    assert data["weekend_free"] is True
    assert data["boost_minutes"] == 45
    assert data["charging_amps"] == 10
    assert data["last_written"] == ""
    assert data["seeded"] is True
    assert data["boost"]["end"] == boost_end.isoformat()
    assert data["boost"]["target_kwh"] is None
    assert data["session"]["kwh"] == 7.5
    assert data["session"]["cost"] == 0.0
    assert "last_energy" not in data["session"]
    assert data["boost_kwh"] == 10
    assert (data["price_day"], data["price_night"]) == (4.32, 2.16)
    assert data["tariff"]["day_kwh"] == 0.0

    assert state(hass, "time", "timer_start").state == "22:00:00"
    assert state(hass, "switch", "weekend_free").state == "on"
    assert state(hass, "number", "boost_minutes").state == "45.0"
    assert state(hass, "number", "charging_amps").state == "10.0"
    # The running session continues (the first sample after the upgrade is a baseline).
    assert state(hass, "sensor", "session_energy").state == "7.5"
    assert state(hass, "sensor", "session_energy").attributes["started_at"] == (
        session_start.isoformat()
    )
    assert entry.runtime_data.boost.mode == "time"

    # The migrated boost still ends on time (outside 22:00-06:00: stop).
    await async_fire_at(hass, freezer, boost_end)
    assert charger.take_calls() == [("stop",), ("set_timer", "22:00", "06:00")]


async def test_store_of_a_newer_version_still_loads(
    hass: HomeAssistant, charger: FakeCharger, hass_storage: dict[str, Any]
) -> None:
    """After a downgrade the data is normalized instead of failing the setup."""
    hass_storage[STORE_KEY] = storage_payload(
        {"night_enabled": True, "seeded": True, "last_written": "23:00-07:00", "future": 1},
        version=3,
    )
    entry = await async_setup_entry(hass, charger, make_entry(hass))
    assert entry.state is ConfigEntryState.LOADED
    assert "future" not in stored(hass_storage)
    assert charger.calls == []
