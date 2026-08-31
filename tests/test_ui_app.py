"""Headless integration coverage for the Tk application state machine."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, cast

from radio_battery_monitor import ui
from radio_battery_monitor.models import (
    AdapterState,
    AdapterStatus,
    BatteryReading,
    DeviceIdentity,
    DeviceState,
    DeviceUpdate,
    LogicalDevice,
    MonitorEvent,
    MonitorEventKind,
    ProtocolName,
)
from radio_battery_monitor.settings import AppSettings
from radio_battery_monitor.theme import DARK_PALETTE

if TYPE_CHECKING:
    import pytest

    from radio_battery_monitor.controller import MonitorController
    from radio_battery_monitor.settings import ThemeMode
    from radio_battery_monitor.theme import Palette


class FakeVariable:
    def __init__(self, *_args: object, value: object = "", **_kwargs: object) -> None:
        self.value = value

    def get(self) -> object:
        return self.value

    def set(self, value: object) -> None:
        self.value = value


class FakeWidget:
    def __init__(self, *_args: object, **kwargs: object) -> None:
        self.config: dict[str, object] = dict(kwargs)
        self.items: dict[str, dict[str, object]] = {}
        self.selected: tuple[str, ...] = ()
        self.content = ""
        self.destroyed = False
        self.after_calls: list[tuple[int, object]] = []
        self.protocol_calls: list[tuple[str, object]] = []
        self.clipboard = ""

    def pack(self, **_kwargs: object) -> None:
        return None

    def grid(self, **_kwargs: object) -> None:
        return None

    def configure(self, **kwargs: object) -> None:
        self.config.update(kwargs)

    def pack_propagate(self, **_kwargs: object) -> bool:
        return False

    def rowconfigure(self, *_args: object, **_kwargs: object) -> None:
        return None

    def columnconfigure(self, *_args: object, **_kwargs: object) -> None:
        return None

    def heading(self, *_args: object, **_kwargs: object) -> None:
        return None

    def column(self, *_args: object, **_kwargs: object) -> None:
        return None

    def bind(self, *_args: object, **_kwargs: object) -> str:
        return "binding"

    def tag_configure(self, *_args: object, **_kwargs: object) -> None:
        return None

    def yview(self, *_args: object) -> None:
        return None

    def xview(self, *_args: object) -> None:
        return None

    def set(self, *_args: object) -> None:
        return None

    def add(self, *_args: object, **_kwargs: object) -> None:
        return None

    def exists(self, item: str) -> bool:
        return item in self.items

    def insert(
        self,
        _parent: object,
        index_or_text: object,
        *,
        iid: str | None = None,
        **kwargs: object,
    ) -> str | None:
        if iid is None:
            self.content += str(index_or_text)
            return None
        self.items[iid] = dict(kwargs)
        return iid

    def item(self, item: str, **kwargs: object) -> None:
        self.items.setdefault(item, {}).update(kwargs)

    def delete(self, first: str, *_args: object) -> None:
        _ = self.items.pop(first, None)
        if first == "1.0":
            self.content = ""

    def selection(self) -> tuple[str, ...]:
        return self.selected

    def index(self, _index: str) -> str:
        return f"{max(1, self.content.count(chr(10)) + 1)}.0"

    def see(self, _index: object) -> None:
        return None

    def get(self, *_args: object) -> str:
        return self.content.rstrip("\n")

    def title(self, value: str) -> None:
        self.config["title"] = value

    def geometry(self, value: str) -> None:
        self.config["geometry"] = value

    def minsize(self, width: int, height: int) -> None:
        self.config["minsize"] = (width, height)

    def iconphoto(self, default: bool, image: object) -> None:
        self.config["iconphoto"] = (default, image)

    def protocol(self, name: str, callback: object) -> None:
        self.protocol_calls.append((name, callback))

    def after(self, milliseconds: int, callback: object) -> str:
        self.after_calls.append((milliseconds, callback))
        return f"after-{len(self.after_calls)}"

    def option_add(self, pattern: str, value: str) -> None:
        self.config[pattern] = value

    def transient(self, _parent: object) -> None:
        return None

    def grab_set(self) -> None:
        return None

    def resizable(self, **_kwargs: object) -> None:
        return None

    def wait_window(self, _window: object) -> None:
        return None

    def update_idletasks(self) -> None:
        return None

    def winfo_rootx(self) -> int:
        return 10

    def winfo_rooty(self) -> int:
        return 20

    def winfo_width(self) -> int:
        return 1000

    def winfo_height(self) -> int:
        return 700

    def winfo_reqwidth(self) -> int:
        return 400

    def winfo_reqheight(self) -> int:
        return 300

    def clipboard_clear(self) -> None:
        self.clipboard = ""

    def clipboard_append(self, content: str) -> None:
        self.clipboard += content

    def destroy(self) -> None:
        self.destroyed = True

    def mainloop(self) -> None:
        return None


class FakeController:
    def __init__(self) -> None:
        self.running = False
        self.events: list[MonitorEvent] = []
        self.calls: list[str] = []

    def probe(self) -> tuple[AdapterStatus, ...]:
        return (
            AdapterStatus(ProtocolName.BLUETOOTH_LE, "Bluetooth", AdapterState.READY, "Ready"),
            AdapterStatus(ProtocolName.ANT_PLUS, "ANT", AdapterState.IN_USE, "Busy"),
        )

    def start(self) -> None:
        self.running = True
        self.calls.append("start")

    def stop(self) -> None:
        self.running = False
        self.calls.append("stop")

    def refresh(self) -> None:
        self.running = True
        self.calls.append("refresh")

    def discover(self) -> None:
        self.running = True
        self.calls.append("discover")

    def drain_events(self) -> tuple[MonitorEvent, ...]:
        events = tuple(self.events)
        self.events.clear()
        return events

    def update_settings(self, _settings: AppSettings) -> None:
        self.calls.append("settings")


def _fake_apply_theme(_root: tk.Tk, _theme: ThemeMode) -> Palette:
    return DARK_PALETTE


def _fake_create_icon(_root: tk.Misc, _palette: Palette) -> tk.PhotoImage:
    return cast("tk.PhotoImage", object())


def _fake_logger() -> logging.Logger:
    return logging.getLogger("radio-battery-monitor-test")


def _message_result(*_args: object, **_kwargs: object) -> bool:
    return True


def _patch_tk(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "Button",
        "Checkbutton",
        "Combobox",
        "Entry",
        "Frame",
        "Label",
        "Labelframe",
        "Notebook",
        "Scrollbar",
        "Separator",
        "Spinbox",
        "Treeview",
    ):
        monkeypatch.setattr(ttk, name, FakeWidget)
    for name in ("Text", "Toplevel"):
        monkeypatch.setattr(tk, name, FakeWidget)
    for name in ("BooleanVar", "IntVar", "StringVar"):
        monkeypatch.setattr(tk, name, FakeVariable)
    monkeypatch.setattr(ui, "apply_theme", _fake_apply_theme)
    monkeypatch.setattr(ui, "create_app_icon", _fake_create_icon)
    monkeypatch.setattr(ui, "configure_logging", _fake_logger)
    monkeypatch.setattr(messagebox, "askyesno", _message_result)
    monkeypatch.setattr(messagebox, "showerror", _message_result)
    monkeypatch.setattr(messagebox, "showinfo", _message_result)
    monkeypatch.setattr(messagebox, "showwarning", _message_result)


def test_application_state_transitions_run_headlessly(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dashboard builds and processes radio, device, and lifecycle events without a display."""
    _patch_tk(monkeypatch)
    root = FakeWidget()
    app = ui.RadioBatteryApp(cast("tk.Tk", root))
    controller = FakeController()
    app._controller = cast("MonitorController", controller)

    app._probe_adapters()
    app._start()
    app._refresh()
    app._discover()
    identity = DeviceIdentity(ProtocolName.BLUETOOTH_LE, "AA:BB")
    update = DeviceUpdate(
        "discovered:aa",
        "Nearby sensor",
        ProtocolName.BLUETOOTH_LE,
        DeviceState.DISCOVERED,
        "Nearby",
        identity=identity,
        reading=BatteryReading(source="ble", percent=87),
    )
    controller.events.extend(
        (
            MonitorEvent(MonitorEventKind.DEVICE, device=update),
            MonitorEvent(MonitorEventKind.DIAGNOSTIC, message="diagnostic"),
        )
    )
    app._drain_events()
    app._refresh_ages()
    tree = cast("FakeWidget", app._tree)
    tree.selected = (app._tree_keys[update.logical_id],)
    app._on_selection(cast("tk.Event[tk.Misc]", object()))
    app._on_device_double_click(cast("tk.Event[tk.Misc]", object()))
    app._append_diagnostic("manual")
    app._copy_diagnostics()
    app._clear_diagnostics()
    app._show_error("Example", RuntimeError("failure"))
    app._remove_row(update.logical_id)
    app._on_close()

    assert root.config["title"] == "Radio Battery Monitor"
    assert controller.calls == ["start", "refresh", "discover", "stop"]
    assert root.destroyed


def test_dialogs_build_and_save_with_fake_widgets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mapping and settings dialogs validate and publish snapshots headlessly."""
    _patch_tk(monkeypatch)
    parent = FakeWidget()
    identity = DeviceIdentity(ProtocolName.ANT_PLUS, "42", device_type=11)
    device = LogicalDevice("power", "Power meter", "sensor", (identity,))
    update = DeviceUpdate("found", "Found", ProtocolName.ANT_PLUS, DeviceState.DISCOVERED, "Nearby", identity)

    mapping = ui.DeviceMappingDialog(cast("tk.Tk", parent), update, (device,))
    mapping._choice.set(mapping._choices[1])
    mapping._on_choice(cast("tk.Event[tk.Misc]", object()))
    mapping._save()
    assert mapping.result == ("power", "Power meter", "sensor", False)

    dialog = ui.SettingsDialog(cast("tk.Tk", parent), AppSettings())
    dialog._save()
    assert dialog.result == AppSettings()
