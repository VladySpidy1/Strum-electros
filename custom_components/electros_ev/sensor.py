"""Sensor platform for ElectroS EV Charger."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import protocol
from .coordinator import ElectroSCoordinator
from .entity import ElectroSEntity, ElectroSSettingEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ElectroS sensors."""
    coordinator: ElectroSCoordinator = entry.runtime_data
    async_add_entities(
        [
            ElectroSStatusSensor(coordinator),
            ElectroSConfiguredCurrentSensor(coordinator),
            ElectroSMeasuredCurrentSensor(coordinator),
            ElectroSVoltageSensor(coordinator),
            ElectroSPowerSensor(coordinator),
            ElectroSEnergySensor(coordinator),
            ElectroSSessionEnergySensor(coordinator),
            ElectroSMonthEnergySensor(coordinator),
            ElectroSBoostEndSensor(coordinator),
        ]
    )


class _DataSensor(ElectroSEntity, SensorEntity):
    """Sensor reading one key of the coordinator data."""

    _data_key: str

    @property
    def native_value(self) -> Any:
        """Return the value from the last status."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._data_key)


class ElectroSStatusSensor(_DataSensor):
    """Charger status (enum)."""

    _attr_icon = "mdi:ev-station"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(protocol.STATUS_OPTIONS)
    _unrecorded_attributes = frozenset({"events"})

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "status", "status")

    @property
    def native_value(self) -> str | None:
        """Return the status key."""
        if not self.coordinator.data:
            return None
        value = self.coordinator.data.get("status_text")
        return value if value in protocol.STATUS_OPTIONS else protocol.STATUS_UNKNOWN

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Raw code and the device event log."""
        data = self.coordinator.data or {}
        return {
            "status_code": data.get("status_code"),
            "online": data.get("online"),
            "device_time": data.get("device_time"),
            "events": data.get("events") or [],
        }


class ElectroSConfiguredCurrentSensor(_DataSensor):
    """Charging current configured on the charger (field 7)."""

    _attr_icon = "mdi:current-ac"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _data_key = "current_amps"

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "configured_current", "current_amps")


class ElectroSMeasuredCurrentSensor(_DataSensor):
    """Measured charging current (field 9)."""

    _attr_icon = "mdi:flash"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _data_key = "measured_current"

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "measured_current", "measured_current")


class ElectroSVoltageSensor(_DataSensor):
    """Current mains voltage (field 10) with min/max/avg attributes."""

    _attr_icon = "mdi:flash-outline"
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _data_key = "mains_voltage"

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "voltage", "mains_voltage")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Min (17), max (16), average (20)."""
        data = self.coordinator.data or {}
        return {
            "min": data.get("voltage_min"),
            "max": data.get("voltage_max"),
            "avg": data.get("voltage_avg"),
        }


class ElectroSPowerSensor(_DataSensor):
    """Charging power reported by the charger (field 12)."""

    _attr_icon = "mdi:lightning-bolt"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _data_key = "charging_power_w"

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "power", "charging_power")


class ElectroSEnergySensor(_DataSensor):
    """Energy since the last "Reset kWh" (field 11), usable in the Energy dashboard."""

    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2
    _data_key = "energy_kwh"

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "energy", "energy")


class ElectroSSessionEnergySensor(ElectroSEntity, SensorEntity):
    """Energy of the current/last charging session; device history as attribute."""

    _attr_icon = "mdi:car-electric"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2
    _unrecorded_attributes = frozenset({"history"})

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "session_energy")

    @property
    def native_value(self) -> float | None:
        """kWh of the current/last session."""
        return self.coordinator.session_energy

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Session start and the device's session history (newest first)."""
        started = self.coordinator.stored.session.started_at
        return {
            "started_at": started.isoformat() if started else None,
            "history": list((self.coordinator.data or {}).get("history") or []),
        }


class ElectroSMonthEnergySensor(ElectroSEntity, SensorEntity):
    """Energy of the current month (field 31)."""

    _attr_icon = "mdi:calendar-month"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "month_energy")

    @property
    def native_value(self) -> float | None:
        """kWh of the current month."""
        data = self.coordinator.data or {}
        return protocol.month_energy(data.get("current_month"), data.get("months"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """All 12 months (Jan..Dec, kWh) and the current month number."""
        data = self.coordinator.data or {}
        months = data.get("months")
        return {
            "months": list(months) if months else None,
            "current_month": data.get("current_month"),
        }


class ElectroSBoostEndSensor(ElectroSSettingEntity, SensorEntity):
    """When the current timed boost ends (None when no timed boost is active)."""

    _attr_icon = "mdi:timer-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: ElectroSCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator, "boost_end")

    @property
    def native_value(self) -> datetime | None:
        """End of the timed boost."""
        boost = self.coordinator.boost
        return boost.end if boost is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Boost details and the last start problem."""
        boost = self.coordinator.boost
        return {
            "active": boost is not None,
            "unlimited": bool(boost and boost.unlimited),
            "minutes": boost.minutes if boost is not None else 0,
            "last_error": self.coordinator.last_error,
        }
