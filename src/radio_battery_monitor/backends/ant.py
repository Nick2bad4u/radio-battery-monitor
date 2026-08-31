"""Dynastream ANT USB Stick 2 monitoring and Common Data Page 82 decoding."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Event, Lock, Thread, Timer
from time import monotonic
from typing import TYPE_CHECKING, Final, Protocol, override

import usb.backend.libusb0
import usb.core
from openant.base.driver import DriverException, DriverNotFound
from openant.devices import ANTPLUS_NETWORK_KEY
from openant.devices.common import AntPlusDevice
from openant.devices.scanner import Scanner
from openant.easy.exception import AntException
from openant.easy.node import Node

from radio_battery_monitor.models import (
    AdapterState,
    AdapterStatus,
    BatteryReading,
    BatteryStatus,
    DeviceIdentity,
    DeviceState,
    DeviceUpdate,
    MonitorEvent,
    MonitorEventKind,
    ProtocolName,
)

if TYPE_CHECKING:
    from radio_battery_monitor.backends.base import EventSink
    from radio_battery_monitor.models import LogicalDevice

ANT_USB_VENDOR_ID: Final = 0x0FCF
ANT_USB_STICK_2_PRODUCT_ID: Final = 0x1008
ANT_BATTERY_PAGE: Final = 82
ANT_PAGE_REQUEST_COUNT: Final = 3
ANT_DEFAULT_PERIOD: Final = 8070
ANT_PAGE_LENGTH: Final = 8
ANT_EXTENDED_PAGE_LENGTH: Final = 13
ANT_UNUSED_FIELD: Final = 0xFF
ANT_INVALID_COARSE_VOLTAGE: Final = 0x0F
ANT_PERIODS: Final[dict[int, int]] = {
    11: 8182,  # Bicycle power.
    17: 8192,  # Fitness equipment.
    120: 8070,  # Heart rate.
}
ANT_DEVICE_TYPE_NAMES: Final[dict[int, str]] = {
    11: "bicycle power",
    17: "fitness equipment",
    120: "heart rate",
}
CONTROL_POLL_SECONDS: Final = 0.25
DEFAULT_REPORT_TIMEOUT_SECONDS: Final = 300.0
DISCOVERY_WINDOW_SECONDS: Final = 15.0
ANT_INITIALIZATION_SECONDS: Final = 5.0
ANT_OPEN_ATTEMPTS: Final = 3
ANT_OPEN_RETRY_SECONDS: Final = 1.0


@dataclass(frozen=True, slots=True)
class AntBatteryPage:
    """Decoded fields from ANT+ Common Data Page 82."""

    battery_id: int | None
    battery_count: int | None
    voltage: float | None
    status: BatteryStatus
    operating_time_seconds: int

    def as_reading(self) -> BatteryReading:
        """Convert the protocol page to the shared observation type.

        Returns:
            A normalized battery observation.
        """
        return BatteryReading(
            source="ant:common-page-82",
            voltage=self.voltage,
            status=self.status,
            operating_time_seconds=self.operating_time_seconds,
            battery_id=self.battery_id,
            battery_count=self.battery_count,
        )


type DiscoveryCallback = Callable[[DeviceIdentity], None]
type ReadingCallback = Callable[[DeviceIdentity, BatteryReading], None]


class AntTransport(Protocol):
    """Blocking ANT transport isolated behind a testable boundary."""

    def run(
        self,
        devices: tuple[LogicalDevice, ...],
        on_discovery: DiscoveryCallback,
        on_reading: ReadingCallback,
        stop_event: Event,
        refresh_event: Event,
    ) -> None:
        """Run until the supplied stop event is set."""
        ...

    def stop(self) -> None:
        """Release the USB node and unblock ``run``."""
        ...


type TransportFactory = Callable[[], AntTransport]
type NodeFactory = Callable[[], Node]


class AntInitializationCancelledError(AntException):
    """Raised when ANT initialization is cancelled during shutdown."""

    def __init__(self) -> None:
        """Create a stable user-facing cancellation error."""
        super().__init__("Initialization was cancelled")


class AntInitializationTimeoutError(AntException):
    """Raised when OpenANT's internal USB worker does not become ready."""

    def __init__(self) -> None:
        """Create a stable user-facing timeout error."""
        super().__init__("USB initialization timed out")


