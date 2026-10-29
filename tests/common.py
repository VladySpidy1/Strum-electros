"""Shared helpers for the Home Assistant level tests.

``FakeCharger`` stands in for ``ElectroSApi``: it answers status requests with
realistic status messages (built like tests/test_protocol.py and parsed by the
real ``protocol.parse_status``) and reacts to commands the way the charger does.
Nothing here opens a connection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.electros_ev import protocol
from custom_components.electros_ev.charger_api import ElectroSError
from custom_components.electros_ev.const import DOMAIN

LOGIN = "ES123456"
PASSWORD = "cloud-pass"
HOST = "192.168.1.125"
ENTRY_ID = "electros_test_entry"
STORE_KEY = f"electros_ev.{ENTRY_ID}"

#: Fake values of the secret status fields (Wi-Fi, access point, product number,
#: cloud password). They must never reach Home Assistant states or logs.
SECRETS = {
    0: "MyRouterSSID-SECRET0",
    1: "router-password-SECRET1",
    2: "ElectroS_AP-SECRET2",
    3: "ap-password-SECRET3",
    28: "PRODUCT-SECRET28",
    29: "cloud-password-SECRET29",
}

DEFAULT_HISTORY = (
    "25.10 22:29:57 25800000 30.12 10:00:00 5000000 "
    "27.10 01:10:00 12345678 00.00 00:00:00 0"
)
DEFAULT_MONTHS = "10 1000 2000 3000 4000 5000 6000 7000 8000 9000 120500 0 0"

#: (platform, translation_key) -> unique_id suffix. Pre-2.0 entities keep
#: their old suffixes; new ones use the translation key.
EXPECTED_ENTITIES: dict[tuple[str, str], str] = {
    ("sensor", "status"): "status",
    ("sensor", "configured_current"): "current_amps",
    ("sensor", "measured_current"): "measured_current",
    ("sensor", "voltage"): "mains_voltage",
    ("sensor", "power"): "charging_power",
    ("sensor", "energy"): "energy",
    ("sensor", "session_energy"): "session_energy",
    ("sensor", "month_energy"): "month_energy",
    ("sensor", "boost_end"): "boost_end",
    ("sensor", "energy_day_tariff"): "energy_day_tariff",
    ("sensor", "energy_night_tariff"): "energy_night_tariff",
    ("sensor", "energy_cost"): "energy_cost",
    ("sensor", "current_price"): "current_price",
    ("switch", "charging"): "charging_switch",
    ("switch", "night_timer"): "night_timer",
    ("switch", "weekend_free"): "weekend_free",
    ("number", "charging_amps"): "charging_amps",
    ("number", "boost_minutes"): "boost_minutes",
    ("number", "boost_kwh"): "boost_kwh",
    ("number", "price_day"): "price_day",
    ("number", "price_night"): "price_night",
    ("time", "timer_start"): "timer_start",
    ("time", "timer_end"): "timer_end",
    ("button", "boost"): "boost",
    ("button", "boost_energy"): "boost_energy",
    ("button", "reset_energy"): "reset_energy",
}


#: (platform, translation_key) -> entity_id of a new install, without the
#: ``electros_<login>_`` prefix. Home Assistant builds entity ids from the
#: English names (also for a Ukrainian or Russian UI); README.md and examples/
#: use these ids.
ENTITY_OBJECT_IDS: dict[tuple[str, str], str] = {
    ("sensor", "status"): "status",
    ("sensor", "configured_current"): "configured_current",
    ("sensor", "measured_current"): "measured_current",
    ("sensor", "voltage"): "mains_voltage",
    ("sensor", "power"): "charging_power",
    ("sensor", "energy"): "consumed_energy",
    ("sensor", "session_energy"): "session_energy",
    ("sensor", "month_energy"): "energy_this_month",
    ("sensor", "boost_end"): "boost_ends",
    ("sensor", "energy_day_tariff"): "energy_day_tariff",
    ("sensor", "energy_night_tariff"): "energy_night_tariff",
    ("sensor", "energy_cost"): "energy_cost",
    ("sensor", "current_price"): "current_price",
    ("switch", "charging"): "charging",
    ("switch", "night_timer"): "night_timer",
    ("switch", "weekend_free"): "free_charging_on_weekends",
    ("number", "charging_amps"): "charging_current",
    ("number", "boost_minutes"): "boost_duration",
    ("number", "boost_kwh"): "boost_kwh",
    ("number", "price_day"): "price_day",
    ("number", "price_night"): "price_night",
    ("time", "timer_start"): "night_window_start",
    ("time", "timer_end"): "night_window_end",
    ("button", "boost"): "charge_now_boost",
    ("button", "boost_energy"): "charge_energy_boost",
    ("button", "reset_energy"): "reset_kwh",
}


def build_status(
    *,
    code: str = "1",
    amps: int | str = 16,
    timer: tuple[str, str] | None = ("23:00", "07:00"),
    energy_raw: int | str = 25_800_000,
    power_raw: int | str = 36340,
    clock: str = "28.10.2026T20:15:03",
    history: str = DEFAULT_HISTORY,
    months: str = DEFAULT_MONTHS,
    **fields: str,
) -> str:
    """A realistic 37-line status message (see docs/protocol.md), fake secrets.

    ``timer=None`` reports ``00:00``/``00:00``, which is what the charger shows
    when its timer is disabled. ``f<N>="..."`` overrides any field.
    """
    start, end = timer if timer is not None else (protocol.DISABLED_TIME,) * 2
    lines = [
        SECRETS[0],  # 0 router SSID
        SECRETS[1],  # 1 router password
        SECRETS[2],  # 2 charger AP name
        SECRETS[3],  # 3 charger AP password
        "electros.org.ua",  # 4 server host
        "8000",  # 5 server port
        "32",  # 6 ? (earth control)
        str(amps),  # 7 configured current, A
        code,  # 8 status
        "158",  # 9 measured current x0.1 A
        "229",  # 10 mains voltage now, V
        str(energy_raw),  # 11 energy kWh x1e6
        str(power_raw),  # 12 power kW x1e4
        "3",  # 13 neutral voltage
        "0",  # 14 ?
        clock,  # 15 charger clock
        "247",  # 16 mains voltage max
        "201",  # 17 mains voltage min
        "5",  # 18 neutral max
        "0",  # 19 neutral min
        "226",  # 20 mains voltage average
        "2",  # 21 neutral average
        "1000",  # 22 voltage calibration
        "1000",  # 23 current calibration
        "180",  # 24 min charging voltage
        "0",  # 25 leakage control index
        start,  # 26 timer start
        end,  # 27 timer end
        SECRETS[28],  # 28 product number
        SECRETS[29],  # 29 cloud password
        "07:00:00 21 23:00:00 20 23:00:05 2",  # 30 event log
        months,  # 31 months (Wh)
        history,  # 32 sessions
        "Memory: 1234 5678",  # 33
        "0",  # 34
        "0",  # 35
        "",  # 36 trailing
    ]
    assert len(lines) == 37
    for key, value in fields.items():
        lines[int(key.lstrip("f"))] = value
    return "\n".join(lines)


class FakeCharger:
    """In-memory ElectroS charger with the ``ElectroSApi`` interface.

    Commands are recorded in ``calls`` (status polls are not) and change the
    simulated device: ``start`` -> status ``start_status`` (charging by default),
    ``stop`` -> ``stop_status``, ``set_timer``/``disable_timer`` -> window,
    ``resetkwt`` -> energy 0. Command names in ``fail`` return False (the way the
    real client reports a failed command). ``unreachable`` makes polls raise.
    """

    def __init__(self) -> None:
        self.status = "1"
        self.amps = 16
        self.timer: tuple[str, str] | None = ("23:00", "07:00")
        self.energy_raw = 25_800_000
        self.power_raw = 36340
        self.clock = "28.10.2026T20:15:03"
        self.history = DEFAULT_HISTORY
        self.months = DEFAULT_MONTHS
        self.extra_fields: dict[str, str] = {}
        self.start_status = "2"
        self.stop_status = "1"
        self.unreachable = False
        self.fail: set[str] = set()
        self.calls: list[tuple[Any, ...]] = []
        self.clock_writes: list[datetime] = []
        self.polls = 0

    # -- what the charger reports --------------------------------------------
    def message(self) -> str:
        """Current status message."""
        return build_status(
            code=self.status,
            amps=self.amps,
            timer=self.timer,
            energy_raw=self.energy_raw,
            power_raw=self.power_raw,
            clock=self.clock,
            history=self.history,
            months=self.months,
            **self.extra_fields,
        )

    async def get_state(self) -> dict[str, Any]:
        """Poll (``ElectroSApi.get_state``)."""
        self.polls += 1
        if self.unreachable:
            raise ElectroSError("timeout")
        return protocol.parse_status(self.message())

    async def test_connection(self) -> bool:
        """Config flow connection test."""
        return not self.unreachable

    # -- commands --------------------------------------------------------------
    def _record(self, *call: Any) -> bool:
        self.calls.append(call)
        return call[0] not in self.fail

    async def start_charging(self, amps: int) -> bool:
        """``start`` command."""
        if not self._record("start", amps):
            return False
        self.amps = amps
        self.status = self.start_status
        return True

    async def stop_charging(self) -> bool:
        """``stop`` command."""
        if not self._record("stop"):
            return False
        self.status = self.stop_status
        return True

    async def reset_energy(self) -> bool:
        """``resetkwt`` command."""
        if not self._record("resetkwt"):
            return False
        self.energy_raw = 0
        return True

    async def set_timer(self, start: str, end: str, now: datetime) -> bool:
        """``datetime`` command with a window."""
        if not self._record("set_timer", start, end):
            return False
        self.clock_writes.append(now)
        self.timer = (start, end)
        return True

    async def disable_timer(self, now: datetime) -> bool:
        """``datetime`` command without a window."""
        if not self._record("disable_timer"):
            return False
        self.clock_writes.append(now)
        self.timer = None
        return True

    # -- helpers for assertions ------------------------------------------------
    def take_calls(self) -> list[tuple[Any, ...]]:
        """Return and forget the recorded commands."""
        calls, self.calls = self.calls, []
        return calls


def local_dt(year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0) -> datetime:
    """Aware UTC datetime for a wall-clock time in the HA time zone."""
    return dt_util.as_utc(
        datetime(year, month, day, hour, minute, second, tzinfo=dt_util.get_default_time_zone())
    )


def default_entry_data(**overrides: Any) -> dict[str, Any]:
    """Config entry data of a cloud-mode charger."""
    data: dict[str, Any] = {
        "charger_login": LOGIN,
        "charger_password": PASSWORD,
        "scan_interval": 60,
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value is not None}


def make_entry(
    hass: HomeAssistant,
    data: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Add (but do not set up) a config entry with a fixed entry_id."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=LOGIN,
        entry_id=ENTRY_ID,
        title=f"ElectroS {LOGIN}",
        data=data if data is not None else default_entry_data(),
        options=options or {},
    )
    entry.add_to_hass(hass)
    return entry


def patch_api(charger: FakeCharger):
    """Make the integration use ``charger`` as its API (the config flow is patched separately)."""
    return patch("custom_components.electros_ev.ElectroSApi", return_value=charger)


async def async_setup_entry(
    hass: HomeAssistant, charger: FakeCharger, entry: MockConfigEntry | None = None
) -> MockConfigEntry:
    """Set up the integration with ``charger`` and wait until startup is done."""
    if entry is None:
        entry = make_entry(hass)
    with patch_api(charger):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def async_settle(hass: HomeAssistant) -> None:
    """Let commands, follow-up refreshes and background tasks finish."""
    await hass.async_block_till_done(wait_background_tasks=True)


async def async_poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Run one coordinator poll now and let what it triggers finish."""
    await entry.runtime_data.async_refresh()
    await async_settle(hass)


