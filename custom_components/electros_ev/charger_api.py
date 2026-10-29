"""ElectroS EV Charger WebSocket API client.

Raw WebSocket implementation (the cloud needs a non-standard handshake with the
``example-protocol`` subprotocol and session cookies). Message formats and
status parsing live in :mod:`.protocol`.

Every public call is serialized with an ``asyncio.Lock``: the charger and the
cloud handle one request at a time, and polls must not interleave with commands.
Status replies contain Wi-Fi and cloud secrets, so raw messages are never logged.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime
import logging
import os
import ssl
import struct
from typing import Any

import aiohttp

from . import protocol
from .const import (
    DEFAULT_BASE_URL,
    DEFAULT_LOCAL_PORT,
    DEFAULT_LOCAL_WS_PATH,
    DEFAULT_WS_HOST,
    DEFAULT_WS_ORIGIN,
    DEFAULT_WS_PATH,
    DEFAULT_WS_PROTOCOL,
)
from .protocol import normalize_amps  # noqa: F401  (re-exported for platforms)

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 10
#: After a command is sent the server usually answers with a status frame.
#: If it does not, the command has still been delivered; do not treat it as failure.
COMMAND_REPLY_TIMEOUT = 5
HTTP_TIMEOUT = 30

_SSL_CONTEXT: ssl.SSLContext | None = None


def _insecure_ssl_context() -> ssl.SSLContext:
    """TLS context without certificate checks (as the original integration).

    Built without ``create_default_context`` so no CA bundle is loaded from disk
    inside the event loop.
    """
    global _SSL_CONTEXT  # noqa: PLW0603
    if _SSL_CONTEXT is None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        _SSL_CONTEXT = context
    return _SSL_CONTEXT


class ElectroSError(Exception):
    """Communication with the charger failed."""


class ElectroSApi:
    """API client for ElectroS EV Charger."""

    def __init__(
        self,
        login: str,
        password: str | None = None,
        host: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        ws_host: str = DEFAULT_WS_HOST,
        ws_path: str = DEFAULT_WS_PATH,
        ws_origin: str = DEFAULT_WS_ORIGIN,
        ws_protocol: str = DEFAULT_WS_PROTOCOL,
    ) -> None:
        """Initialize the API client.

        If ``host`` is set, connect directly to the charger on the LAN
        (ws://host/ws, no cloud login, no TLS, no password needed); otherwise go
        through the cloud, which needs the cloud password.
        """
        self._login = login
        self._password = password or ""
        self._host = (host or "").strip() or None
        self._base_url = base_url
        self._ws_host = ws_host
        self._ws_path = ws_path
        self._ws_origin = ws_origin
        self._ws_protocol = ws_protocol
        self._cookies: dict[str, str] | None = None
        self._lock = asyncio.Lock()

    @property
    def login(self) -> str:
        """Charger login (device name)."""
        return self._login

    @property
    def is_local(self) -> bool:
        """True when configured for a direct local-network connection."""
        return bool(self._host)

    # Backwards-compatible private alias used by older code.
    _local = is_local

    # ------------------------------------------------------------------ #
    # Transport
    # ------------------------------------------------------------------ #

    async def _login_session(self) -> dict[str, str]:
        """Log in to the cloud and return session cookies."""
        if not self._password:
            raise ElectroSError("cloud mode needs the charger password")
        connector = aiohttp.TCPConnector(ssl=_insecure_ssl_context())
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
        async with aiohttp.ClientSession(connector=connector) as session:
            await session.get(self._base_url + "main.php", timeout=timeout)
            await session.post(
                self._base_url + "login.php",
                data={"username": self._login, "password": self._password},
                timeout=timeout,
                allow_redirects=True,
            )
            cookies = {cookie.key: cookie.value for cookie in session.cookie_jar}
        self._cookies = cookies
        return cookies

    async def _ws_communicate(
        self, message: str, reply_timeout: float | None = None
    ) -> str | None:
        """Open a WebSocket, send one text frame, return the first text reply.

        Returns None if no text frame arrived within ``reply_timeout``.
        """
        if reply_timeout is None:
            reply_timeout = READ_TIMEOUT
        key = base64.b64encode(os.urandom(16)).decode()

        if self.is_local:
            # Direct LAN connection: plain ws://host/ws, no login, no TLS.
            host, port, path = self._host, DEFAULT_LOCAL_PORT, DEFAULT_LOCAL_WS_PATH
            ssl_context: ssl.SSLContext | None = None
            extra_headers = ""
        else:
            if not self._cookies:
                await self._login_session()
            cookie_header = "; ".join(
                f"{name}={value}" for name, value in (self._cookies or {}).items()
            )
            ssl_context = _insecure_ssl_context()
            host, port, path = self._ws_host, 443, self._ws_path
            extra_headers = f"Origin: {self._ws_origin}\r\nCookie: {cookie_header}\r\n"

        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=ssl_context),
            timeout=CONNECT_TIMEOUT,
        )
        try:
            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\n"
                f"Sec-WebSocket-Protocol: {self._ws_protocol}\r\n"
                f"{extra_headers}"
                "\r\n"
            )
            writer.write(request.encode("utf-8"))
            await writer.drain()

            # readuntil() leaves a frame that follows the handshake in the buffer.
            try:
                response = await asyncio.wait_for(
                    reader.readuntil(b"\r\n\r\n"), timeout=READ_TIMEOUT
                )
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as err:
                raise ElectroSError("WebSocket upgrade failed") from err
            if b" 101" not in response.split(b"\r\n", 1)[0]:
                raise ElectroSError("WebSocket upgrade failed")

            writer.write(_encode_text_frame(message))
            await writer.drain()

            # The frame is on the wire now: a missing reply (timeout, or the server
            # closing the connection) does not undo it, see docs/protocol.md.
            try:
                return await asyncio.wait_for(
                    _read_text_frame(reader), timeout=reply_timeout
                )
            except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
                return None
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=2)
            except Exception:  # noqa: BLE001 - closing errors are irrelevant
                pass

    async def _send(self, message: str, reply_timeout: float | None = None) -> str | None:
        """Send one message (logging in first in cloud mode), serialized."""
        async with self._lock:
            if not self.is_local:
                # Fresh session for every request, as the original integration did.
                self._cookies = None
                await self._login_session()
            return await self._ws_communicate(message, reply_timeout)

    async def _command(self, message: str, what: str) -> bool:
        """Send a command; True on success, False (logged) on failure."""
        try:
            await self._send(message, reply_timeout=COMMAND_REPLY_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Failed to send %s command: %s", what, _safe_error(exc))
            return False
        _LOGGER.debug("Sent %s command", what)
        return True

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def get_state(self) -> dict[str, Any]:
        """Fetch and parse the charger status. Raises ElectroSError on failure."""
        try:
            response = await self._send(protocol.build_status(self._login))
        except ElectroSError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ElectroSError(_safe_error(exc)) from exc
        if response is None:
            raise ElectroSError("no status reply")
        try:
            state = protocol.parse_status(response)
        except ValueError as exc:
            raise ElectroSError("unexpected reply") from exc
        _LOGGER.debug(
            "Status %s (%s), %s A set, %s A measured, %s V, timer %s",
            state["status_code"],
            state["status_text"],
            state["current_amps"],
            state["measured_current"],
            state["mains_voltage"],
            protocol.window_to_str(state["timer_window"]) or "off",
        )
        return state

    async def start_charging(self, amps: int) -> bool:
        """Start charging at the given current (A)."""
        amps = normalize_amps(amps)
        if amps <= 0:
            return await self.stop_charging()
        ok = await self._command(protocol.build_start(self._login, amps), "start")
        if ok:
            _LOGGER.info("Sent start command with %d A", amps)
        return ok

    async def stop_charging(self) -> bool:
        """Stop charging."""
        ok = await self._command(protocol.build_stop(self._login), "stop")
        if ok:
            _LOGGER.info("Sent stop command")
        return ok

    async def reset_energy(self) -> bool:
        """Reset the consumed-energy (kWh) counter.

        Local mode talks to the charger directly (no cloud login).
        """
        ok = await self._command(protocol.build_resetkwt(self._login), "resetkwt")
        if ok:
            _LOGGER.info("Sent energy counter reset command")
        return ok

    async def set_timer(self, start: str, end: str, now: datetime) -> bool:
        """Enable the daily timer window ``start``-``end`` and set the clock to ``now``."""
        window = (protocol.parse_hhmm(start), protocol.parse_hhmm(end))
        ok = await self._command(
            protocol.build_datetime(self._login, now, window), "datetime"
        )
        if ok:
            _LOGGER.info("Set charger timer to %s-%s", window[0], window[1])
        return ok

    async def disable_timer(self, now: datetime) -> bool:
        """Disable the daily timer window and set the clock to ``now``."""
        ok = await self._command(
            protocol.build_datetime(self._login, now, None), "datetime"
        )
        if ok:
            _LOGGER.info("Disabled charger timer")
        return ok

    async def test_connection(self) -> bool:
        """Return True if a status message can be read."""
        try:
            await self.get_state()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("Connection test failed: %s", _safe_error(exc))
            return False
        return True


def _safe_error(exc: BaseException) -> str:
    """Describe an exception without leaking message contents."""
    if isinstance(exc, asyncio.TimeoutError):
        return "timeout"
    text = str(exc) or exc.__class__.__name__
    return text[:200]


def _encode_text_frame(message: str) -> bytes:
    """Build a masked client text frame."""
    payload = message.encode("utf-8")
    header = bytearray([0x81])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    mask = os.urandom(4)
    header.extend(mask)
    masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
    return bytes(header) + masked


async def _read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """Read one frame; returns (opcode, payload)."""
    byte1, byte2 = await reader.readexactly(2)
    opcode = byte1 & 0x0F
    length = byte2 & 0x7F
    is_masked = (byte2 & 0x80) != 0
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]
    frame_mask = await reader.readexactly(4) if is_masked else b""
    payload = await reader.readexactly(length) if length else b""
    if is_masked:
        payload = bytes(byte ^ frame_mask[i % 4] for i, byte in enumerate(payload))
    return opcode, payload


async def _read_text_frame(reader: asyncio.StreamReader) -> str | None:
    """Return the first text frame (skipping ping/pong); None on close/binary."""
    for _ in range(10):
        opcode, payload = await _read_frame(reader)
        if opcode == 0x1:
            return payload.decode("utf-8", errors="replace")
        if opcode in (0x9, 0xA):  # ping / pong
            continue
        return None
    return None
