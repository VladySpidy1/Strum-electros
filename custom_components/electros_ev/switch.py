"""Switch platform for ElectroS EV Charger."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import protocol
from .coordinator import ElectroSCoordinator
from .entity import ElectroSEntity, ElectroSSettingEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ElectroS switches."""
    coordinator: ElectroSCoordinator = entry.runtime_data
    async_add_entities(
        [
            ElectroSChargingSwitch(coordinator),
            ElectroSNightTimerSwitch(coordinator),
            ElectroSWeekendFreeSwitch(coordinator),
        ]
    )


class ElectroSChargingSwitch(ElectroSEntity, SwitchEntity):
    """On = charging. On starts now (bypassing a blocking timer), off stops."""

    _attr_icon = "mdi:ev-station"
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "charging", "charging_switch")

    @property
    def is_on(self) -> bool | None:
        """Return true if charging."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("status_text") == protocol.STATUS_CHARGING

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start charging with the configured current."""
        _LOGGER.info("Turn ON requested (%d A)", self.coordinator.target_amps)
        await self.coordinator.async_start_charging()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop charging, cancel any boost, restore the timer."""
        _LOGGER.info("Turn OFF requested")
        await self.coordinator.async_stop_charging()


class ElectroSNightTimerSwitch(ElectroSSettingEntity, SwitchEntity):
    """Whether the charger's daily window (timer_start..timer_end) is enabled."""

    _attr_icon = "mdi:weather-night"

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "night_timer")

    @property
    def is_on(self) -> bool:
        """Night timer enabled in HA."""
        return self.coordinator.stored.night_enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Window currently on the device (None = disabled or unknown)."""
        data = self.coordinator.data or {}
        window = data.get("timer_window") if data.get("timer_known") else None
        return {
            "device_window": protocol.window_to_str(window) or None,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable."""
        await self.coordinator.async_set_night_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable."""
        await self.coordinator.async_set_night_enabled(False)


class ElectroSWeekendFreeSwitch(ElectroSSettingEntity, SwitchEntity):
    """When on, the device timer is disabled on Saturday and Sunday."""

    _attr_icon = "mdi:calendar-weekend"

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "weekend_free")

    @property
    def is_on(self) -> bool:
        """Weekend free enabled."""
        return self.coordinator.stored.weekend_free

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable."""
        await self.coordinator.async_set_weekend_free(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable."""
        await self.coordinator.async_set_weekend_free(False)
