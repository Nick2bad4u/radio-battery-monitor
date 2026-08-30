"""Tests for pure dashboard formatting and severity rules."""

from radio_battery_monitor.models import BatteryReading, BatteryStatus, DeviceState
from radio_battery_monitor.settings import AppSettings
from radio_battery_monitor.ui import battery_severity, format_battery


def test_format_battery_does_not_infer_percent_from_voltage() -> None:
    """ANT voltage remains a voltage, not a guessed percentage."""
    reading = BatteryReading(source="ant", voltage=3.5, status=BatteryStatus.GOOD)
    assert format_battery(reading, DeviceState.CURRENT) == "3.500 V · Good"


def test_format_host_powered_device_is_explicit() -> None:
    """PC-powered radios and equipment never look like missing telemetry."""
    assert format_battery(None, DeviceState.NOT_REPORTED, host_powered=True) == "N/A — host powered"


def test_percentage_thresholds_and_ant_status_drive_severity() -> None:
    """Percentage and qualitative protocol states share UI severity tags."""
    settings = AppSettings(low_percent=20, critical_percent=10)
    assert battery_severity(BatteryReading(source="ble", percent=10), settings) == "critical"
    assert battery_severity(BatteryReading(source="ble", percent=20), settings) == "low"
    assert battery_severity(BatteryReading(source="ant", status=BatteryStatus.CRITICAL), settings) == "critical"
