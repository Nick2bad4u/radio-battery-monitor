"""Typed domain models shared by radio backends and the user interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

MAX_BATTERY_PERCENT = 100
MAX_BATTERY_COUNT = 15


class ProtocolName(StrEnum):
    """A supported transport or host radio."""

    BLUETOOTH_LE = "bluetooth_le"
    ANT_PLUS = "ant_plus"


class AdapterState(StrEnum):
    """Availability state for a host radio."""

    READY = "ready"
    SCANNING = "scanning"
    IN_USE = "in_use"
    DRIVER_UNAVAILABLE = "driver_unavailable"
    DISCONNECTED = "disconnected"
    DISABLED = "disabled"
    ERROR = "error"
    STOPPED = "stopped"


class DeviceState(StrEnum):
    """Current monitoring state for a logical device."""

    DISCOVERED = "discovered"
    READING = "reading"
    CURRENT = "current"
    NOT_REPORTED = "not_reported"
    SLEEPING = "sleeping_or_out_of_range"
    ERROR = "error"
    STOPPED = "stopped"


class BatteryStatus(StrEnum):
    """Transport-independent qualitative battery state."""

    UNKNOWN = "unknown"
    NEW = "new"
    GOOD = "good"
    OK = "ok"
    LOW = "low"
    CRITICAL = "critical"
    INVALID = "invalid"


class MonitorEventKind(StrEnum):
    """Kinds of events produced by radio workers."""

    ADAPTER = "adapter"
    DEVICE = "device"
    DISCOVERY = "discovery"
    DIAGNOSTIC = "diagnostic"


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    """Protocol-specific identity for a physical device."""

    protocol: ProtocolName
    identifier: str
    device_type: int | None = None
    transmission_type: int | None = None

    @property
    def key(self) -> str:
        """Return a stable key suitable for mappings and events."""
        return f"{self.protocol.value}:{self.identifier}:{self.device_type}"


@dataclass(frozen=True, slots=True)
class LogicalDevice:
    """A user-visible physical device with one or more radio identities."""

    logical_id: str
    alias: str
    kind: str
    identities: tuple[DeviceIdentity, ...]
    host_powered: bool = False


@dataclass(frozen=True, slots=True)
class BatteryReading:
    """A battery observation without inferred values."""

    source: str
    observed_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    percent: int | None = None
    voltage: float | None = None
    status: BatteryStatus | None = None
    operating_time_seconds: int | None = None
    battery_id: int | None = None
    battery_count: int | None = None

    def __post_init__(self) -> None:
        """Reject malformed readings at the protocol boundary."""
        if self.percent is not None and not 0 <= self.percent <= MAX_BATTERY_PERCENT:
            msg = "Battery percentage must be between 0 and 100."
            raise ValueError(msg)
        if self.voltage is not None and self.voltage < 0:
            msg = "Battery voltage cannot be negative."
            raise ValueError(msg)
        if self.operating_time_seconds is not None and self.operating_time_seconds < 0:
            msg = "Battery operating time cannot be negative."
            raise ValueError(msg)
        if self.battery_count is not None and not 1 <= self.battery_count <= MAX_BATTERY_COUNT:
            msg = "Battery count must be between 1 and 15."
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class AdapterStatus:
    """Current state of a host radio."""

    protocol: ProtocolName
    display_name: str
    state: AdapterState
    detail: str
    host_powered: bool = True


@dataclass(frozen=True, slots=True)
class DeviceUpdate:
    """Current state of a discovered or configured logical device."""

    logical_id: str
    display_name: str
    protocol: ProtocolName
    state: DeviceState
    detail: str
    identity: DeviceIdentity | None = None
    reading: BatteryReading | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


@dataclass(frozen=True, slots=True)
class MonitorEvent:
    """An immutable message sent from a backend to the UI."""

    kind: MonitorEventKind
    adapter: AdapterStatus | None = None
    device: DeviceUpdate | None = None
    message: str | None = None


def newest_reading(readings: tuple[BatteryReading, ...]) -> BatteryReading | None:
    """Return the newest reading without preferring one radio protocol."""
    if not readings:
        return None
    return max(readings, key=lambda reading: reading.observed_at)
