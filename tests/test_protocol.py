"""Tests for custom_components/electros_ev/protocol.py.

protocol.py is stdlib-only, so these tests run on bare Python without Home
Assistant: ``python -m pytest tests/test_protocol.py`` (add ``--noconftest`` if a
conftest.py in tests/ needs Home Assistant). The module is loaded by file path
so the integration package (which imports homeassistant) is not imported.
"""

from __future__ import annotations

from datetime import datetime, time
import importlib.util
from pathlib import Path
import sys

import pytest

_PROTOCOL_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "electros_ev"
    / "protocol.py"
)
_spec = importlib.util.spec_from_file_location("electros_protocol", _PROTOCOL_PATH)
protocol = importlib.util.module_from_spec(_spec)
sys.modules["electros_protocol"] = protocol
_spec.loader.exec_module(protocol)

LOGIN = "ES123456"

SECRETS = {
    0: "MyRouterSSID-SECRET0",
    1: "router-password-SECRET1",
    2: "ElectroS_AP-SECRET2",
    3: "ap-password-SECRET3",
    28: "PRODUCT-SECRET28",
    29: "cloud-password-SECRET29",
}


def build_status(**overrides: str) -> str:
    """A realistic 37-line status message (see docs/protocol.md), fake secrets."""
    fields = [
        SECRETS[0],  # 0 router SSID
        SECRETS[1],  # 1 router password
        SECRETS[2],  # 2 charger AP name
        SECRETS[3],  # 3 charger AP password
        "electros.org.ua",  # 4 server host
        "8000",  # 5 server port
        "32",  # 6 ? (earth control)
        "16",  # 7 configured current, A
        "2",  # 8 status
        "158",  # 9 measured current x0.1 A
        "229",  # 10 mains voltage now, V
        "25800000",  # 11 energy kWh x1e6
        "36340",  # 12 power kW x1e4
        "3",  # 13 neutral voltage
        "0",  # 14 ?
        "28.10.2026T20:15:03",  # 15 charger clock
        "247",  # 16 mains voltage max
        "201",  # 17 mains voltage min
        "5",  # 18 neutral max
        "0",  # 19 neutral min
        "226",  # 20 mains voltage average
        "2",  # 21 neutral average
        "1000",  # 22 voltage calibration
        "1000",  # 23 current calibration
        "180",  # 24 min charging voltage
        "0",  # 25 leakage control index
        "23:00",  # 26 timer start
        "07:00",  # 27 timer end
        SECRETS[28],  # 28 product number
        SECRETS[29],  # 29 cloud password
        "07:00:00 21 23:00:00 20 23:00:05 2",  # 30 event log
        "10 1000 2000 3000 4000 5000 6000 7000 8000 9000 120500 0 0",  # 31 months (Wh)
        # 32 sessions: dd.mm HH:MM:SS kWh x1e6 (unsorted, one from last year, one empty slot)
        "25.10 22:29:57 25800000 30.12 10:00:00 5000000 "
        "27.10 01:10:00 12345678 00.00 00:00:00 0",
        "Memory: 1234 5678",  # 33
        "0",  # 34
        "0",  # 35
        "",  # 36 trailing
    ]
    assert len(fields) == 37
    for key, value in overrides.items():
        fields[int(key.lstrip("f"))] = value
    return "\n".join(fields)


# --------------------------------------------------------------------------- #
# Status parsing
# --------------------------------------------------------------------------- #


def test_parse_status_values() -> None:
    state = protocol.parse_status(build_status())
    assert state["online"] is True
    assert state["status_code"] == "2"
    assert state["status_text"] == "charging"
    assert state["current_amps"] == 16
    assert state["measured_current"] == 15.8
    assert state["energy_kwh"] == 25.8
    assert state["charging_power_w"] == 3634.0
    assert state["device_time"] == "28.10.2026T20:15:03"
    assert state["device_datetime"] == datetime(2026, 10, 28, 20, 15, 3)
    assert state["voltage_max"] == 247
    assert state["voltage_min"] == 201
    assert state["voltage_avg"] == 226


def test_voltage_is_current_field_10_not_max_16() -> None:
    state = protocol.parse_status(build_status(f10="231", f16="250"))
    assert state["mains_voltage"] == 231
    assert state["voltage_max"] == 250


def test_secret_fields_never_exposed() -> None:
    state = protocol.parse_status(build_status())
    dumped = repr(state)
    for secret in SECRETS.values():
        assert secret not in dumped
    assert "electros.org.ua" not in dumped  # field 4 is not needed either
    assert 0 in protocol.SECRET_FIELDS and 1 in protocol.SECRET_FIELDS
    assert 28 in protocol.SECRET_FIELDS and 29 in protocol.SECRET_FIELDS


