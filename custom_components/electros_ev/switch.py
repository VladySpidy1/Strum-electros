"""Switch platform for ElectroS EV Charger."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CHARGER_LOGIN, DOMAIN
from .coordinator import ElectroSCoordinator

_LOGGER = logging.getLogger(__name__)

# Statuses that mean the charger is actively charging or trying to charge
_CHARGING_STATUSES = {"charging"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ElectroS switches."""
    coordinator: ElectroSCoordinator = entry.runtime_data
    login = entry.data[CONF_CHARGER_LOGIN]

    async_add_entities([ElectroSChargingSwitch(coordinator, entry, login)])


class ElectroSChargingSwitch(CoordinatorEntity[ElectroSCoordinator], SwitchEntity):
    """Switch to start/stop charging."""

    _attr_has_entity_name = True
    _attr_name = "Зарядка"
    _attr_icon = "mdi:ev-station"
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self,
        coordinator: ElectroSCoordinator,
        entry: ConfigEntry,
        login: str,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._login = login
        self._attr_unique_id = f"{login}_charging_switch"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, login)},
            "name": f"ElectroS {login}",
            "manufacturer": "ElectroS",
            "model": "EV Charger",
        }

    @property
    def is_on(self) -> bool | None:
        """Return true if charging."""
        if self.coordinator.data is None:
            return None
        status = self.coordinator.data.get("status_text")
        return status in _CHARGING_STATUSES

    async def async_turn_on(self, **kwargs) -> None:
        """Start charging at the target amperage from coordinator."""
        amps = self.coordinator.target_amps
        _LOGGER.info("Turn ON requested, starting at %d A", amps)
        await self.coordinator.async_start_charging(amps)

    async def async_turn_off(self, **kwargs) -> None:
        """Stop charging."""
        _LOGGER.info("Turn OFF requested")
        await self.coordinator.async_stop_charging()
