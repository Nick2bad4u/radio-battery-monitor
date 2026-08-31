"""Dark-first Tkinter dashboard for Bluetooth LE and ANT+ battery telemetry."""

from __future__ import annotations

import logging
import tkinter as tk
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from radio_battery_monitor.backends.ant import AntBackend
from radio_battery_monitor.backends.ble import BluetoothBackend
from radio_battery_monitor.controller import MonitorController
from radio_battery_monitor.diagnostics import configure_logging
from radio_battery_monitor.models import (
    AdapterState,
    BatteryStatus,
    DeviceState,
    DeviceUpdate,
    MonitorEventKind,
    ProtocolName,
    newest_reading,
)
from radio_battery_monitor.settings import (
    AppSettings,
    SettingsError,
    SettingsStore,
    ThemeMode,
    add_or_link_identity,
    remove_identity,
)
from radio_battery_monitor.theme import apply_theme, create_app_icon

if TYPE_CHECKING:
    from radio_battery_monitor.models import AdapterStatus, BatteryReading, DeviceIdentity, LogicalDevice, MonitorEvent

EVENT_POLL_MILLISECONDS: Final = 100
AGE_REFRESH_MILLISECONDS: Final = 1_000
MAX_DIAGNOSTIC_LINES: Final = 300
SECONDS_PER_MINUTE: Final = 60
DEFAULT_WINDOW_GEOMETRY: Final = "1240x780"


@dataclass(slots=True)
class RowState:
    """UI-only accumulated state for one logical or discovered device."""

    update: DeviceUpdate
    readings: tuple[BatteryReading, ...] = ()

    @property
    def newest_reading(self) -> BatteryReading | None:
        """The newest reading across radio identities."""
        return newest_reading(self.readings)


@dataclass(frozen=True, slots=True)
class AdapterCard:
    """Widgets updated together when a host-radio state changes."""

    indicator: ttk.Label
    state: ttk.Label
    detail: ttk.Label


def format_battery(reading: BatteryReading | None, state: DeviceState, *, host_powered: bool = False) -> str:
    """Format a truthful battery cell without deriving missing values.

    Returns:
        A display value assembled only from reported battery information.
    """
    if host_powered:
        return "N/A — host powered"
    if reading is None:
        return "Not reported" if state is DeviceState.NOT_REPORTED else "—"
    parts: list[str] = []
    if reading.percent is not None:
        parts.append(f"{reading.percent}%")
    if reading.voltage is not None:
        parts.append(f"{reading.voltage:.3f} V")
    if reading.status is not None:
        parts.append(reading.status.value.title())
    return " · ".join(parts) if parts else "Not reported"


def battery_severity(reading: BatteryReading | None, settings: AppSettings) -> str:
    """Return the semantic row tag for percentage or qualitative ANT status."""
    if reading is None:
        return "normal"
    if reading.status is BatteryStatus.CRITICAL or (
        reading.percent is not None and reading.percent <= settings.critical_percent
    ):
        return "critical"
    if reading.status is BatteryStatus.INVALID:
        return "warning"
    if reading.status is BatteryStatus.LOW or (reading.percent is not None and reading.percent <= settings.low_percent):
        return "low"
    return "normal"


