"""Tkinter color palettes, styles, and code-native application artwork."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk

from radio_battery_monitor.settings import ThemeMode


@dataclass(frozen=True, slots=True)
class Palette:
    """Semantic colors used by both ttk styles and classic Tk widgets."""

    background: str
    surface: str
    surface_alt: str
    border: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    success: str
    warning: str
    critical: str
    selection: str


DARK_PALETTE = Palette(
    background="#0B0F14",
    surface="#121821",
    surface_alt="#18212C",
    border="#263241",
    text="#E6EDF3",
    muted="#8B9AAF",
    accent="#2F81F7",
    accent_hover="#58A6FF",
    success="#3FB950",
    warning="#D29922",
    critical="#F85149",
    selection="#1F6FEB",
)

LIGHT_PALETTE = Palette(
    background="#F4F7FA",
    surface="#FFFFFF",
    surface_alt="#EAF0F6",
    border="#C9D4E0",
    text="#17212B",
    muted="#526273",
    accent="#0969DA",
    accent_hover="#0550AE",
    success="#1A7F37",
    warning="#9A6700",
    critical="#CF222E",
    selection="#0969DA",
)


def palette_for(theme: ThemeMode) -> Palette:
    """Return the semantic palette for a persisted theme mode."""
    return DARK_PALETTE if theme is ThemeMode.DARK else LIGHT_PALETTE


def apply_theme(root: tk.Tk, theme: ThemeMode) -> Palette:
    """Apply an accessible application-wide ttk theme.

    Returns:
        The semantic palette applied to the application.
    """
    palette = palette_for(theme)
    _ = root.configure(background=palette.background)
    style = ttk.Style(root)
    _ = style.theme_use("clam")
    root.option_add(  # pyright: ignore[reportUnknownMemberType] -- tkinter stubs use unknown option types.
        "*TCombobox*Listbox*Background", palette.surface
    )
    root.option_add(  # pyright: ignore[reportUnknownMemberType] -- tkinter stubs use unknown option types.
        "*TCombobox*Listbox*Foreground", palette.text
    )
    root.option_add(  # pyright: ignore[reportUnknownMemberType] -- tkinter stubs use unknown option types.
        "*TCombobox*Listbox*selectBackground", palette.selection
    )
    root.option_add(  # pyright: ignore[reportUnknownMemberType] -- tkinter stubs use unknown option types.
        "*TCombobox*Listbox*selectForeground", "#FFFFFF"
    )

    style.configure(".", background=palette.background, foreground=palette.text, font=("Segoe UI", 10))
    style.configure("TFrame", background=palette.background)
    style.configure("Surface.TFrame", background=palette.surface)
    style.configure(
        "Card.TFrame",
        background=palette.surface,
        bordercolor=palette.border,
        borderwidth=1,
        relief="solid",
    )
    style.configure("TLabel", background=palette.background, foreground=palette.text)
    style.configure("Surface.TLabel", background=palette.surface, foreground=palette.text)
    style.configure("Title.TLabel", font=("Segoe UI Semibold", 20), foreground=palette.text)
    style.configure("Section.TLabel", font=("Segoe UI Semibold", 12), foreground=palette.text)
    style.configure("CardTitle.TLabel", background=palette.surface, font=("Segoe UI Semibold", 11))
    style.configure("Muted.TLabel", foreground=palette.muted)
    style.configure("SurfaceMuted.TLabel", background=palette.surface, foreground=palette.muted)
    style.configure("Success.TLabel", background=palette.surface, foreground=palette.success)
    style.configure("Warning.TLabel", background=palette.surface, foreground=palette.warning)
    style.configure("Critical.TLabel", background=palette.surface, foreground=palette.critical)

    style.configure("TButton", padding=(12, 7), borderwidth=1, relief="flat")
    _ = style.map(
        "TButton",
        background=[("active", palette.surface_alt)],
        foreground=[("disabled", palette.muted)],
    )
    style.configure("Accent.TButton", background=palette.accent, foreground="#FFFFFF")
    _ = style.map("Accent.TButton", background=[("active", palette.accent_hover), ("disabled", palette.border)])
    style.configure("Danger.TButton", foreground=palette.critical)
    style.configure("Icon.TButton", padding=(9, 7))

    style.configure(
        "Treeview",
        background=palette.surface,
        fieldbackground=palette.surface,
        foreground=palette.text,
        bordercolor=palette.border,
        borderwidth=1,
        rowheight=31,
    )
    style.configure(
        "Treeview.Heading",
        background=palette.surface_alt,
        foreground=palette.text,
        font=("Segoe UI Semibold", 10),
        padding=(8, 8),
        relief="flat",
    )
    _ = style.map(
        "Treeview",
        background=[("selected", palette.selection)],
        foreground=[("selected", "#FFFFFF")],
    )
    _ = style.map("Treeview.Heading", background=[("active", palette.border)])

    style.configure("TNotebook", background=palette.background, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(16, 8), background=palette.background, foreground=palette.muted)
    _ = style.map(
        "TNotebook.Tab",
        background=[("selected", palette.surface)],
        foreground=[("selected", palette.text), ("active", palette.text)],
    )
    style.configure("TLabelframe", background=palette.surface, bordercolor=palette.border)
    style.configure("TLabelframe.Label", background=palette.surface, foreground=palette.text)
    style.configure("TEntry", fieldbackground=palette.surface_alt, foreground=palette.text, bordercolor=palette.border)
    style.configure("TSpinbox", fieldbackground=palette.surface_alt, foreground=palette.text)
    style.configure("TCombobox", fieldbackground=palette.surface_alt, foreground=palette.text)
    _ = style.map("TCombobox", fieldbackground=[("readonly", palette.surface_alt)])
    style.configure("TCheckbutton", background=palette.background, foreground=palette.text)
    _ = style.map("TCheckbutton", background=[("active", palette.background)])
    style.configure("TSeparator", background=palette.border)
    style.configure("TScrollbar", background=palette.surface_alt, troughcolor=palette.background, borderwidth=0)
    return palette


def create_app_icon(root: tk.Misc, palette: Palette) -> tk.PhotoImage:
    """Create a small battery/radio icon without shipping an opaque binary asset.

    Returns:
        The generated Tk image.
    """
    icon = tk.PhotoImage(master=root, width=32, height=32)
    icon.put(palette.background, to=(0, 0, 32, 32))
    icon.put(palette.accent, to=(4, 7, 26, 25))
    icon.put(palette.surface, to=(7, 10, 23, 22))
    icon.put(palette.accent, to=(26, 12, 29, 20))
    icon.put(palette.success, to=(9, 12, 19, 20))
    icon.put(palette.text, to=(20, 14, 22, 18))
    return icon
