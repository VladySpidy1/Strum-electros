"""Coordinator and timer/boost engine for ElectroS EV Charger.

Polling (DataUpdateCoordinator) plus the logic that owns the charger's daily
timer window:

* The device timer is written ONLY on transitions: user changes (night timer,
  times, weekend_free, ``set_timer``), boost start/stop/expiry, the daily
  weekend-boundary check at 00:00:05 and once after startup if the device
  still has what HA last wrote but HA wants something else. Never on a plain poll.
* If a poll shows a window that differs from what HA last wrote, at least
  ``ADOPT_GRACE`` seconds after HA's last write and with no boost active, the
  user changed it in the electros web panel: HA adopts it instead of fighting.
  The same applies at startup (changed while HA was stopped).
* A boost disables the timer, starts charging and is ended by HA; the timer is
  then restored (reconciled):

  - timed ("charge for N minutes"): a scheduled callback at the end. If the
    restored night window allows charging at that moment, charging is not
    stopped: the schedule simply continues.
  - energy ("charge N kWh"): ends with a stop when the energy delivered since
    the start reaches the target (positive counter deltas, so a counter reset
    does not end it), or at a safety deadline (stop + ``last_error``). Polls run
    every ``ENERGY_BOOST_SCAN_INTERVAL`` seconds meanwhile.
  - unlimited: a manual start while the timer blocks charging (or ∞ during a
    timed/energy boost); ends when charging stops.

* While the last status says the charger is offline (7/77), timer writes and
  stops are not sent: the cloud would take them, but they never reach the
  charger. The first online status afterwards reconciles the timer instead of
  adopting the window the charger reports (nobody could change it in the web
  panel meanwhile, and HA's writes may have missed it).
* A stop that did not reach the charger (failed, or sent to an offline
  charger) is persisted as ``stop_pending`` and retried on every online poll
  while the charger still charges; ``last_error`` is ``stop_failed`` meanwhile.

* Two-zone tariff: every successful poll adds the counter delta to the day or
  night total (tariff.py) and to the cumulative cost.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_utc_time,
    async_track_time_change,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import protocol, tariff
from .charger_api import ElectroSApi
from .const import (
    CONF_CHARGER_LOGIN,
    DEFAULT_CHARGING_AMPS,
    DEFAULT_TARIFF_NIGHT_END,
    DEFAULT_TARIFF_NIGHT_START,
    DOMAIN,
    ENERGY_BOOST_SCAN_INTERVAL,
    MAX_BOOST_KWH,
    MAX_BOOST_MINUTES,
    MAX_PRICE,
    MIN_BOOST_KWH,
    MIN_BOOST_MINUTES,
    MIN_PRICE,
)
from .storage import (
    STOP_PENDING_ALWAYS,
    STOP_PENDING_UNLESS_WINDOW,
    BoostState,
    ElectroSStoredState,
    create_store,
)

_LOGGER = logging.getLogger(__name__)

COMMAND_SETTLE_DELAY = 3  # s after a command before refreshing
TIMER_SETTLE_DELAY = 3  # s between disabling the timer and sending start
FOLLOWUP_REFRESH_DELAY = 10  # s, one more refresh after a command
ADOPT_GRACE = 120  # s after HA's own timer write during which polls never adopt
START_CHECK_WINDOW = 120  # s after a start during which status 4 is reported
UNLIMITED_START_GRACE = timedelta(minutes=15)  # unlimited bypass that never charged
# s, coalesced saves for frequently changing values (session, tariff, energy
# boost). Shorter than the fastest poll: Store.async_delay_save is a debounce.
SAVE_DELAY = 10

ERROR_CHARGING_BLOCKED = "charging_blocked"
ERROR_ENERGY_DEADLINE = "energy_deadline"
ERROR_STOP_FAILED = "stop_failed"

# How _async_end_boost re-checks that the boost should still end.
_CHECK_EXPIRY = "expiry"  # the scheduled end/deadline has really passed
_CHECK_TARGET = "target"  # the energy target is still reached

_STATUSES_WITHOUT_INFO = {protocol.STATUS_OFFLINE, protocol.STATUS_UNKNOWN}


class ElectroSCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the charger and drives the timer/boost engine."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: ElectroSApi,
        scan_interval: int,
        tariff_window: tuple[str, str] = (
            DEFAULT_TARIFF_NIGHT_START,
            DEFAULT_TARIFF_NIGHT_END,
        ),
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.api = api
        self.login: str = entry.data[CONF_CHARGER_LOGIN]
        self.scan_interval = scan_interval
        self.tariff_window: tuple[str, str] = tariff_window
        self.stored = ElectroSStoredState()
        self.last_error: str | None = None
        self._store = create_store(hass, entry.entry_id)
        self._op_lock = asyncio.Lock()
        self._device_amps: int | None = None
        self._unsub_boost: CALLBACK_TYPE | None = None
        self._unsub_followup: CALLBACK_TYPE | None = None
        self._last_write_mono: float | None = None
        self._started = False
        self._startup_pending = True
        self._reconcile_retry = False
        self._ending_boost = False
        self._start_check_until: float | None = None
        self._blocked_warned = False
        #: The last status said the charger is not connected to the cloud (7/77).
        self._offline = False
        #: Kind (STOP_PENDING_*) of a stop sent since the last online status.
        self._stop_unconfirmed: str | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def async_load(self) -> None:
        """Load persisted state (before the first refresh)."""
        data = await self._store.async_load()
        self.stored = ElectroSStoredState.from_dict(data)
        self._apply_poll_interval()

    @callback
    def async_start(self) -> None:
        """Start timers and the startup task (after platforms are set up)."""
        entry = self.config_entry
        entry.async_on_unload(
            async_track_time_change(
                self.hass, self._handle_daily, hour=0, minute=0, second=5
            )
        )
        for boundary in sorted(set(self.tariff_window)):
            hour, minute = tariff.parse_hm(boundary)
            entry.async_on_unload(
                async_track_time_change(
                    self.hass, self._handle_tariff_boundary, hour=hour, minute=minute, second=0
                )
            )
        entry.async_on_unload(
            self.hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, self._handle_ha_stop)
        )
        entry.async_on_unload(self._cancel_timers)
        self._create_task(self._async_startup(), "startup")

    async def async_unload(self) -> None:
        """Cancel timers and flush the store (entry unload)."""
        self._cancel_timers()
        await self._async_save()

    @callback
    def _cancel_timers(self) -> None:
        self._cancel_boost_timer()
        if self._unsub_followup is not None:
            self._unsub_followup()
            self._unsub_followup = None

    def _create_task(self, coro: Coroutine[Any, Any, Any], name: str) -> None:
        # Not eager: tasks created while polling must see the new self.data,
        # which the coordinator assigns right after _async_update_data returns.
        self.config_entry.async_create_background_task(
            self.hass, coro, f"{DOMAIN} {self.login} {name}", eager_start=False
        )

    async def _async_save(self) -> None:
        await self._store.async_save(self.stored.as_dict())

    @callback
    def _save_soon(self, delay: float = 1) -> None:
        self._store.async_delay_save(self.stored.as_dict, delay)

    @callback
    def _handle_ha_stop(self, _event: Event) -> None:
        """Persist the latest counter sample with the final write."""
        self._save_soon(0)

    async def _async_startup(self) -> None:
        """Restore a persisted boost, then reconcile once if the device is known.

        A stop that did not reach the charger before the restart is retried
        here if the charger still charges.
        """
        stopped = False
        async with self._op_lock:
            pending_before = self.stored.stop_pending
            boost = self.stored.boost
            due = boost.expires_at if boost is not None else None
            if boost is not None and due is not None:
                if due <= dt_util.utcnow():
                    stop = True
                    if boost.is_energy:
                        self._set_deadline_error()
                    elif self._schedule_allows_now():
                        stop = False
                    _LOGGER.info(
                        "Boost ended while Home Assistant was stopped; %s",
                        "stopping" if stop else "the night window continues",
                    )
                    if stop:
                        await self._async_send_stop_locked(
                            STOP_PENDING_ALWAYS
                            if boost.is_energy
                            else STOP_PENDING_UNLESS_WINDOW,
                            "boost expired while Home Assistant was stopped",
                        )
                    self.stored.boost = None
                    await self._async_save()
                    stopped = True
                else:
                    self._schedule_boost_expiry()
            self._apply_poll_interval()
            self._started = True
            if self._device_known():
                await self._async_startup_reconcile_locked()
            if pending_before is not None and await self._async_retry_stop_locked():
                stopped = True
        self.async_update_listeners()
        if stopped:
            self._create_task(self._async_refresh_after_command(), "refresh")

    async def _async_startup_reconcile(self) -> None:
        async with self._op_lock:
            await self._async_startup_reconcile_locked()
        self.async_update_listeners()

    async def _async_startup_reconcile_locked(self) -> None:
        self._startup_pending = False
        device = self._device_window()
        if not self.stored.seeded:
            # First run: take the user's current device settings as ours.
            self.stored.seeded = True
            if device is not None:
                self.stored.night_enabled = True
                self.stored.timer_start, self.stored.timer_end = device
            else:
                self.stored.night_enabled = False
            self.stored.last_written = protocol.window_to_str(device)
            _LOGGER.info(
                "Initialized night timer from the charger: %s",
                protocol.window_to_str(device) or "off",
            )
            await self._async_save()
            return
        if self.stored.last_written is None:
            self.stored.last_written = protocol.window_to_str(device)
        elif self.stored.boost is None and device != self.stored.last_written_window:
            # Changed in the web panel while HA was stopped: the user wins.
            self._adopt_window(device, "while Home Assistant was stopped")
            await self._async_save()
            return
        # The device still has what HA wrote: write only if HA wants something else.
        await self._async_reconcile_logged("startup")

    # ------------------------------------------------------------------ #
    # Polling
    # ------------------------------------------------------------------ #

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the charger."""
        try:
            data = await self.api.get_state()
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Error communicating with charger: {err}") from err
        if not isinstance(data, dict):
            raise UpdateFailed("Charger returned no data")
        status = str(data.get("status_text") or "")
        if status.startswith("error"):  # legacy offline-state dicts
            raise UpdateFailed(f"Charger returned error: {status}")
        self._process_status(data)
        return data

    @callback
    def _process_status(self, data: dict[str, Any]) -> None:
        """Derive engine state from a fresh status (no device writes here)."""
        amps = data.get("current_amps")
        if isinstance(amps, int) and protocol.normalize_amps(amps) > 0:
            self._device_amps = protocol.normalize_amps(amps)
        if not data.get("online", True):
            self._handle_offline_status()
            return
        if self._offline:
            _LOGGER.info("Charger %s is online again", self.login)
        self._offline = False
        self._stop_unconfirmed = None
        status = data.get("status_text") or protocol.STATUS_UNKNOWN
        delta, cost = self._track_tariff(data.get("energy_kwh"))
        self._track_session(status, delta, cost)
        self._track_energy_boost(delta)
        if status not in _STATUSES_WITHOUT_INFO:
            self._check_after_start(status)
            self._track_unlimited(status)
            self._check_pending_stop(status)
        if not self._started or not data.get("timer_known", False):
            return
        if self._startup_pending:
            self._startup_pending = False
            self._create_task(self._async_startup_reconcile(), "startup reconcile")
            return
        if self._reconcile_retry and not self._op_lock.locked():
            self._reconcile_retry = False
            self._create_task(self._async_background_reconcile("retry"), "reconcile")
            return
        self._maybe_adopt(data.get("timer_window"))

    @callback
    def _track_tariff(self, energy: Any) -> tuple[float, float]:
        """Add the counter delta since the last sample to the zone totals.

        Returns ``(delta_kwh, cost)`` of this poll for the session and the energy
        boost. Polls without a counter value change nothing.
        """
        if isinstance(energy, bool) or not isinstance(energy, (int, float)):
            return 0.0, 0.0
        state = self.stored.tariff
        energy = float(energy)
        now = dt_util.utcnow()
        delta = tariff.counter_delta(state.last_energy, energy)
        cost = 0.0
        if delta > 0:
            previous = (
                dt_util.as_local(state.last_sample_at)
                if state.last_sample_at is not None
                else None
            )
            day, night = tariff.split_delta(
                delta,
                previous,
                dt_util.as_local(now),
                *self.tariff_window,
                timedelta(seconds=self.scan_interval * tariff.GAP_POLLS),
            )
            cost = tariff.cost_of(day, night, self.stored.price_day, self.stored.price_night)
            state.day_kwh = round(state.day_kwh + day, 6)
            state.night_kwh = round(state.night_kwh + night, 6)
            state.cost = round(state.cost + cost, 6)
        state.last_energy = energy
        state.last_sample_at = now
        if delta > 0:
            self._save_soon(SAVE_DELAY)
        # Idle polls only move the sample time; it is saved with the next change
        # and at Home Assistant shutdown.
        return delta, cost

    @callback
    def _track_session(self, status: str, delta: float, cost: float) -> None:
        session = self.stored.session

        def _accumulate() -> None:
            if delta > 0:
                session.kwh = round(session.kwh + delta, 6)
                session.cost = round(session.cost + cost, 6)

        if status in _STATUSES_WITHOUT_INFO:
            # Unknown status code: keep counting an active session, change nothing else.
            if session.active and delta > 0:
                _accumulate()
                self._save_soon(SAVE_DELAY)
            return
        if status == protocol.STATUS_CHARGING:
            if not session.active:
                session.active = True
                session.started_at = dt_util.utcnow()
                session.kwh = 0.0
                session.cost = 0.0
                self._save_soon()
            else:
                _accumulate()
                self._save_soon(SAVE_DELAY)
        elif session.active:
            _accumulate()
            session.active = False
            self._save_soon()

    @callback
    def _track_energy_boost(self, delta: float) -> None:
        """Count delivered energy; end the boost once the target is reached."""
        boost = self.stored.boost
        if boost is None or not boost.is_energy or self._ending_boost:
            return
        if delta > 0:
            boost.delivered_kwh = round(boost.delivered_kwh + delta, 6)
            self._save_soon(SAVE_DELAY)
        if boost.target_reached:
            self._ending_boost = True
            self._create_task(
                self._async_end_boost(
                    boost, stop=True, reason="energy target reached", check=_CHECK_TARGET
                ),
                "energy target",
            )

    @callback
    def _check_after_start(self, status: str) -> None:
        if self._start_check_until is None:
            return
        if time.monotonic() > self._start_check_until:
            self._start_check_until = None
            return
        if status == protocol.STATUS_CHARGING:
            self.last_error = None
            self._start_check_until = None
        elif status == protocol.STATUS_CHARGING_BLOCKED and not self._blocked_warned:
            self._blocked_warned = True
            self.last_error = ERROR_CHARGING_BLOCKED
            _LOGGER.warning(
                "Charger %s reports charging blocked (status 4) after a start command",
                self.login,
            )

    @callback
    def _track_unlimited(self, status: str) -> None:
        boost = self.stored.boost
        if boost is None or not boost.unlimited or self._ending_boost:
            return
        if status == protocol.STATUS_CHARGING:
            if not boost.charging_seen:
                boost.charging_seen = True
                self._save_soon()
            return
        reason: str | None = None
        if boost.charging_seen:
            reason = "charging finished"
        elif (
            boost.started_at is not None
            and dt_util.utcnow() - boost.started_at > UNLIMITED_START_GRACE
        ):
            reason = "charging never started"
        if reason is not None:
            self._ending_boost = True
            self._create_task(
                self._async_end_boost(boost, stop=False, reason=reason), "end bypass"
            )

    @callback
    def _handle_offline_status(self) -> None:
        """Status 7/77: the charger is not connected to the cloud.

        Until it is back, timer writes and stops are not sent (they would not
        reach it). A stop sent since the last online status is not confirmed, so
        it becomes pending. The first online status reconciles the timer instead
        of adopting what the charger reports: nobody could change it in the web
        panel meanwhile, and HA's last writes may have missed it.
        """
        if not self._offline:
            _LOGGER.info("Charger %s is offline", self.login)
        self._offline = True
        self._reconcile_retry = True
        if self._stop_unconfirmed is not None and self.stored.stop_pending is None:
            self.stored.stop_pending = self._stop_unconfirmed
            self._save_soon()
        self._stop_unconfirmed = None

    @callback
    def _check_pending_stop(self, status: str) -> None:
        """Retry a stop that did not reach the charger while it still charges."""
        if self.stored.stop_pending is None:
            return
        if not self._stop_still_needed(status):
            self._clear_pending_stop()
            return
        if self._started and not self._op_lock.locked():
            self._create_task(self._async_retry_stop(), "stop retry")

    def _stop_still_needed(self, status: str | None) -> bool:
        pending = self.stored.stop_pending
        if pending is None or status != protocol.STATUS_CHARGING:
            # Not charging any more: the stop arrived after all, or the car is full.
            return False
        if self.stored.boost is not None:
            return False  # charging was asked for again
        # End of a timed boost: once the restored night window allows charging,
        # the schedule continues the charge (as at the expiry itself).
        return not (
            pending == STOP_PENDING_UNLESS_WINDOW and self._schedule_allows_now()
        )

    @callback
    def _clear_pending_stop(self) -> None:
        if self.stored.stop_pending is not None:
            self.stored.stop_pending = None
            self._save_soon()
        if self.last_error == ERROR_STOP_FAILED:
            self.last_error = None

    @callback
    def _maybe_adopt(self, device: protocol.Window | None) -> None:
        """Adopt a window changed in the web panel (never fight the user)."""
        if (
            self.stored.boost is not None
            or self._op_lock.locked()
            or not self.stored.seeded
        ):
            return
        if (
            self._last_write_mono is not None
            and time.monotonic() - self._last_write_mono < ADOPT_GRACE
        ):
            return
        if self.stored.last_written is None:
            self.stored.last_written = protocol.window_to_str(device)
            self._save_soon()
            return
        if device == self.stored.last_written_window:
            return
        self._adopt_window(device, "outside Home Assistant")
        self._save_soon()

    @callback
    def _adopt_window(self, device: protocol.Window | None, where: str) -> None:
        _LOGGER.info(
            "Timer changed %s (%s -> %s); adopting it",
            where,
            self.stored.last_written or "off",
            protocol.window_to_str(device) or "off",
        )
        if device is None:
            self.stored.night_enabled = False
        else:
            self.stored.night_enabled = True
            self.stored.timer_start, self.stored.timer_end = device
        self.stored.last_written = protocol.window_to_str(device)

    # ------------------------------------------------------------------ #
    # Derived state
    # ------------------------------------------------------------------ #

    @property
    def target_amps(self) -> int:
        """Current used for the next start (persisted user choice or device value)."""
        if self.stored.charging_amps:
            return self.stored.charging_amps
        if self._device_amps:
            return self._device_amps
        return DEFAULT_CHARGING_AMPS

    @target_amps.setter
    def target_amps(self, amps: int) -> None:
        normalized = protocol.normalize_amps(amps)
        self.stored.charging_amps = normalized or None

    @property
    def is_charging(self) -> bool:
        """True if the last status says charging."""
        return bool(self.data) and self.data.get("status_text") == protocol.STATUS_CHARGING

    @property
    def boost(self) -> BoostState | None:
        """Active boost, if any."""
        return self.stored.boost

    def _device_known(self) -> bool:
        data = self.data
        return bool(data) and bool(data.get("online", True)) and bool(
            data.get("timer_known", False)
        )

    def _device_window(self) -> protocol.Window | None:
        return (self.data or {}).get("timer_window")

    def _best_known_window(self) -> protocol.Window | None:
        """Device window if known, else what HA last wrote, else the desired one."""
        if self._device_known():
            return self._device_window()
        if self.stored.last_written is not None:
            return self.stored.last_written_window
        return protocol.parse_window(self.stored.timer_start, self.stored.timer_end)

    def desired_window(self, local_now: datetime | None = None) -> protocol.Window | None:
        """Window the device should have now."""
        return protocol.desired_window(
            self.stored.night_enabled,
            self.stored.timer_start,
            self.stored.timer_end,
            self.stored.boost is not None,
            self.stored.weekend_free,
            local_now or dt_util.now(),
        )

    def _timer_blocks_now(self) -> bool:
        window = self._best_known_window()
        if window is None:
            return False
        if (self.data or {}).get("status_text") == protocol.STATUS_CHARGING_BLOCKED:
            return True
        return not protocol.window_allows(window, dt_util.now())

    def _schedule_allows_now(self) -> bool:
        """The night window HA restores after a boost allows charging right now.

        False when there is no window to restore (night timer off, weekend_free
        at the weekend): then nothing would continue the charge.
        """
        now = dt_util.now()
        window = protocol.desired_window(
            self.stored.night_enabled,
            self.stored.timer_start,
            self.stored.timer_end,
            False,
            self.stored.weekend_free,
            now,
        )
        return window is not None and protocol.window_allows(window, now)

    @property
    def tariff_zone(self) -> str:
        """Tariff zone right now (``day`` or ``night``)."""
        return tariff.zone_at(dt_util.now(), *self.tariff_window)

    @property
    def current_price(self) -> float:
        """Price per kWh right now."""
        if self.tariff_zone == tariff.ZONE_NIGHT:
            return self.stored.price_night
        return self.stored.price_day

    @property
    def boost_estimated_end(self) -> datetime | None:
        """Energy boost: when the target is reached at the current power."""
        boost = self.stored.boost
        if boost is None or not boost.is_energy or not self.last_update_success:
            return None
        power = (self.data or {}).get("charging_power_w")
        return tariff.estimate_end(dt_util.utcnow(), boost.remaining_kwh, power)

    # ------------------------------------------------------------------ #
    # Timer writes
    # ------------------------------------------------------------------ #

    async def _async_write_window_locked(
        self, window: protocol.Window | None, reason: str
    ) -> bool:
        if self._offline:
            # The cloud would take the frame, but it never reaches an offline
            # charger: reconcile on the first online status instead.
            self._reconcile_retry = True
            _LOGGER.debug("Charger %s is offline; timer write (%s) postponed", self.login, reason)
            return False
        now = dt_util.now()
        if window is None:
            ok = await self.api.disable_timer(now)
        else:
            ok = await self.api.set_timer(window[0], window[1], now)
        if not ok:
            return False
        self._last_write_mono = time.monotonic()
        self.stored.last_written = protocol.window_to_str(window)
        await self._async_save()
        if self.data is not None:
            self.data = {
                **self.data,
                "timer_window": window,
                "timer_start": window[0] if window else protocol.DISABLED_TIME,
                "timer_end": window[1] if window else protocol.DISABLED_TIME,
            }
        _LOGGER.info(
            "Timer written (%s): %s", reason, protocol.window_to_str(window) or "off"
        )
        return True

    async def _async_reconcile_locked(self, reason: str) -> bool:
        """Write the desired window if the device differs. False if a write failed."""
        desired = self.desired_window()
        if self._device_known() and self._device_window() == desired:
            encoded = protocol.window_to_str(desired)
            if self.stored.last_written != encoded:
                self.stored.last_written = encoded
                self._save_soon()
            return True
        ok = await self._async_write_window_locked(desired, reason)
        if not ok:
            self._reconcile_retry = True
        return ok

    async def _async_reconcile_logged(self, reason: str) -> None:
        if not await self._async_reconcile_locked(reason):
            _LOGGER.warning("Could not update the charger timer (%s); will retry", reason)

    async def _async_background_reconcile(self, reason: str) -> None:
        async with self._op_lock:
            if self.stored.seeded:
                await self._async_reconcile_logged(reason)
        self.async_update_listeners()

    @callback
    def _handle_daily(self, _now: datetime) -> None:
        """00:00:05 local: weekend boundary (weekend_free) and general check."""
        if self._started and not self._startup_pending:
            self._create_task(self._async_background_reconcile("daily"), "daily reconcile")

    @callback
    def _handle_tariff_boundary(self, _now: datetime) -> None:
        """Night/day zone change: current_price changes even without a poll."""
        self.async_update_listeners()

    async def _async_apply_settings_locked(self, reason: str) -> None:
        self.stored.seeded = True
        await self._async_save()
        self.async_update_listeners()
        if not await self._async_reconcile_locked(reason):
            raise _command_failed("timer")

    # ------------------------------------------------------------------ #
    # Boost scheduling
    # ------------------------------------------------------------------ #

    @callback
    def _apply_poll_interval(self) -> None:
        """Poll every 20 s while an energy boost runs, else at the configured interval.

        Takes effect when the coordinator schedules its next poll; every boost
        change is followed by a refresh.
        """
        seconds = self.scan_interval
        boost = self.stored.boost
        if boost is not None and boost.is_energy:
            seconds = min(seconds, ENERGY_BOOST_SCAN_INTERVAL)
        interval = timedelta(seconds=seconds)
        if self.update_interval != interval:
            self.update_interval = interval

    @callback
    def _cancel_boost_timer(self) -> None:
        if self._unsub_boost is not None:
            self._unsub_boost()
            self._unsub_boost = None

    @callback
    def _schedule_boost_expiry(self) -> None:
        self._cancel_boost_timer()
        boost = self.stored.boost
        due = boost.expires_at if boost is not None else None
        if due is None:
            return
        self._unsub_boost = async_track_point_in_utc_time(
            self.hass, self._handle_boost_expired, due
        )

    @callback
    def _handle_boost_expired(self, _now: datetime) -> None:
        self._unsub_boost = None
        boost = self.stored.boost
        if boost is None:
            return
        if boost.is_energy:
            self._create_task(
                self._async_end_boost(
                    boost,
                    stop=True,
                    reason="energy boost deadline",
                    check=_CHECK_EXPIRY,
                    deadline=True,
                ),
                "boost deadline",
            )
            return
        self._create_task(
            self._async_end_boost(
                boost,
                stop=True,
                reason="boost expired",
                check=_CHECK_EXPIRY,
                keep_if_window_allows=True,
            ),
            "boost expiry",
        )

    @callback
    def _set_deadline_error(self) -> None:
        self.last_error = ERROR_ENERGY_DEADLINE
        _LOGGER.warning(
            "Charger %s: energy target not reached by the safety deadline; stopping",
            self.login,
        )

    async def _async_end_boost(
        self,
        expected: BoostState,
        *,
        stop: bool,
        reason: str,
        check: str | None = None,
        keep_if_window_allows: bool = False,
        deadline: bool = False,
    ) -> None:
        async with self._op_lock:
            self._ending_boost = False
            boost = self.stored.boost
            if boost is not expected:
                return
            if check == _CHECK_EXPIRY:
                due = boost.expires_at
                if due is not None and due > dt_util.utcnow() + timedelta(seconds=1):
                    # Extended meanwhile; the new expiry is already scheduled.
                    return
            elif check == _CHECK_TARGET and not boost.target_reached:
                return  # the target was raised meanwhile
            if stop and keep_if_window_allows and self._schedule_allows_now():
                stop = False
                reason = f"{reason}; inside the night window, charging continues"
            if deadline:
                self._set_deadline_error()
            _LOGGER.info("Ending boost: %s", reason)
            if stop:
                await self._async_send_stop_locked(
                    STOP_PENDING_UNLESS_WINDOW if keep_if_window_allows else STOP_PENDING_ALWAYS,
                    reason,
                )
            self._cancel_boost_timer()
            self.stored.boost = None
            self._apply_poll_interval()
            await self._async_save()
            await self._async_reconcile_logged(reason)
        self.async_update_listeners()
        self._create_task(self._async_refresh_after_command(), "refresh")

    async def _async_send_stop_locked(self, kind: str, reason: str) -> bool:
        """Stop charging when HA ends a charge; remember a stop that did not arrive.

        Nothing is sent while the charger is offline. A stop that failed or was
        not sent is persisted as ``stop_pending`` (``kind``: STOP_PENDING_*) and
        retried on online polls while the charger still charges. The caller saves.
        """
        if self._offline:
            ok = False
            _LOGGER.warning(
                "Charger %s is offline; stop (%s) is sent when it is back", self.login, reason
            )
        else:
            ok = await self.api.stop_charging()
            if not ok:
                _LOGGER.warning("Could not stop charging (%s); will retry", reason)
        if ok:
            self._stop_unconfirmed = kind
            self._clear_pending_stop()
        else:
            self.stored.stop_pending = kind
            self.last_error = ERROR_STOP_FAILED
        return ok

    async def _async_retry_stop(self) -> None:
        async with self._op_lock:
            sent = await self._async_retry_stop_locked()
        self.async_update_listeners()
        if sent:
            self._create_task(self._async_refresh_after_command(), "refresh")

    async def _async_retry_stop_locked(self) -> bool:
        """Re-send a pending stop if the (online) charger still charges. True if sent."""
        pending = self.stored.stop_pending
        status = (self.data or {}).get("status_text") or protocol.STATUS_UNKNOWN
        if pending is None or self._offline or status in _STATUSES_WITHOUT_INFO:
            return False
        if not self._stop_still_needed(status):
            self._clear_pending_stop()
            await self._async_save()
            return False
        _LOGGER.info("Charger %s still charging; sending the stop again", self.login)
        ok = await self._async_send_stop_locked(pending, "retry")
        await self._async_save()
        return ok

    @callback
    def _arm_start_check(self) -> None:
        self.last_error = None
        self._blocked_warned = False
        self._start_check_until = time.monotonic() + START_CHECK_WINDOW
        # Charging is wanted again: a stop that did not arrive is void.
        self._stop_unconfirmed = None
        self._clear_pending_stop()

    async def _async_refresh_after_command(self) -> None:
        await asyncio.sleep(COMMAND_SETTLE_DELAY)
        await self.async_refresh()
        if self._unsub_followup is not None:
            self._unsub_followup()
        self._unsub_followup = async_call_later(
            self.hass, FOLLOWUP_REFRESH_DELAY, self._handle_followup
        )

    @callback
    def _handle_followup(self, _now: datetime) -> None:
        self._unsub_followup = None
        self._create_task(self.async_request_refresh(), "followup refresh")

    def _after_command(self) -> None:
        self.async_update_listeners()
        self._create_task(self._async_refresh_after_command(), "refresh")

    async def _async_start_boost_locked(
        self, start_amps: int, build: Callable[[datetime, bool], BoostState]
    ) -> BoostState:
        """Disable the timer if needed, start charging and install a new boost.

        A boost that is already active (also while charging) is replaced; the
        timer is then already off and only ``start`` is sent.
        """
        previous = self.stored.boost
        restore = previous.restore if previous is not None else False
        if self._best_known_window() is not None:
            if not await self._async_write_window_locked(None, "boost"):
                raise _command_failed("timer")
            restore = True
            await asyncio.sleep(TIMER_SETTLE_DELAY)
        if not await self.api.start_charging(start_amps):
            if previous is None:
                await self._async_reconcile_logged("boost start failed")
            raise _command_failed("start")
        self._cancel_boost_timer()
        boost = build(dt_util.utcnow(), restore)
        self.stored.boost = boost
        self.stored.seeded = True
        await self._async_save()
        self._schedule_boost_expiry()
        self._apply_poll_interval()
        self._arm_start_check()
        return boost

    def _use_amps(self, amps: int | None) -> int:
        """Validate optional service amps (stored as the new current); current to start with."""
        if amps is not None:
            normalized = protocol.normalize_amps(amps)
            if normalized <= 0:
                raise HomeAssistantError(f"unsupported current: {amps}")
            self.stored.charging_amps = normalized
        return self.target_amps

    # ------------------------------------------------------------------ #
    # User actions (entities and services)
    # ------------------------------------------------------------------ #

    async def async_charge_for(self, minutes: int, amps: int | None = None) -> None:
        """Charge now for ``minutes``, bypassing the device timer.

        Replaces any active boost (a chip tapped while charging re-times it).
        """
        minutes = int(minutes)
        if minutes <= 0:
            raise HomeAssistantError("minutes must be positive")
        start_amps = self._use_amps(amps)

        def _build(now: datetime, restore: bool) -> BoostState:
            return BoostState(
                end=now + timedelta(minutes=minutes),
                unlimited=False,
                minutes=minutes,
                restore=restore,
                started_at=now,
                amps=start_amps,
            )

        async with self._op_lock:
            await self._async_start_boost_locked(start_amps, _build)
            _LOGGER.info("Boost started: %d min at %d A", minutes, start_amps)
        self._after_command()

    async def async_charge_energy(self, kwh: float, amps: int | None = None) -> None:
        """Charge now until ``kwh`` were delivered, bypassing the device timer."""
        kwh = round(float(kwh), 3)
        if not kwh > 0:
            raise HomeAssistantError("kwh must be positive")
        start_amps = self._use_amps(amps)

        def _build(now: datetime, restore: bool) -> BoostState:
            return BoostState(
                end=None,
                unlimited=False,
                minutes=0,
                restore=restore,
                started_at=now,
                target_kwh=kwh,
                delivered_kwh=0.0,
                deadline=tariff.energy_deadline(now, kwh, start_amps),
                amps=start_amps,
            )

        async with self._op_lock:
            boost = await self._async_start_boost_locked(start_amps, _build)
            _LOGGER.info(
                "Energy boost started: %s kWh at %d A (deadline %s)",
                kwh,
                start_amps,
                boost.deadline,
            )
        self._after_command()

    async def async_extend_charge(
        self, minutes: int | None = None, kwh: float | None = None
    ) -> None:
        """Add ``minutes`` to a timed boost or ``kwh`` to an energy boost.

        Exactly one of them. Without a boost of that kind, one is started.
        """
        if (minutes is None) == (kwh is None):
            raise HomeAssistantError("give either minutes or kwh")
        if kwh is not None:
            await self._async_extend_energy(kwh)
            return
        minutes = int(minutes)  # type: ignore[arg-type]
        if minutes <= 0:
            raise HomeAssistantError("minutes must be positive")
        async with self._op_lock:
            boost = self.stored.boost
            if boost is not None and boost.is_timed and boost.end is not None:
                boost.end = max(boost.end, dt_util.utcnow()) + timedelta(minutes=minutes)
                boost.minutes += minutes
                await self._async_save()
                self._schedule_boost_expiry()
                _LOGGER.info("Boost extended by %d min (until %s)", minutes, boost.end)
                extended = True
            else:
                extended = False
        if extended:
            self.async_update_listeners()
            return
        await self.async_charge_for(minutes)

    async def _async_extend_energy(self, kwh: float) -> None:
        kwh = round(float(kwh), 3)
        if not kwh > 0:
            raise HomeAssistantError("kwh must be positive")
        async with self._op_lock:
            boost = self.stored.boost
            if boost is not None and boost.is_energy and boost.target_kwh is not None:
                now = dt_util.utcnow()
                boost.target_kwh = round(boost.target_kwh + kwh, 3)
                boost.deadline = max(
                    tariff.energy_deadline(boost.started_at or now, boost.target_kwh, boost.amps),
                    tariff.energy_deadline(now, boost.remaining_kwh, boost.amps),
                )
                await self._async_save()
                self._schedule_boost_expiry()
                _LOGGER.info(
                    "Energy boost extended by %s kWh (target %s kWh)", kwh, boost.target_kwh
                )
                extended = True
            else:
                extended = False
        if extended:
            self.async_update_listeners()
            return
        await self.async_charge_energy(kwh)

    async def async_stop_charging(self) -> None:
        """Stop charging, cancel any boost and restore the timer.

        Fails (nothing sent) while the charger is offline.
        """
        async with self._op_lock:
            if self._offline or not await self.api.stop_charging():
                raise _command_failed("stop")
            self._stop_unconfirmed = STOP_PENDING_ALWAYS
            self._clear_pending_stop()
            self._cancel_boost_timer()
            self._start_check_until = None
            if self.stored.boost is not None:
                self.stored.boost = None
                self._apply_poll_interval()
                await self._async_save()
            if self.stored.seeded:
                await self._async_reconcile_logged("stop")
        self._after_command()

    async def async_start_charging(self, amps: int | None = None) -> None:
        """Start charging now (switch.charging on).

        If the night timer currently blocks charging this becomes an unlimited
        bypass: timer disabled until charging stops. During a timed or energy
        boost it turns that boost into an unlimited bypass (∞). ``amps``
        (optional) also becomes the stored charging current.
        """
        if amps is not None:
            if protocol.normalize_amps(amps) <= 0:
                raise HomeAssistantError(f"unsupported current: {amps}")
            self.target_amps = amps
        amps = self.target_amps
        async with self._op_lock:
            boost = self.stored.boost
            if boost is not None and not boost.unlimited:
                if not await self.api.start_charging(amps):
                    raise _command_failed("start")
                self._cancel_boost_timer()
                self.stored.boost = BoostState(
                    end=None,
                    unlimited=True,
                    minutes=0,
                    restore=boost.restore,
                    started_at=dt_util.utcnow(),
                    charging_seen=self.is_charging,
                    amps=amps,
                )
                self._apply_poll_interval()
                await self._async_save()
                _LOGGER.info("%s boost turned into an unlimited charge", boost.mode)
            elif boost is None and self._timer_blocks_now():
                if not await self._async_write_window_locked(None, "manual charge"):
                    raise _command_failed("timer")
                await asyncio.sleep(TIMER_SETTLE_DELAY)
                if not await self.api.start_charging(amps):
                    await self._async_reconcile_logged("manual charge failed")
                    raise _command_failed("start")
                self.stored.boost = BoostState(
                    end=None,
                    unlimited=True,
                    minutes=0,
                    restore=True,
                    started_at=dt_util.utcnow(),
                    amps=amps,
                )
                self.stored.seeded = True
                await self._async_save()
                _LOGGER.info("Manual charge at %d A bypasses the night timer", amps)
            elif not await self.api.start_charging(amps):
                raise _command_failed("start")
            self._arm_start_check()
        self._after_command()

    async def async_set_target_amps(self, amps: int) -> None:
        """Set the charging current; applied immediately while charging."""
        normalized = protocol.normalize_amps(amps)
        if normalized <= 0:
            raise HomeAssistantError(f"unsupported current: {amps}")
        self.stored.charging_amps = normalized
        boost = self.stored.boost
        if boost is not None and boost.is_energy and boost.deadline is not None:
            # A lower current needs more time: never cut the safety deadline short.
            boost.amps = normalized
            boost.deadline = max(
                boost.deadline,
                tariff.energy_deadline(dt_util.utcnow(), boost.remaining_kwh, normalized),
            )
            self._schedule_boost_expiry()
        await self._async_save()
        self.async_update_listeners()
        if self.is_charging:
            async with self._op_lock:
                if not await self.api.start_charging(normalized):
                    raise _command_failed("start")
            self._after_command()

    async_set_amps = async_set_target_amps  # pre-2.0 name

    async def async_set_boost_minutes(self, minutes: int) -> None:
        """Set the default boost duration (button.boost)."""
        minutes = int(minutes)
        self.stored.boost_minutes = min(MAX_BOOST_MINUTES, max(MIN_BOOST_MINUTES, minutes))
        await self._async_save()
        self.async_update_listeners()

    async def async_set_boost_kwh(self, kwh: float) -> None:
        """Set the default energy target (button.boost_energy)."""
        value = int(round(float(kwh)))
        self.stored.boost_kwh = min(MAX_BOOST_KWH, max(MIN_BOOST_KWH, value))
        await self._async_save()
        self.async_update_listeners()

    async def async_set_price(self, zone: str, price: float) -> None:
        """Set the price per kWh of a tariff zone (future energy only)."""
        value = round(min(MAX_PRICE, max(MIN_PRICE, float(price))), 4)
        if zone == tariff.ZONE_NIGHT:
            self.stored.price_night = value
        elif zone == tariff.ZONE_DAY:
            self.stored.price_day = value
        else:
            raise HomeAssistantError(f"unknown tariff zone: {zone}")
        await self._async_save()
        self.async_update_listeners()

    async def async_boost(self) -> None:
        """Charge now for the configured boost duration."""
        await self.async_charge_for(self.stored.boost_minutes)

    async def async_boost_energy(self) -> None:
        """Charge now for the configured energy target."""
        await self.async_charge_energy(self.stored.boost_kwh)

    async def async_set_night_enabled(self, enabled: bool) -> None:
        """Enable/disable the device's daily window."""
        async with self._op_lock:
            self.stored.night_enabled = bool(enabled)
            await self._async_apply_settings_locked("night timer")

    async def async_set_weekend_free(self, enabled: bool) -> None:
        """Allow charging any time on Saturday and Sunday."""
        async with self._op_lock:
            self.stored.weekend_free = bool(enabled)
            await self._async_apply_settings_locked("weekend free")

    async def async_set_timer(
        self,
        start: Any = None,
        end: Any = None,
        enabled: bool | None = None,
    ) -> None:
        """Change the night window times and/or whether it is enabled."""
        new_start = protocol.parse_hhmm(start) if start is not None else None
        new_end = protocol.parse_hhmm(end) if end is not None else None
        async with self._op_lock:
            if new_start is not None:
                self.stored.timer_start = new_start
            if new_end is not None:
                self.stored.timer_end = new_end
            if enabled is not None:
                self.stored.night_enabled = bool(enabled)
            await self._async_apply_settings_locked("set timer")

    async def async_reset_energy(self) -> None:
        """Reset the charger's consumed-energy counter."""
        async with self._op_lock:
            if not await self.api.reset_energy():
                raise _command_failed("resetkwt")
        self._after_command()

    # ------------------------------------------------------------------ #
    # Values for entities
    # ------------------------------------------------------------------ #

    @property
    def session_energy(self) -> float | None:
        """kWh of the current/last session (falls back to the device history)."""
        session = self.stored.session
        if session.started_at is not None:
            return round(session.kwh, 3)
        history = (self.data or {}).get("history") or []
        if history:
            return history[0].get("kwh")
        return None

    @property
    def session_cost(self) -> float | None:
        """Cost of the current/last session tracked by HA (None before the first one)."""
        session = self.stored.session
        if session.started_at is None:
            return None
        return round(session.cost, 2)


def _command_failed(command: str) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="command_failed",
        translation_placeholders={"command": command},
    )
