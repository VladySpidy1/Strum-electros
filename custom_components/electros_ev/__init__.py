"""ElectroS EV Charger integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import protocol
from .charger_api import ElectroSApi
from .const import (
    CONF_CHARGER_LOGIN,
    CONF_CHARGER_PASSWORD,
    CONF_HOST,
    CONF_SCAN_INTERVAL,
    CONF_TARIFF_NIGHT_END,
    CONF_TARIFF_NIGHT_START,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TARIFF_NIGHT_END,
    DEFAULT_TARIFF_NIGHT_START,
    DOMAIN,
)
from .coordinator import ElectroSCoordinator
from .frontend import async_register_card
from .services import async_setup_services
from .storage import create_store

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.BUTTON,
    Platform.TIME,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

ElectroSConfigEntry = ConfigEntry[ElectroSCoordinator]


def entry_option(entry: ConfigEntry, key: str, default=None):
    """Read a setting: options override the initial data."""
    if key in entry.options:
        return entry.options[key]
    return entry.data.get(key, default)


def entry_hhmm_option(entry: ConfigEntry, key: str, default: str) -> str:
    """An HH:MM option (the time selector stores HH:MM:SS); default if invalid."""
    try:
        return protocol.parse_hhmm(entry_option(entry, key, default))
    except ValueError:
        return default


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up services and the Lovelace card (once per Home Assistant run)."""
    async_setup_services(hass)
    await async_register_card(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ElectroSConfigEntry) -> bool:
    """Set up ElectroS EV Charger from a config entry."""
    login = entry.data[CONF_CHARGER_LOGIN]
    password = entry.data.get(CONF_CHARGER_PASSWORD) or None
    host = str(entry_option(entry, CONF_HOST) or "").strip() or None
    scan_interval = int(
        entry_option(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        or DEFAULT_SCAN_INTERVAL
    )
    if host is None and password is None:
        raise ConfigEntryError(
            "Cloud mode needs the charger password; set a local host in the options"
        )

    tariff_window = (
        entry_hhmm_option(entry, CONF_TARIFF_NIGHT_START, DEFAULT_TARIFF_NIGHT_START),
        entry_hhmm_option(entry, CONF_TARIFF_NIGHT_END, DEFAULT_TARIFF_NIGHT_END),
    )

    api = ElectroSApi(login=login, password=password, host=host)
    coordinator = ElectroSCoordinator(hass, entry, api, scan_interval, tariff_window)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ElectroSConfigEntry) -> None:
    """Reload when the options (host, scan interval, tariff zone) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ElectroSConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_unload()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the persisted state when the entry is removed."""
    await create_store(hass, entry.entry_id).async_remove()