@pytest.mark.parametrize(
    ("code", "key", "online"),
    [
        ("0", "waiting_for_vehicle", True),
        ("1", "vehicle_connected_idle", True),
        ("2", "charging", True),
        ("3", "charging_fault", True),
        ("4", "charging_blocked", True),
        ("5", "mains_voltage_low", True),
        ("6", "charger_communication_fault", True),
        ("7", "offline", False),
        ("16", "leakage_current_fault", True),
        ("32", "overcurrent_fault", True),
        ("77", "offline", False),
        ("99", "unknown", True),
    ],
)
def test_status_codes(code: str, key: str, online: bool) -> None:
    state = protocol.parse_status(build_status(f8=code))
    assert state["status_code"] == code
    assert state["status_text"] == key
    assert state["online"] is online
    assert key in protocol.STATUS_OPTIONS


def test_status_options_match_contract() -> None:
    assert protocol.STATUS_OPTIONS == [
        "waiting_for_vehicle",
        "vehicle_connected_idle",
        "charging",
        "charging_fault",
        "charging_blocked",
        "mains_voltage_low",
        "charger_communication_fault",
        "offline",
        "leakage_current_fault",
        "overcurrent_fault",
        "unknown",
    ]


def test_not_a_status_message() -> None:
    with pytest.raises(ValueError):
        protocol.parse_status("ok")
    with pytest.raises(ValueError):
        protocol.parse_status("a\nb\nc")


def test_short_message_has_no_timer_info() -> None:
    message = "\n".join(build_status().split("\n")[:20])
    state = protocol.parse_status(message)
    assert state["status_text"] == "charging"
    assert state["timer_known"] is False
    assert state["timer_window"] is None
    assert state["history"] == []
    assert state["months"] is None


def test_garbage_numbers_become_none() -> None:
    state = protocol.parse_status(build_status(f9="", f10="n/a", f11="nan", f12="-"))
    assert state["measured_current"] is None
    assert state["mains_voltage"] is None
    assert state["energy_kwh"] is None
    assert state["charging_power_w"] is None


def test_crlf_tolerated() -> None:
    state = protocol.parse_status(build_status().replace("\n", "\r\n"))
    assert state["mains_voltage"] == 229
    assert state["timer_window"] == ("23:00", "07:00")


# --------------------------------------------------------------------------- #
# Timer window
# --------------------------------------------------------------------------- #


def test_status_timer_window_enabled() -> None:
    state = protocol.parse_status(build_status())
    assert state["timer_known"] is True
    assert state["timer_window"] == ("23:00", "07:00")


@pytest.mark.parametrize(("start", "end"), [("00:00", "00:00"), ("", ""), ("05:00", "05:00")])
def test_status_timer_window_disabled(start: str, end: str) -> None:
    state = protocol.parse_status(build_status(f26=start, f27=end))
    assert state["timer_known"] is True
    assert state["timer_window"] is None


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        ("23:00", "07:00", ("23:00", "07:00")),
        ("7:05", "9:00", ("07:05", "09:00")),
        ("22:30:00", "06:15:59", ("22:30", "06:15")),
        (time(1, 2), time(3, 4), ("01:02", "03:04")),
        ("00:00", "00:00", None),
        ("", "", None),
        ("", "07:00", None),
        (None, None, None),
        ("25:00", "07:00", None),
        ("junk", "07:00", None),
    ],
)
def test_parse_window(start, end, expected) -> None:
    assert protocol.parse_window(start, end) == expected


def test_parse_hhmm() -> None:
    assert protocol.parse_hhmm("7:00") == "07:00"
    assert protocol.parse_hhmm(" 23:59 ") == "23:59"
    assert protocol.parse_hhmm(time(6, 5)) == "06:05"
    for bad in ("24:00", "12:60", "1200", "", None, 5):
        with pytest.raises(ValueError):
            protocol.parse_hhmm(bad)
    assert protocol.hhmm_to_time("23:00") == time(23, 0)


def test_window_str_roundtrip() -> None:
    assert protocol.window_to_str(("23:00", "07:00")) == "23:00-07:00"
    assert protocol.window_to_str(None) == ""
    assert protocol.window_from_str("23:00-07:00") == ("23:00", "07:00")
    assert protocol.window_from_str("") is None
    assert protocol.window_from_str(None) is None
    assert protocol.window_from_str("garbage") is None


