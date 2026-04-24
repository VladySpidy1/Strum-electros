# ElectroS EV Charger — Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Custom Home Assistant integration for **ElectroS EV** chargers (electros.org.ua).

Reads real-time charger data and allows controlling charging via Home Assistant.

## Features

### Sensors
| Sensor | Unit | Description |
|--------|------|-------------|
| Status | — | Charger status (waiting, charging, offline, fault...) |
| Configured current | A | Currently set charging amperage |
| Measured current | A | Actual measured current |
| Mains voltage | V | Grid voltage |
| Charging power | W | Calculated power (current × voltage) |

### Controls
| Entity | Type | Description |
|--------|------|-------------|
| Charging switch | Switch | Start / stop charging |
| Charging amps | Number (slider) | Set amperage: 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32 A |

## Installation

### HACS (Custom Repository)

1. Open HACS in Home Assistant
2. Click **⋮** (top right) → **Custom repositories**
3. Add this repository URL, category: **Integration**
4. Click **Download**
5. Restart Home Assistant

### Manual

1. Copy `custom_components/electros_ev/` to your HA `config/custom_components/`
2. Restart Home Assistant

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **ElectroS EV Charger**
3. Enter your electros.org.ua login (device name) and password
4. Set the update interval (default: 60 seconds)

## How it works

The integration communicates with the ElectroS cloud service (electros.org.ua) via WebSocket.
It logs in with your credentials, opens a WebSocket connection, and reads/sends charger commands.

## Supported charger statuses

| Code | Status |
|------|--------|
| 0 | Waiting for vehicle |
| 1 | Vehicle connected (idle) |
| 2 | Charging |
| 3 | Charging fault |
| 4 | Charging blocked |
| 5 | Mains voltage low |
| 6 | Communication fault |
| 7 | Offline |
| 16 | Leakage current fault |

## Requirements

- An ElectroS EV charger registered at electros.org.ua
- Home Assistant 2024.1+

## Disclaimer

This project is **not affiliated with, endorsed by, or associated with ElectroS** or any of its subsidiaries.

This software is provided **"as is"**, without warranty of any kind, express or implied. The authors and contributors are **not responsible for any damage, data loss, hardware malfunction, or any other harm** that may result from the use of this integration.

By using this integration you acknowledge that:

- You use it **at your own risk**
- The integration communicates with a third-party cloud service (electros.org.ua) which may change its API at any time without notice
- The authors have no control over the ElectroS service availability or behavior
- Incorrect use of charging controls may affect your EV charger or vehicle — always verify charger behavior independently
- Your credentials are stored locally in Home Assistant and are only sent to electros.org.ua for authentication

## License

MIT