class RadioBatteryApp:
    """Main application window and event-driven session state."""

    def __init__(self, root: tk.Tk, *, settings_store: SettingsStore | None = None) -> None:
        """Build the dashboard without claiming either radio."""
        self._root = root
        self._settings_store = settings_store or SettingsStore()
        self._logger = configure_logging()
        self._settings = self._load_settings()
        self._palette = apply_theme(self._root, self._settings.theme)
        self._icon = create_app_icon(self._root, self._palette)
        self._controller = self._make_controller()
        self._rows: dict[str, RowState] = {}
        self._tree_keys: dict[str, str] = {}
        self._adapter_cards: dict[ProtocolName, AdapterCard] = {}
        self._closing = False

        self._configure_window()
        self._build_widgets()
        self._configure_classic_widgets()
        self._configure_tree_tags()
        self._seed_configured_rows()
        self._root.protocol("WM_DELETE_WINDOW", self._on_close)
        _ = self._root.after(50, self._probe_adapters)
        _ = self._root.after(EVENT_POLL_MILLISECONDS, self._drain_events)
        _ = self._root.after(AGE_REFRESH_MILLISECONDS, self._refresh_ages)
        if self._settings.start_monitoring_on_launch:
            _ = self._root.after(300, self._start)

    def _load_settings(self) -> AppSettings:
        try:
            return self._settings_store.load()
        except SettingsError as error:
            self._logger.exception("Settings could not be loaded")
            _ = messagebox.showwarning(
                "Settings could not be loaded",
                f"{error}\n\nDefaults will be used. The existing file was not overwritten.",
            )
            return AppSettings()

    def _make_controller(self) -> MonitorController:
        return MonitorController(
            (
                BluetoothBackend(refresh_seconds=float(self._settings.refresh_seconds)),
                AntBackend(report_timeout_seconds=float(self._settings.refresh_seconds)),
            ),
            self._settings,
        )

    def _configure_window(self) -> None:
        self._root.title("Radio Battery Monitor")
        self._root.geometry(DEFAULT_WINDOW_GEOMETRY)
        self._root.minsize(980, 620)
        default_icon = True
        self._root.iconphoto(default_icon, self._icon)

    def _build_widgets(self) -> None:
        outer = ttk.Frame(self._root, padding=(20, 16, 20, 12))
        outer.pack(fill=tk.BOTH, expand=True)
        header = ttk.Frame(outer)
        header.pack(fill=tk.X, pady=(0, 14))
        title_group = ttk.Frame(header)
        title_group.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(title_group, text="Radio Battery Monitor", style="Title.TLabel").pack(anchor=tk.W)
        ttk.Label(
            title_group,
            text="One honest battery view across Bluetooth LE and ANT+",
            style="Muted.TLabel",
        ).pack(anchor=tk.W, pady=(2, 0))
        ttk.Button(header, text="⚙  Settings", command=self._open_settings, style="Icon.TButton").pack(side=tk.RIGHT)

        cards = ttk.Frame(outer)
        cards.pack(fill=tk.X, pady=(0, 14))
        self._add_adapter_card(cards, ProtocolName.BLUETOOTH_LE, "BLUETOOTH LE", "Bluetooth adapter", 0)
        self._add_adapter_card(cards, ProtocolName.ANT_PLUS, "ANT+ USB", "Dynastream USB Stick 2", 1)

        toolbar = ttk.Frame(outer)
        toolbar.pack(fill=tk.X, pady=(0, 10))
        self._start_button = ttk.Button(
            toolbar,
            text="▶  Start monitoring",
            command=self._start,
            style="Accent.TButton",
        )
        self._start_button.pack(side=tk.LEFT)
        self._stop_button = ttk.Button(toolbar, text="■  Stop", command=self._stop, state=tk.DISABLED)
        self._stop_button.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=2)
        ttk.Button(toolbar, text="↻  Refresh", command=self._refresh).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="⌕  Discover", command=self._discover).pack(side=tk.LEFT, padx=(8, 0))
        self._remove_button = ttk.Button(
            toolbar,
            text="✕  Remove",
            command=self._remove_selected,
            style="Danger.TButton",
            state=tk.DISABLED,
        )
        self._remove_button.pack(side=tk.RIGHT)
        self._add_button = ttk.Button(
            toolbar,
            text="✚  Add / link",
            command=self._add_selected,
            state=tk.DISABLED,
        )
        self._add_button.pack(side=tk.RIGHT, padx=(0, 8))

        self._notebook = ttk.Notebook(outer)
        self._notebook.pack(fill=tk.BOTH, expand=True)
        devices_tab = ttk.Frame(self._notebook, padding=(0, 8, 0, 0))
        activity_tab = ttk.Frame(self._notebook, padding=(0, 8, 0, 0))
        self._notebook.add(devices_tab, text="  Devices  ")
        self._notebook.add(activity_tab, text="  Activity  ")
        self._build_devices_tab(devices_tab)
        self._build_activity_tab(activity_tab)

        status_bar = ttk.Frame(outer, style="Surface.TFrame", padding=(10, 7))
        status_bar.pack(fill=tk.X, pady=(10, 0))
        _ = status_bar.configure(height=34)
        _ = status_bar.pack_propagate(flag=False)
        self._session_indicator = ttk.Label(status_bar, text="●", style="SurfaceMuted.TLabel")
        self._session_indicator.pack(side=tk.LEFT)
        self._status_text = tk.StringVar(value="Ready — radios have not been claimed")
        ttk.Label(status_bar, textvariable=self._status_text, style="SurfaceMuted.TLabel").pack(
            side=tk.LEFT,
            padx=(7, 0),
        )
        self._device_count_text = tk.StringVar(value="0 devices")
        ttk.Label(status_bar, textvariable=self._device_count_text, style="SurfaceMuted.TLabel").pack(side=tk.RIGHT)

    def _build_devices_tab(self, parent: ttk.Frame) -> None:
        table_frame = ttk.Frame(parent, style="Surface.TFrame")
        table_frame.pack(fill=tk.BOTH, expand=True)
        columns = ("radio", "battery", "last_seen", "state", "detail")
        self._tree = ttk.Treeview(table_frame, columns=columns, show="tree headings", selectmode="browse")
        headings = {
            "#0": "Device",
            "radio": "Radio",
            "battery": "Battery",
            "last_seen": "Last seen",
            "state": "State",
            "detail": "Detail",
        }
        for column, title in headings.items():
            self._tree.heading(column, text=title)
        _ = self._tree.column("#0", width=220, minwidth=160)
        _ = self._tree.column("radio", width=115, minwidth=90, anchor=tk.CENTER)
        _ = self._tree.column("battery", width=185, minwidth=130)
        _ = self._tree.column("last_seen", width=100, minwidth=80, anchor=tk.CENTER)
        _ = self._tree.column("state", width=130, minwidth=105)
        _ = self._tree.column("detail", width=500, minwidth=260)
        vertical = ttk.Scrollbar(
            table_frame,
            orient=tk.VERTICAL,
            command=self._tree.yview,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType] -- tkinter callback is untyped.
        )
        horizontal = ttk.Scrollbar(
            table_frame,
            orient=tk.HORIZONTAL,
            command=self._tree.xview,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType] -- tkinter callback is untyped.
        )
        _ = self._tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self._tree.grid(row=0, column=0, sticky=tk.NSEW)
        vertical.grid(row=0, column=1, sticky=tk.NS)
        horizontal.grid(row=1, column=0, sticky=tk.EW)
        _ = table_frame.rowconfigure(0, weight=1)
        _ = table_frame.columnconfigure(0, weight=1)
        _ = self._tree.bind("<<TreeviewSelect>>", self._on_selection)
        _ = self._tree.bind("<Double-1>", self._on_device_double_click)
        _ = self._tree.bind("<Delete>", self._on_remove_key)

    def _build_activity_tab(self, parent: ttk.Frame) -> None:
        controls = ttk.Frame(parent)
        controls.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(controls, text="Session activity", style="Section.TLabel").pack(side=tk.LEFT)
        ttk.Button(controls, text="⧉  Copy", command=self._copy_diagnostics).pack(side=tk.RIGHT)
        ttk.Button(controls, text="⌫  Clear", command=self._clear_diagnostics).pack(side=tk.RIGHT, padx=(0, 8))
        text_frame = ttk.Frame(parent, style="Surface.TFrame")
        text_frame.pack(fill=tk.BOTH, expand=True)
        self._diagnostics = tk.Text(
            text_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            borderwidth=0,
            padx=12,
            pady=10,
            font=("Cascadia Mono", 9),
        )
        scrollbar = ttk.Scrollbar(
            text_frame,
            orient=tk.VERTICAL,
            command=self._diagnostics.yview,  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType] -- tkinter callback is untyped.
        )
        _ = self._diagnostics.configure(yscrollcommand=scrollbar.set)
        self._diagnostics.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _add_adapter_card(
        self,
        parent: ttk.Frame,
        protocol: ProtocolName,
        eyebrow: str,
        title: str,
        column: int,
    ) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=14)
        card.grid(row=0, column=column, sticky=tk.NSEW, padx=(0, 7) if column == 0 else (7, 0))
        _ = parent.columnconfigure(column, weight=1, uniform="adapter")
        top = ttk.Frame(card, style="Surface.TFrame")
        top.pack(fill=tk.X)
        indicator = ttk.Label(top, text="●", style="SurfaceMuted.TLabel", font=("Segoe UI Symbol", 13))
        indicator.pack(side=tk.LEFT)
        title_group = ttk.Frame(top, style="Surface.TFrame")
        title_group.pack(side=tk.LEFT, padx=(9, 0))
        ttk.Label(title_group, text=eyebrow, style="SurfaceMuted.TLabel", font=("Segoe UI Semibold", 8)).pack(
            anchor=tk.W,
        )
        ttk.Label(title_group, text=title, style="CardTitle.TLabel").pack(anchor=tk.W)
        state = ttk.Label(top, text="CHECKING", style="SurfaceMuted.TLabel", font=("Segoe UI Semibold", 9))
        state.pack(side=tk.RIGHT)
        detail = ttk.Label(
            card, text="Inspecting adapter without claiming it…", style="SurfaceMuted.TLabel", wraplength=500
        )
        detail.pack(fill=tk.X, anchor=tk.W, pady=(11, 0))
        self._adapter_cards[protocol] = AdapterCard(indicator, state, detail)

    def _configure_classic_widgets(self) -> None:
        _ = self._diagnostics.configure(
            background=self._palette.surface,
            foreground=self._palette.text,
            insertbackground=self._palette.text,
            selectbackground=self._palette.selection,
            selectforeground="#FFFFFF",
        )

    def _configure_tree_tags(self) -> None:
        _ = self._tree.tag_configure("even", background=self._palette.surface)
        _ = self._tree.tag_configure("odd", background=self._palette.surface_alt)
        _ = self._tree.tag_configure("warning", foreground=self._palette.warning)
        _ = self._tree.tag_configure("low", foreground=self._palette.warning)
        _ = self._tree.tag_configure("critical", foreground=self._palette.critical)
        _ = self._tree.tag_configure("discovered", foreground=self._palette.muted)

    def _seed_configured_rows(self) -> None:
        for device in self._settings.devices:
            if not device.identities:
                continue
            identity = device.identities[0]
            self._apply_device_update(
                DeviceUpdate(
                    logical_id=device.logical_id,
                    display_name=device.alias,
                    protocol=identity.protocol,
                    state=DeviceState.STOPPED,
                    detail="Configured — monitoring has not started",
                    identity=identity,
                ),
            )

    def _probe_adapters(self) -> None:
        if self._closing:
            return
        for status in self._controller.probe():
            self._apply_adapter_status(status)

    def _start(self) -> None:
        try:
            self._controller.start()
        except (OSError, RuntimeError) as error:
            self._show_error("Unable to start monitoring", error)
            return
        self._set_session_active(active=True)
        minutes = max(1, self._settings.refresh_seconds // SECONDS_PER_MINUTE)
        self._status_text.set(f"Monitoring — automatic refresh every {minutes} min")
        self._append_diagnostic("Monitoring session started.")

    def _stop(self) -> None:
        self._controller.stop()
        self._controller = self._make_controller()
        self._set_session_active(active=False)
        self._status_text.set("Stopped — radios released")
        self._append_diagnostic("Monitoring session stopped and radios released.")
        self._probe_adapters()

    def _set_session_active(self, *, active: bool) -> None:
        _ = self._start_button.configure(state=tk.DISABLED if active else tk.NORMAL)
        _ = self._stop_button.configure(state=tk.NORMAL if active else tk.DISABLED)
        _ = self._session_indicator.configure(style="Success.TLabel" if active else "SurfaceMuted.TLabel")

    def _refresh(self) -> None:
        try:
            self._controller.refresh()
        except (OSError, RuntimeError) as error:
            self._show_error("Unable to refresh", error)
            return
        self._set_session_active(active=True)
        self._status_text.set("Refreshing configured devices…")
        self._append_diagnostic("Immediate refresh requested.")

    def _discover(self) -> None:
        try:
            self._controller.discover()
        except (OSError, RuntimeError) as error:
            self._show_error("Unable to discover devices", error)
            return
        self._set_session_active(active=True)
        self._status_text.set("Discovering for 15 seconds — wake or move sensors")
        self._append_diagnostic("Device discovery requested.")

    def _drain_events(self) -> None:
        if self._closing:
            return
        for event in self._controller.drain_events():
            self._handle_event(event)
        _ = self._root.after(EVENT_POLL_MILLISECONDS, self._drain_events)

    def _handle_event(self, event: MonitorEvent) -> None:
        if event.kind is MonitorEventKind.ADAPTER and event.adapter is not None:
            self._apply_adapter_status(event.adapter)
        elif event.device is not None:
            self._apply_device_update(event.device)
        if event.message:
            self._append_diagnostic(event.message)

    def _apply_adapter_status(self, status: AdapterStatus) -> None:
        card = self._adapter_cards[status.protocol]
        state_text = status.state.value.replace("_", " ").upper()
        style = _adapter_state_style(status.state)
        _ = card.indicator.configure(style=style)
        _ = card.state.configure(text=state_text, style=style)
        _ = card.detail.configure(text=status.detail)
        level = logging.ERROR if status.state in {AdapterState.ERROR, AdapterState.DRIVER_UNAVAILABLE} else logging.INFO
        self._logger.log(level, "%s adapter: %s", status.protocol.value, status.state.value)
        if status.state in {AdapterState.ERROR, AdapterState.IN_USE, AdapterState.DRIVER_UNAVAILABLE}:
            self._append_diagnostic(f"{status.display_name}: {status.detail}")

    def _apply_device_update(self, update: DeviceUpdate) -> None:
        if (
            update.state is DeviceState.DISCOVERED
            and not self._settings.show_unconfigured_devices
            and self._configured_device(update.logical_id) is None
        ):
            return
        existing = self._rows.get(update.logical_id)
        readings = existing.readings if existing is not None else ()
        if update.reading is not None:
            readings = (*readings[-7:], update.reading)
        row = RowState(update=update, readings=readings)
        self._rows[update.logical_id] = row
        self._render_row(update.logical_id, row)
        self._update_device_count()
        self._logger.info("Device update: protocol=%s state=%s", update.protocol.value, update.state.value)
        if update.state in {DeviceState.ERROR, DeviceState.NOT_REPORTED}:
            self._append_diagnostic(f"{update.display_name}: {update.detail}")

    def _render_row(self, row_key: str, row: RowState) -> None:
        tree_key = self._tree_keys.get(row_key)
        if tree_key is None:
            tree_key = f"row-{len(self._tree_keys)}"
            self._tree_keys[row_key] = tree_key
        update = row.update
        configured = self._configured_device(update.logical_id)
        battery = format_battery(
            row.newest_reading,
            update.state,
            host_powered=configured.host_powered if configured is not None else False,
        )
        values = (
            _protocol_label(configured, update.protocol, update.identity),
            battery,
            _last_seen_text(update),
            update.state.value.replace("_", " ").title(),
            update.detail,
        )
        row_number = list(self._rows).index(row_key)
        tags = ["even" if row_number % 2 == 0 else "odd"]
        severity = battery_severity(row.newest_reading, self._settings)
        if severity != "normal":
            tags.append(severity)
        if update.state is DeviceState.DISCOVERED:
            tags.append("discovered")
        if self._tree.exists(tree_key):
            self._tree.item(tree_key, text=update.display_name, values=values, tags=tuple(tags))
        else:
            _ = self._tree.insert("", tk.END, iid=tree_key, text=update.display_name, values=values, tags=tuple(tags))

    def _refresh_ages(self) -> None:
        if self._closing:
            return
        for row_key, row in self._rows.items():
            self._render_row(row_key, row)
        _ = self._root.after(AGE_REFRESH_MILLISECONDS, self._refresh_ages)

    def _add_selected(self) -> None:
        row = self._selected_row()
        if row is None or row.update.identity is None:
            _ = messagebox.showinfo("Select a device", "Select a discovered or configured device first.")
            return
        dialog = DeviceMappingDialog(self._root, row.update, self._settings.devices)
        self._root.wait_window(dialog.window)
        if dialog.result is None:
            return
        logical_id, alias, kind, host_powered = dialog.result
        try:
            updated = add_or_link_identity(
                self._settings,
                identity=row.update.identity,
                alias=alias,
                kind=kind,
                logical_id=logical_id,
            )
            updated = _set_host_powered(updated, logical_id, host_powered=host_powered)
            self._replace_settings(updated)
        except (OSError, SettingsError, RuntimeError) as error:
            self._show_error("Unable to save device", error)
            return
        self._promote_mapping(row.update.logical_id, logical_id, alias, row.update.identity)
        self._append_diagnostic(f"Saved mapping for {alias}.")
        if self._controller.running:
            self._append_diagnostic("The new radio identity will be monitored after the next Stop / Start cycle.")

    def _promote_mapping(
        self,
        source_key: str,
        logical_id: str,
        alias: str,
        identity: DeviceIdentity,
    ) -> None:
        source = self._rows.get(source_key)
        self._remove_row(source_key)
        for key, row in tuple(self._rows.items()):
            if key != logical_id and row.update.identity is not None and row.update.identity.key == identity.key:
                self._remove_row(key)
        existing = self._rows.get(logical_id)
        readings = existing.readings if existing is not None else ()
        if source is not None:
            readings = (*readings, *source.readings)[-8:]
            update = replace(
                source.update,
                logical_id=logical_id,
                display_name=alias,
                state=DeviceState.STOPPED,
                detail="Configured — restart monitoring to apply this identity",
            )
        elif existing is not None:
            update = replace(existing.update, display_name=alias)
        else:
            update = DeviceUpdate(
                logical_id=logical_id,
                display_name=alias,
                protocol=identity.protocol,
                state=DeviceState.STOPPED,
                detail="Configured — restart monitoring to apply this identity",
                identity=identity,
            )
        self._rows[logical_id] = RowState(update, readings)
        self._render_row(logical_id, self._rows[logical_id])
        self._update_device_count()

    def _remove_selected(self) -> None:
        row = self._selected_row()
        if row is None or row.update.identity is None:
            _ = messagebox.showinfo("Select a device", "Select a configured device identity first.")
            return
        if self._configured_device(row.update.logical_id) is None:
            _ = messagebox.showinfo("Not configured", "That discovery result has not been added.")
            return
        if not messagebox.askyesno("Remove identity", "Stop monitoring this selected radio identity?"):
            return
        try:
            updated = remove_identity(self._settings, row.update.identity)
            self._replace_settings(updated)
        except (OSError, RuntimeError) as error:
            self._show_error("Unable to remove device", error)
            return
        self._remove_row(row.update.logical_id)
        remaining = next((device for device in updated.devices if device.logical_id == row.update.logical_id), None)
        if remaining is not None and remaining.identities:
            self._apply_device_update(
                DeviceUpdate(
                    logical_id=remaining.logical_id,
                    display_name=remaining.alias,
                    protocol=remaining.identities[0].protocol,
                    state=DeviceState.STOPPED,
                    detail="Identity removed — restart monitoring to apply",
                    identity=remaining.identities[0],
                ),
            )
        self._append_diagnostic("Selected radio identity removed.")

    def _replace_settings(self, settings: AppSettings) -> None:
        was_running = self._controller.running
        previous = self._settings
        self._settings_store.save(settings)
        self._settings = settings
        self._controller.update_settings(settings)
        if not was_running:
            self._controller = self._make_controller()
        if settings.theme is not previous.theme:
            self._apply_theme(settings.theme)
        if previous.show_unconfigured_devices and not settings.show_unconfigured_devices:
            self._hide_unconfigured_rows()
        for row_key, row in self._rows.items():
            self._render_row(row_key, row)
        if was_running and _radio_settings_changed(previous, settings):
            self._append_diagnostic("Radio setting changes will apply after the next Stop / Start cycle.")

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._root, self._settings)
        self._root.wait_window(dialog.window)
        if dialog.result is None:
            return
        try:
            self._replace_settings(dialog.result)
        except (OSError, SettingsError, RuntimeError) as error:
            self._show_error("Unable to save settings", error)
            return
        self._append_diagnostic("Settings saved.")

    def _apply_theme(self, theme: ThemeMode) -> None:
        self._palette = apply_theme(self._root, theme)
        self._icon = create_app_icon(self._root, self._palette)
        default_icon = True
        self._root.iconphoto(default_icon, self._icon)
        self._configure_classic_widgets()
        self._configure_tree_tags()

    def _hide_unconfigured_rows(self) -> None:
        for row_key, row in tuple(self._rows.items()):
            if row.update.state is DeviceState.DISCOVERED and self._configured_device(row_key) is None:
                self._remove_row(row_key)

    def _remove_row(self, row_key: str) -> None:
        _ = self._rows.pop(row_key, None)
        tree_key = self._tree_keys.pop(row_key, None)
        if tree_key is not None and self._tree.exists(tree_key):
            self._tree.delete(tree_key)
        self._update_device_count()

    def _selected_row(self) -> RowState | None:
        selection = self._tree.selection()
        if not selection:
            return None
        selected_tree_key = selection[0]
        row_key = next((key for key, value in self._tree_keys.items() if value == selected_tree_key), None)
        return self._rows.get(row_key) if row_key is not None else None

    def _configured_device(self, logical_id: str) -> LogicalDevice | None:
        return next((device for device in self._settings.devices if device.logical_id == logical_id), None)

    def _on_selection(self, _event: tk.Event[tk.Misc]) -> None:
        row = self._selected_row()
        has_identity = row is not None and row.update.identity is not None
        is_configured = row is not None and self._configured_device(row.update.logical_id) is not None
        _ = self._add_button.configure(state=tk.NORMAL if has_identity else tk.DISABLED)
        _ = self._remove_button.configure(state=tk.NORMAL if is_configured else tk.DISABLED)

    def _on_device_double_click(self, _event: tk.Event[tk.Misc]) -> None:
        if self._selected_row() is not None:
            self._add_selected()

    def _on_remove_key(self, _event: tk.Event[tk.Misc]) -> None:
        if self._selected_row() is not None:
            self._remove_selected()

    def _append_diagnostic(self, message: str) -> None:
        timestamp = datetime.now(tz=UTC).astimezone().strftime("%H:%M:%S")
        _ = self._diagnostics.configure(state=tk.NORMAL)
        self._diagnostics.insert(tk.END, f"{timestamp}  {message}\n")
        line_count = int(self._diagnostics.index("end-1c").split(".")[0])
        if line_count > MAX_DIAGNOSTIC_LINES:
            self._diagnostics.delete("1.0", f"{line_count - MAX_DIAGNOSTIC_LINES}.0")
        self._diagnostics.see(tk.END)
        _ = self._diagnostics.configure(state=tk.DISABLED)

    def _copy_diagnostics(self) -> None:
        content = self._diagnostics.get("1.0", "end-1c")
        self._root.clipboard_clear()
        self._root.clipboard_append(content)
        self._status_text.set("Activity copied to the clipboard")

    def _clear_diagnostics(self) -> None:
        _ = self._diagnostics.configure(state=tk.NORMAL)
        self._diagnostics.delete("1.0", tk.END)
        _ = self._diagnostics.configure(state=tk.DISABLED)
        self._status_text.set("Session activity cleared")

    def _update_device_count(self) -> None:
        configured = sum(1 for key in self._rows if self._configured_device(key) is not None)
        discovered = sum(1 for row in self._rows.values() if row.update.state is DeviceState.DISCOVERED)
        self._device_count_text.set(f"{configured} configured  ·  {discovered} nearby")

    def _show_error(self, title: str, error: Exception) -> None:
        self._logger.error(title, exc_info=error)
        _ = messagebox.showerror(title, str(error))

    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._status_text.set("Stopping workers and releasing radios…")
        self._controller.stop()
        self._root.destroy()


