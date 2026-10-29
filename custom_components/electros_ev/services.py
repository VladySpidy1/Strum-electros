"""Services of the ElectroS EV Charger integration."""

from __future__ import annotations

from collections.abc import Callable
import math
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from . import protocol
from .const import (
    ATTR_AMPS,
    ATTR_DEVICE_ID,
    ATTR_ENABLED,
    ATTR_END,
    ATTR_KWH,
    ATTR_MINUTES,
    ATTR_START,
    DOMAIN,
    SERVICE_CHARGE_ENERGY,
    SERVICE_CHARGE_FOR,
    SERVICE_EXTEND_CHARGE,
    SERVICE_MAX_KWH,
    SERVICE_MAX_MINUTES,
    SERVICE_MIN_KWH,
    SERVICE_SET_TIMER,
    SERVICE_STOP_CHARGING,
)

if TYPE_CHECKING:
    from .coordinator import ElectroSCoordinator


class InvalidServiceData(ServiceValidationError, vol.MultipleInvalid):
    """Bad service data.

    A ServiceValidationError (shown to the user as a validation problem) that is
    also a voluptuous ``MultipleInvalid``, which Home Assistant re-raises
    unchanged from a service schema.
    """

    def __init__(self, err: vol.Invalid) -> None:
        """Wrap a voluptuous error."""
        message = str(err)
        super().__init__(
            message,
            translation_domain=DOMAIN,
            translation_key="invalid_input",
            translation_placeholders={"error": message},
        )
        self.errors = (
            list(err.errors) if isinstance(err, vol.MultipleInvalid) else [err]
        )


def _validated(schema: Callable[[Any], Any]) -> Callable[[Any], Any]:
    """Run a voluptuous schema, raising InvalidServiceData on bad input."""

    def _validate(data: Any) -> Any:
        try:
            return schema(data)
        except vol.Invalid as err:
            raise InvalidServiceData(err) from err

    return _validate


def _hhmm(value: Any) -> str:
    """Accept HH:MM (or HH:MM:SS from the time selector); return HH:MM."""
    try:
        return protocol.parse_hhmm(value)
    except ValueError as err:
        raise vol.Invalid("expected a time as HH:MM") from err


def _finite(value: float) -> float:
    if not math.isfinite(value):
        raise vol.Invalid("expected a finite number")
    return value


_MINUTES = vol.All(vol.Coerce(int), vol.Range(min=1, max=SERVICE_MAX_MINUTES))
_KWH = vol.All(
    vol.Coerce(float), _finite, vol.Range(min=SERVICE_MIN_KWH, max=SERVICE_MAX_KWH)
)
_AMPS = vol.All(vol.Coerce(int), vol.Range(min=protocol.MIN_AMPS, max=protocol.MAX_AMPS))

CHARGE_FOR_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_MINUTES): _MINUTES,
        vol.Optional(ATTR_AMPS): _AMPS,
    }
)
CHARGE_ENERGY_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_KWH): _KWH,
        vol.Optional(ATTR_AMPS): _AMPS,
    }
)
EXTEND_CHARGE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Optional(ATTR_MINUTES): _MINUTES,
            vol.Optional(ATTR_KWH): _KWH,
        }
    ),
    cv.has_at_least_one_key(ATTR_MINUTES, ATTR_KWH),
    cv.has_at_most_one_key(ATTR_MINUTES, ATTR_KWH),
)
STOP_CHARGING_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string})
SET_TIMER_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Required(ATTR_DEVICE_ID): cv.string,
            vol.Optional(ATTR_START): _hhmm,
            vol.Optional(ATTR_END): _hhmm,
            vol.Optional(ATTR_ENABLED): cv.boolean,
        }
    ),
    cv.has_at_least_one_key(ATTR_START, ATTR_END, ATTR_ENABLED),
)


def _coordinator_for(hass: HomeAssistant, device_id: str) -> ElectroSCoordinator:
    """Resolve a device id of this integration to its coordinator."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_device",
            translation_placeholders={"device_id": device_id},
        )
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            continue
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
                translation_placeholders={"device_id": device_id},
            )
        return entry.runtime_data
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="invalid_device",
        translation_placeholders={"device_id": device_id},
    )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration services (once)."""
    if hass.services.has_service(DOMAIN, SERVICE_CHARGE_FOR):
        return

    async def _charge_for(call: ServiceCall) -> None:
        coordinator = _coordinator_for(hass, call.data[ATTR_DEVICE_ID])
        amps = call.data.get(ATTR_AMPS)
        if amps is not None:
            amps = protocol.normalize_amps(amps)
        await coordinator.async_charge_for(call.data[ATTR_MINUTES], amps)

    async def _charge_energy(call: ServiceCall) -> None:
        coordinator = _coordinator_for(hass, call.data[ATTR_DEVICE_ID])
        amps = call.data.get(ATTR_AMPS)
        if amps is not None:
            amps = protocol.normalize_amps(amps)
        await coordinator.async_charge_energy(call.data[ATTR_KWH], amps)

    async def _extend_charge(call: ServiceCall) -> None:
        coordinator = _coordinator_for(hass, call.data[ATTR_DEVICE_ID])
        await coordinator.async_extend_charge(
            minutes=call.data.get(ATTR_MINUTES), kwh=call.data.get(ATTR_KWH)
        )

    async def _stop_charging(call: ServiceCall) -> None:
        coordinator = _coordinator_for(hass, call.data[ATTR_DEVICE_ID])
        await coordinator.async_stop_charging()

    async def _set_timer(call: ServiceCall) -> None:
        coordinator = _coordinator_for(hass, call.data[ATTR_DEVICE_ID])
        await coordinator.async_set_timer(
            start=call.data.get(ATTR_START),
            end=call.data.get(ATTR_END),
            enabled=call.data.get(ATTR_ENABLED),
        )

    hass.services.async_register(
        DOMAIN, SERVICE_CHARGE_FOR, _charge_for, schema=_validated(CHARGE_FOR_SCHEMA)
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CHARGE_ENERGY, _charge_energy, schema=_validated(CHARGE_ENERGY_SCHEMA)
    )
    hass.services.async_register(
        DOMAIN, SERVICE_EXTEND_CHARGE, _extend_charge, schema=_validated(EXTEND_CHARGE_SCHEMA)
    )
    hass.services.async_register(
        DOMAIN, SERVICE_STOP_CHARGING, _stop_charging, schema=_validated(STOP_CHARGING_SCHEMA)
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SET_TIMER, _set_timer, schema=_validated(SET_TIMER_SCHEMA)
    )
