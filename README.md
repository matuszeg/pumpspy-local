# Levee

Local, cloud-free monitoring for Wi-Fi sump pump battery backup systems — brought into [Home Assistant](https://www.home-assistant.io/).

Works with PitBoss+ (Richtech Industries) and PumpSpy monitors, which are the same underlying platform.

## Why

These monitors report their telemetry to a vendor cloud over **unencrypted HTTP**. Levee intercepts that traffic on your own network, parses it, and republishes it to Home Assistant over MQTT — so you get real-time, local sensors with no vendor account and no dependency on the vendor's servers staying up. The original data is still forwarded upstream, so the vendor's own app keeps working exactly as before.

Nothing is installed on the device, and its firmware is never modified. Levee only reads (and relays) what the device already sends.

## How it works

1. A DNS redirect on your network points the device's reporting hostname at the machine running Levee (e.g. an [AdGuard](https://adguard.com/) / Pi-hole rewrite, or a per-network DNS setting on an isolated VLAN).
2. Levee receives the device's HTTP telemetry, parses it, and:
   - publishes the readings to Home Assistant via MQTT discovery (sensors are created automatically), and
   - forwards the requests upstream to the vendor unchanged.

## Planned sensors

- Battery voltage — resting and **under load** (the reading that actually reveals a failing battery)
- High-water alarm
- Pump-failure alarm
- Per-pump run current and duration (primary and backup)
- Wi-Fi signal strength

## Planned features

- Home Assistant MQTT discovery — sensors appear with no manual configuration
- Firmware-update **capture and hold-for-approval** — new firmware is quarantined for your review instead of installing silently (opt-in)
- Distributed as both a Home Assistant add-on and a standalone Docker image

## Status

Early development. Protocol decoding is in progress against real hardware.

## License

[MIT](LICENSE)