@pytest.mark.parametrize(
    ("moment", "allowed"),
    [
        (time(23, 0), True),
        (time(23, 30), True),
        (time(0, 0), True),
        (time(3, 0), True),
        (time(6, 59), True),
        (time(7, 0), False),
        (time(12, 0), False),
        (time(22, 59), False),
    ],
)
def test_window_allows_crossing_midnight(moment: time, allowed: bool) -> None:
    assert protocol.window_allows(("23:00", "07:00"), moment) is allowed


def test_window_allows_same_day_and_disabled() -> None:
    window = ("01:00", "05:00")
    assert protocol.window_allows(window, time(1, 0)) is True
    assert protocol.window_allows(window, time(4, 59)) is True
    assert protocol.window_allows(window, time(5, 0)) is False
    assert protocol.window_allows(window, time(0, 59)) is False
    assert protocol.window_allows(window, datetime(2026, 10, 28, 2, 0)) is True
    assert protocol.window_allows(None, time(12, 0)) is True


# --------------------------------------------------------------------------- #
# Device datetime format
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 3, 5, 8, 7, 9), "5.03.2026T08:07:09"),
        (datetime(2026, 10, 28, 23, 0, 0), "28.10.2026T23:00:00"),
        (datetime(2027, 1, 1, 0, 0, 5), "1.01.2027T00:00:05"),
    ],
)
def test_format_device_datetime(moment: datetime, expected: str) -> None:
    assert protocol.format_device_datetime(moment) == expected


def test_parse_device_datetime() -> None:
    assert protocol.parse_device_datetime("5.03.2026T08:07:09") == datetime(2026, 3, 5, 8, 7, 9)
    assert protocol.parse_device_datetime("28.10.2026T20:15:03") == datetime(2026, 10, 28, 20, 15, 3)
    assert protocol.parse_device_datetime("31.02.2026T00:00:00") is None
    assert protocol.parse_device_datetime("") is None
    assert protocol.parse_device_datetime(None) is None
    moment = datetime(2026, 7, 9, 1, 2, 3)
    assert protocol.parse_device_datetime(protocol.format_device_datetime(moment)) == moment


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #


def test_status_stop_resetkwt_commands() -> None:
    assert protocol.build_status(LOGIN) == "site ES123456"
    assert protocol.build_stop(LOGIN) == "site ES123456\nstop"
    assert protocol.build_resetkwt(LOGIN) == "site ES123456\nresetkwt"


@pytest.mark.parametrize(
    ("amps", "index"),
    [(32, 0), (30, 1), (16, 8), (10, 11), (6, 13), (7, 13), (17, 8), (40, 0)],
)
def test_start_command(amps: int, index: int) -> None:
    assert protocol.build_start(LOGIN, amps) == f"site ES123456\nstart\n{index}\n0\n"


def test_start_command_rejects_too_low_current() -> None:
    with pytest.raises(ValueError):
        protocol.build_start(LOGIN, 4)


def test_amp_index_table() -> None:
    assert protocol.SUPPORTED_AMPS == list(range(6, 33, 2))
    assert protocol.AMP_TO_INDEX[32] == 0
    assert protocol.AMP_TO_INDEX[6] == 13
    assert len(protocol.AMP_TO_INDEX) == 14


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 0), (5, 0), (6, 6), (7, 6), (15, 14), (16, 16), (32, 32), (33, 32), (None, 0), ("x", 0)],
)
def test_normalize_amps(value, expected) -> None:
    assert protocol.normalize_amps(value) == expected


def test_datetime_command_with_window() -> None:
    moment = datetime(2026, 3, 5, 22, 59, 1)
    assert (
        protocol.build_datetime(LOGIN, moment, ("23:00", "7:00"))
        == "site ES123456\ndatetime\n5.03.2026T22:59:01\n23:00\n07:00\n"
    )


def test_datetime_command_disabled() -> None:
    moment = datetime(2026, 10, 28, 20, 0, 0)
    assert (
        protocol.build_datetime(LOGIN, moment, None)
        == "site ES123456\ndatetime\n28.10.2026T20:00:00\n\n\n"
    )
    assert protocol.build_datetime(LOGIN, moment) == protocol.build_datetime(LOGIN, moment, None)


# --------------------------------------------------------------------------- #
# Events, months, history
# --------------------------------------------------------------------------- #


def test_events() -> None:
    state = protocol.parse_status(build_status())
    assert state["events"] == [
        {"time": "07:00:00", "code": 21},
        {"time": "23:00:00", "code": 20},
        {"time": "23:00:05", "code": 2},
    ]
    assert protocol.parse_events("") == []
    assert protocol.parse_events("garbage 12:00:00 10") == [{"time": "12:00:00", "code": 10}]


