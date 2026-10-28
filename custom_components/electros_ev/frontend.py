"""Serve and register the ElectroS Lovelace card (electros-card / electros-tile)."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend as ha_frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import CARD_FILENAME, CARD_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

CARD_PATH = Path(__file__).parent / "frontend" / CARD_FILENAME
_DATA_CARD_REGISTERED = f"{DOMAIN}_card_registered"


async def async_register_card(hass: HomeAssistant) -> None:
    """Register /electros_ev/electros-card.js and load it on every dashboard.

    Runs once per Home Assistant run; the ``?v=<version>`` suffix busts the
    browser cache after an update.
    """
    if hass.data.get(_DATA_CARD_REGISTERED):
        return
    if getattr(hass, "http", None) is None:
        _LOGGER.debug("HTTP server not available; ElectroS card not registered")
        return
    hass.data[_DATA_CARD_REGISTERED] = True

    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), cache_headers=False)]
    )

    integration = await async_get_integration(hass, DOMAIN)
    version = str(integration.version or "0")
    try:
        ha_frontend.add_extra_js_url(hass, f"{CARD_URL}?v={version}")
    except KeyError:  # frontend not loaded (e.g. minimal test setups)
        _LOGGER.debug("Frontend not loaded; ElectroS card URL not added")
