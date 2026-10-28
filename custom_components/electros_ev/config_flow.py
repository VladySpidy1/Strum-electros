"""Config and options flow for ElectroS EV Charger integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .charger_api import ElectroSApi
from .const import (
    CONF_CHARGER_LOGIN,
    CONF_CHARGER_PASSWORD,
    CONF_HOST,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

_SCAN_INTERVAL = vol.All(int, vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL))
_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CHARGER_LOGIN): str,
        vol.Optional(CONF_CHARGER_PASSWORD): _PASSWORD,
        vol.Optional(CONF_HOST): str,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): _SCAN_INTERVAL,
    }
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


async def _async_can_connect(login: str, password: str, host: str) -> bool:
    api = ElectroSApi(login=login, password=password or None, host=host or None)
    return await api.test_connection()


class ElectroSConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ElectroS EV Charger."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ElectroSOptionsFlow:
        """Options: local host and scan interval."""
        return ElectroSOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step.

        The password is only needed for the cloud; with a local host it is optional.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            login = _clean(user_input.get(CONF_CHARGER_LOGIN))
            password = user_input.get(CONF_CHARGER_PASSWORD) or ""
            host = _clean(user_input.get(CONF_HOST))
            scan_interval = user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

            if not login:
                errors[CONF_CHARGER_LOGIN] = "login_required"
            elif not host and not password:
                errors[CONF_CHARGER_PASSWORD] = "password_required"
            else:
                await self.async_set_unique_id(login)
                self._abort_if_unique_id_configured()
                try:
                    if await _async_can_connect(login, password, host):
                        data: dict[str, Any] = {
                            CONF_CHARGER_LOGIN: login,
                            CONF_SCAN_INTERVAL: scan_interval,
                        }
                        if password:
                            data[CONF_CHARGER_PASSWORD] = password
                        if host:
                            data[CONF_HOST] = host
                        return self.async_create_entry(
                            title=f"ElectroS {login}", data=data
                        )
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Unexpected error during config flow")
                    errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_DATA_SCHEMA,
                {
                    key: value
                    for key, value in (user_input or {}).items()
                    if key != CONF_CHARGER_PASSWORD
                },
            ),
            errors=errors,
        )


class ElectroSOptionsFlow(OptionsFlow):
    """Change the local host and the scan interval (the entry reloads)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        entry = self.config_entry
        errors: dict[str, str] = {}
        current_host = _clean(entry.options.get(CONF_HOST, entry.data.get(CONF_HOST)))
        current_interval = entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )

        if user_input is not None:
            host = _clean(user_input.get(CONF_HOST))
            scan_interval = user_input.get(CONF_SCAN_INTERVAL, current_interval)
            password = entry.data.get(CONF_CHARGER_PASSWORD) or ""
            if not host and not password:
                errors[CONF_HOST] = "password_required"
            else:
                ok = True
                if host != current_host:
                    try:
                        ok = await _async_can_connect(
                            entry.data[CONF_CHARGER_LOGIN], password, host
                        )
                    except Exception:  # noqa: BLE001
                        _LOGGER.exception("Unexpected error during options flow")
                        ok = False
                if ok:
                    return self.async_create_entry(
                        data={CONF_HOST: host, CONF_SCAN_INTERVAL: scan_interval}
                    )
                errors["base"] = "cannot_connect"

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_HOST,
                    description={"suggested_value": current_host},
                ): str,
                vol.Optional(CONF_SCAN_INTERVAL, default=current_interval): _SCAN_INTERVAL,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
