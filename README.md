# pumpspy-local

[![Validate](https://github.com/matuszeg/pumpspy-local/actions/workflows/validate.yml/badge.svg)](https://github.com/matuszeg/pumpspy-local/actions/workflows/validate.yml)
[![HACS: custom repository](https://img.shields.io/badge/HACS-custom%20repository-41BDF5.svg)](https://hacs.xyz/)

**Local, cloud-free monitoring for PumpSpy and PitBoss+ sump pump battery backup systems, in [Home Assistant](https://www.home-assistant.io/).**

No vendor account. No cloud dependency. No polling delay.

Supports PumpSpy monitors and the PitBoss+ system from Richtech Industries, which share the same underlying platform.

## Why

These monitors report their telemetry to a vendor cloud service over **unencrypted HTTP**. `pumpspy-local` reads that traffic on your own network, parses it, and turns it into Home Assistant entities.

The result is real-time, entirely local sensors — no account, no credentials sent anywhere, and no dependency on the vendor's servers being up or their API staying the same.

The device's data is still **forwarded upstream unchanged**, so the vendor's own app and alerting keep working exactly as before. This is a read-and-relay tool: it adds a local view of data your device already broadcasts.

### How this differs from existing projects

Other Home Assistant integrations for these pumps authenticate to the vendor's cloud API with your account and poll it every few minutes. This project doesn't talk to the vendor's API at all, and never handles your credentials. Readings arrive as the device reports them.

## How it works

1. Your network sends the device's reporting traffic to the machine running `pumpspy-local` instead of to the vendor — a redirect on your router, or a DNS rewrite if your device will re-resolve. The [setup guide](docs/setup.md) covers both, and why the redirect is usually the one that actually works.
2. `pumpspy-local` receives the device's HTTP telemetry and:
   - creates Home Assistant entities from it automatically, and
   - forwards the original requests upstream to the vendor, unmodified.

Nothing is installed on the device. Its firmware is never modified.

### One consequence, stated plainly

Once the device's traffic goes through Home Assistant, **the vendor's own alerting depends on Home Assistant being up** — it did not before. The device does not buffer: it retries three times, then drops the event for good.

If you rely on those cloud alerts as a flood safety net, run the [fail-open shim](docs/fail-open-shim.md) as well. It is a small nginx config that tries Home Assistant first and falls back to the vendor automatically. On the reference install it took the cost of a Home Assistant restart from more than seven minutes of lost vendor delivery down to zero.

## Sensors

- **Battery voltage** — resting, and **under load** (the reading that actually reveals a failing battery)
- **Mains power** — lost / restored
- **High water** alarm
- **Pump failure** alarm
- **Pump cycles** — per run: which pump, duration, motor current, estimated gallons
- **Wi-Fi signal strength**

## Features

- Entities appear automatically — no MQTT broker, no YAML
- Real-time updates, as the device reports
- Firmware update **capture and hold-for-approval** — new firmware can be quarantined for your review instead of installing silently (opt-in, off by default)
- Installs through HACS, and works on Home Assistant OS, Supervised, Container, and Core
- An optional [dashboard](dashboard/) built for the one question that matters at 2 a.m. — is the pit being kept dry, and will the backup work if it is needed

## Install

### Through HACS

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Add `https://github.com/matuszeg/pumpspy-local`, with category **Integration**.
3. Find *pumpspy-local* in the HACS list, install it, and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration** and search for *pumpspy-local*.

### By hand

Copy `custom_components/pumpspy_local/` into your Home Assistant configuration
directory, under `custom_components/`, and restart. Then add it from **Settings →
Devices & services** as above.

Installing it is the easy half. The integration sits there doing nothing until
the device's traffic actually reaches it, and that part depends on your network
— see the [setup guide](docs/setup.md), which walks through the options and how
to tell whether it worked.

## Requirements

- [HACS](https://hacs.xyz/) for installation, or copy the integration in by hand
- A way to make the device's traffic reach Home Assistant: a router that can redirect it, or DNS you control *and* a device that will re-resolve. This is the one requirement that depends on your network — see the [setup guide](docs/setup.md)

## Status

Working, and running against real hardware. The protocol was decoded from packet captures rather than guessed at, and the integration, firmware quarantine, the dashboard and the fail-open shim are all in daily use on a real pump. Expect rough edges in setup rather than in operation: getting the device's traffic to Home Assistant is the part that varies most between networks.

## Disclaimer

This project is **not affiliated with, endorsed by, or sponsored by** PumpSpy, Richtech Industries, or any related company. "PumpSpy", "PitBoss", and "PitBoss+" are trademarks of their respective owners, used here only to identify the hardware this software is compatible with.

This software observes and relays data that your own device transmits on your own network. It is provided for interoperability and personal use, without warranty of any kind. You are responsible for how you use it.

## License

[MIT](LICENSE)
