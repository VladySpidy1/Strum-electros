"""WebSocket transport (charger_api.ElectroSApi) against an in-memory fake server.

``asyncio.open_connection`` is replaced with a fake that returns a StreamReader
pre-filled with the server's bytes and a writer that records what the client
sent. Nothing here opens a socket; the cloud login is replaced as well.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import ssl
import struct
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.electros_ev import charger_api, protocol
from custom_components.electros_ev.charger_api import ElectroSApi, ElectroSError

from .common import HOST, LOGIN, PASSWORD, SECRETS, build_status

#: The real cloud login, captured before tests/conftest.py replaces it with a guard.
REAL_LOGIN_SESSION = ElectroSApi._login_session

HANDSHAKE_OK = (
    b"HTTP/1.1 101 Switching Protocols\r\n"
    b"Upgrade: websocket\r\n"
    b"Connection: Upgrade\r\n"
    b"Sec-WebSocket-Protocol: example-protocol\r\n"
    b"\r\n"
)


def server_frame(payload: bytes | str, opcode: int = 0x1) -> bytes:
    """An unmasked server frame (servers never mask)."""
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    header = bytearray([0x80 | opcode])
    if len(data) < 126:
        header.append(len(data))
    elif len(data) < 65536:
        header.append(126)
        header.extend(struct.pack("!H", len(data)))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", len(data)))
    return bytes(header) + data


class FakeWriter:
    """Records what the client writes."""

    def __init__(self, server: FakeServer) -> None:
        self.server = server
        self.data = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.server.active -= 1

    def is_closing(self) -> bool:
        return self.closed

    async def wait_closed(self) -> None:
        return None

    # -- decoding for assertions ------------------------------------------------
    @property
    def request(self) -> str:
        """The HTTP upgrade request."""
        head, _, _ = bytes(self.data).partition(b"\r\n\r\n")
        return head.decode("utf-8")

    def frames(self) -> list[tuple[int, bytes, bool]]:
        """Frames sent after the upgrade request: (opcode, payload, masked).

        Decoded here independently of charger_api (RFC 6455, section 5.2).
        """
        _, _, rest = bytes(self.data).partition(b"\r\n\r\n")
        frames = []
        pos = 0
        while pos < len(rest):
            byte1, byte2 = rest[pos], rest[pos + 1]
            masked = bool(byte2 & 0x80)
            length = byte2 & 0x7F
            pos += 2
            if length == 126:
                (length,) = struct.unpack("!H", rest[pos : pos + 2])
                pos += 2
            elif length == 127:
                (length,) = struct.unpack("!Q", rest[pos : pos + 8])
                pos += 8
            mask = rest[pos : pos + 4] if masked else b""
            pos += len(mask)
            payload = rest[pos : pos + length]
            pos += length
            if masked:
                payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
            frames.append((byte1 & 0x0F, bytes(payload), masked))
        return frames


class FakeServer:
    """Stands in for ``asyncio.open_connection``.

    Every connection gets ``handshake`` followed by ``frames`` (in one segment,
    like a server that answers fast). ``reply_delay`` feeds the frames later
    instead; ``eof`` closes the stream after them.
    """

    def __init__(
        self,
        frames: list[bytes] | None = None,
        handshake: bytes = HANDSHAKE_OK,
        *,
        eof: bool = False,
        reply_delay: float | None = None,
    ) -> None:
        self.frames = frames if frames is not None else [server_frame(build_status())]
        self.handshake = handshake
        self.eof = eof
        self.reply_delay = reply_delay
        self.connections: list[dict[str, Any]] = []
        self.writers: list[FakeWriter] = []
        self.active = 0
        self.max_active = 0

    async def open_connection(self, host: str, port: int, **kwargs: Any):
        self.connections.append({"host": host, "port": port, **kwargs})
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        reader = asyncio.StreamReader()
        writer = FakeWriter(self)
        self.writers.append(writer)
        payload = self.handshake + b"".join(self.frames)

        def _feed() -> None:
            reader.feed_data(payload)
            if self.eof:
                reader.feed_eof()

        if self.reply_delay is None:
            _feed()
        else:
            asyncio.get_running_loop().call_later(self.reply_delay, _feed)
        return reader, writer

    def patch(self):
        return patch.object(charger_api.asyncio, "open_connection", self.open_connection)


class FakeLogin:
    """Replaces the cloud login (aiohttp): records calls, sets session cookies."""

    def __init__(self) -> None:
        self.calls = 0

    def patch(self):
        fake = self

        async def _login(api: ElectroSApi) -> dict[str, str]:
            fake.calls += 1
            api._cookies = {"PHPSESSID": f"session{fake.calls}", "lang": "uk"}
            return api._cookies

        return patch.object(ElectroSApi, "_login_session", _login)


def local_api() -> ElectroSApi:
    return ElectroSApi(login=LOGIN, host=f"  {HOST} ")


def cloud_api() -> ElectroSApi:
    return ElectroSApi(login=LOGIN, password=PASSWORD)


def sent_texts(server: FakeServer) -> list[str]:
    """Text frames the client sent, one per connection."""
    texts = []
    for writer in server.writers:
        frames = writer.frames()
        assert len(frames) == 1
        opcode, payload, masked = frames[0]
        assert opcode == 0x1
        assert masked, "client frames must be masked"
        texts.append(payload.decode("utf-8"))
    return texts


# --------------------------------------------------------------------------- #
# Local and cloud mode
# --------------------------------------------------------------------------- #


async def test_local_mode_reads_status_without_login() -> None:
    """ws://<host>/ws on port 80: no TLS, no cookies, no cloud login."""
    server = FakeServer()
    api = local_api()
    assert api.is_local
    with server.patch():
        state = await api.get_state()

    assert state["status_text"] == "vehicle_connected_idle"
    assert state["energy_kwh"] == 25.8
    assert server.connections == [{"host": HOST, "port": 80, "ssl": None}]
    request = server.writers[0].request.split("\r\n")
    assert request[0] == "GET /ws HTTP/1.1"
    assert f"Host: {HOST}" in request
    assert "Upgrade: websocket" in request
    assert "Sec-WebSocket-Version: 13" in request
    assert "Sec-WebSocket-Protocol: example-protocol" in request
    assert not any(line.startswith(("Cookie:", "Origin:")) for line in request)
    assert sent_texts(server) == [f"site {LOGIN}"]
    assert server.writers[0].closed


