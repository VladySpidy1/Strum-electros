"""Config flow and options flow."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from custom_components.electros_ev.const import DOMAIN

from .common import (
    HOST,
    LOGIN,
    PASSWORD,
    FakeCharger,
    async_setup_entry,
    default_entry_data,
    make_entry,
    patch_api,
)

FLOW_API = "custom_components.electros_ev.config_flow.ElectroSApi"


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}
    return result["flow_id"]


async def test_cloud_mode_creates_entry(hass: HomeAssistant, charger: FakeCharger) -> None:
    """Login + password, no host: cloud mode, password stored, connection tested."""
    flow_id = await _start(hass)
    with patch(FLOW_API, return_value=charger) as flow_api, patch_api(charger) as init_api:
        result = await hass.config_entries.flow.async_configure(
            flow_id,
            {"charger_login": f"  {LOGIN} ", "charger_password": PASSWORD, "scan_interval": 90},
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"ElectroS {LOGIN}"
    assert result["data"] == {
        "charger_login": LOGIN,
        "charger_password": PASSWORD,
        "scan_interval": 90,
    }
    assert flow_api.call_args.kwargs == {"login": LOGIN, "password": PASSWORD, "host": None}
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == LOGIN
    assert entry.state is ConfigEntryState.LOADED
    assert init_api.call_args.kwargs == {"login": LOGIN, "password": PASSWORD, "host": None}
    assert entry.runtime_data.update_interval == timedelta(seconds=90)


async def test_local_mode_without_password(hass: HomeAssistant, charger: FakeCharger) -> None:
    """With a host the password is optional and is not stored."""
    flow_id = await _start(hass)
    with patch(FLOW_API, return_value=charger) as flow_api, patch_api(charger) as init_api:
        result = await hass.config_entries.flow.async_configure(
            flow_id, {"charger_login": LOGIN, "host": f" {HOST} "}
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"charger_login": LOGIN, "host": HOST, "scan_interval": 60}
    assert flow_api.call_args.kwargs == {"login": LOGIN, "password": None, "host": HOST}
    assert init_api.call_args.kwargs == {"login": LOGIN, "password": None, "host": HOST}
    assert hass.config_entries.async_entries(DOMAIN)[0].state is ConfigEntryState.LOADED


async def test_password_required_for_cloud(hass: HomeAssistant, charger: FakeCharger) -> None:
    flow_id = await _start(hass)
    with patch(FLOW_API, return_value=charger) as flow_api:
        result = await hass.config_entries.flow.async_configure(
            flow_id, {"charger_login": LOGIN}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"charger_password": "password_required"}
    flow_api.assert_not_called()


async def test_blank_login(hass: HomeAssistant, charger: FakeCharger) -> None:
    flow_id = await _start(hass)
    with patch(FLOW_API, return_value=charger) as flow_api:
        result = await hass.config_entries.flow.async_configure(
            flow_id, {"charger_login": "   ", "charger_password": PASSWORD}
        )
    assert result["errors"] == {"charger_login": "login_required"}
    flow_api.assert_not_called()


@pytest.mark.parametrize("interval", [10, 29, 601])
async def test_scan_interval_out_of_range(hass: HomeAssistant, interval: int) -> None:
    flow_id = await _start(hass)
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            flow_id,
            {"charger_login": LOGIN, "charger_password": PASSWORD, "scan_interval": interval},
        )


async def test_cannot_connect_then_recover(hass: HomeAssistant, charger: FakeCharger) -> None:
    """A failed connection test shows an error; the password is not echoed back."""
    charger.unreachable = True
    flow_id = await _start(hass)
    with patch(FLOW_API, return_value=charger):
        result = await hass.config_entries.flow.async_configure(
            flow_id, {"charger_login": LOGIN, "charger_password": PASSWORD}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    suggested = {
        str(key): key.description.get("suggested_value")
        for key in result["data_schema"].schema
        if key.description
    }
    assert suggested.get("charger_login") == LOGIN
    assert suggested.get("charger_password") is None

    charger.unreachable = False
    with patch(FLOW_API, return_value=charger), patch_api(charger):
        result = await hass.config_entries.flow.async_configure(
            flow_id, {"charger_login": LOGIN, "charger_password": PASSWORD}
        )
        await hass.async_block_till_done(wait_background_tasks=True)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_unexpected_error(hass: HomeAssistant) -> None:
    api = MagicMock()
    api.test_connection = AsyncMock(side_effect=RuntimeError("boom"))
    flow_id = await _start(hass)
    with patch(FLOW_API, return_value=api):
        result = await hass.config_entries.flow.async_configure(
            flow_id, {"charger_login": LOGIN, "charger_password": PASSWORD}
        )
    assert result["errors"] == {"base": "unknown"}


async def test_already_configured(hass: HomeAssistant, charger: FakeCharger) -> None:
    make_entry(hass)
    flow_id = await _start(hass)
    with patch(FLOW_API, return_value=charger) as flow_api:
        result = await hass.config_entries.flow.async_configure(
            flow_id, {"charger_login": LOGIN, "charger_password": PASSWORD}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    flow_api.assert_not_called()


# --------------------------------------------------------------------------- #
# Options flow
# --------------------------------------------------------------------------- #


async def test_options_switch_to_local_host_reloads(
    hass: HomeAssistant, charger: FakeCharger
) -> None:
    """Setting a host tests the connection, saves the options and reloads the entry."""
    entry = await async_setup_entry(hass, charger)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    with patch(FLOW_API, return_value=charger) as flow_api, patch_api(charger) as init_api:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"host": f" {HOST} ", "scan_interval": 120}
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "host": HOST,
        "scan_interval": 120,
        "tariff_night_start": "23:00",
        "tariff_night_end": "07:00",
    }
    assert flow_api.call_args.kwargs == {"login": LOGIN, "password": PASSWORD, "host": HOST}
    # Reloaded with the new settings.
    assert entry.state is ConfigEntryState.LOADED
    assert init_api.call_args.kwargs == {"login": LOGIN, "password": PASSWORD, "host": HOST}
    assert entry.runtime_data.update_interval == timedelta(seconds=120)


async def test_options_interval_only_skips_connection_test(
    hass: HomeAssistant, charger: FakeCharger
) -> None:
    entry = await async_setup_entry(hass, charger)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch(FLOW_API, return_value=charger) as flow_api, patch_api(charger):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 300}
        )
        await hass.async_block_till_done(wait_background_tasks=True)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "host": "",
        "scan_interval": 300,
        "tariff_night_start": "23:00",
        "tariff_night_end": "07:00",
    }
    flow_api.assert_not_called()
    assert entry.runtime_data.update_interval == timedelta(seconds=300)


async def test_options_cannot_connect(hass: HomeAssistant, charger: FakeCharger) -> None:
    entry = await async_setup_entry(hass, charger)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    unreachable = FakeCharger()
    unreachable.unreachable = True
    with patch(FLOW_API, return_value=unreachable):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"host": "10.0.0.99", "scan_interval": 60}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.options == {}


async def test_options_local_entry_cannot_drop_host(
    hass: HomeAssistant, charger: FakeCharger
) -> None:
    """An entry set up without a password cannot switch to the cloud."""
    entry = make_entry(hass, data=default_entry_data(charger_password=None, host=HOST))
    await async_setup_entry(hass, charger, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema_keys = {str(key) for key in result["data_schema"].schema}
    assert schema_keys == {"host", "scan_interval", "tariff_night_start", "tariff_night_end"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"host": "", "scan_interval": 60}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"host": "password_required"}


async def test_options_back_to_cloud(hass: HomeAssistant, charger: FakeCharger) -> None:
    """An entry with a password can clear the host (cloud mode again)."""
    entry = make_entry(hass, data=default_entry_data(host=HOST))
    await async_setup_entry(hass, charger, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch(FLOW_API, return_value=charger) as flow_api, patch_api(charger) as init_api:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"host": "", "scan_interval": 60}
        )
        await hass.async_block_till_done(wait_background_tasks=True)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert flow_api.call_args.kwargs == {"login": LOGIN, "password": PASSWORD, "host": None}
    assert init_api.call_args.kwargs == {"login": LOGIN, "password": PASSWORD, "host": None}


# --------------------------------------------------------------------------- #
# Entry setup errors
# --------------------------------------------------------------------------- #


async def test_cloud_entry_without_password_fails_setup(
    hass: HomeAssistant, charger: FakeCharger
) -> None:
    entry = make_entry(hass, data=default_entry_data(charger_password=None))
    with patch_api(charger) as init_api:
        assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    init_api.assert_not_called()


async def test_unreachable_charger_retries_setup(
    hass: HomeAssistant, charger: FakeCharger
) -> None:
    charger.unreachable = True
    entry = make_entry(hass)
    with patch_api(charger):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert charger.calls == []
