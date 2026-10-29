"""Persisted per-entry state (homeassistant.helpers.storage.Store).

Schema history:

* 1 (v2.0.0): settings, boost, last_written, seeded, session.
* 2 (v2.1.0): energy boosts (boost.target_kwh/delivered_kwh/deadline/amps),
  boost_kwh, price_day/price_night, tariff totals, session cost, stop_pending.
  Version 1 data is migrated by :class:`ElectroSStore`; every setting is kept
  and the new values start at their defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import protocol, tariff
from .const import (
    DEFAULT_BOOST_KWH,
    DEFAULT_BOOST_MINUTES,
    DEFAULT_PRICE_DAY,
    DEFAULT_PRICE_NIGHT,
    DEFAULT_TIMER_END,
    DEFAULT_TIMER_START,
    MAX_BOOST_KWH,
    MAX_BOOST_MINUTES,
    MAX_PRICE,
    MIN_BOOST_KWH,
    MIN_BOOST_MINUTES,
    MIN_PRICE,
)

STORAGE_VERSION = 2

BOOST_MODE_TIME = "time"
BOOST_MODE_ENERGY = "energy"
BOOST_MODE_UNLIMITED = "unlimited"

#: ``stop_pending``: a stop HA sent (or had to send) did not reach the charger.
#: End of a timed boost: void once the restored night window allows charging.
STOP_PENDING_UNLESS_WINDOW = "unless_window"
#: End of an energy boost (target or deadline), or a stop the user asked for.
STOP_PENDING_ALWAYS = "always"
STOP_PENDING_KINDS = (STOP_PENDING_UNLESS_WINDOW, STOP_PENDING_ALWAYS)


def storage_key(entry_id: str) -> str:
    """Store key of a config entry."""
    return f"electros_ev.{entry_id}"


def migrate_stored_data(old_version: int, data: Any) -> dict[str, Any]:
    """Bring stored data of any older schema to the current one.

    Every schema change so far only added values, so the data is normalized
    through :class:`ElectroSStoredState`: known values are kept, missing ones get
    their defaults (version 1 -> 2: boost_kwh, prices, tariff totals, session
    cost; ``session.last_energy`` is dropped, the tariff counter takes over).
    """
    return ElectroSStoredState.from_dict(data).as_dict()


class ElectroSStore(Store[dict[str, Any]]):
    """Store with the schema migration of this integration."""

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        """Migrate older (or, after a downgrade, newer) data."""
        return migrate_stored_data(old_major_version, old_data)


def create_store(hass: HomeAssistant, entry_id: str) -> ElectroSStore:
    """The Store of a config entry."""
    return ElectroSStore(hass, STORAGE_VERSION, storage_key(entry_id))


def _dt_to_str(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _dt_from_str(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.UTC)
    return dt_util.as_utc(parsed)


def _hhmm_or(value: Any, default: str) -> str:
    try:
        return protocol.parse_hhmm(value)
    except ValueError:
        return default


def _float_or(value: Any, default: float | None) -> float | None:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _kwh_total(value: Any) -> float:
    number = _float_or(value, 0.0) or 0.0
    return max(0.0, number)


def _price_or(value: Any, default: float) -> float:
    number = _float_or(value, None)
    if number is None or not MIN_PRICE <= number <= MAX_PRICE:
        return default
    return number


@dataclass
class BoostState:
    """An active "charge now" that bypasses the device timer.

    Modes:

    * time: ``end`` is the end of a "charge for N minutes";
    * energy: ``target_kwh`` set, ``end`` None; ends when ``delivered_kwh``
      reaches the target or at ``deadline``;
    * unlimited: ``end`` None, bypass until charging stops.

    ``restore`` records that HA disabled the device timer for this boost.
    """

    end: datetime | None
    unlimited: bool = False
    minutes: int = 0
    restore: bool = False
    started_at: datetime | None = None
    charging_seen: bool = False
    target_kwh: float | None = None
    delivered_kwh: float = 0.0
    deadline: datetime | None = None
    amps: int | None = None

    @property
    def mode(self) -> str:
        """``time``, ``energy`` or ``unlimited``."""
        if self.unlimited:
            return BOOST_MODE_UNLIMITED
        if self.target_kwh is not None:
            return BOOST_MODE_ENERGY
        return BOOST_MODE_TIME

    @property
    def is_energy(self) -> bool:
        """Energy-target boost."""
        return self.mode == BOOST_MODE_ENERGY

    @property
    def is_timed(self) -> bool:
        """Timed boost."""
        return self.mode == BOOST_MODE_TIME

    @property
    def expires_at(self) -> datetime | None:
        """When the scheduled callback ends it: the end (time) or the deadline (energy)."""
        if self.is_timed:
            return self.end
        if self.is_energy:
            return self.deadline
        return None

    @property
    def remaining_kwh(self) -> float:
        """kWh still to deliver (energy mode; 0 otherwise)."""
        if self.target_kwh is None:
            return 0.0
        return max(0.0, self.target_kwh - self.delivered_kwh)

    @property
    def target_reached(self) -> bool:
        """Energy mode: delivered >= target."""
        return self.target_kwh is not None and self.delivered_kwh >= self.target_kwh - 1e-9

    def as_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "end": _dt_to_str(self.end),
            "unlimited": self.unlimited,
            "minutes": self.minutes,
            "restore": self.restore,
            "started_at": _dt_to_str(self.started_at),
            "charging_seen": self.charging_seen,
            "target_kwh": self.target_kwh,
            "delivered_kwh": self.delivered_kwh,
            "deadline": _dt_to_str(self.deadline),
            "amps": self.amps,
        }

    @classmethod
    def from_dict(cls, data: Any) -> BoostState | None:
        """Deserialize; None if there is no valid boost."""
        if not isinstance(data, dict):
            return None
        end = _dt_from_str(data.get("end"))
        unlimited = bool(data.get("unlimited", False))
        target = _float_or(data.get("target_kwh"), None)
        if target is not None and target <= 0:
            target = None
        if unlimited:
            target = None
        elif target is None and end is None:
            return None
        try:
            minutes = max(0, int(data.get("minutes") or 0))
        except (TypeError, ValueError):
            minutes = 0
        amps_raw = data.get("amps")
        amps = protocol.normalize_amps(amps_raw) if amps_raw is not None else 0
        started_at = _dt_from_str(data.get("started_at"))
        deadline = _dt_from_str(data.get("deadline")) if target is not None else None
        if target is not None and deadline is None:
            deadline = tariff.energy_deadline(
                started_at or dt_util.utcnow(), target, amps or None
            )
        return cls(
            end=None if unlimited or target is not None else end,
            unlimited=unlimited,
            minutes=minutes,
            restore=bool(data.get("restore", False)),
            started_at=started_at,
            charging_seen=bool(data.get("charging_seen", False)),
            target_kwh=target,
            delivered_kwh=_kwh_total(data.get("delivered_kwh")) if target is not None else 0.0,
            deadline=deadline,
            amps=amps or None,
        )


@dataclass
class SessionState:
    """Energy (and its cost) of the current/last charging session, tracked by HA."""

    active: bool = False
    started_at: datetime | None = None
    kwh: float = 0.0
    cost: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "active": self.active,
            "started_at": _dt_to_str(self.started_at),
            "kwh": self.kwh,
            "cost": self.cost,
        }

    @classmethod
    def from_dict(cls, data: Any) -> SessionState:
        """Deserialize."""
        if not isinstance(data, dict):
            return cls()
        return cls(
            active=bool(data.get("active", False)),
            started_at=_dt_from_str(data.get("started_at")),
            kwh=_kwh_total(data.get("kwh")),
            cost=_kwh_total(data.get("cost")),
        )


@dataclass
class TariffState:
    """Running two-zone totals and the last counter sample they were taken from."""

    day_kwh: float = 0.0
    night_kwh: float = 0.0
    cost: float = 0.0
    last_energy: float | None = None
    last_sample_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "day_kwh": self.day_kwh,
            "night_kwh": self.night_kwh,
            "cost": self.cost,
            "last_energy": self.last_energy,
            "last_sample_at": _dt_to_str(self.last_sample_at),
        }

    @classmethod
    def from_dict(cls, data: Any) -> TariffState:
        """Deserialize."""
        if not isinstance(data, dict):
            return cls()
        last_energy = _float_or(data.get("last_energy"), None)
        return cls(
            day_kwh=_kwh_total(data.get("day_kwh")),
            night_kwh=_kwh_total(data.get("night_kwh")),
            cost=_kwh_total(data.get("cost")),
            last_energy=last_energy if last_energy is None or last_energy >= 0 else None,
            last_sample_at=_dt_from_str(data.get("last_sample_at")),
        )


@dataclass
class ElectroSStoredState:
    """Everything the integration keeps across restarts for one charger."""

    night_enabled: bool = False
    timer_start: str = DEFAULT_TIMER_START
    timer_end: str = DEFAULT_TIMER_END
    weekend_free: bool = False
    boost_minutes: int = DEFAULT_BOOST_MINUTES
    boost_kwh: int = DEFAULT_BOOST_KWH
    charging_amps: int | None = None
    boost: BoostState | None = None
    #: Window HA last wrote to (or agreed with on) the device:
    #: "HH:MM-HH:MM", "" = disabled, None = unknown.
    last_written: str | None = None
    #: False until the settings were initialized from the device (first run).
    seeded: bool = False
    session: SessionState = field(default_factory=SessionState)
    price_day: float = DEFAULT_PRICE_DAY
    price_night: float = DEFAULT_PRICE_NIGHT
    tariff: TariffState = field(default_factory=TariffState)
    #: A stop that did not reach the charger (failed, or the charger was
    #: offline): retried while it still charges. ``STOP_PENDING_*`` or None.
    stop_pending: str | None = None

    @property
    def last_written_window(self) -> protocol.Window | None:
        """Decoded ``last_written`` (None also when unknown; check ``last_written``)."""
        return protocol.window_from_str(self.last_written)

    def as_dict(self) -> dict[str, Any]:
        """Serialize for the Store."""
        return {
            "night_enabled": self.night_enabled,
            "timer_start": self.timer_start,
            "timer_end": self.timer_end,
            "weekend_free": self.weekend_free,
            "boost_minutes": self.boost_minutes,
            "boost_kwh": self.boost_kwh,
            "charging_amps": self.charging_amps,
            "boost": self.boost.as_dict() if self.boost else None,
            "last_written": self.last_written,
            "seeded": self.seeded,
            "session": self.session.as_dict(),
            "price_day": self.price_day,
            "price_night": self.price_night,
            "tariff": self.tariff.as_dict(),
            "stop_pending": self.stop_pending,
        }

    @classmethod
    def from_dict(cls, data: Any) -> ElectroSStoredState:
        """Deserialize, falling back to defaults for anything invalid."""
        if not isinstance(data, dict):
            return cls()
        try:
            boost_minutes = int(data.get("boost_minutes", DEFAULT_BOOST_MINUTES))
        except (TypeError, ValueError):
            boost_minutes = DEFAULT_BOOST_MINUTES
        boost_minutes = min(MAX_BOOST_MINUTES, max(MIN_BOOST_MINUTES, boost_minutes))
        try:
            boost_kwh = int(data.get("boost_kwh", DEFAULT_BOOST_KWH))
        except (TypeError, ValueError):
            boost_kwh = DEFAULT_BOOST_KWH
        boost_kwh = min(MAX_BOOST_KWH, max(MIN_BOOST_KWH, boost_kwh))
        amps_raw = data.get("charging_amps")
        amps = protocol.normalize_amps(amps_raw) if amps_raw is not None else 0
        last_written = data.get("last_written")
        if last_written is not None and not isinstance(last_written, str):
            last_written = None
        if last_written:
            last_written = protocol.window_to_str(protocol.window_from_str(last_written))
        stop_pending = data.get("stop_pending")
        if stop_pending not in STOP_PENDING_KINDS:
            # Unknown but set: the safe reading is "stop while it still charges".
            stop_pending = STOP_PENDING_ALWAYS if stop_pending else None
        return cls(
            night_enabled=bool(data.get("night_enabled", False)),
            timer_start=_hhmm_or(data.get("timer_start"), DEFAULT_TIMER_START),
            timer_end=_hhmm_or(data.get("timer_end"), DEFAULT_TIMER_END),
            weekend_free=bool(data.get("weekend_free", False)),
            boost_minutes=boost_minutes,
            boost_kwh=boost_kwh,
            charging_amps=amps or None,
            boost=BoostState.from_dict(data.get("boost")),
            last_written=last_written,
            seeded=bool(data.get("seeded", False)),
            session=SessionState.from_dict(data.get("session")),
            price_day=_price_or(data.get("price_day"), DEFAULT_PRICE_DAY),
            price_night=_price_or(data.get("price_night"), DEFAULT_PRICE_NIGHT),
            tariff=TariffState.from_dict(data.get("tariff")),
            stop_pending=stop_pending,
        )
