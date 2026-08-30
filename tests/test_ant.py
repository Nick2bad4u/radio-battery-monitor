"""Tests for ANT+ radio monitoring and Common Data Page 82."""

from __future__ import annotations

import time
from threading import Event
from typing import TYPE_CHECKING

import pytest
import usb.core
from openant.easy.exception import AntException
from openant.easy.node import Node

from radio_battery_monitor.backends.ant import (
    AntBackend,
    AntInitializationCancelledError,
    create_ant_node,
    decode_battery_page,
)
from radio_battery_monitor.models import (
    AdapterState,
    BatteryReading,
    BatteryStatus,
    DeviceIdentity,
    LogicalDevice,
    ProtocolName,
)

if TYPE_CHECKING:
    from radio_battery_monitor.backends.ant import DiscoveryCallback, ReadingCallback
    from radio_battery_monitor.models import MonitorEvent

EXPECTED_OPERATING_UNITS = 0x030201
EXPECTED_BATTERY_ID = 2
EXPECTED_VOLTAGE = 3.5
SIXTEEN_SECOND_RESOLUTION = 16
EXPECTED_OPEN_ATTEMPTS = 3


class TransientUsbOpenError(usb.core.USBError):
    """Stable fake for Windows libusb pseudo-device open failures."""

    def __init__(self) -> None:
        """Create an error containing the native failure markers."""
        super().__init__("libusb0 os_open: device does not exist")


class FakeTransport:
    """Blocking transport that publishes one configured reading."""

    def __init__(self) -> None:
        """Initialize an active fake transport."""
        self.stopped = False

    def run(
        self,
        devices: tuple[LogicalDevice, ...],
        on_discovery: DiscoveryCallback,
        on_reading: ReadingCallback,
        stop_event: Event,
        refresh_event: Event,
    ) -> None:
        """Publish a deterministic observation, then wait for shutdown."""
        del devices, refresh_event
        identity = DeviceIdentity(ProtocolName.ANT_PLUS, "38933", device_type=11, transmission_type=1)
        on_discovery(identity)
        on_reading(identity, BatteryReading(source="fake", voltage=3.5, status=BatteryStatus.GOOD))
        _ = stop_event.wait(2.0)

    def stop(self) -> None:
        """Record transport shutdown."""
        self.stopped = True


class UnresponsiveTransport:
    """Transport that models OpenANT's response timeout when a stick is busy."""

    def run(
        self,
        devices: tuple[LogicalDevice, ...],
        on_discovery: DiscoveryCallback,
        on_reading: ReadingCallback,
        stop_event: Event,
        refresh_event: Event,
    ) -> None:
        """Fail before opening channels."""
        del devices, on_discovery, on_reading, stop_event, refresh_event
        raise AntException

    def stop(self) -> None:
        """Require no cleanup for the failed fake."""


class CancelledTransport:
    """Transport cancelled by a deliberate backend stop during initialization."""

    def run(
        self,
        devices: tuple[LogicalDevice, ...],
        on_discovery: DiscoveryCallback,
        on_reading: ReadingCallback,
        stop_event: Event,
        refresh_event: Event,
    ) -> None:
        """Surface the same cancellation exception as a stopped node open."""
        del devices, on_discovery, on_reading, stop_event, refresh_event
        raise AntInitializationCancelledError

    def stop(self) -> None:
        """Require no cleanup for the cancelled fake."""


def test_decode_page_82_uses_three_byte_operating_time() -> None:
    """The full 24-bit field and two-second resolution bit are honored."""
    page = decode_battery_page([82, 0xFF, 0x21, 0x01, 0x02, 0x03, 0x80, 0xA3])
    assert page.battery_id == EXPECTED_BATTERY_ID
    assert page.battery_count == 1
    assert page.voltage == EXPECTED_VOLTAGE
    assert page.status is BatteryStatus.GOOD
    assert page.operating_time_seconds == EXPECTED_OPERATING_UNITS * 2


def test_decode_page_82_handles_invalid_voltage_and_sixteen_second_resolution() -> None:
    """Invalid coarse voltage stays unknown and bit zero selects sixteen seconds."""
    page = decode_battery_page(bytes([82, 0xFF, 0xFF, 1, 0, 0, 0xFF, 0x5F]))
    assert page.battery_id is None
    assert page.battery_count is None
    assert page.voltage is None
    assert page.status is BatteryStatus.CRITICAL
    assert page.operating_time_seconds == SIXTEEN_SECOND_RESOLUTION


@pytest.mark.parametrize("payload", [b"", bytes([81, 0, 0, 0, 0, 0, 0, 0])])
def test_decode_page_82_rejects_wrong_payload(payload: bytes) -> None:
    """Short frames and non-battery pages fail at the boundary."""
    with pytest.raises(ValueError, match=r"at least 8 bytes|Expected ANT page 82"):
        _ = decode_battery_page(payload)


def test_backend_maps_transport_reading_to_logical_device() -> None:
    """Transport identities are merged into the configured logical row."""
    transport = FakeTransport()
    identity = DeviceIdentity(ProtocolName.ANT_PLUS, "38933", device_type=11, transmission_type=1)
    logical = LogicalDevice("pedal", "Assioma", "power meter", (identity,))
    events: list[MonitorEvent] = []
    backend = AntBackend(transport_factory=lambda: transport, report_timeout_seconds=1.0)
    backend.start((logical,), events.append)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not any(
        event.device is not None and event.device.reading is not None for event in events
    ):
        time.sleep(0.01)
    backend.stop()
    readings = [
        event.device.reading for event in events if event.device is not None and event.device.reading is not None
    ]
    assert readings[0].voltage == EXPECTED_VOLTAGE
    assert transport.stopped


def test_backend_maps_openant_timeout_to_in_use() -> None:
    """An OpenANT handshake timeout produces actionable adapter state."""
    events: list[MonitorEvent] = []
    backend = AntBackend(transport_factory=UnresponsiveTransport)
    backend.start((), events.append)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not any(
        event.adapter is not None and event.adapter.state is AdapterState.IN_USE for event in events
    ):
        time.sleep(0.01)
    backend.stop()
    in_use = [
        event.adapter for event in events if event.adapter is not None and event.adapter.state is AdapterState.IN_USE
    ]
    assert "Close Garmin Express, Zwift" in in_use[0].detail


def test_ant_node_open_retries_transient_libusb_failure() -> None:
    """A stale libusb pseudo-device path is retried before surfacing an adapter error."""
    attempts = 0
    expected_node = object.__new__(Node)

    def node_factory() -> Node:
        nonlocal attempts
        attempts += 1
        if attempts < EXPECTED_OPEN_ATTEMPTS:
            raise TransientUsbOpenError
        return expected_node

    node = create_ant_node(
        Event(),
        node_factory=node_factory,
        attempts=EXPECTED_OPEN_ATTEMPTS,
        retry_seconds=0.0,
    )
    assert node is expected_node
    assert attempts == EXPECTED_OPEN_ATTEMPTS


def test_deliberate_initialization_cancellation_is_not_reported_as_in_use() -> None:
    """Stopping during an open retry does not produce a misleading ownership error."""
    events: list[MonitorEvent] = []
    backend = AntBackend(transport_factory=CancelledTransport)
    backend.start((), events.append)
    time.sleep(0.05)
    backend.stop()
    states = [event.adapter.state for event in events if event.adapter is not None]
    assert AdapterState.IN_USE not in states
    assert states[-1] is AdapterState.STOPPED