async def test_cloud_mode_logs_in_before_every_request() -> None:
    """Cloud: a fresh login per request, TLS to electros.org.ua:443, cookies + Origin."""
    server = FakeServer()
    login = FakeLogin()
    api = cloud_api()
    assert not api.is_local
    with server.patch(), login.patch():
        await api.get_state()
        assert await api.stop_charging() is True

    assert login.calls == 2
    assert [(c["host"], c["port"]) for c in server.connections] == [("electros.org.ua", 443)] * 2
    context = server.connections[0]["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_NONE  # as the original integration
    first, second = (writer.request.split("\r\n") for writer in server.writers)
    assert first[0] == "GET /ws/ HTTP/1.1"
    assert "Host: electros.org.ua" in first
    assert "Origin: https://electros.org.ua" in first
    assert "Cookie: PHPSESSID=session1; lang=uk" in first
    assert "Cookie: PHPSESSID=session2; lang=uk" in second  # the new session
    assert sent_texts(server) == [f"site {LOGIN}", f"site {LOGIN}\nstop"]


async def test_cloud_mode_without_password_never_connects() -> None:
    server = FakeServer()
    api = ElectroSApi(login=LOGIN, password=None)
    with server.patch(), patch.object(ElectroSApi, "_login_session", REAL_LOGIN_SESSION):
        with pytest.raises(ElectroSError, match="password"):
            await api.get_state()
        assert await api.test_connection() is False
        assert await api.start_charging(16) is False
    assert server.connections == []


async def test_local_reset_energy_does_not_need_the_cloud() -> None:
    """PR #3 always logged in for resetkwt; in local mode it goes to the charger only."""
    server = FakeServer()
    with server.patch():  # the conftest guard fails the test on any cloud login
        assert await local_api().reset_energy() is True
    assert sent_texts(server) == [f"site {LOGIN}\nresetkwt"]
    assert server.connections[0]["host"] == HOST


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("amps", "index"), [(32, 0), (16, 8), (17, 8), (7, 13), (6, 13), (40, 0)]
)
async def test_start_command_uses_the_current_index(amps: int, index: int) -> None:
    server = FakeServer()
    with server.patch():
        assert await local_api().start_charging(amps) is True
    assert sent_texts(server) == [f"site {LOGIN}\nstart\n{index}\n0\n"]