def decode_battery_page(payload: bytes | bytearray | list[int]) -> AntBatteryPage:
    """Decode ANT+ Common Data Page 82 without OpenANT's two-byte truncation bug.

    Returns:
        The decoded battery-page fields.

    Raises:
        ValueError: The payload is too short or is not Common Data Page 82.
    """
    if len(payload) < ANT_PAGE_LENGTH:
        msg = f"ANT battery page requires at least 8 bytes, received {len(payload)}."
        raise ValueError(msg)
    if payload[0] != ANT_BATTERY_PAGE:
        msg = f"Expected ANT page 82, received page {payload[0]}."
        raise ValueError(msg)

    battery_identifier = payload[2]
    if battery_identifier == ANT_UNUSED_FIELD:
        battery_id = None
        battery_count = None
    else:
        battery_id = (battery_identifier >> 4) & 0x0F
        battery_count_value = battery_identifier & 0x0F
        battery_count = battery_count_value if battery_count_value > 0 else None

    descriptive = payload[7]
    coarse_voltage = descriptive & 0x0F
    fractional_voltage = payload[6] / 256
    voltage = None if coarse_voltage == ANT_INVALID_COARSE_VOLTAGE else coarse_voltage + fractional_voltage
    status_value = (descriptive >> 4) & 0x07
    status = _battery_status(status_value)
    resolution_seconds = 2 if descriptive & 0x80 else 16
    cumulative_units = int.from_bytes(bytes(payload[3:6]), byteorder="little")
    return AntBatteryPage(
        battery_id=battery_id,
        battery_count=battery_count,
        voltage=voltage,
        status=status,
        operating_time_seconds=cumulative_units * resolution_seconds,
    )