def test_months() -> None:
    state = protocol.parse_status(build_status())
    assert state["current_month"] == 10
    assert state["months"] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 120.5, 0.0, 0.0]
    assert protocol.month_energy(state["current_month"], state["months"]) == 120.5
    assert protocol.month_energy(None, state["months"]) is None
    assert protocol.month_energy(10, None) is None


def test_months_short_or_invalid() -> None:
    assert protocol.parse_months("") == (None, None)
    assert protocol.parse_months("3 1 2 3") == (3, None)
    current, months = protocol.parse_months("13 " + " ".join(["0"] * 12))
    assert current is None and months == [0.0] * 12
    # Invalid month number: falls back to the charger clock month.
    state = protocol.parse_status(build_status(f31="0 " + " ".join(["1000"] * 12)))
    assert state["current_month"] == 10


def test_history_newest_first_and_year_inference() -> None:
    state = protocol.parse_status(build_status())
    assert state["history"] == [
        {"date": "27.10", "time": "01:10:00", "kwh": 12.346},
        {"date": "25.10", "time": "22:29:57", "kwh": 25.8},
        {"date": "30.12", "time": "10:00:00", "kwh": 5.0},  # 30.12.2025
    ]


def test_history_limit_and_without_reference() -> None:
    raw = " ".join(f"{(i % 28) + 1:02d}.01 10:00:{i % 60:02d} 1000000" for i in range(80))
    history = protocol.parse_history(raw)
    assert len(history) == protocol.HISTORY_MAX == 50
    assert history[0]["date"] == "28.01"
    assert protocol.parse_history("") == []
    assert protocol.parse_history("00.00 00:00:00 0") == []


# --------------------------------------------------------------------------- #
# desired_window
# --------------------------------------------------------------------------- #

FRIDAY = datetime(2026, 10, 30, 21, 0)
SATURDAY = datetime(2026, 10, 31, 0, 0, 5)
SUNDAY = datetime(2026, 11, 1, 23, 30)
MONDAY = datetime(2026, 11, 2, 0, 0, 5)
WINDOW = ("23:00", "07:00")


def test_weekday_fixtures() -> None:
    assert [d.weekday() for d in (FRIDAY, SATURDAY, SUNDAY, MONDAY)] == [4, 5, 6, 0]


def test_desired_window_night_on_off() -> None:
    assert protocol.desired_window(True, "23:00", "07:00", False, False, FRIDAY) == WINDOW
    assert protocol.desired_window(False, "23:00", "07:00", False, False, FRIDAY) is None


def test_desired_window_boost_disables_timer() -> None:
    assert protocol.desired_window(True, "23:00", "07:00", True, False, FRIDAY) is None
    assert protocol.desired_window(True, "23:00", "07:00", True, True, MONDAY) is None


@pytest.mark.parametrize(
    ("moment", "expected"),
    [(FRIDAY, WINDOW), (SATURDAY, None), (SUNDAY, None), (MONDAY, WINDOW)],
)
def test_desired_window_weekend_free(moment: datetime, expected) -> None:
    assert protocol.desired_window(True, "23:00", "07:00", False, True, moment) == expected


def test_desired_window_weekend_free_off_keeps_window_on_weekend() -> None:
    assert protocol.desired_window(True, "23:00", "07:00", False, False, SATURDAY) == WINDOW
    assert protocol.desired_window(True, "23:00", "07:00", False, False, SUNDAY) == WINDOW


def test_desired_window_crossing_midnight_weekend_boundary() -> None:
    """Friday night window 23:00-07:00 runs into Saturday: the 00:00:05 check frees it."""
    friday_late = datetime(2026, 10, 30, 23, 59, 59)
    saturday_early = datetime(2026, 10, 31, 0, 0, 5)
    sunday_late = datetime(2026, 11, 1, 23, 59, 59)
    monday_early = datetime(2026, 11, 2, 0, 0, 5)
    assert protocol.desired_window(True, "23:00", "07:00", False, True, friday_late) == WINDOW
    assert protocol.desired_window(True, "23:00", "07:00", False, True, saturday_early) is None
    assert protocol.desired_window(True, "23:00", "07:00", False, True, sunday_late) is None
    # Monday 00:00:05 restores the window, and it already allows charging until 07:00.
    window = protocol.desired_window(True, "23:00", "07:00", False, True, monday_early)
    assert window == WINDOW
    assert protocol.window_allows(window, monday_early) is True


def test_desired_window_normalizes_and_rejects_empty_window() -> None:
    assert protocol.desired_window(True, "7:00", time(9, 30), False, False, FRIDAY) == ("07:00", "09:30")
    assert protocol.desired_window(True, "05:00", "05:00", False, False, FRIDAY) is None
    assert protocol.desired_window(True, "", "", False, False, FRIDAY) is None