async def test_start_below_6_amps_stops_instead() -> None:
    server = FakeServer()
    with server.patch():
        assert await local_api().start_charging(4) is True
    assert sent_texts(server) == [f"site {LOGIN}\nstop"]


async def test_timer_commands_set_the_clock() -> None:
    """``datetime`` with a window, and without one (timer off); day without a leading zero."""
    server = FakeServer()
    api = local_api()
    with server.patch():
        assert await api.set_timer("23:00:00", "7:00", datetime(2026, 10, 5, 20, 15, 3)) is True
        assert await api.disable_timer(datetime(2026, 11, 28, 9, 5, 0)) is True
    assert sent_texts(server) == [
        f"site {LOGIN}\ndatetime\n5.10.2026T20:15:03\n23:00\n07:00\n",
        f"site {LOGIN}\ndatetime\n28.11.2026T09:05:00\n\n\n",
    ]


async def test_command_without_reply_still_counts_as_sent() -> None:
    """The frame is on the wire; a silent server does not make the command fail."""
    server = FakeServer(frames=[])
    with server.patch(), patch.object(charger_api, "COMMAND_REPLY_TIMEOUT", 0.05):
        assert await local_api().stop_charging() is True
    assert sent_texts(server) == [f"site {LOGIN}\nstop"]


# --------------------------------------------------------------------------- #
# Framing and failures
# --------------------------------------------------------------------------- #


async def test_ping_before_the_status_is_skipped() -> None:
    server = FakeServer(frames=[server_frame(b"", 0x9), server_frame(b"", 0xA), server_frame(build_status(code="2"))])
    with server.patch():
        state = await local_api().get_state()
    assert state["status_text"] == "charging"


async def test_large_status_frame_with_64_bit_length() -> None:
    """Status messages grow with the history; any frame length is read completely."""
    long_history = " ".join(["25.10 22:29:57 25800000"] * 3000)  # > 64 KiB
    message = build_status(history=long_history)
    assert len(message.encode()) > 65535
    server = FakeServer(frames=[server_frame(message)])
    with server.patch():
        state = await local_api().get_state()
    assert state["status_text"] == "vehicle_connected_idle"
    assert len(state["history"]) > 0


@pytest.mark.parametrize("length", [0, 5, 125, 126, 300, 65535, 65536, 70000])
async def test_client_frames_round_trip(length: int) -> None:
    message = "x" * length
    frame = charger_api._encode_text_frame(message)
    assert frame[0] == 0x81  # FIN + text
    assert frame[1] & 0x80  # masked
    reader = asyncio.StreamReader()
    reader.feed_data(frame)
    reader.feed_eof()
    opcode, payload = await charger_api._read_frame(reader)
    assert (opcode, payload.decode()) == (0x1, message)


