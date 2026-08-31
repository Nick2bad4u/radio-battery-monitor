"""Tests for backend orchestration."""

from dataclasses import dataclass, field
from typing import override

import pytest

from radio_battery_monitor.backends.base import EventSink
from radio_battery_monitor.controller import MonitorController
from radio_battery_monitor.models import (
    AdapterState,
    AdapterStatus,
    LogicalDevice,
    MonitorEvent,
    MonitorEventKind,
    ProtocolName,
)
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


class FailingBackend(FakeBackend):
    """Backend that fails while claiming its radio."""

    @override
    def start(self, devices: tuple[LogicalDevice, ...], sink: EventSink) -> None:
        """Fail every startup request.

        Raises:
            RuntimeError: Always, to model an unavailable radio.
        """
        del devices, sink
        raise RuntimeError("radio unavailable")


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


def test_refresh_starts_once_and_forwards_each_request() -> None:
    """Refresh starts an inactive session and then reuses it."""
    backend = FakeBackend()
    controller = MonitorController((backend,), AppSettings())
    controller.refresh()
    controller.refresh()
    assert backend.calls == ["start:0", "refresh", "refresh"]


def test_failed_start_rolls_back_started_backends() -> None:
    """A partial backend startup releases earlier radios and resets state."""
    first = FakeBackend()
    failing = FailingBackend()
    controller = MonitorController((first, failing), AppSettings())
    with pytest.raises(RuntimeError, match="radio unavailable"):
        controller.start()
    assert first.calls == ["start:0", "stop"]
    assert not controller.running


def test_drain_events_honors_limit_and_empty_queue() -> None:
    """Queued backend events are drained without blocking or over-reading."""
    backend = FakeBackend()
    controller = MonitorController((backend,), AppSettings())
    controller.start()
    assert backend.sink is not None
    backend.sink(MonitorEvent(MonitorEventKind.DIAGNOSTIC, message="one"))
    backend.sink(MonitorEvent(MonitorEventKind.DIAGNOSTIC, message="two"))
    assert [event.message for event in controller.drain_events(limit=1)] == ["one"]
    assert [event.message for event in controller.drain_events()] == ["two"]
    assert controller.drain_events() == ()
    controller.stop()


def test_repeated_start_and_stop_are_idempotent() -> None:
    """Repeated lifecycle calls do not duplicate radio ownership changes."""
    backend = FakeBackend()
    controller = MonitorController((backend,), AppSettings())
    controller.start()
    controller.start()
    controller.stop()
    controller.stop()
    assert backend.calls == ["start:0", "stop"]