def device_id(hass: HomeAssistant, login: str = LOGIN) -> str:
    """HA device id of the charger."""
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, login)})
    assert device is not None
    return device.id


def entity_id(hass: HomeAssistant, domain: str, key: str, login: str = LOGIN) -> str:
    """Find an entity the way the card does: device_id + translation_key (+ domain)."""
    dev = device_id(hass, login)
    found = [
        entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if entry.device_id == dev
        and entry.translation_key == key
        and entry.domain == domain
    ]
    assert len(found) == 1, f"{domain}.{key}: {found}"
    return found[0]


def state(hass: HomeAssistant, domain: str, key: str):
    """Current state object of the entity with this translation key."""
    current = hass.states.get(entity_id(hass, domain, key))
    assert current is not None, f"{domain}.{key} has no state"
    return current


def stored(hass_storage: dict[str, Any]) -> dict[str, Any]:
    """Data the integration persisted for the test entry."""
    return hass_storage[STORE_KEY]["data"]


def storage_payload(data: dict[str, Any], version: int = 1) -> dict[str, Any]:
    """A Store file for the test entry, to pre-load ``hass_storage``.

    Version 1 (the v2.0.0 schema) by default, so restart tests also run the
    migration to the current schema.
    """
    return {"version": version, "minor_version": 1, "key": STORE_KEY, "data": data}


