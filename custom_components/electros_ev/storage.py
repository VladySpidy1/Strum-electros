"""Persisted per-entry state (homeassistant.helpers.storage.Store)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

from . import protocol
from .const import (
    DEFAULT_BOOST_MINUTES,
    DEFAULT_TIMER_END,
    DEFAULT_TIMER_START,
    MAX_BOOST_MINUTES,
    MIN_BOOST_MINUTES,
)

STORAGE_VERSION = 1


def storage_key(entry_id: str) -> str:
    """Store key of a config entry."""
    return f"electros_ev.{entry_id}"


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


@dataclass
class BoostState:
    """An active "charge now" that bypasses the device timer.

    ``end`` is None for an unlimited manual charge (bypass until charging stops).
    ``restore`` records that HA disabled the device timer for this boost.
    """

    end: datetime | None
    unlimited: bool = False
    minutes: int = 0
    restore: bool = False
    started_at: datetime | None = None
    charging_seen: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "end": _dt_to_str(self.end),
            "unlimited": self.unlimited,
            "minutes": self.minutes,
            "restore": self.restore,
            "started_at": _dt_to_str(self.started_at),
            "charging_seen": self.charging_seen,
        }

    @classmethod
    def from_dict(cls, data: Any) -> BoostState | None:
        """Deserialize; None if there is no valid boost."""
        if not isinstance(data, dict):
            return None
        end = _dt_from_str(data.get("end"))
        unlimited = bool(data.get("unlimited", False))
        if end is None and not unlimited:
            return None
        try:
            minutes = max(0, int(data.get("minutes") or 0))
        except (TypeError, ValueError):
            minutes = 0
        return cls(
            end=None if unlimited else end,
            unlimited=unlimited,
            minutes=minutes,
            restore=bool(data.get("restore", False)),
            started_at=_dt_from_str(data.get("started_at")),
            charging_seen=bool(data.get("charging_seen", False)),
        )


@dataclass
class SessionState:
    """Energy of the current/last charging session, tracked by HA."""

    active: bool = False
    started_at: datetime | None = None
    kwh: float = 0.0
    last_energy: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialize."""
        return {
            "active": self.active,
            "started_at": _dt_to_str(self.started_at),
            "kwh": self.kwh,
            "last_energy": self.last_energy,
        }

    @classmethod
    def from_dict(cls, data: Any) -> SessionState:
        """Deserialize."""
        if not isinstance(data, dict):
            return cls()
        try:
            kwh = float(data.get("kwh") or 0.0)
        except (TypeError, ValueError):
            kwh = 0.0
        last = data.get("last_energy")
        try:
            last_energy = float(last) if last is not None else None
        except (TypeError, ValueError):
            last_energy = None
        return cls(
            active=bool(data.get("active", False)),
            started_at=_dt_from_str(data.get("started_at")),
            kwh=kwh,
            last_energy=last_energy,
        )


@dataclass
class ElectroSStoredState:
    """Everything the integration keeps across restarts for one charger."""

    night_enabled: bool = False
    timer_start: str = DEFAULT_TIMER_START
    timer_end: str = DEFAULT_TIMER_END
    weekend_free: bool = False
    boost_minutes: int = DEFAULT_BOOST_MINUTES
    charging_amps: int | None = None
    boost: BoostState | None = None
    #: Window HA last wrote to (or agreed with on) the device:
    #: "HH:MM-HH:MM", "" = disabled, None = unknown.
    last_written: str | None = None
    #: False until the settings were initialized from the device (first run).
    seeded: bool = False
    session: SessionState = field(default_factory=SessionState)

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
            "charging_amps": self.charging_amps,
            "boost": self.boost.as_dict() if self.boost else None,
            "last_written": self.last_written,
            "seeded": self.seeded,
            "session": self.session.as_dict(),
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
        amps_raw = data.get("charging_amps")
        amps = protocol.normalize_amps(amps_raw) if amps_raw is not None else 0
        last_written = data.get("last_written")
        if last_written is not None and not isinstance(last_written, str):
            last_written = None
        if last_written:
            last_written = protocol.window_to_str(protocol.window_from_str(last_written))
        return cls(
            night_enabled=bool(data.get("night_enabled", False)),
            timer_start=_hhmm_or(data.get("timer_start"), DEFAULT_TIMER_START),
            timer_end=_hhmm_or(data.get("timer_end"), DEFAULT_TIMER_END),
            weekend_free=bool(data.get("weekend_free", False)),
            boost_minutes=boost_minutes,
            charging_amps=amps or None,
            boost=BoostState.from_dict(data.get("boost")),
            last_written=last_written,
            seeded=bool(data.get("seeded", False)),
            session=SessionState.from_dict(data.get("session")),
        )
