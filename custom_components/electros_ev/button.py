"""Button platform for ElectroS EV Charger."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ElectroSCoordinator
from .entity import ElectroSEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ElectroS buttons."""
    coordinator: ElectroSCoordinator = entry.runtime_data
    async_add_entities(
        [
            ElectroSBoostButton(coordinator),
            ElectroSBoostEnergyButton(coordinator),
            ElectroSResetEnergyButton(coordinator),
        ]
    )


class ElectroSBoostButton(ElectroSEntity, ButtonEntity):
    """Charge now for number.boost_minutes."""

    _attr_icon = "mdi:rocket-launch-outline"

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "boost")

    async def async_press(self) -> None:
        """Start the boost."""
        _LOGGER.info("Boost requested (%d min)", self.coordinator.stored.boost_minutes)
        await self.coordinator.async_boost()


class ElectroSBoostEnergyButton(ElectroSEntity, ButtonEntity):
    """Charge now until number.boost_kwh were delivered."""

    _attr_icon = "mdi:battery-charging-high"

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "boost_energy")

    async def async_press(self) -> None:
        """Start the energy boost."""
        _LOGGER.info("Energy boost requested (%d kWh)", self.coordinator.stored.boost_kwh)
        await self.coordinator.async_boost_energy()


class ElectroSResetEnergyButton(ElectroSEntity, ButtonEntity):
    """Reset the consumed-energy (kWh) counter."""

    _attr_icon = "mdi:counter"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "reset_energy", "reset_energy")

    async def async_press(self) -> None:
        """Reset the energy counter."""
        _LOGGER.info("Reset energy counter requested")
        await self.coordinator.async_reset_energy()