# --------------------------------------------------------------------------- #
# Driving the integration like a user / the clock
# --------------------------------------------------------------------------- #


async def async_fire_at(hass: HomeAssistant, freezer: Any, moment: datetime) -> None:
    """Move the frozen clock to ``moment`` and run every timer due by then."""
    freezer.move_to(moment)
    async_fire_time_changed(hass, moment)
    await async_settle(hass)


async def async_call(hass: HomeAssistant, service: str, **data: Any) -> None:
    """Call an electros_ev service for the test charger."""
    await hass.services.async_call(
        DOMAIN, service, {"device_id": device_id(hass), **data}, blocking=True
    )
    await async_settle(hass)


async def async_switch(hass: HomeAssistant, key: str, on: bool) -> None:
    """Turn a switch of the charger on or off."""
    await hass.services.async_call(
        "switch",
        "turn_on" if on else "turn_off",
        {"entity_id": entity_id(hass, "switch", key)},
        blocking=True,
    )
    await async_settle(hass)


async def async_press(hass: HomeAssistant, key: str) -> None:
    """Press a button of the charger."""
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id(hass, "button", key)}, blocking=True
    )
    await async_settle(hass)


async def async_set_number(hass: HomeAssistant, key: str, value: float) -> None:
    """Set a number entity of the charger."""
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id(hass, "number", key), "value": value},
        blocking=True,
    )
    await async_settle(hass)


def boost_state(hass: HomeAssistant) -> tuple[str, dict[str, Any]]:
    """State and attributes of sensor.boost_end."""
    current = state(hass, "sensor", "boost_end")
    return current.state, dict(current.attributes)