class AntBackend:
    """Threaded ANT+ monitor for the installed Dynastream USB radio."""

    def __init__(
        self,
        *,
        transport_factory: TransportFactory | None = None,
        report_timeout_seconds: float = DEFAULT_REPORT_TIMEOUT_SECONDS,
    ) -> None:
        """Configure transport injection and report timeout without claiming USB."""
        self._transport_factory = transport_factory or _default_transport_factory
        self._report_timeout_seconds = report_timeout_seconds
        self._transport: AntTransport | None = None
        self._thread: Thread | None = None
        self._watchdog_thread: Thread | None = None
        self._stop_event = Event()
        self._refresh_event = Event()
        self._discover_event = Event()
        self._discovery_lock = Lock()
        self._discovered_keys: set[str] = set()
        self._discovery_timer: Timer | None = None
        self._lifecycle_lock = Lock()
        self._seen_lock = Lock()
        self._seen_logical_ids: set[str] = set()
        self._devices: tuple[LogicalDevice, ...] = ()
        self._sink: EventSink | None = None

    def probe(self) -> AdapterStatus:
        """Enumerate the exact ANT USB Stick 2 without claiming it.

        Returns:
            The current ANT adapter availability and driver state.
        """
        backend = usb.backend.libusb0.get_backend()
        if backend is None:
            return AdapterStatus(
                ProtocolName.ANT_PLUS,
                "ANT USB Stick 2",
                AdapterState.DRIVER_UNAVAILABLE,
                "libusb-win32 backend is unavailable.",
            )
        try:
            device = usb.core.find(
                idVendor=ANT_USB_VENDOR_ID,
                idProduct=ANT_USB_STICK_2_PRODUCT_ID,
                backend=backend,
            )
        except usb.core.USBError as error:
            return AdapterStatus(
                ProtocolName.ANT_PLUS,
                "ANT USB Stick 2",
                AdapterState.DRIVER_UNAVAILABLE,
                f"Unable to enumerate ANT USB: {error}",
            )
        if device is None:
            return AdapterStatus(
                ProtocolName.ANT_PLUS,
                "ANT USB Stick 2",
                AdapterState.DISCONNECTED,
                "Dynastream 0FCF:1008 was not found.",
            )
        return AdapterStatus(
            ProtocolName.ANT_PLUS,
            "ANT USB Stick 2",
            AdapterState.READY,
            "Stick and libusb-win32 driver detected. Battery: N/A — USB powered.",
        )

    def start(self, devices: tuple[LogicalDevice, ...], sink: EventSink) -> None:
        """Start OpenANT and a reporting watchdog once."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._devices = tuple(
                device
                for device in devices
                if any(identity.protocol is ProtocolName.ANT_PLUS for identity in device.identities)
            )
            self._sink = sink
            self._stop_event.clear()
            self._refresh_event.clear()
            self._discover_event.clear()
            with self._discovery_lock:
                self._discovered_keys.clear()
            with self._seen_lock:
                self._seen_logical_ids.clear()
            self._transport = self._transport_factory()
            self._thread = Thread(target=self._thread_main, name="ant-monitor", daemon=True)
            self._watchdog_thread = Thread(target=self._watchdog, name="ant-report-watchdog", daemon=True)
            self._thread.start()
            self._watchdog_thread.start()

    def refresh(self) -> None:
        """Request Page 82 again from synchronized configured devices."""
        self._refresh_event.set()

    def discover(self) -> None:
        """Publish identities observed by the continuously active scanner."""
        with self._discovery_lock:
            self._discovered_keys.clear()
            if self._discovery_timer is not None:
                self._discovery_timer.cancel()
            self._discovery_timer = Timer(DISCOVERY_WINDOW_SECONDS, self._discover_event.clear)
            self._discovery_timer.daemon = True
            self._discovery_timer.start()
        self._discover_event.set()

    def stop(self) -> None:
        """Stop OpenANT and wait for both worker threads to finish."""
        with self._lifecycle_lock:
            transport = self._transport
            thread = self._thread
            watchdog = self._watchdog_thread
            self._transport = None
            self._thread = None
            self._watchdog_thread = None
        with self._discovery_lock:
            discovery_timer = self._discovery_timer
            self._discovery_timer = None
        self._stop_event.set()
        self._refresh_event.set()
        if discovery_timer is not None:
            discovery_timer.cancel()
        if transport is not None:
            transport.stop()
        if thread is not None:
            thread.join(timeout=5.0)
        if watchdog is not None:
            watchdog.join(timeout=1.0)
        self._emit_adapter(AdapterState.STOPPED, "ANT+ monitoring stopped.")

    def _thread_main(self) -> None:
        transport = self._transport
        if transport is None:
            return
        self._emit_adapter(AdapterState.SCANNING, "Listening for selected ANT+ devices and optional Page 82…")
        try:
            transport.run(
                self._devices,
                self._on_discovery,
                self._on_reading,
                self._stop_event,
                self._refresh_event,
            )
        except AntInitializationCancelledError:
            return
        except (DriverNotFound, DriverException) as error:
            self._emit_adapter(AdapterState.DRIVER_UNAVAILABLE, f"ANT driver unavailable: {error}")
        except usb.core.USBError as error:
            state = _usb_error_state(error)
            self._emit_adapter(state, _usb_error_detail(state))
        except AntException as error:
            self._emit_adapter(
                AdapterState.IN_USE,
                f"ANT stick did not respond: {error}. Close Garmin Express, Zwift, or other ANT software and retry.",
            )
        except (OSError, RuntimeError, ValueError) as error:
            self._emit_adapter(AdapterState.ERROR, f"ANT worker failed: {error}")

    def _watchdog(self) -> None:
        if self._stop_event.wait(self._report_timeout_seconds):
            return
        with self._seen_lock:
            seen = self._seen_logical_ids.copy()
        for device in self._devices:
            if device.logical_id in seen:
                continue
            identity = next(
                (item for item in device.identities if item.protocol is ProtocolName.ANT_PLUS),
                None,
            )
            if identity is not None:
                detail = (
                    "Battery: N/A — externally powered."
                    if device.host_powered
                    else "ANT+ Common Page 82 was not reported during this session."
                )
                self._emit_device(device, identity, DeviceState.NOT_REPORTED, detail)

    def _on_discovery(self, identity: DeviceIdentity) -> None:
        if not self._discover_event.is_set():
            return
        with self._discovery_lock:
            if identity.key in self._discovered_keys:
                return
            self._discovered_keys.add(identity.key)
        sink = self._sink
        if sink is not None:
            profile = ANT_DEVICE_TYPE_NAMES.get(identity.device_type or -1, "device")
            sink(
                MonitorEvent(
                    MonitorEventKind.DISCOVERY,
                    device=DeviceUpdate(
                        logical_id=f"discovered:{identity.key}",
                        display_name=f"ANT+ {profile} {identity.identifier}",
                        protocol=ProtocolName.ANT_PLUS,
                        state=DeviceState.DISCOVERED,
                        detail=f"{profile.title()} profile (type {identity.device_type}); add or link it to monitor.",
                        identity=identity,
                    ),
                ),
            )

    def _on_reading(self, identity: DeviceIdentity, reading: BatteryReading) -> None:
        logical_device = _logical_device_for_identity(self._devices, identity)
        if logical_device is None:
            if self._discover_event.is_set():
                self._on_discovery(identity)
            return
        with self._seen_lock:
            self._seen_logical_ids.add(logical_device.logical_id)
        self._emit_device(
            logical_device,
            identity,
            DeviceState.CURRENT,
            "ANT+ Common Battery Status Page 82.",
            reading=reading,
        )

    def _emit_adapter(self, state: AdapterState, detail: str) -> None:
        sink = self._sink
        if sink is not None:
            sink(
                MonitorEvent(
                    MonitorEventKind.ADAPTER,
                    adapter=AdapterStatus(ProtocolName.ANT_PLUS, "ANT USB Stick 2", state, detail),
                ),
            )

    def _emit_device(
        self,
        logical_device: LogicalDevice,
        identity: DeviceIdentity,
        state: DeviceState,
        detail: str,
        *,
        reading: BatteryReading | None = None,
    ) -> None:
        sink = self._sink
        if sink is not None:
            sink(
                MonitorEvent(
                    MonitorEventKind.DEVICE,
                    device=DeviceUpdate(
                        logical_id=logical_device.logical_id,
                        display_name=logical_device.alias,
                        protocol=ProtocolName.ANT_PLUS,
                        state=state,
                        detail=detail,
                        identity=identity,
                        reading=reading,
                    ),
                ),
            )


class _OpenAntTransport:
    """OpenANT implementation kept behind the typed transport protocol."""

    def __init__(self) -> None:
        self._node: Node | None = None
        self._node_lock = Lock()

    def run(
        self,
        devices: tuple[LogicalDevice, ...],
        on_discovery: DiscoveryCallback,
        on_reading: ReadingCallback,
        stop_event: Event,
        refresh_event: Event,
    ) -> None:
        """Create scanner and targeted channels, then enter OpenANT's message loop."""
        node = create_ant_node(stop_event)
        with self._node_lock:
            self._node = node
        try:
            _configure_node(node, stop_event)
            scanner = _BatteryScanner(node, on_discovery, on_reading)
            targets = _build_target_devices(node, devices, on_reading)
            control = Thread(
                target=_request_loop,
                args=(targets, stop_event, refresh_event),
                name="ant-page-request",
                daemon=True,
            )
            control.start()
            try:
                node.start()
            finally:
                stop_event.set()
                refresh_event.set()
                control.join(timeout=1.0)
                _ = scanner
        finally:
            with self._node_lock:
                should_stop = self._node is node
                if should_stop:
                    self._node = None
            if should_stop:
                node.stop()

    def stop(self) -> None:
        """Stop OpenANT, which closes the driver and unblocks the message loop."""
        with self._node_lock:
            node = self._node
            self._node = None
        if node is not None:
            node.stop()


