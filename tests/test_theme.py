"""Tests for semantic UI palette selection."""

from radio_battery_monitor.settings import ThemeMode
from radio_battery_monitor.theme import DARK_PALETTE, LIGHT_PALETTE, palette_for


def test_dark_theme_is_explicit_default_palette() -> None:
    """Dark mode resolves to the designed high-contrast palette."""
    assert palette_for(ThemeMode.DARK) is DARK_PALETTE
    assert DARK_PALETTE.background == "#0B0F14"


def test_light_theme_remains_available() -> None:
    """Users can opt into a light palette from settings."""
    assert palette_for(ThemeMode.LIGHT) is LIGHT_PALETTE
