"""Windows Bluetooth Low Energy monitoring through Bleak."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, Final, Protocol

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.exc import BleakCharacteristicNotFoundError, BleakError
from winrt.windows.devices.radios import Radio, RadioKind, RadioState

from radio_battery_monitor.models import (
    MAX_BATTERY_PERCENT,
    AdapterState,
    AdapterStatus,
    BatteryReading,
    DeviceIdentity,
    DeviceState,
    DeviceUpdate,
    MonitorEvent,
    MonitorEventKind,
    ProtocolName,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from radio_battery_monitor.backends.base import EventSink
    from radio_battery_monitor.models import LogicalDevice

BATTERY_LEVEL_UUID: Final = "00002a19-0000-1000-8000-00805f9b34fb"
SCAN_SECONDS: Final = 15.0
CONNECT_TIMEOUT_SECONDS: Final = 15.0
READ_TIMEOUT_SECONDS: Final = 5.0
DEFAULT_REFRESH_SECONDS: Final = 300.0
EVENT_POLL_SECONDS: Final = 0.2


class BleDeviceLike(Protocol):
    """Minimal discovered-device surface consumed by the backend."""

    @property
    def address(self) -> str:
        """Return the Windows Bluetooth address."""
        ...

    @property
    def name(self) -> str | None:
        """Return the OS-provided display name, when available."""
        ...


class BleScannerLike(Protocol):
    """Scanner operations required from Bleak or a test double."""

    @property
    def discovered_devices(self) -> Sequence[BleDeviceLike]:
        """Return devices observed during the current scan."""
        ...

    async def start(self) -> None:
        """Start scanning."""
        ...

    async def stop(self) -> None:
        """Stop scanning."""
        ...


class BleClientLike(Protocol):
    """GATT client operations required from Bleak or a test double."""

    async def connect(self) -> None:
        """Connect to the GATT server."""
        ...

    async def disconnect(self) -> None:
        """Disconnect from the GATT server."""
        ...

    async def read_gatt_char(self, characteristic: str) -> bytearray:
        """Read one GATT characteristic."""
        ...


type ScannerFactory = Callable[[], BleScannerLike]
type ClientFactory = Callable[[BleDeviceLike], BleClientLike]


def parse_battery_level(payload: bytes | bytearray) -> int:
    """Decode the one-octet Bluetooth Battery Level characteristic."""
    if len(payload) != 1:
        msg = f"Battery Level must contain exactly one byte, received {len(payload)}."
        raise ValueError(msg)
    percent = payload[0]
    if percent > MAX_BATTERY_PERCENT:
        msg = f"Battery Level is outside the valid 0-100 range: {percent}."
        raise ValueError(msg)
    return percent


class BluetoothBackend:
    """Threaded BLE monitor that isolates WinRT and asyncio from Tkinter."""

    def __init__(
        self,
        *,
        scanner_factory: ScannerFactory | None = None,
        client_factory: ClientFactory | None = None,
        scan_seconds: float = SCAN_SECONDS,
        refresh_seconds: float = DEFAULT_REFRESH_SECONDS,
    ) -> None:
        """Configure factories and timings without touching the radio."""
        self._scanner_factory = scanner_factory or _default_scanner_factory
        self._client_factory = client_factory or _default_client_factory
        self._scan_seconds = scan_seconds
        self._refresh_seconds = refresh_seconds
        self._stop_event = Event()
        self._refresh_event = Event()
        self._discover_event = Event()
        self._thread: Thread | None = None
        self._lifecycle_lock = Lock()
        self._devices: tuple[LogicalDevice, ...] = ()
        self._sink: EventSink | None = None

    def probe(self) -> AdapterStatus:
        """Inspect Windows Bluetooth radio state without scanning."""
        try:
            state = asyncio.run(_bluetooth_radio_state())
        except (OSError, RuntimeError) as error:
            return AdapterStatus(
                ProtocolName.BLUETOOTH_LE,
                "Windows Bluetooth adapter",
                AdapterState.ERROR,
                f"Unable to query Bluetooth radio: {error}",
            )
        if state is None:
            return AdapterStatus(
                ProtocolName.BLUETOOTH_LE,
                "Windows Bluetooth adapter",
                AdapterState.DISCONNECTED,
                "No Bluetooth radio was found.",
            )
        if state is RadioState.ON:
            return AdapterStatus(
                ProtocolName.BLUETOOTH_LE,
                "Windows Bluetooth adapter",
                AdapterState.READY,
                "Radio is on and ready. Battery: N/A — host powered.",
            )
        return AdapterStatus(
            ProtocolName.BLUETOOTH_LE,
            "Windows Bluetooth adapter",
            AdapterState.DISABLED,
            f"Radio state is {state.name.lower()}. Turn Bluetooth on in Windows.",
        )

    def start(self, devices: tuple[LogicalDevice, ...], sink: EventSink) -> None:
        """Start a dedicated BLE asyncio worker once."""
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._devices = tuple(
                device
                for device in devices
                if any(identity.protocol is ProtocolName.BLUETOOTH_LE for identity in device.identities)
            )
            self._sink = sink
            self._stop_event.clear()
            self._refresh_event.set()
            self._discover_event.clear()
            self._thread = Thread(target=self._thread_main, name="bluetooth-monitor", daemon=True)
            self._thread.start()

    def refresh(self) -> None:
        """Wake the worker for an immediate read sweep."""
        self._refresh_event.set()

    def discover(self) -> None:
        """Request that the next scan publish every observed device."""
        self._discover_event.set()
        self._refresh_event.set()

    def stop(self) -> None:
        """Stop the asyncio worker and wait briefly for native cleanup."""
        with self._lifecycle_lock:
            thread = self._thread
            self._thread = None
        if thread is None:
            return
        self._stop_event.set()
        self._refresh_event.set()
        thread.join(timeout=max(self._scan_seconds + 2.0, CONNECT_TIMEOUT_SECONDS + 2.0))
        self._emit_adapter(AdapterState.STOPPED, "Bluetooth monitoring stopped.")

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except (BleakError, OSError, RuntimeError) as error:
            self._emit_adapter(AdapterState.ERROR, f"Bluetooth worker failed: {error}")

    async def _run(self) -> None:
        self._emit_adapter(AdapterState.SCANNING, "Scanning for selected BLE devices…")
        while not self._stop_event.is_set():
            self._refresh_event.clear()
            publish_discovery = self._discover_event.is_set()
            self._discover_event.clear()
            await self._scan_and_read(publish_discovery=publish_discovery)
            self._emit_adapter(AdapterState.READY, "Monitoring selected BLE devices. Battery: N/A — host powered.")
            await self._wait_for_refresh()

    async def _scan_and_read(self, *, publish_discovery: bool) -> None:
        scanner = self._scanner_factory()
        await scanner.start()
        try:
            await self._sleep_interruptibly(self._scan_seconds)
        finally:
            await scanner.stop()
        observed = tuple(scanner.discovered_devices)
        if publish_discovery:
            for device in observed:
                self._emit_discovery(device)
        configured = _configured_ble_identities(self._devices)
        observed_by_address = {device.address.casefold(): device for device in observed}
        for address, (logical_device, identity) in configured.items():
            if self._stop_event.is_set():
                return
            if logical_device.host_powered:
                self._emit_device(
                    logical_device,
                    identity,
                    DeviceState.NOT_REPORTED,
                    "Battery: N/A — externally powered.",
                )
                continue
            observed_device = observed_by_address.get(address)
            if observed_device is None:
                self._emit_device(
                    logical_device,
                    identity,
                    DeviceState.SLEEPING,
                    "Not observed during this scan. Wake the device and retry.",
                )
                continue
            await self._read_device(logical_device, identity, observed_device)

    async def _read_device(
        self,
        logical_device: LogicalDevice,
        identity: DeviceIdentity,
        observed_device: BleDeviceLike,
    ) -> None:
        self._emit_device(logical_device, identity, DeviceState.READING, "Reading standard BLE Battery Service…")
        client = self._client_factory(observed_device)
        connected = False
        try:
            await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT_SECONDS)
            connected = True
            payload = await asyncio.wait_for(
                client.read_gatt_char(BATTERY_LEVEL_UUID),
                timeout=READ_TIMEOUT_SECONDS,
            )
            percent = parse_battery_level(payload)
        except BleakCharacteristicNotFoundError:
            self._emit_device(
                logical_device,
                identity,
                DeviceState.NOT_REPORTED,
                "Standard BLE Battery Service is unavailable.",
            )
        except TimeoutError:
            self._emit_device(
                logical_device,
                identity,
                DeviceState.SLEEPING,
                "Connection or battery read timed out.",
            )
        except (BleakError, OSError, ValueError) as error:
            self._emit_device(logical_device, identity, DeviceState.ERROR, f"Bluetooth read failed: {error}")
        else:
            self._emit_device(
                logical_device,
                identity,
                DeviceState.CURRENT,
                "Standard BLE Battery Level.",
                reading=BatteryReading(source="ble:gatt-2a19", percent=percent),
            )
        finally:
            if connected:
                with suppress(BleakError, OSError):
                    await client.disconnect()

    async def _wait_for_refresh(self) -> None:
        elapsed = 0.0
        while elapsed < self._refresh_seconds:
            if self._stop_event.is_set() or self._refresh_event.is_set():
                return
            await asyncio.sleep(EVENT_POLL_SECONDS)
            elapsed += EVENT_POLL_SECONDS

    async def _sleep_interruptibly(self, duration: float) -> None:
        elapsed = 0.0
        while elapsed < duration:
            if self._stop_event.is_set():
                return
            step = min(EVENT_POLL_SECONDS, duration - elapsed)
            await asyncio.sleep(step)
            elapsed += step

    def _emit_adapter(self, state: AdapterState, detail: str) -> None:
        sink = self._sink
        if sink is not None:
            sink(
                MonitorEvent(
                    MonitorEventKind.ADAPTER,
                    adapter=AdapterStatus(ProtocolName.BLUETOOTH_LE, "Windows Bluetooth adapter", state, detail),
                ),
            )

    def _emit_discovery(self, device: BleDeviceLike) -> None:
        identity = DeviceIdentity(ProtocolName.BLUETOOTH_LE, device.address)
        display_name = device.name or "Unnamed BLE device"
        sink = self._sink
        if sink is not None:
            sink(
                MonitorEvent(
                    MonitorEventKind.DISCOVERY,
                    device=DeviceUpdate(
                        logical_id=f"discovered:{identity.key}",
                        display_name=display_name,
                        protocol=ProtocolName.BLUETOOTH_LE,
                        state=DeviceState.DISCOVERED,
                        detail="Nearby BLE device. Add it to monitor its Battery Service.",
                        identity=identity,
                    ),
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
                        protocol=ProtocolName.BLUETOOTH_LE,
                        state=state,
                        detail=detail,
                        identity=identity,
                        reading=reading,
                    ),
                ),
            )


async def _bluetooth_radio_state() -> RadioState | None:
    radios = await Radio.get_radios_async()
    for radio in radios:
        if radio.kind is RadioKind.BLUETOOTH:
            return radio.state
    return None


def _configured_ble_identities(
    devices: tuple[LogicalDevice, ...],
) -> dict[str, tuple[LogicalDevice, DeviceIdentity]]:
    configured: dict[str, tuple[LogicalDevice, DeviceIdentity]] = {}
    for device in devices:
        for identity in device.identities:
            if identity.protocol is ProtocolName.BLUETOOTH_LE:
                configured[identity.identifier.casefold()] = (device, identity)
    return configured


def _default_scanner_factory() -> BleScannerLike:
    return BleakScanner()


def _default_client_factory(device: BleDeviceLike) -> BleClientLike:
    if not isinstance(device, BLEDevice):
        msg = "The default Bleak client factory requires a BLEDevice."
        raise TypeError(msg)
    return _BleakClientAdapter(BleakClient(device))


class _BleakClientAdapter:
    """Narrow Bleak's flexible client API to the typed operations used here."""

    def __init__(self, client: BleakClient) -> None:
        self._client = client

    async def connect(self) -> None:
        """Connect to the wrapped GATT client."""
        await self._client.connect()

    async def disconnect(self) -> None:
        """Disconnect the wrapped GATT client."""
        await self._client.disconnect()

    async def read_gatt_char(self, characteristic: str) -> bytearray:
        """Read a characteristic by normalized UUID."""
        return await self._client.read_gatt_char(characteristic)