def _configure_node(node: Node, stop_event: Event) -> None:
    """Set the ANT+ network key without inheriting OpenANT's ten-second blocking wait.

    Raises:
        AntInitializationCancelledError: Shutdown was requested during initialization.
        AntInitializationTimeoutError: OpenANT did not finish initialization in time.
        RuntimeError: The third-party operation failed with an unexpected exception.
    """
    result: Queue[Exception | None] = Queue(maxsize=1)
    configure_thread = Thread(
        target=_set_network_key,
        args=(node, result),
        name="ant-network-key",
        daemon=True,
    )
    configure_thread.start()
    deadline = monotonic() + ANT_INITIALIZATION_SECONDS
    while True:
        if stop_event.is_set():
            raise AntInitializationCancelledError
        try:
            error = result.get(timeout=CONTROL_POLL_SECONDS)
        except Empty:
            if monotonic() >= deadline:
                raise AntInitializationTimeoutError from None
            continue
        if error is None:
            return
        if isinstance(error, AntException):
            raise error
        msg = "Unable to configure the ANT+ network"
        raise RuntimeError(msg) from error


def create_ant_node(
    stop_event: Event,
    *,
    node_factory: NodeFactory = Node,
    attempts: int = ANT_OPEN_ATTEMPTS,
    retry_seconds: float = ANT_OPEN_RETRY_SECONDS,
) -> Node:
    """Open an ANT node with bounded retries while Windows settles the USB handle.

    Returns:
        An initialized OpenANT node.

    Raises:
        ValueError: Fewer than one open attempt was requested.
        AntInitializationCancelledError: Shutdown was requested between attempts.
        usb.core.USBError: Every USB open attempt failed.
        RuntimeError: The retry loop ended without returning or raising a USB error.
    """
    if attempts < 1:
        msg = "ANT open attempts must be at least one."
        raise ValueError(msg)
    for attempt in range(1, attempts + 1):
        try:
            return node_factory()
        except usb.core.USBError:
            if attempt == attempts:
                raise
            if stop_event.wait(retry_seconds):
                raise AntInitializationCancelledError from None
    msg = "ANT node retry loop exhausted unexpectedly."
    raise RuntimeError(msg)


