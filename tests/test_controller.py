"""Tests for backend orchestration."""

from dataclasses import dataclass, field

from radio_battery_monitor.backends.base import EventSink
from radio_battery_monitor.controller import MonitorController
from radio_battery_monitor.models import AdapterState, AdapterStatus, LogicalDevice, ProtocolName
from radio_battery_monitor.settings import AppSettings


@dataclass
class FakeBackend:
    """Minimal lifecycle-recording backend."""

    calls: list[str] = field(default_factory=list[str])
    sink: EventSink | None = None

    def probe(self) -> AdapterStatus:
        """Return a ready fake adapter."""
        self.calls.append("probe")
        return AdapterStatus(ProtocolName.BLUETOOTH_LE, "Fake", AdapterState.READY, "Ready")

    def start(self, devices: tuple[LogicalDevice, ...], sink: EventSink) -> None:
        """Record session start."""
        self.calls.append(f"start:{len(devices)}")
        self.sink = sink

    def refresh(self) -> None:
        """Record refresh."""
        self.calls.append("refresh")

    def discover(self) -> None:
        """Record discovery."""
        self.calls.append("discover")

    def stop(self) -> None:
        """Record stop."""
        self.calls.append("stop")


def test_discover_starts_session_once() -> None:
    """Discovery transparently starts inactive backends."""
    backend = FakeBackend()
    controller = MonitorController((backend,), AppSettings())
    controller.discover()
    controller.discover()
    controller.stop()
    assert backend.calls == ["start:0", "discover", "discover", "stop"]


def test_probe_does_not_start_session() -> None:
    """Adapter cards can be populated before hardware is claimed."""
    backend = FakeBackend()
    controller = MonitorController((backend,), AppSettings())
    assert controller.probe()[0].state is AdapterState.READY
    assert backend.calls == ["probe"]
    assert not controller.running


def test_settings_update_does_not_restart_active_backends() -> None:
    """Mapping edits are retained for the next session without reopening USB handles."""
    backend = FakeBackend()
    controller = MonitorController((backend,), AppSettings())
    controller.start()
    controller.update_settings(AppSettings(refresh_seconds=120))
    controller.stop()
    assert backend.calls == ["start:0", "stop"]
