"""Backend orchestration independent of Tkinter."""

from __future__ import annotations

from queue import Empty, SimpleQueue
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from radio_battery_monitor.backends.base import RadioBackend
    from radio_battery_monitor.models import AdapterStatus, MonitorEvent
    from radio_battery_monitor.settings import AppSettings


class MonitorController:
    """Coordinate backend lifecycle and expose one event queue to the GUI."""

    def __init__(self, backends: tuple[RadioBackend, ...], settings: AppSettings) -> None:
        """Initialize the controller without touching hardware."""
        self._backends = backends
        self._settings = settings
        self._events: SimpleQueue[MonitorEvent] = SimpleQueue()
        self._lifecycle_lock = Lock()
        self._running = False

    @property
    def running(self) -> bool:
        """Whether a monitoring session is active."""
        with self._lifecycle_lock:
            return self._running

    def update_settings(self, settings: AppSettings) -> None:
        """Replace settings without churning active native radio handles."""
        with self._lifecycle_lock:
            self._settings = settings

    def probe(self) -> tuple[AdapterStatus, ...]:
        """Probe every backend without opening a monitoring session.

        Returns:
            The independent state of each configured radio backend.
        """
        return tuple(backend.probe() for backend in self._backends)

    def start(self) -> None:
        """Start every backend once."""
        with self._lifecycle_lock:
            if self._running:
                return
            self._running = True
            devices = self._settings.devices
        started: list[RadioBackend] = []
        try:
            for backend in self._backends:
                backend.start(devices, self._events.put)
                started.append(backend)
        except Exception:
            for backend in reversed(started):
                backend.stop()
            with self._lifecycle_lock:
                self._running = False
            raise

    def refresh(self) -> None:
        """Request a refresh from active backends."""
        if not self.running:
            self.start()
        for backend in self._backends:
            backend.refresh()

    def discover(self) -> None:
        """Start a session if needed and request radio discovery."""
        if not self.running:
            self.start()
        for backend in self._backends:
            backend.discover()

    def stop(self) -> None:
        """Stop all backends and make repeated calls safe."""
        with self._lifecycle_lock:
            if not self._running:
                return
            self._running = False
        for backend in reversed(self._backends):
            backend.stop()

    def drain_events(self, limit: int = 100) -> tuple[MonitorEvent, ...]:
        """Return up to ``limit`` queued events without blocking."""
        events: list[MonitorEvent] = []
        for _ in range(limit):
            try:
                events.append(self._events.get_nowait())
            except Empty:
                break
        return tuple(events)