class DeviceMappingDialog:
    """Modal for creating or linking one discovered radio identity."""

    def __init__(self, parent: tk.Tk, update: DeviceUpdate, devices: tuple[LogicalDevice, ...]) -> None:
        """Build the modal and expose the submitted result after it closes."""
        self.window = tk.Toplevel(parent)
        self.window.title("Add or link device")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.resizable(width=False, height=False)
        self.result: tuple[str, str, str, bool] | None = None
        self._devices = devices
        self._choices = ["Create a new device", *(f"Link to: {device.alias}" for device in devices)]
        self._choice = tk.StringVar(value=self._choices[0])
        self._alias = tk.StringVar(value=update.display_name)
        self._kind = tk.StringVar(value="fitness sensor")
        self._host_powered = tk.BooleanVar(value=False)
        frame = ttk.Frame(self.window, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="Add radio identity", style="Section.TLabel").grid(
            row=0,
            column=0,
            columnspan=2,
            sticky=tk.W,
            pady=(0, 12),
        )
        ttk.Label(frame, text="Identity target").grid(row=1, column=0, sticky=tk.W, pady=6)
        choice = ttk.Combobox(frame, textvariable=self._choice, values=self._choices, state="readonly", width=40)
        choice.grid(row=1, column=1, sticky=tk.EW, pady=6)
        _ = choice.bind("<<ComboboxSelected>>", self._on_choice)
        ttk.Label(frame, text="Display name").grid(row=2, column=0, sticky=tk.W, pady=6)
        ttk.Entry(frame, textvariable=self._alias).grid(row=2, column=1, sticky=tk.EW, pady=6)
        ttk.Label(frame, text="Device kind").grid(row=3, column=0, sticky=tk.W, pady=6)
        ttk.Entry(frame, textvariable=self._kind).grid(row=3, column=1, sticky=tk.EW, pady=6)
        ttk.Checkbutton(
            frame,
            text="Externally / host powered (battery N/A)",
            variable=self._host_powered,
        ).grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(10, 6))
        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, sticky=tk.E, pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=self.window.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Save device", command=self._save, style="Accent.TButton").pack(
            side=tk.RIGHT,
            padx=(0, 8),
        )
        _ = frame.columnconfigure(1, weight=1)
        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
        _center_dialog(self.window, parent)

    def _on_choice(self, _event: tk.Event[tk.Misc]) -> None:
        index = self._choices.index(self._choice.get())
        if index > 0:
            device = self._devices[index - 1]
            self._alias.set(device.alias)
            self._kind.set(device.kind)
            self._host_powered.set(device.host_powered)

    def _save(self) -> None:
        alias = self._alias.get().strip()
        kind = self._kind.get().strip()
        if not alias or not kind:
            _ = messagebox.showerror("Missing value", "Display name and device kind are required.", parent=self.window)
            return
        index = self._choices.index(self._choice.get())
        logical_id = self._devices[index - 1].logical_id if index > 0 else uuid4().hex
        self.result = (logical_id, alias, kind, self._host_powered.get())
        self.window.destroy()


