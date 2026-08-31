"""Headless tests for deterministic dashboard state helpers."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from radio_battery_monitor import ui
from radio_battery_monitor.models import (
    AdapterState,
    BatteryReading,
    BatteryStatus,
    DeviceIdentity,
    DeviceState,
    DeviceUpdate,
    LogicalDevice,
    ProtocolName,
)
from radio_battery_monitor.settings import AppSettings


@pytest.mark.parametrize(
    ("reading", "state", "expected"),
    [
        (None, DeviceState.NOT_REPORTED, "Not reported"),
        (None, DeviceState.CURRENT, "—"),
        (BatteryReading(source="ble", percent=87), DeviceState.CURRENT, "87%"),
        (BatteryReading(source="empty"), DeviceState.CURRENT, "Not reported"),
    ],
)
def test_battery_formatting_branches(
    reading: BatteryReading | None,
    state: DeviceState,
    expected: str,
) -> None:
    """Missing, percentage, and empty observations remain explicit."""
    assert ui.format_battery(reading, state) == expected


@pytest.mark.parametrize(
    ("reading", "expected"),
    [
        (None, "normal"),
        (BatteryReading(source="ant", status=BatteryStatus.INVALID), "warning"),
        (BatteryReading(source="ant", status=BatteryStatus.LOW), "low"),
        (BatteryReading(source="ble", percent=80), "normal"),
    ],
)
def test_battery_severity_remaining_branches(reading: BatteryReading | None, expected: str) -> None:
    """Qualitative and normal battery states receive stable semantic tags."""
    assert (
        ui.battery_severity(
            reading,
            AppSettings(),
        )
        == expected
    )


def test_protocol_labels_cover_single_combined_and_unknown_devices() -> None:
    """Radio labels describe configured identities without inventing profiles."""
    ble_identity = DeviceIdentity(ProtocolName.BLUETOOTH_LE, "AA")
    ant_power = DeviceIdentity(ProtocolName.ANT_PLUS, "42", device_type=11)
    ant_unknown = DeviceIdentity(ProtocolName.ANT_PLUS, "43", device_type=99)
    combined = LogicalDevice("combo", "Combo", "sensor", (ble_identity, ant_power))
    multi_ant = LogicalDevice("ant", "ANT", "sensor", (ant_power, ant_unknown))
    assert ui._protocol_label(None, ProtocolName.BLUETOOTH_LE, None) == "BLE"
    assert ui._protocol_label(None, ProtocolName.ANT_PLUS, ant_power) == "ANT+ Power"
    assert ui._protocol_label(combined, ProtocolName.ANT_PLUS, None) == "BLE + ANT+"
    assert ui._protocol_label(multi_ant, ProtocolName.ANT_PLUS, None) == "ANT+ Multi"
    assert ui._ant_protocol_label(ant_unknown) == "ANT+"
    assert ui._ant_protocol_label(None) == "ANT+"


@pytest.mark.parametrize(
    ("state", "style"),
    [
        (AdapterState.READY, "Success.TLabel"),
        (AdapterState.SCANNING, "Warning.TLabel"),
        (AdapterState.IN_USE, "Warning.TLabel"),
        (AdapterState.ERROR, "Critical.TLabel"),
        (AdapterState.STOPPED, "SurfaceMuted.TLabel"),
    ],
)
def test_adapter_styles(state: AdapterState, style: str) -> None:
    """Adapter states map to stable semantic styles."""
    assert ui._adapter_state_style(state) == style


def test_host_power_and_radio_change_helpers_are_immutable() -> None:
    """UI settings helpers replace only the requested fields."""
    identity = DeviceIdentity(ProtocolName.BLUETOOTH_LE, "AA")
    device = LogicalDevice("sensor", "Sensor", "sensor", (identity,))
    original = AppSettings(devices=(device,))
    updated = ui._set_host_powered(original, "sensor", host_powered=True)
    assert updated.devices[0].host_powered
    assert not original.devices[0].host_powered
    assert ui._radio_settings_changed(original, updated)
    assert ui._radio_settings_changed(original, AppSettings(devices=(device,), refresh_seconds=600))
    assert not ui._radio_settings_changed(original, AppSettings(devices=(device,), theme=original.theme))


@pytest.mark.parametrize(
    ("delta", "suffix"),
    [
        (timedelta(seconds=-2), "0s ago"),
        (timedelta(seconds=5), "5s ago"),
        (timedelta(minutes=5), "5m ago"),
        (timedelta(hours=3), "3h ago"),
    ],
)
def test_age_formatting(delta: timedelta, suffix: str) -> None:
    """Recent, minute, hour, and future timestamps remain readable."""
    assert ui._format_age(datetime.now(tz=UTC) - delta).endswith(suffix)


def test_last_seen_uses_never_for_stopped_device() -> None:
    """An inactive configured row never pretends to have telemetry."""
    update = DeviceUpdate("id", "Sensor", ProtocolName.BLUETOOTH_LE, DeviceState.STOPPED, "configured")
    assert ui._last_seen_text(update) == "Never"
    assert ui._last_seen_text(
        DeviceUpdate("id", "Sensor", ProtocolName.BLUETOOTH_LE, DeviceState.CURRENT, "seen")
    ).endswith("s ago")
