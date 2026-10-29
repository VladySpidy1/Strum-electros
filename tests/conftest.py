"""Fixtures for the Home Assistant level tests.

Without pytest-homeassistant-custom-component (plain Python), only
tests/test_protocol.py is collected; the other test modules are skipped.
"""

from __future__ import annotations

from collections.abc import Generator
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

HAS_HA_HARNESS = importlib.util.find_spec("pytest_homeassistant_custom_component") is not None

#: Test modules of the pure (stdlib-only) modules; they run without Home Assistant.
PURE_TEST_MODULES = {"test_protocol.py", "test_tariff.py"}

if not HAS_HA_HARNESS:
    collect_ignore = [
        path.name
        for path in Path(__file__).parent.glob("test_*.py")
        if path.name not in PURE_TEST_MODULES
    ]
else:
    from homeassistant.core import HomeAssistant

    from custom_components.electros_ev import charger_api, coordinator as coordinator_module

    from .common import FakeCharger

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
        """Load custom_components/electros_ev in every test."""

    @pytest.fixture(autouse=True)
    def no_web_frontend(hass: HomeAssistant) -> None:
        """Treat the http and frontend dependencies as set up, without setting them up.

        The integration depends on http and frontend (for its Lovelace card).
        Setting up the real frontend needs the home-assistant-frontend wheel
        (hass_frontend) and would start a web server; the tests need neither.
        With both marked as loaded, Home Assistant skips their setup, and
        ``async_register_card`` sees no HTTP server and registers nothing
        (test_entities.py tests the registration with a mocked ``hass.http``).
        """
        hass.config.components.add("http")
        hass.config.components.add("frontend")

    @pytest.fixture(autouse=True)
    def no_network() -> Generator[None]:
        """Fail loudly if anything in the integration tries to reach the network.

        Tests of the transport patch ``asyncio.open_connection`` themselves with a
        fake connection, which takes precedence inside the test.
        """
        refuse = AsyncMock(side_effect=AssertionError("network access in tests"))
        with patch.object(charger_api.asyncio, "open_connection", refuse), patch.object(
            charger_api.ElectroSApi,
            "_login_session",
            AsyncMock(side_effect=AssertionError("cloud login in tests")),
        ):
            yield

    @pytest.fixture(autouse=True)
    def fast_command_delays() -> Generator[None]:
        """No real waiting between timer writes, commands and refreshes."""
        with patch.object(coordinator_module, "COMMAND_SETTLE_DELAY", 0), patch.object(
            coordinator_module, "TIMER_SETTLE_DELAY", 0
        ):
            yield

    @pytest.fixture
    async def kyiv(hass: HomeAssistant) -> None:
        """Run the test in the charger's real time zone (Europe/Kyiv)."""
        await hass.config.async_set_time_zone("Europe/Kyiv")

    @pytest.fixture
    def charger(kyiv: None) -> FakeCharger:
        """The simulated charger (patched in as ElectroSApi by the setup helper)."""
        return FakeCharger()
