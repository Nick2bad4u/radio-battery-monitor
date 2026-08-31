"""Tests for settings validation and persistence."""

from pathlib import Path

import pytest

from radio_battery_monitor.models import DeviceIdentity, LogicalDevice, ProtocolName
from radio_battery_monitor.settings import (
    AppSettings,
    SettingsError,
    SettingsStore,
    ThemeMode,
    add_or_link_identity,
    remove_identity,
)


def test_settings_round_trip(tmp_path: Path) -> None:
    """All logical identities survive JSON persistence."""
    path = tmp_path / "settings.json"
    identity = DeviceIdentity(protocol=ProtocolName.ANT_PLUS, identifier="38933", device_type=11)
    settings = AppSettings(
        devices=(LogicalDevice(logical_id="pedal", alias="Assioma", kind="power meter", identities=(identity,)),),
        refresh_seconds=120,
        theme=ThemeMode.LIGHT,
        start_monitoring_on_launch=True,
        show_unconfigured_devices=False,
    )
    store = SettingsStore(path)
    store.save(settings)
    assert store.load() == settings


def test_missing_settings_use_defaults(tmp_path: Path) -> None:
    """First launch does not require a settings document."""
    assert SettingsStore(tmp_path / "missing.json").load() == AppSettings()


def test_corrupt_settings_are_rejected(tmp_path: Path) -> None:
    """A malformed document is never silently replaced."""
    path = tmp_path / "settings.json"
    _ = path.write_text("not-json", encoding="utf-8")
    with pytest.raises(SettingsError, match="Unable to read settings"):
        _ = SettingsStore(path).load()


def test_link_identity_moves_it_between_logical_devices() -> None:
    """One radio identity cannot remain assigned to two logical devices."""
    identity = DeviceIdentity(protocol=ProtocolName.BLUETOOTH_LE, identifier="AA:BB")
    original = AppSettings(
        devices=(LogicalDevice(logical_id="old", alias="Old", kind="sensor", identities=(identity,)),),
    )
    updated = add_or_link_identity(
        original,
        identity=identity,
        alias="New",
        kind="pedal",
        logical_id="new",
    )
    assert len(updated.devices) == 1
    assert updated.devices[0].identities == (identity,)


def test_threshold_order_is_validated() -> None:
    """Critical threshold cannot exceed the low threshold."""
    with pytest.raises(SettingsError, match="critical"):
        _ = AppSettings(low_percent=10, critical_percent=20)


def test_remove_last_identity_removes_logical_device() -> None:
    """Settings do not retain unusable empty logical devices."""
    identity = DeviceIdentity(protocol=ProtocolName.BLUETOOTH_LE, identifier="AA:BB")
    settings = AppSettings(devices=(LogicalDevice("sensor", "Sensor", "sensor", (identity,)),))
    assert remove_identity(settings, identity).devices == ()


def test_version_one_settings_migrate_to_dark_defaults(tmp_path: Path) -> None:
    """Existing mappings remain readable when appearance settings are introduced."""
    path = tmp_path / "settings.json"
    _ = path.write_text(
        '{"schema_version":1,"refresh_seconds":300,"low_percent":20,"critical_percent":10,"devices":[]}',
        encoding="utf-8",
    )
    settings = SettingsStore(path).load()
    assert settings.theme is ThemeMode.DARK
    assert not settings.start_monitoring_on_launch
    assert settings.show_unconfigured_devices


def test_legacy_ant_transmission_variants_collapse_to_latest_mapping(tmp_path: Path) -> None:
    """Transmission metadata cannot create duplicate rows for one physical ANT device."""
    path = tmp_path / "settings.json"
    _ = path.write_text(
        """{
          "schema_version": 1,
          "refresh_seconds": 300,
          "low_percent": 20,
          "critical_percent": 10,
          "devices": [
            {"logical_id":"old","alias":"Old","kind":"sensor","host_powered":false,
             "identities":[{"protocol":"ant_plus","identifier":"15281","device_type":11,"transmission_type":1}]},
            {"logical_id":"new","alias":"New","kind":"sensor","host_powered":false,
             "identities":[{"protocol":"ant_plus","identifier":"15281","device_type":11,"transmission_type":5}]}
          ]
        }""",
        encoding="utf-8",
    )
    settings = SettingsStore(path).load()
    assert len(settings.devices) == 1
    assert settings.devices[0].alias == "New"


@pytest.mark.parametrize(
    ("refresh_seconds", "low_percent", "critical_percent", "message"),
    [
        (29, 20, 10, "at least 30"),
        (300, 101, 10, "thresholds"),
    ],
)
def test_invalid_scalar_settings_are_rejected(
    refresh_seconds: int,
    low_percent: int,
    critical_percent: int,
    message: str,
) -> None:
    """Out-of-range persisted controls fail before hardware can use them."""
    with pytest.raises(SettingsError, match=message):
        _ = AppSettings(
            refresh_seconds=refresh_seconds,
            low_percent=low_percent,
            critical_percent=critical_percent,
        )


def test_duplicate_logical_ids_and_identities_are_rejected() -> None:
    """Logical IDs and physical identities remain globally unique."""
    identity = DeviceIdentity(ProtocolName.BLUETOOTH_LE, "AA:BB")
    first = LogicalDevice("same", "First", "sensor", (identity,))
    second_id = LogicalDevice("same", "Second", "sensor", (DeviceIdentity(ProtocolName.ANT_PLUS, "1"),))
    with pytest.raises(SettingsError, match="identifiers must be unique"):
        _ = AppSettings(devices=(first, second_id))
    duplicate_identity = LogicalDevice("other", "Other", "sensor", (identity,))
    with pytest.raises(SettingsError, match="cannot be assigned"):
        _ = AppSettings(devices=(first, duplicate_identity))


def test_empty_device_and_mapping_labels_are_rejected() -> None:
    """Unusable logical devices and blank labels never enter settings."""
    with pytest.raises(SettingsError, match="at least one radio identity"):
        _ = AppSettings(devices=(LogicalDevice("empty", "Empty", "sensor", ()),))
    identity = DeviceIdentity(ProtocolName.BLUETOOTH_LE, "AA:BB")
    with pytest.raises(SettingsError, match="cannot be empty"):
        _ = add_or_link_identity(AppSettings(), identity=identity, alias=" ", kind="sensor", logical_id="new")


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("[]", "settings must be an object"),
        ('{"schema_version":99,"devices":[]}', "Unsupported settings schema"),
        ('{"schema_version":2,"devices":"bad"}', "devices must be an array"),
        (
            (
                '{"schema_version":2,"refresh_seconds":300,"low_percent":20,"critical_percent":10,'
                '"theme":"blue","devices":[]}'
            ),
            "Unsupported theme",
        ),
        (
            (
                '{"schema_version":2,"refresh_seconds":true,"low_percent":20,"critical_percent":10,'
                '"theme":"dark","devices":[]}'
            ),
            "refresh_seconds must be an integer",
        ),
    ],
)
def test_malformed_settings_shapes_are_rejected(tmp_path: Path, document: str, message: str) -> None:
    """Schema-shape errors produce targeted settings failures."""
    path = tmp_path / "settings.json"
    _ = path.write_text(document, encoding="utf-8")
    with pytest.raises(SettingsError, match=message):
        _ = SettingsStore(path).load()
