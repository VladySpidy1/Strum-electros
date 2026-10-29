"""Number platform for ElectroS EV Charger."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import protocol, tariff
from .const import (
    BOOST_KWH_STEP,
    BOOST_MINUTES_STEP,
    MAX_BOOST_KWH,
    MAX_BOOST_MINUTES,
    MAX_PRICE,
    MIN_BOOST_KWH,
    MIN_BOOST_MINUTES,
    MIN_PRICE,
    PRICE_STEP,
)
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
            ElectroSBoostKwhNumber(coordinator),
            ElectroSPriceNumber(coordinator, tariff.ZONE_DAY),
            ElectroSPriceNumber(coordinator, tariff.ZONE_NIGHT),
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


class ElectroSBoostKwhNumber(ElectroSSettingEntity, NumberEntity):
    """Energy target used by button.boost_energy ("charge N kWh")."""

    _attr_icon = "mdi:battery-charging-medium"
    _attr_native_min_value = MIN_BOOST_KWH
    _attr_native_max_value = MAX_BOOST_KWH
    _attr_native_step = BOOST_KWH_STEP
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = NumberDeviceClass.ENERGY
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "boost_kwh")

    @property
    def native_value(self) -> float:
        """Energy target in kWh."""
        return float(self.coordinator.stored.boost_kwh)

    async def async_set_native_value(self, value: float) -> None:
        """Persist the target."""
        await self.coordinator.async_set_boost_kwh(value)


class ElectroSPriceNumber(ElectroSSettingEntity, NumberEntity):
    """Price per kWh of the day or night tariff zone."""

    _attr_native_min_value = MIN_PRICE
    _attr_native_max_value = MAX_PRICE
    _attr_native_step = PRICE_STEP
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ElectroSCoordinator, zone: str) -> None:
        """Initialize."""
        super().__init__(coordinator, f"price_{zone}")
        self._zone = zone
        self._attr_native_unit_of_measurement = f"{coordinator.hass.config.currency}/kWh"
        self._attr_icon = (
            "mdi:cash-clock" if zone == tariff.ZONE_NIGHT else "mdi:cash"
        )

    @property
    def native_value(self) -> float:
        """Stored price."""
        stored = self.coordinator.stored
        return stored.price_night if self._zone == tariff.ZONE_NIGHT else stored.price_day

    async def async_set_native_value(self, value: float) -> None:
        """Persist the price (applies to energy counted from now on)."""
        await self.coordinator.async_set_price(self._zone, value)
