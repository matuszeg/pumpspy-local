"""Shared constants."""

DOMAIN = "pumpspy_local"

MANUFACTURER = "Richtech"

# The integration's own device, which the vendor-reachability sensor belongs to
# and the pumps hang off. Not hardware, so it has no manufacturer.
SERVICE_DEVICE_NAME = "PumpSpy Local"

# Together with the device name this is what Home Assistant slugifies into the
# sensor's entity id, which the documented automation names. Kept here so the
# two cannot drift apart quietly.
VENDOR_ENTITY_NAME = "Vendor reachable"
MODEL = "PumpSpy / PitBoss+"

CONF_PORT = "port"
CONF_UPSTREAM = "upstream"
CONF_FLOW_RATE = "flow_rate"
CONF_FIRMWARE_POLICY = "firmware_policy"
CONF_CHECK_INTERVAL_HOURS = "firmware_check_hours"

# The redirect that sends the device here answers for this host too, so the
# vendor has to be located some other way: a resolver the redirect is not
# installed in, or an address given outright.
CONF_NAMESERVER = "nameserver"
CONF_UPSTREAM_IP = "upstream_ip"

# The endpoint the device polls every ~13 seconds.
FIRMWARE_PATH = "/new_firmware"

POLICY_OBSERVE = "observe"
POLICY_QUARANTINE = "quarantine"
DEFAULT_FIRMWARE_POLICY = POLICY_OBSERVE
DEFAULT_CHECK_INTERVAL_HOURS = 24

# Fired when the vendor offers an update.
SIGNAL_FIRMWARE = f"{DOMAIN}_firmware"

# Fired after every forward attempt, with the config entry id. Carries no
# payload of its own: what changed is the running verdict on the vendor, which
# the entity reads back out of the runtime.
SIGNAL_VENDOR = f"{DOMAIN}_vendor"

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