class SettingsDialog:
    """Modal editor for persisted appearance and monitoring preferences."""

    def __init__(self, parent: tk.Tk, settings: AppSettings) -> None:
        """Build a validated settings form from the current snapshot."""
        self.window = tk.Toplevel(parent)
        self.window.title("Settings")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.resizable(width=False, height=False)
        self.result: AppSettings | None = None
        self._original = settings
        self._theme = tk.StringVar(value=settings.theme.value.title())
        self._refresh = tk.IntVar(value=settings.refresh_seconds)
        self._low = tk.IntVar(value=settings.low_percent)
        self._critical = tk.IntVar(value=settings.critical_percent)
        self._auto_start = tk.BooleanVar(value=settings.start_monitoring_on_launch)
        self._show_discovered = tk.BooleanVar(value=settings.show_unconfigured_devices)
        frame = ttk.Frame(self.window, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(frame, text="Settings", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky=tk.W)
        ttk.Label(
            frame,
            text="Radio changes apply after the next Stop / Start cycle.",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(2, 16))
        ttk.Label(frame, text="Theme").grid(row=2, column=0, sticky=tk.W, pady=7)
        ttk.Combobox(
            frame,
            textvariable=self._theme,
            values=("Dark", "Light"),
            state="readonly",
            width=20,
        ).grid(row=2, column=1, sticky=tk.EW, pady=7)
        ttk.Label(frame, text="Refresh interval (seconds)").grid(row=3, column=0, sticky=tk.W, pady=7)
        ttk.Spinbox(frame, from_=30, to=3600, increment=30, textvariable=self._refresh, width=12).grid(
            row=3,
            column=1,
            sticky=tk.W,
            pady=7,
        )
        ttk.Label(frame, text="Low battery threshold (%)").grid(row=4, column=0, sticky=tk.W, pady=7)
        ttk.Spinbox(frame, from_=0, to=100, textvariable=self._low, width=12).grid(
            row=4,
            column=1,
            sticky=tk.W,
            pady=7,
        )
        ttk.Label(frame, text="Critical threshold (%)").grid(row=5, column=0, sticky=tk.W, pady=7)
        ttk.Spinbox(frame, from_=0, to=100, textvariable=self._critical, width=12).grid(
            row=5,
            column=1,
            sticky=tk.W,
            pady=7,
        )
        ttk.Checkbutton(frame, text="Start monitoring when the app opens", variable=self._auto_start).grid(
            row=6,
            column=0,
            columnspan=2,
            sticky=tk.W,
            pady=(12, 6),
        )
        ttk.Checkbutton(frame, text="Show unconfigured discovery results", variable=self._show_discovered).grid(
            row=7,
            column=0,
            columnspan=2,
            sticky=tk.W,
            pady=6,
        )
        buttons = ttk.Frame(frame)
        buttons.grid(row=8, column=0, columnspan=2, sticky=tk.E, pady=(18, 0))
        ttk.Button(buttons, text="Cancel", command=self.window.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Save settings", command=self._save, style="Accent.TButton").pack(
            side=tk.RIGHT,
            padx=(0, 8),
        )
        _ = frame.columnconfigure(1, weight=1)
        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
        _center_dialog(self.window, parent)

    def _save(self) -> None:
        try:
            theme = ThemeMode(self._theme.get().casefold())
            updated = replace(
                self._original,
                refresh_seconds=self._refresh.get(),
                low_percent=self._low.get(),
                critical_percent=self._critical.get(),
                theme=theme,
                start_monitoring_on_launch=self._auto_start.get(),
                show_unconfigured_devices=self._show_discovered.get(),
            )
        except (SettingsError, ValueError, tk.TclError) as error:
            _ = messagebox.showerror("Invalid settings", str(error), parent=self.window)
            return
        self.result = updated
        self.window.destroy()


def _set_host_powered(settings: AppSettings, logical_id: str, *, host_powered: bool) -> AppSettings:
    devices = tuple(
        replace(device, host_powered=host_powered if device.logical_id == logical_id else device.host_powered)
        for device in settings.devices
    )
    return replace(settings, devices=devices)


def _protocol_label(
    configured: LogicalDevice | None,
    fallback: ProtocolName,
    identity: DeviceIdentity | None,
) -> str:
    if configured is None:
        return "BLE" if fallback is ProtocolName.BLUETOOTH_LE else _ant_protocol_label(identity)
    protocols = {identity.protocol for identity in configured.identities}
    if protocols == {ProtocolName.BLUETOOTH_LE, ProtocolName.ANT_PLUS}:
        return "BLE + ANT+"
    if ProtocolName.BLUETOOTH_LE in protocols:
        return "BLE"
    ant_identities = [item for item in configured.identities if item.protocol is ProtocolName.ANT_PLUS]
    return _ant_protocol_label(ant_identities[0]) if len(ant_identities) == 1 else "ANT+ Multi"


def _ant_protocol_label(identity: DeviceIdentity | None) -> str:
    if identity is None:
        return "ANT+"
    labels = {11: "ANT+ Power", 17: "ANT+ FE", 120: "ANT+ HR"}
    device_type = identity.device_type if identity.device_type is not None else -1
    return labels.get(device_type, "ANT+")


def _adapter_state_style(state: AdapterState) -> str:
    if state is AdapterState.READY:
        return "Success.TLabel"
    if state in {AdapterState.SCANNING, AdapterState.IN_USE}:
        return "Warning.TLabel"
    if state in {AdapterState.ERROR, AdapterState.DRIVER_UNAVAILABLE, AdapterState.DISCONNECTED}:
        return "Critical.TLabel"
    return "SurfaceMuted.TLabel"


def _radio_settings_changed(previous: AppSettings, current: AppSettings) -> bool:
    return previous.devices != current.devices or previous.refresh_seconds != current.refresh_seconds


def _center_dialog(window: tk.Toplevel, parent: tk.Tk) -> None:
    window.update_idletasks()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - window.winfo_reqwidth()) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - window.winfo_reqheight()) // 2)
    window.geometry(f"+{x}+{y}")


def _format_age(observed_at: datetime) -> str:
    age_seconds = max(0, int((datetime.now(tz=UTC) - observed_at.astimezone(UTC)).total_seconds()))
    if age_seconds < SECONDS_PER_MINUTE:
        return f"{age_seconds}s ago"
    age_minutes = age_seconds // SECONDS_PER_MINUTE
    if age_minutes < SECONDS_PER_MINUTE:
        return f"{age_minutes}m ago"
    return f"{age_minutes // SECONDS_PER_MINUTE}h ago"


def _last_seen_text(update: DeviceUpdate) -> str:
    if update.state is DeviceState.STOPPED:
        return "Never"
    return _format_age(update.observed_at)


def run_app() -> None:
    """Launch the Radio Battery Monitor dashboard."""
    root = tk.Tk()
    _ = RadioBatteryApp(root)
    root.mainloop()
