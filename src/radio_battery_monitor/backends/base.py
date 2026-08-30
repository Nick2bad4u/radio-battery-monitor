"""Radio backend contracts."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from radio_battery_monitor.models import AdapterStatus, LogicalDevice, MonitorEvent

type EventSink = Callable[[MonitorEvent], None]


class RadioBackend(Protocol):
    """Lifecycle shared by Bluetooth LE and ANT+ workers."""

    def probe(self) -> AdapterStatus:
        """Inspect adapter availability without starting a monitoring session."""
        ...

    def start(self, devices: tuple[LogicalDevice, ...], sink: EventSink) -> None:
        """Start monitoring configured devices and publishing events."""
        ...

    def refresh(self) -> None:
        """Request an immediate refresh of configured devices."""
        ...

    def discover(self) -> None:
        """Request discovery of nearby devices."""
        ...

    def stop(self) -> None:
        """Stop monitoring and release all native resources."""
        ...
