"""Tests for Bluetooth Battery Service monitoring."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from radio_battery_monitor.backends.ble import BATTERY_LEVEL_UUID, BluetoothBackend, parse_battery_level
from radio_battery_monitor.models import DeviceIdentity, LogicalDevice, ProtocolName

if TYPE_CHECKING:
    from radio_battery_monitor.models import MonitorEvent

EXPECTED_PERCENT = 87


@dataclass(frozen=True)
class FakeBleDevice:
    """Discovered BLE device used by backend tests."""

    address: str
    name: str | None


@dataclass
class FakeScanner:
    """Immediate scanner with deterministic observations."""

    discovered_devices: tuple[FakeBleDevice, ...]
    started: bool = False

    async def start(self) -> None:
        """Record scan start."""
        self.started = True

    async def stop(self) -> None:
        """Record scan stop."""
        self.started = False


@dataclass
class FakeClient:
    """Battery-reading GATT client."""

    payload: bytearray
    reads: list[str] = field(default_factory=list[str])
    connected: bool = False

    async def connect(self) -> None:
        """Record connection."""
        self.connected = True

    async def disconnect(self) -> None:
        """Record disconnection."""
        self.connected = False

    async def read_gatt_char(self, characteristic: str) -> bytearray:
        """Return the configured battery payload."""
        self.reads.append(characteristic)
        return self.payload


@pytest.mark.parametrize(("payload", "expected"), [(b"\x00", 0), (b"\x64", 100), (bytearray(b"\x2a"), 42)])
def test_parse_battery_level(payload: bytes | bytearray, expected: int) -> None:
    """Bluetooth Battery Level is one unsigned percentage byte."""
    assert parse_battery_level(payload) == expected


@pytest.mark.parametrize("payload", [b"", b"\x01\x02", b"\x65"])
def test_parse_battery_level_rejects_invalid_payload(payload: bytes) -> None:
    """Malformed or reserved percentage values are rejected."""
    with pytest.raises(ValueError, match=r"exactly one byte|outside"):
        _ = parse_battery_level(payload)


def test_backend_reads_configured_device_and_disconnects() -> None:
    """A configured address produces a typed battery event."""
    observed = FakeBleDevice("AA:BB", "Assioma")
    scanner = FakeScanner((observed,))
    client = FakeClient(bytearray(b"\x57"))
    events: list[MonitorEvent] = []
    logical = LogicalDevice(
        logical_id="pedal",
        alias="Assioma",
        kind="power meter",
        identities=(DeviceIdentity(ProtocolName.BLUETOOTH_LE, "AA:BB"),),
    )
    backend = BluetoothBackend(
        scanner_factory=lambda: scanner,
        client_factory=lambda _device: client,
        scan_seconds=0.01,
        refresh_seconds=60.0,
    )
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
    assert readings[0].percent == EXPECTED_PERCENT
    assert client.reads == [BATTERY_LEVEL_UUID]
    assert not client.connected


def test_discovery_publishes_unconfigured_devices() -> None:
    """Explicit discovery exposes nearby identities without connecting."""
    scanner = FakeScanner((FakeBleDevice("11:22", "RunPod"),))
    events: list[MonitorEvent] = []
    backend = BluetoothBackend(scanner_factory=lambda: scanner, scan_seconds=0.01, refresh_seconds=60.0)
    backend.start((), events.append)
    backend.discover()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not any(event.device is not None for event in events):
        time.sleep(0.01)
    backend.stop()
    discovered = [event.device for event in events if event.device is not None]
    assert discovered[0].identity is not None
    assert discovered[0].identity.identifier == "11:22"


def test_host_powered_device_is_not_connected() -> None:
    """A host-powered device is explicitly N/A and never queried for a battery."""
    observed = FakeBleDevice("AA:BB", "Trainer")
    scanner = FakeScanner((observed,))
    client = FakeClient(bytearray(b"\x64"))
    events: list[MonitorEvent] = []
    logical = LogicalDevice(
        logical_id="trainer",
        alias="Trainer",
        kind="smart trainer",
        identities=(DeviceIdentity(ProtocolName.BLUETOOTH_LE, "AA:BB"),),
        host_powered=True,
    )
    backend = BluetoothBackend(
        scanner_factory=lambda: scanner,
        client_factory=lambda _device: client,
        scan_seconds=0.01,
        refresh_seconds=60.0,
    )
    backend.start((logical,), events.append)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not any(event.device is not None for event in events):
        time.sleep(0.01)
    backend.stop()
    device_events = [event.device for event in events if event.device is not None]
    assert device_events[0].detail == "Battery: N/A — externally powered."
    assert client.reads == []
