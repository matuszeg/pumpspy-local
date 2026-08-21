"""Binary sensor entities: the alarms."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    SERVICE_DEVICE_NAME,
    VENDOR_ENTITY_NAME,
    SIGNAL_FIRMWARE,
    SIGNAL_NEW_DEVICE,
    SIGNAL_VENDOR,
)
from .core.state import DeviceState
from .entity import PumpspyEntity


@dataclass(frozen=True, kw_only=True)
class PumpspyBinarySensorDescription(BinarySensorEntityDescription):
    """A binary sensor and how to read it out of device state."""

    value_fn: Callable[[DeviceState], bool | None]


BINARY_SENSORS: tuple[PumpspyBinarySensorDescription, ...] = (
    PumpspyBinarySensorDescription(
        key="mains_power",
        name="Mains power",
        device_class=BinarySensorDeviceClass.POWER,
        # ac_power 1 means mains present, so "off" is the alarming state here.
        value_fn=lambda device: device.ac_power,
    ),
    PumpspyBinarySensorDescription(
        key="high_water",
        name="High water",
        device_class=BinarySensorDeviceClass.MOISTURE,
        value_fn=lambda device: device.high_water,
    ),
    PumpspyBinarySensorDescription(
        key="pump_failure",
        name="Pump failure",
        device_class=BinarySensorDeviceClass.PROBLEM,
        # The device latches this and never sends 0; clearing it is issue #5.
        value_fn=lambda device: device.motor_fail,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors, now and as devices appear."""
    runtime = hass.data[DOMAIN][entry.entry_id]

    @callback
    def _add(device: DeviceState) -> None:
        entities: list[PumpspyEntity] = [
            PumpspyBinarySensor(device, description, entry.entry_id)
            for description in BINARY_SENSORS
        ]
        entities.append(
            FirmwareUpdate(device, FIRMWARE_DESCRIPTION, runtime, entry.entry_id)
        )
        async_add_entities(entities)

    for device in runtime.devices.values():
        _add(device)

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW_DEVICE, _add))

    # Added unconditionally, not when a device appears: this is the one entity
    # that is wanted precisely when no device is reporting.
    async_add_entities([VendorReachable(entry, runtime)])


class PumpspyBinarySensor(PumpspyEntity, BinarySensorEntity):
    """An alarm pulled out of device state."""

    entity_description: PumpspyBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self._device)


FIRMWARE_DESCRIPTION = BinarySensorEntityDescription(
    key="firmware_update",
    name="Firmware update",
    device_class=BinarySensorDeviceClass.UPDATE,
)


class FirmwareUpdate(PumpspyEntity, BinarySensorEntity):
    """Whether the vendor is currently offering this device an update.

    Reads from the firmware checker rather than device state: this is something
    we observed about the vendor's answer, not something the device told us.
    """

    def __init__(self, device: DeviceState, description, runtime, entry_id) -> None:
        super().__init__(device, description, entry_id)
        self._runtime = runtime

    @property
    def is_on(self) -> bool:
        return self._runtime.firmware_for(self._device.device_id).update_offered

    @property
    def extra_state_attributes(self) -> dict[str, bool]:
        checker = self._runtime.firmware_for(self._device.device_id)
        return {"held_for_approval": checker.held is not None}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_FIRMWARE, self._handle_firmware
            )
        )

    @callback
    def _handle_firmware(self, device_id: str) -> None:
        if device_id == self._device.device_id:
            self.async_write_ha_state()


class VendorReachable(BinarySensorEntity):
    """Whether the vendor is answering the requests we forward it.

    The point of this is telling two silences apart. When the vendor's device
    API died on 2026-08-20 the phone alert blamed Home Assistant and the
    redirect rules, and both were fine -- the vendor was down, and the device
    eventually gave up talking to anyone, which looks identical from here.

    It belongs to the config entry rather than to a pump: the vendor is reached
    over one connection no matter how many devices report through it. So it
    hangs off a service device of its own, which also means it exists from the
    moment the integration loads, rather than waiting for a device to report.

    Reachability freezes once the device goes quiet, because a quiet device
    means nothing is being forwarded and so nothing is being learned. That is
    deliberate: the alternative is originating our own traffic to the vendor,
    which this project has never done. A frozen verdict still answers the
    question that matters -- was the vendor failing when the reports stopped?
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_name = VENDOR_ENTITY_NAME

    def __init__(self, entry: ConfigEntry, runtime) -> None:
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_vendor_reachable"
        self._entry_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            # A service, not hardware: nobody manufactured this one.
            entry_type=DeviceEntryType.SERVICE,
            name=SERVICE_DEVICE_NAME,
        )

    @property
    def is_on(self) -> bool | None:
        """None until something has been forwarded.

        "Never asked" is not "answering fine", and an alert that cannot tell
        those apart is the thing this exists to fix.
        """
        return self._runtime.vendor.reachable

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        vendor = self._runtime.vendor
        return {
            # When a message last got through, which is the figure the
            # notification actually wants to quote.
            "last_delivery": vendor.last_delivery,
            "consecutive_failures": vendor.consecutive_failures,
            "last_error": vendor.last_error,
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_VENDOR, self._handle_vendor)
        )

    @callback
    def _handle_vendor(self, entry_id: str) -> None:
        if entry_id == self._entry_id:
            self.async_write_ha_state()
