"""DataUpdateCoordinator for ElectroS EV Charger."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .charger_api import ElectroSApi
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


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

    async def _async_update_data(self) -> dict:
        """Fetch data from the charger."""
        try:
            data = await self.api.get_state()
        except Exception as err:
            raise UpdateFailed(f"Error communicating with charger: {err}") from err

        if data.get("status_text", "").startswith("error"):
            raise UpdateFailed(f"Charger returned error: {data['status_text']}")

        return data
