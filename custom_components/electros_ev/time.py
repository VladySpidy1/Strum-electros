"""Time platform for ElectroS EV Charger: start/end of the night window."""

from __future__ import annotations

from datetime import time as dt_time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import protocol
from .coordinator import ElectroSCoordinator
from .entity import ElectroSSettingEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ElectroS time entities."""
    coordinator: ElectroSCoordinator = entry.runtime_data
    async_add_entities(
        [
            ElectroSTimerTime(coordinator, "timer_start"),
            ElectroSTimerTime(coordinator, "timer_end"),
        ]
    )


class ElectroSTimerTime(ElectroSSettingEntity, TimeEntity):
    """Start or end of the daily window in which the charger may charge."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ElectroSCoordinator, key: str) -> None:
        """Initialize."""
        super().__init__(coordinator, key)
        self._key = key
        self._attr_icon = "mdi:clock-start" if key == "timer_start" else "mdi:clock-end"

    @property
    def native_value(self) -> dt_time:
        """Stored HH:MM."""
        value = (
            self.coordinator.stored.timer_start
            if self._key == "timer_start"
            else self.coordinator.stored.timer_end
        )
        return protocol.hhmm_to_time(value)

    async def async_set_value(self, value: dt_time) -> None:
        """Persist and apply to the charger if the night timer is on."""
        if self._key == "timer_start":
            await self.coordinator.async_set_timer(start=value)
        else:
            await self.coordinator.async_set_timer(end=value)
