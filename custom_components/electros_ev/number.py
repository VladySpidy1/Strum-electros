"""Number platform for ElectroS EV Charger."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfElectricCurrent, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import protocol
from .const import BOOST_MINUTES_STEP, MAX_BOOST_MINUTES, MIN_BOOST_MINUTES
from .coordinator import ElectroSCoordinator
from .entity import ElectroSSettingEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ElectroS number entities."""
    coordinator: ElectroSCoordinator = entry.runtime_data
    async_add_entities(
        [
            ElectroSAmpsNumber(coordinator),
            ElectroSBoostMinutesNumber(coordinator),
        ]
    )


class ElectroSAmpsNumber(ElectroSSettingEntity, NumberEntity):
    """Charging current used for starts; applied at once while charging."""

    _attr_icon = "mdi:current-ac"
    _attr_native_min_value = protocol.MIN_AMPS
    _attr_native_max_value = protocol.MAX_AMPS
    _attr_native_step = protocol.AMPS_STEP
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = NumberDeviceClass.CURRENT
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "charging_amps", "charging_amps")

    @property
    def native_value(self) -> float:
        """Target current (what the user set, not a stale poll)."""
        return float(self.coordinator.target_amps)

    async def async_set_native_value(self, value: float) -> None:
        """Store the current; send it now if charging."""
        _LOGGER.info("Charging current set to %s A", value)
        await self.coordinator.async_set_target_amps(int(value))


class ElectroSBoostMinutesNumber(ElectroSSettingEntity, NumberEntity):
    """Duration used by button.boost."""

    _attr_icon = "mdi:timer-cog-outline"
    _attr_native_min_value = MIN_BOOST_MINUTES
    _attr_native_max_value = MAX_BOOST_MINUTES
    _attr_native_step = BOOST_MINUTES_STEP
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "boost_minutes")

    @property
    def native_value(self) -> float:
        """Boost duration in minutes."""
        return float(self.coordinator.stored.boost_minutes)

    async def async_set_native_value(self, value: float) -> None:
        """Persist the duration."""
        await self.coordinator.async_set_boost_minutes(int(value))