def _set_network_key(node: Node, result: Queue[Exception | None]) -> None:
    """Run OpenANT's blocking network-key handshake and publish its outcome."""
    try:
        _ = node.set_network_key(0, ANTPLUS_NETWORK_KEY)
    except Exception as error:  # ruff: ignore[blind-except] - OpenANT exposes undocumented exception types.
        result.put(error)
    else:
        result.put(None)


class _BatteryScanner(Scanner):
    """Scanner extension that observes Page 82 and extended device identities."""

    def __init__(
        self,
        node: Node,
        on_discovery: DiscoveryCallback,
        on_reading: ReadingCallback,
    ) -> None:
        self._discovery_callback = on_discovery
        self._reading_callback = on_reading
        super().__init__(node)

    @override
    def _on_data(self, data: list[int]) -> None:
        if len(data) >= ANT_EXTENDED_PAGE_LENGTH:
            identity = DeviceIdentity(
                ProtocolName.ANT_PLUS,
                str(data[9] | (data[10] << 8)),
                device_type=data[11],
                transmission_type=data[12],
            )
            self._discovery_callback(identity)
            if data[0] == ANT_BATTERY_PAGE:
                self._reading_callback(identity, decode_battery_page(data).as_reading())
        super()._on_data(data)


class _BatteryDevice(AntPlusDevice):
    """Generic targeted ANT receiver that requests and decodes Page 82."""

    def __init__(
        self,
        node: Node,
        identity: DeviceIdentity,
        on_reading: ReadingCallback,
    ) -> None:
        if identity.device_type is None:
            msg = "A targeted ANT identity requires a device type."
            raise ValueError(msg)
        self._identity = identity
        self._reading_callback = on_reading
        super().__init__(
            node,
            device_type=identity.device_type,
            device_id=int(identity.identifier),
            period=ANT_PERIODS.get(identity.device_type, ANT_DEFAULT_PERIOD),
            trans_type=identity.transmission_type or 0,
            name="battery-monitor",
        )

    @override
    def on_found(self) -> None:
        """Request three Page 82 transmissions after channel synchronization."""
        self.request_dp(ANT_BATTERY_PAGE, ANT_PAGE_REQUEST_COUNT)

    @override
    def on_data(self, data: list[int]) -> None:
        """Forward valid Page 82 broadcasts to the shared decoder."""
        if data and data[0] == ANT_BATTERY_PAGE:
            self._reading_callback(self._identity, decode_battery_page(data).as_reading())


def _build_target_devices(
    node: Node,
    devices: tuple[LogicalDevice, ...],
    on_reading: ReadingCallback,
) -> tuple[_BatteryDevice, ...]:
    return tuple(
        _BatteryDevice(node, identity, on_reading)
        for logical_device in devices
        if not logical_device.host_powered
        for identity in logical_device.identities
        if identity.protocol is ProtocolName.ANT_PLUS and identity.device_type is not None
    )


def _request_loop(
    targets: tuple[_BatteryDevice, ...],
    stop_event: Event,
    refresh_event: Event,
) -> None:
    while not stop_event.wait(CONTROL_POLL_SECONDS):
        if not refresh_event.is_set():
            continue
        refresh_event.clear()
        for target in targets:
            target.request_dp(ANT_BATTERY_PAGE, ANT_PAGE_REQUEST_COUNT)


def _logical_device_for_identity(
    devices: tuple[LogicalDevice, ...],
    identity: DeviceIdentity,
) -> LogicalDevice | None:
    for device in devices:
        if any(item.key == identity.key for item in device.identities):
            return device
    return None


def _battery_status(value: int) -> BatteryStatus:
    statuses = {
        1: BatteryStatus.NEW,
        2: BatteryStatus.GOOD,
        3: BatteryStatus.OK,
        4: BatteryStatus.LOW,
        5: BatteryStatus.CRITICAL,
        7: BatteryStatus.INVALID,
    }
    return statuses.get(value, BatteryStatus.UNKNOWN)


def _usb_error_state(error: usb.core.USBError) -> AdapterState:
    detail = str(error).casefold()
    if (
        error.errno in {5, 13, 16}
        or "busy" in detail
        or "access" in detail
        or "os_open" in detail
        or "does not exist" in detail
    ):
        return AdapterState.IN_USE
    return AdapterState.DRIVER_UNAVAILABLE


def _usb_error_detail(state: AdapterState) -> str:
    if state is AdapterState.IN_USE:
        return (
            "The stick could not be opened after three attempts. Close Garmin Express, Zwift, and other ANT+ apps; "
            "then unplug/reconnect the stick if needed."
        )
    return "The ANT USB stick or its libusb-win32 driver is unavailable. Reconnect the stick and retry."


def _default_transport_factory() -> AntTransport:
    return _OpenAntTransport()
