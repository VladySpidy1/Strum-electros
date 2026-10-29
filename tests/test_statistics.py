"""Long-term statistics of the tariff sensors (the card's charts) and the Energy dashboard.

These tests run the real recorder (in-memory SQLite) and send the card's
``recorder/statistics_during_period`` message through Home Assistant's websocket
command handler (no web server, no socket). Time is frozen in Europe/Kyiv; the
tariff night zone is 23:00-07:00, prices 4.32 / 2.16 EUR per kWh.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta
import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from freezegun.api import FrozenDateTimeFactory

from homeassistant.components.energy import data as energy_data, validate
from homeassistant.components.recorder import Recorder
from homeassistant.components.websocket_api.connection import ActiveConnection
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockUser
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
    do_adhoc_statistics,
)

from .common import FakeCharger, async_poll, async_setup_entry, entity_id, local_dt

KEYS = ("energy_day_tariff", "energy_night_tariff", "energy_cost")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: Recorder, enable_custom_integrations: None
) -> Generator[None]:
    """As in conftest.py, but the recorder must start before ``hass``."""
    yield


class WebSocket:
    """Calls websocket commands the way the frontend does, without a connection."""

    def __init__(self, hass: HomeAssistant, user: MockUser) -> None:
        self.hass = hass
        self.replies: dict[int, dict[str, Any]] = {}
        self._next_id = 1
        self._connection = ActiveConnection(
            logging.getLogger(__name__),
            hass,
            self._receive,
            user,
            SimpleNamespace(id="test-refresh-token"),
        )

    def _receive(self, message: Any) -> None:
        if isinstance(message, (bytes, str)):
            message = json.loads(message)
        self.replies[message["id"]] = message

    async def call(self, **message: Any) -> Any:
        msg_id = self._next_id
        self._next_id += 1
        self._connection.async_handle({"id": msg_id, **message})
        # Handlers answer from a background task that waits for the recorder thread.
        for _ in range(50):
            await async_wait_recording_done(self.hass)
            await self.hass.async_block_till_done(wait_background_tasks=True)
            if msg_id in self.replies:
                break
        reply = self.replies[msg_id]
        assert reply["success"], reply
        return reply["result"]


def _iso(moment: datetime) -> str:
    """``new Date(...).toISOString()`` as the card sends it."""
    return dt_util.as_utc(moment).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _ms(moment: datetime) -> float:
    return dt_util.as_utc(moment).timestamp() * 1000


async def _compile(hass: HomeAssistant, first: datetime, last: datetime) -> None:
    """Compile every 5-minute period (and the hours they complete) from first to last."""
    moment = first
    while moment <= last:
        do_adhoc_statistics(hass, start=moment)
        moment += timedelta(minutes=5)
    await async_wait_recording_done(hass)


async def _charge_an_evening(
    hass: HomeAssistant, charger: FakeCharger, freezer: FrozenDateTimeFactory
) -> dict[str, str]:
    """1 kWh at 22:10 (day), 2 kWh at 23:00:30 (night) on Wednesday 28.10.2026."""
    freezer.move_to(local_dt(2026, 10, 28, 21, 58))
    entry = await async_setup_entry(hass, charger)
    ids = {key: entity_id(hass, "sensor", key) for key in KEYS}
    await async_wait_recording_done(hass)

    for moment, kwh in (
        (local_dt(2026, 10, 28, 22, 10), 26.8),
        (local_dt(2026, 10, 28, 22, 59), 26.8),
        (local_dt(2026, 10, 28, 23, 0, 30), 28.8),
    ):
        freezer.move_to(moment)
        charger.energy_raw = round(kwh * 1_000_000)
        await async_poll(hass, entry)
        await async_wait_recording_done(hass)

    freezer.move_to(local_dt(2026, 10, 29, 0, 5))
    await _compile(hass, local_dt(2026, 10, 28, 21, 55), local_dt(2026, 10, 28, 23, 55))
    return ids


def _changes(result: dict[str, list[dict[str, Any]]], statistic_id: str) -> list[tuple[float, float]]:
    return [(row["start"], round(row["change"], 6)) for row in result.get(statistic_id, [])]


async def test_tariff_statistics_as_the_card_reads_them(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
    hass_admin_user: MockUser,
) -> None:
    ids = await _charge_an_evening(hass, charger, freezer)
    ws = WebSocket(hass, hass_admin_user)
    statistic_ids = [ids["energy_day_tariff"], ids["energy_night_tariff"], ids["energy_cost"]]

    # День: hourly bars of 28.10, exactly the message the card sends.
    day_start, day_end = local_dt(2026, 10, 28), local_dt(2026, 10, 29)
    result = await ws.call(
        type="recorder/statistics_during_period",
        start_time=_iso(day_start),
        end_time=_iso(day_end),
        statistic_ids=statistic_ids,
        period="hour",
        types=["change"],
        units={"energy": "kWh"},
    )
    hour = {h: _ms(local_dt(2026, 10, 28, h)) for h in (21, 22, 23)}
    assert _changes(result, ids["energy_day_tariff"]) == [
        (hour[21], 0.0),
        (hour[22], 1.0),
        (hour[23], 0.0),
    ]
    assert _changes(result, ids["energy_night_tariff"]) == [
        (hour[21], 0.0),
        (hour[22], 0.0),
        (hour[23], 2.0),
    ]
    assert _changes(result, ids["energy_cost"]) == [
        (hour[21], 0.0),
        (hour[22], 4.32),
        (hour[23], 4.32),
    ]
    # Rows carry ``start`` as epoch milliseconds (what the card's statTime expects).
    row = result[ids["energy_day_tariff"]][0]
    assert isinstance(row["start"], (int, float)) and row["start"] > 1e12
    assert set(row) == {"start", "end", "change"}

    # Тиждень / Місяць: daily bars, buckets in the HA time zone (Kyiv midnight).
    week = await ws.call(
        type="recorder/statistics_during_period",
        start_time=_iso(local_dt(2026, 10, 26)),  # Monday
        end_time=_iso(local_dt(2026, 11, 2)),
        statistic_ids=statistic_ids,
        period="day",
        types=["change"],
        units={"energy": "kWh"},
    )
    assert _changes(week, ids["energy_day_tariff"]) == [(_ms(day_start), 1.0)]
    assert _changes(week, ids["energy_night_tariff"]) == [(_ms(day_start), 2.0)]
    assert _changes(week, ids["energy_cost"]) == [(_ms(day_start), 8.64)]

    # Рік: monthly bars.
    year = await ws.call(
        type="recorder/statistics_during_period",
        start_time=_iso(local_dt(2026, 1, 1)),
        end_time=_iso(local_dt(2027, 1, 1)),
        statistic_ids=statistic_ids,
        period="month",
        types=["change"],
        units={"energy": "kWh"},
    )
    october = _ms(local_dt(2026, 10, 1))
    assert _changes(year, ids["energy_day_tariff"]) == [(october, 1.0)]
    assert _changes(year, ids["energy_night_tariff"]) == [(october, 2.0)]

    # ``units`` converts energy only; the cost keeps the currency.
    in_wh = await ws.call(
        type="recorder/statistics_during_period",
        start_time=_iso(day_start),
        end_time=_iso(day_end),
        statistic_ids=statistic_ids,
        period="day",
        types=["change"],
        units={"energy": "Wh"},
    )
    assert _changes(in_wh, ids["energy_night_tariff"]) == [(_ms(day_start), 2000.0)]
    assert _changes(in_wh, ids["energy_cost"]) == [(_ms(day_start), 8.64)]

    # Before the installation there are no statistics at all ("Статистика накопичується").
    before = await ws.call(
        type="recorder/statistics_during_period",
        start_time=_iso(local_dt(2026, 10, 19)),
        end_time=_iso(local_dt(2026, 10, 26)),
        statistic_ids=statistic_ids,
        period="day",
        types=["change"],
        units={"energy": "kWh"},
    )
    assert before == {}

    # The recorder sees nothing wrong with the sensors (units, state classes).
    issues = await ws.call(type="recorder/validate_statistics")
    ours = {key: value for key, value in issues.items() if "electros" in key}
    assert ours == {}

    metadata = {
        item["statistic_id"]: item
        for item in await ws.call(type="recorder/list_statistic_ids", statistic_type="sum")
    }
    assert metadata[ids["energy_day_tariff"]]["statistics_unit_of_measurement"] == "kWh"
    assert metadata[ids["energy_night_tariff"]]["statistics_unit_of_measurement"] == "kWh"
    assert metadata[ids["energy_cost"]]["statistics_unit_of_measurement"] == "EUR"


async def test_energy_dashboard_accepts_energy_and_current_price(
    hass: HomeAssistant,
    charger: FakeCharger,
    freezer: FrozenDateTimeFactory,
) -> None:
    """README: sensor.energy as consumption, sensor.current_price as its price entity."""
    await _charge_an_evening(hass, charger, freezer)
    energy = entity_id(hass, "sensor", "energy")
    price = entity_id(hass, "sensor", "current_price")

    # The energy panel's own dependencies need a web server; the validation does not.
    hass.config.components.update({"websocket_api", "history"})
    assert await async_setup_component(hass, "energy", {})
    manager = await energy_data.async_get_manager(hass)
    await manager.async_update(
        {
            "energy_sources": [
                {
                    "type": "grid",
                    "flow_from": [
                        {
                            "stat_energy_from": energy,
                            "stat_cost": None,
                            "entity_energy_price": price,
                            "number_energy_price": None,
                        }
                    ],
                    "flow_to": [],
                    "cost_adjustment_day": 0,
                }
            ],
            "device_consumption": [{"stat_consumption": energy, "name": "EV"}],
        }
    )
    await hass.async_block_till_done()
    await _compile(hass, local_dt(2026, 10, 29, 0, 0), local_dt(2026, 10, 29, 0, 0))

    result = (await validate.async_validate(hass)).as_dict()
    assert result["energy_sources"] == [[]]
    assert result["device_consumption"] == [[]]

    # Home Assistant creates the cost sensor from the price entity.
    cost = hass.states.get(f"{energy}_cost")
    assert cost is not None
    assert cost.attributes["unit_of_measurement"] == "EUR"
