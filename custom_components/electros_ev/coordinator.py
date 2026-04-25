"""DataUpdateCoordinator for ElectroS EV Charger."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .charger_api import ElectroSApi, normalize_amps
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

COMMAND_SETTLE_DELAY = 3  # seconds to wait after sending a command


class ElectroSCoordinator(DataUpdateCoordinator[dict]):
    """Coordinator to manage fetching ElectroS charger data."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: ElectroSApi,
        scan_interval: int,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.api = api
        self.target_amps: int = 6  # safe default, updated from charger on first poll

    async def _async_update_data(self) -> dict:
        """Fetch data from the charger."""
        try:
            data = await self.api.get_state()
        except Exception as err:
            raise UpdateFailed(f"Error communicating with charger: {err}") from err

        if data.get("status_text", "").startswith("error"):
            raise UpdateFailed(f"Charger returned error: {data['status_text']}")

        # Sync target_amps from charger on poll (only if charger reports a value)
        charger_amps = data.get("current_amps")
        if charger_amps is not None and charger_amps > 0:
            self.target_amps = charger_amps

        return data

    async def async_start_charging(self, amps: int) -> bool:
        """Start charging at specified amperage with settle delay."""
        amps = normalize_amps(amps)
        if amps <= 0:
            _LOGGER.warning("Cannot start charging with 0 amps")
            return False
        _LOGGER.info("Starting charging at %d A", amps)
        success = await self.api.start_charging(amps)
        if success:
            self.target_amps = amps
            await asyncio.sleep(COMMAND_SETTLE_DELAY)
            await self.async_request_refresh()
        return success

    async def async_stop_charging(self) -> bool:
        """Stop charging with settle delay."""
        _LOGGER.info("Stopping charging")
        success = await self.api.stop_charging()
        if success:
            await asyncio.sleep(COMMAND_SETTLE_DELAY)
            await self.async_request_refresh()
        return success

    async def async_set_amps(self, amps: int) -> bool:
        """Set target amperage. Only sends command if currently charging."""
        amps = normalize_amps(amps)
        if amps <= 0:
            _LOGGER.warning("Invalid amperage: %d", amps)
            return False

        old_amps = self.target_amps
        self.target_amps = amps
        _LOGGER.info("Target amps set to %d A (was %d A)", amps, old_amps)

        # Only send command to charger if it's currently charging
        if self.data and self.data.get("status_text") == "charging":
            _LOGGER.info("Charger is active, updating amps to %d A", amps)
            success = await self.api.start_charging(amps)
            if success:
                await asyncio.sleep(COMMAND_SETTLE_DELAY)
                await self.async_request_refresh()
            return success

        # Not charging — just store the value, will be used on next turn_on
        self.async_set_updated_data(self.data)
        return True
