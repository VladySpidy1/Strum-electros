"""Number platform for ElectroS EV Charger."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .charger_api import normalize_amps
from .const import CONF_CHARGER_LOGIN, DOMAIN, SUPPORTED_AMPS
from .coordinator import ElectroSCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ElectroS number entities."""
    coordinator: ElectroSCoordinator = entry.runtime_data
    login = entry.data[CONF_CHARGER_LOGIN]

    async_add_entities([ElectroSAmpsNumber(coordinator, entry, login)])


class ElectroSAmpsNumber(CoordinatorEntity[ElectroSCoordinator], NumberEntity):
    """Number entity to set charging amperage."""

    _attr_has_entity_name = True
    _attr_name = "Струм зарядки"
    _attr_icon = "mdi:current-ac"
    _attr_native_min_value = 6
    _attr_native_max_value = 32
    _attr_native_step = 2
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_mode = NumberMode.SLIDER

    def __init__(
        self,
        coordinator: ElectroSCoordinator,
        entry: ConfigEntry,
        login: str,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self._login = login
        self._attr_unique_id = f"{login}_charging_amps"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, login)},
            "name": f"ElectroS {login}",
            "manufacturer": "ElectroS",
            "model": "EV Charger",
        }

    @property
    def native_value(self) -> float | None:
        """Return target amps from coordinator (not from charger poll).

        This ensures the slider always shows what the user set,
        not a stale value from the charger.
        """
        return float(self.coordinator.target_amps)

    async def async_set_native_value(self, value: float) -> None:
        """Set charging amperage.

        If charger is currently charging, sends the command immediately.
        Otherwise, just stores the value for the next turn_on.
        """
        amps = normalize_amps(int(value))
        if amps <= 0:
            _LOGGER.warning("Invalid amperage value: %s", value)
            return
        _LOGGER.info("Slider set to %d A", amps)
        await self.coordinator.async_set_amps(amps)
