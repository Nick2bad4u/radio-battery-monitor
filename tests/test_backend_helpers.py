"""Focused tests for radio backend boundary helpers."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from threading import Event
from typing import TYPE_CHECKING

import pytest
import usb.core

from radio_battery_monitor.backends import ant, ble
from radio_battery_monitor.models import AdapterState, BatteryStatus, DeviceIdentity, LogicalDevice, ProtocolName

if TYPE_CHECKING:
    from openant.easy.node import Node


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, BatteryStatus.UNKNOWN),
        (1, BatteryStatus.NEW),
        (2, BatteryStatus.GOOD),
        (3, BatteryStatus.OK),
        (4, BatteryStatus.LOW),
        (5, BatteryStatus.CRITICAL),
        (6, BatteryStatus.UNKNOWN),
        (7, BatteryStatus.INVALID),
    ],
)
def test_ant_battery_status_mapping(value: int, expected: BatteryStatus) -> None:
    """Every three-bit ANT battery status maps to an explicit domain value."""
    assert ant._battery_status(value) is expected


def test_decoded_ant_page_converts_to_shared_reading() -> None:
    """Page metadata survives conversion to the common battery model."""
    reading = ant.decode_battery_page([82, 0, 0x21, 1, 0, 0, 128, 0x23]).as_reading()
    assert reading.source == "ant:common-page-82"
    assert reading.battery_id == 2
    assert reading.battery_count == 1


@pytest.mark.parametrize(
    ("message", "state"),
    [
        ("libusb0 os_open: device does not exist", AdapterState.IN_USE),
        ("Access denied", AdapterState.IN_USE),
        ("Resource busy", AdapterState.IN_USE),
        ("Unexpected native failure", AdapterState.DRIVER_UNAVAILABLE),
    ],
)
def test_usb_errors_map_to_actionable_states(message: str, state: AdapterState) -> None:
    """Native USB failures become stable user-facing state and guidance."""
    mapped = ant._usb_error_state(usb.core.USBError(message))
    assert mapped is state
    assert ant._usb_error_detail(mapped)


def test_identity_matching_ignores_ant_transmission_variants() -> None:
    """ANT transmission type changes do not split one physical device."""
    configured = DeviceIdentity(ProtocolName.ANT_PLUS, "42", device_type=11, transmission_type=1)
    observed = DeviceIdentity(ProtocolName.ANT_PLUS, "42", device_type=11, transmission_type=5)
    device = LogicalDevice("power", "Power", "meter", (configured,))
    assert ant._logical_device_for_identity((device,), observed) is device
    assert ant._logical_device_for_identity((device,), DeviceIdentity(ProtocolName.ANT_PLUS, "9")) is None


def test_configured_ble_identities_skip_other_protocols() -> None:
    """BLE scans select only Bluetooth identities and preserve logical devices."""
    ble_identity = DeviceIdentity(ProtocolName.BLUETOOTH_LE, "AA:BB")
    ant_identity = DeviceIdentity(ProtocolName.ANT_PLUS, "42")
    device = LogicalDevice("combo", "Combo", "sensor", (ble_identity, ant_identity))
    assert ble._configured_ble_identities((device,)) == {"aa:bb": (device, ble_identity)}


def test_ant_node_rejects_invalid_attempt_count_and_cancellation() -> None:
    """Node retries validate their contract and honor shutdown between failures."""
    with pytest.raises(ValueError, match="at least one"):
        _ = ant.create_ant_node(Event(), attempts=0)
    stop = Event()

    def fail_node() -> Node:
        stop.set()
        raise usb.core.USBError("busy")

    with pytest.raises(ant.AntInitializationCancelledError):
        _ = ant.create_ant_node(stop, node_factory=fail_node, attempts=2, retry_seconds=0)
