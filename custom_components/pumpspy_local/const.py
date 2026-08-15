"""Shared constants."""

DOMAIN = "pumpspy_local"

MANUFACTURER = "Richtech"
MODEL = "PumpSpy / PitBoss+"

CONF_PORT = "port"
CONF_UPSTREAM = "upstream"
CONF_FLOW_RATE = "flow_rate"
CONF_FIRMWARE_POLICY = "firmware_policy"
CONF_CHECK_INTERVAL_HOURS = "firmware_check_hours"

# The endpoint the device polls every ~13 seconds.
FIRMWARE_PATH = "/new_firmware"

POLICY_OBSERVE = "observe"
POLICY_QUARANTINE = "quarantine"
DEFAULT_FIRMWARE_POLICY = POLICY_OBSERVE
DEFAULT_CHECK_INTERVAL_HOURS = 24

# Fired when the vendor offers an update.
SIGNAL_FIRMWARE = f"{DOMAIN}_firmware"

# The port the device reports to, and the vendor it reports to. Both are what a
# real installation needs; a development instance must point upstream somewhere
# harmless instead.
DEFAULT_PORT = 8081
DEFAULT_UPSTREAM = "http://www.pumpspy.com:8081"

# Fired with a DeviceState when a device reports for the first time. Devices are
# never configured up front; they are adopted when they show up.
SIGNAL_NEW_DEVICE = f"{DOMAIN}_new_device"


def signal_device_update(device_id: str) -> str:
    """Signal fired when a device's state changes."""
    return f"{DOMAIN}_update_{device_id}"


def signal_pump_run(device_id: str) -> str:
    """Signal fired with a PumpRun when a device reports a pump cycle.

    Separate from the state-update signal because a run is an occurrence, not a
    value: firing it on every message would invent runs that never happened.
    """
    return f"{DOMAIN}_pump_run_{device_id}"
