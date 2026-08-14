"""Shared constants."""

DOMAIN = "pumpspy_local"

MANUFACTURER = "Richtech"
MODEL = "PumpSpy / PitBoss+"

# Fired with a DeviceState when a device reports for the first time. Devices are
# never configured up front; they are adopted when they show up.
SIGNAL_NEW_DEVICE = f"{DOMAIN}_new_device"


def signal_device_update(device_id: str) -> str:
    """Signal fired when a device's state changes."""
    return f"{DOMAIN}_update_{device_id}"
