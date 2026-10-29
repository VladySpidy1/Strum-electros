"""Constants for ElectroS EV Charger integration."""

from __future__ import annotations

from .protocol import (  # re-exported for backwards compatibility
    AMP_TO_INDEX,
    OFFLINE_STATUS_CODES,
    STATUS_KEYS as STATUS_TEXT_EN,
    STATUS_OPTIONS,
    SUPPORTED_AMPS,
)

DOMAIN = "electros_ev"
DEFAULT_SCAN_INTERVAL = 60
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 600

CONF_CHARGER_LOGIN = "charger_login"
CONF_CHARGER_PASSWORD = "charger_password"
CONF_HOST = "host"  # optional: charger IP/hostname for local (no-cloud) connection
CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_LOCAL_PORT = 80
DEFAULT_LOCAL_WS_PATH = "/ws"
CONF_WS_HOST = "ws_host"
CONF_WS_PATH = "ws_path"
CONF_WS_ORIGIN = "ws_origin"
CONF_WS_PROTOCOL = "ws_protocol"
CONF_BASE_URL = "base_url"

DEFAULT_WS_HOST = "electros.org.ua"
DEFAULT_WS_PATH = "/ws/"
DEFAULT_WS_ORIGIN = "https://electros.org.ua"
DEFAULT_WS_PROTOCOL = "example-protocol"
DEFAULT_BASE_URL = "https://electros.org.ua/auth/"

# Timer / boost defaults
DEFAULT_TIMER_START = "23:00"
DEFAULT_TIMER_END = "07:00"
DEFAULT_BOOST_MINUTES = 30
MIN_BOOST_MINUTES = 5
MAX_BOOST_MINUTES = 480
BOOST_MINUTES_STEP = 5
DEFAULT_CHARGING_AMPS = 6

# Energy boost ("charge N kWh")
DEFAULT_BOOST_KWH = 10
MIN_BOOST_KWH = 1
MAX_BOOST_KWH = 80
BOOST_KWH_STEP = 1
ENERGY_BOOST_SCAN_INTERVAL = 20  # s, polling while an energy boost runs

# Two-zone tariff (prices per kWh in the HA currency; the night zone is an
# options-flow setting, independent of the charger timer)
CONF_TARIFF_NIGHT_START = "tariff_night_start"
CONF_TARIFF_NIGHT_END = "tariff_night_end"
DEFAULT_TARIFF_NIGHT_START = "23:00"
DEFAULT_TARIFF_NIGHT_END = "07:00"
DEFAULT_PRICE_DAY = 4.32
DEFAULT_PRICE_NIGHT = 2.16
MIN_PRICE = 0.0
MAX_PRICE = 100.0
PRICE_STEP = 0.01

# Services
ATTR_DEVICE_ID = "device_id"
ATTR_MINUTES = "minutes"
ATTR_KWH = "kwh"
ATTR_AMPS = "amps"
ATTR_START = "start"
ATTR_END = "end"
ATTR_ENABLED = "enabled"
SERVICE_CHARGE_FOR = "charge_for"
SERVICE_CHARGE_ENERGY = "charge_energy"
SERVICE_EXTEND_CHARGE = "extend_charge"
SERVICE_STOP_CHARGING = "stop_charging"
SERVICE_SET_TIMER = "set_timer"
SERVICE_MAX_MINUTES = 1440
SERVICE_MIN_KWH = 0.5
SERVICE_MAX_KWH = 100

# Frontend card
CARD_URL = f"/{DOMAIN}/electros-card.js"
CARD_FILENAME = "electros-card.js"

# Legacy Ukrainian status names (kept for anything that still imports them).
STATUS_TEXT = {
    "0": "Очікування авто",
    "1": "Авто підключено",
    "2": "Заряджає",
    "3": "Помилка зарядки",
    "4": "Зарядку заблоковано",
    "5": "Низька напруга мережі",
    "6": "Помилка зв'язку",
    "7": "Офлайн",
    "16": "Витік струму",
    "32": "Струм перевищив 40 А",
    "77": "Офлайн",
}

