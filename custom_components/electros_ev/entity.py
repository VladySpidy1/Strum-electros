"""Base entity for ElectroS EV Charger."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ElectroSCoordinator


def device_info(login: str) -> DeviceInfo:
    """One device per config entry, identified by the charger login."""
    return DeviceInfo(
        identifiers={(DOMAIN, login)},
        name=f"ElectroS {login}",
        manufacturer="ElectroS",
        model="EV Charger",
    )


class ElectroSEntity(CoordinatorEntity[ElectroSCoordinator]):
    """Entity bound to the charger device; names come from translations."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ElectroSCoordinator,
        translation_key: str,
        unique_suffix: str | None = None,
    ) -> None:
        """Initialize.

        ``unique_suffix`` keeps the unique_id of entities that existed before
        v2.0.0; new entities use ``{login}_{translation_key}``.
        """
        super().__init__(coordinator)
        self._attr_translation_key = translation_key
        self._attr_unique_id = f"{coordinator.login}_{unique_suffix or translation_key}"
        self._attr_device_info = device_info(coordinator.login)


class ElectroSSettingEntity(ElectroSEntity):
    """Entity backed by HA-side settings: available even if a poll failed."""

    @property
    def available(self) -> bool:
        """Settings can be changed while the charger is unreachable."""
        return True
