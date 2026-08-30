"""Tests for transport-independent domain models."""

from datetime import UTC, datetime, timedelta

import pytest

from radio_battery_monitor.models import (
    MAX_BATTERY_PERCENT,
    BatteryReading,
    DeviceIdentity,
    ProtocolName,
    newest_reading,
)


def test_battery_reading_accepts_boundaries() -> None:
    """Zero and one hundred percent are valid readings."""
    assert BatteryReading(source="test", percent=0).percent == 0
    assert BatteryReading(source="test", percent=MAX_BATTERY_PERCENT).percent == MAX_BATTERY_PERCENT


@pytest.mark.parametrize("percent", [-1, 101])
def test_battery_reading_rejects_invalid_percent(percent: int) -> None:
    """Values outside the Bluetooth-defined percentage range are rejected."""
    with pytest.raises(ValueError, match="between 0 and 100"):
        _ = BatteryReading(source="test", percent=percent)


def test_newest_reading_uses_observation_time() -> None:
    """Transport selection is based on freshness, not protocol preference."""
    observed_at = datetime.now(tz=UTC)
    older = BatteryReading(source="ble", percent=80, observed_at=observed_at)
    newer = BatteryReading(source="ant", voltage=3.1, observed_at=observed_at + timedelta(seconds=1))
    assert newest_reading((older, newer)) is newer


def test_newest_reading_handles_empty_collection() -> None:
    """No observations produce no selected reading."""
    assert newest_reading(()) is None


def test_ant_identity_key_ignores_transmission_metadata() -> None:
    """One physical ANT device remains stable across transmission variants."""
    first = DeviceIdentity(ProtocolName.ANT_PLUS, "15281", device_type=11, transmission_type=1)
    second = DeviceIdentity(ProtocolName.ANT_PLUS, "15281", device_type=11, transmission_type=5)
    assert first.key == second.key
    different_profile = DeviceIdentity(ProtocolName.ANT_PLUS, "15281", device_type=17, transmission_type=5)
    assert first.key != different_profile.key