@pytest.mark.parametrize(
    "handshake",
    [
        b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n",
        b"HTTP/1.1 200 OK\r\n\r\n",
    ],
)
async def test_rejected_upgrade(handshake: bytes) -> None:
    """Expired session or wrong path: polls fail, commands report False."""
    server = FakeServer(handshake=handshake)
    api = local_api()
    with server.patch():
        with pytest.raises(ElectroSError, match="upgrade failed"):
            await api.get_state()
        assert await api.start_charging(16) is False
        assert await api.test_connection() is False
    assert all(writer.closed for writer in server.writers)


async def test_connection_closed_during_the_handshake() -> None:
    server = FakeServer(frames=[], handshake=b"HTTP/1.1 101 Switch", eof=True)
    with server.patch(), pytest.raises(ElectroSError, match="upgrade failed"):
        await local_api().get_state()


@pytest.mark.parametrize(
    ("frames", "error"),
    [
        ([], "no status reply"),  # silent server
        ([server_frame(b"", 0x8)], "no status reply"),  # close frame
        ([server_frame(b"\x00\x01", 0x2)], "no status reply"),  # binary frame
        ([server_frame("site ES123456\nok")], "unexpected reply"),  # not a status
    ],
)
async def test_bad_status_replies(frames: list[bytes], error: str) -> None:
    server = FakeServer(frames=frames)
    with server.patch(), patch.object(charger_api, "READ_TIMEOUT", 0.05):
        with pytest.raises(ElectroSError, match=error):
            await local_api().get_state()


async def test_connect_errors_become_electros_errors() -> None:
    async def _refuse(*_args: Any, **_kwargs: Any):
        raise OSError("connection refused " + "x" * 500)

    with patch.object(charger_api.asyncio, "open_connection", _refuse):
        with pytest.raises(ElectroSError) as err:
            await local_api().get_state()
        assert len(str(err.value)) <= 200
        assert await local_api().stop_charging() is False


async def test_requests_never_overlap() -> None:
    """Polls and commands share one lock: one connection at a time."""
    server = FakeServer(reply_delay=0.02)
    api = local_api()
    with server.patch():
        results = await asyncio.gather(
            api.get_state(), api.start_charging(10), api.get_state(), api.stop_charging()
        )
    assert results[1] is True and results[3] is True
    assert len(server.connections) == 4
    assert server.max_active == 1


async def test_raw_messages_and_secrets_are_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.electros_ev")
    server = FakeServer()
    api = local_api()
    with server.patch():
        state = await api.get_state()
        await api.start_charging(16)
        await api.set_timer("23:00", "07:00", datetime(2026, 10, 28, 20, 15))
    for secret in SECRETS.values():
        assert secret not in caplog.text
        assert secret not in repr(state)
    assert "Status 1 (vehicle_connected_idle)" in caplog.text


def test_safe_error() -> None:
    assert charger_api._safe_error(asyncio.TimeoutError()) == "timeout"
    assert charger_api._safe_error(ValueError()) == "ValueError"
    assert len(charger_api._safe_error(RuntimeError("y" * 1000))) == 200


def test_protocol_constants_match_the_cloud() -> None:
    """docs/protocol.md: wss://electros.org.ua/ws/, subprotocol example-protocol, ws://<ip>/ws."""
    api = cloud_api()
    assert (api._ws_host, api._ws_path, api._ws_protocol) == (
        "electros.org.ua",
        "/ws/",
        "example-protocol",
    )
    assert api._ws_origin == "https://electros.org.ua"
    assert api._base_url == "https://electros.org.ua/auth/"
    assert (charger_api.DEFAULT_LOCAL_PORT, charger_api.DEFAULT_LOCAL_WS_PATH) == (80, "/ws")
    assert protocol.build_status(LOGIN) == f"site {LOGIN}"


async def test_command_counts_as_sent_when_the_server_closes_after_it() -> None:
    """EOF instead of a reply after the frame is written: still sent (docs/protocol.md)."""
    server = FakeServer(frames=[], eof=True)
    with server.patch():
        assert await local_api().stop_charging() is True
    assert sent_texts(server) == [f"site {LOGIN}\nstop"]
