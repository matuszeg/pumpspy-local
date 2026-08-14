# pumpspy-local

**Local, cloud-free monitoring for PumpSpy and PitBoss+ sump pump battery backup systems, in [Home Assistant](https://www.home-assistant.io/).**

No vendor account. No cloud dependency. No polling delay.

Supports PumpSpy monitors and the PitBoss+ system from Richtech Industries, which share the same underlying platform.

## Why

These monitors report their telemetry to a vendor cloud service over **unencrypted HTTP**. `pumpspy-local` reads that traffic on your own network, parses it, and republishes it to Home Assistant over MQTT.

The result is real-time, entirely local sensors — no account, no credentials sent anywhere, and no dependency on the vendor's servers being up or their API staying the same.

The device's data is still **forwarded upstream unchanged**, so the vendor's own app and alerting keep working exactly as before. This is a read-and-relay tool: it adds a local view of data your device already broadcasts.

### How this differs from existing projects

Other Home Assistant integrations for these pumps authenticate to the vendor's cloud API with your account and poll it every few minutes. This project doesn't talk to the vendor's API at all, and never handles your credentials. Readings arrive as the device reports them.

## How it works

1. A DNS redirect on your network points the device's reporting hostname at the machine running `pumpspy-local` — a rewrite in AdGuard Home or Pi-hole, or a DNS setting on an isolated VLAN.
2. `pumpspy-local` receives the device's HTTP telemetry and:
   - publishes readings to Home Assistant via **MQTT discovery** (entities are created automatically), and
   - forwards the original requests upstream to the vendor, unmodified.

Nothing is installed on the device. Its firmware is never modified.

## Sensors

- **Battery voltage** — resting, and **under load** (the reading that actually reveals a failing battery)
- **Mains power** — lost / restored
- **High water** alarm
- **Pump failure** alarm
- **Pump cycles** — per run: which pump, duration, motor current, estimated gallons
- **Wi-Fi signal strength**

## Features

- Home Assistant MQTT discovery — entities appear with no manual configuration
- Real-time updates, as the device reports
- Firmware update **capture and hold-for-approval** — new firmware can be quarantined for your review instead of installing silently (opt-in, off by default)
- Ships as a Home Assistant add-on and as a standalone Docker image

## Requirements

- An MQTT broker reachable by Home Assistant (e.g. the Mosquitto add-on)
- The ability to override DNS for the device on your network

## Status

Early development. The device protocol has been decoded and verified against real hardware; implementation is in progress.

## Disclaimer

This project is **not affiliated with, endorsed by, or sponsored by** PumpSpy, Richtech Industries, or any related company. "PumpSpy", "PitBoss", and "PitBoss+" are trademarks of their respective owners, used here only to identify the hardware this software is compatible with.

This software observes and relays data that your own device transmits on your own network. It is provided for interoperability and personal use, without warranty of any kind. You are responsible for how you use it.

## License

[MIT](LICENSE)
