"""Tests for semantic UI palette selection and application."""

from __future__ import annotations

from tkinter import ttk
from typing import TYPE_CHECKING, cast

from radio_battery_monitor import theme
from radio_battery_monitor.settings import ThemeMode
from radio_battery_monitor.theme import DARK_PALETTE, LIGHT_PALETTE, palette_for

if TYPE_CHECKING:
    import tkinter as tk

    import pytest

STYLE_CALLS: list[str] = []


class FakeRoot:
    """Minimal Tk root surface consumed by theme configuration."""

    def __init__(self) -> None:
        """Initialize operation records."""
        self.configurations: list[dict[str, object]] = []
        self.options: list[tuple[str, str]] = []

    def configure(self, **kwargs: object) -> None:
        """Record root configuration."""
        self.configurations.append(kwargs)

    def option_add(self, pattern: str, value: str) -> None:
        """Record Tk option database entries."""
        self.options.append((pattern, value))


class FakeStyle:
    """Record ttk style operations without requiring a display server."""

    def __init__(self, _root: object) -> None:
        """Accept the fake root used by the application."""

    def theme_use(self, name: str) -> None:
        """Record the selected base theme."""
        STYLE_CALLS.append(f"theme:{name}")

    def configure(self, name: str, **_kwargs: object) -> None:
        """Record a style configuration."""
        STYLE_CALLS.append(f"configure:{name}")

    def map(self, name: str, **_kwargs: object) -> None:
        """Record a state map."""
        STYLE_CALLS.append(f"map:{name}")


def test_dark_theme_is_explicit_default_palette() -> None:
    """Dark mode resolves to the designed high-contrast palette."""
    assert palette_for(ThemeMode.DARK) is DARK_PALETTE
    assert DARK_PALETTE.background == "#0B0F14"


def test_light_theme_remains_available() -> None:
    """Users can opt into a light palette from settings."""
    assert palette_for(ThemeMode.LIGHT) is LIGHT_PALETTE


def test_apply_theme_configures_root_and_ttk_styles(monkeypatch: pytest.MonkeyPatch) -> None:
    """Theme application covers classic Tk options and the full ttk style set."""
    STYLE_CALLS.clear()
    root = FakeRoot()
    monkeypatch.setattr(ttk, "Style", FakeStyle)
    palette = theme.apply_theme(cast("tk.Tk", root), ThemeMode.DARK)
    assert palette is DARK_PALETTE
    assert root.configurations == [{"background": DARK_PALETTE.background}]
    assert len(root.options) == 4
    assert "theme:clam" in STYLE_CALLS
    assert "configure:Treeview" in STYLE_CALLS
    assert "map:TNotebook.Tab" in STYLE_CALLS
