"""Persistent user settings and logical-device mappings."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Final, cast

from radio_battery_monitor.models import MAX_BATTERY_PERCENT, DeviceIdentity, LogicalDevice, ProtocolName

SETTINGS_SCHEMA_VERSION: Final = 2
LEGACY_SETTINGS_SCHEMA_VERSION: Final = 1
DEFAULT_REFRESH_SECONDS: Final = 300
MINIMUM_REFRESH_SECONDS: Final = 30
DEFAULT_LOW_PERCENT: Final = 20
DEFAULT_CRITICAL_PERCENT: Final = 10


class ThemeMode(StrEnum):
    """Supported application color schemes."""

    DARK = "dark"
    LIGHT = "light"


class SettingsError(ValueError):
    """Raised when a settings document is malformed."""


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Validated application settings."""

    devices: tuple[LogicalDevice, ...] = field(default_factory=tuple)
    refresh_seconds: int = DEFAULT_REFRESH_SECONDS
    low_percent: int = DEFAULT_LOW_PERCENT
    critical_percent: int = DEFAULT_CRITICAL_PERCENT
    theme: ThemeMode = ThemeMode.DARK
    start_monitoring_on_launch: bool = False
    show_unconfigured_devices: bool = True

    def __post_init__(self) -> None:
        """Validate thresholds and stable logical identifiers."""
        if self.refresh_seconds < MINIMUM_REFRESH_SECONDS:
            msg = "Refresh interval must be at least 30 seconds."
            raise SettingsError(msg)
        if not 0 <= self.critical_percent <= self.low_percent <= MAX_BATTERY_PERCENT:
            msg = "Battery thresholds must satisfy 0 <= critical <= low <= 100."
            raise SettingsError(msg)
        logical_ids = [device.logical_id for device in self.devices]
        if len(logical_ids) != len(set(logical_ids)):
            msg = "Logical device identifiers must be unique."
            raise SettingsError(msg)
        if any(not device.identities for device in self.devices):
            msg = "Configured devices must contain at least one radio identity."
            raise SettingsError(msg)
        identity_keys = [identity.key for device in self.devices for identity in device.identities]
        if len(identity_keys) != len(set(identity_keys)):
            msg = "A radio identity cannot be assigned to more than one configured device."
            raise SettingsError(msg)


def default_settings_path() -> Path:
    """Return the per-user settings location on Windows."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "RadioBatteryMonitor" / "settings.json"


class SettingsStore:
    """Load and atomically save settings as versioned JSON."""

    def __init__(self, path: Path | None = None) -> None:
        """Use the supplied path or the standard per-user location."""
        self.path = path or default_settings_path()

    def load(self) -> AppSettings:
        """Load settings, returning defaults when no document exists."""
        if not self.path.exists():
            return AppSettings()
        try:
            raw = cast("object", json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as error:
            msg = f"Unable to read settings: {error}"
            raise SettingsError(msg) from error
        return _decode_settings(raw)

    def save(self, settings: AppSettings) -> None:
        """Write settings through a sibling temporary file and atomic replace."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".tmp")
        payload = json.dumps(_encode_settings(settings), indent=2, sort_keys=True)
        try:
            _ = temporary_path.write_text(f"{payload}\n", encoding="utf-8")
            _ = temporary_path.replace(self.path)
        except OSError:
            temporary_path.unlink(missing_ok=True)
            raise


def add_or_link_identity(
    settings: AppSettings,
    *,
    identity: DeviceIdentity,
    alias: str,
    kind: str,
    logical_id: str,
) -> AppSettings:
    """Add an identity to an existing logical device or create a new one."""
    normalized_alias = alias.strip()
    normalized_kind = kind.strip()
    if not normalized_alias or not normalized_kind:
        msg = "Device alias and kind cannot be empty."
        raise SettingsError(msg)

    devices: list[LogicalDevice] = []
    matched = False
    for device in settings.devices:
        identities = tuple(item for item in device.identities if item.key != identity.key)
        if device.logical_id == logical_id:
            matched = True
            devices.append(
                LogicalDevice(
                    logical_id=device.logical_id,
                    alias=normalized_alias,
                    kind=normalized_kind,
                    identities=(*identities, identity),
                    host_powered=device.host_powered,
                ),
            )
        elif identities:
            devices.append(
                LogicalDevice(
                    logical_id=device.logical_id,
                    alias=device.alias,
                    kind=device.kind,
                    identities=identities,
                    host_powered=device.host_powered,
                ),
            )
    if not matched:
        devices.append(
            LogicalDevice(
                logical_id=logical_id,
                alias=normalized_alias,
                kind=normalized_kind,
                identities=(identity,),
            ),
        )
    return replace(settings, devices=tuple(devices))


def remove_identity(settings: AppSettings, identity: DeviceIdentity) -> AppSettings:
    """Remove one protocol identity and any logical device left empty."""
    devices: list[LogicalDevice] = []
    for device in settings.devices:
        identities = tuple(item for item in device.identities if item.key != identity.key)
        if identities:
            devices.append(
                LogicalDevice(
                    logical_id=device.logical_id,
                    alias=device.alias,
                    kind=device.kind,
                    identities=identities,
                    host_powered=device.host_powered,
                ),
            )
    return replace(settings, devices=tuple(devices))


def _encode_settings(settings: AppSettings) -> dict[str, object]:
    return {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "refresh_seconds": settings.refresh_seconds,
        "low_percent": settings.low_percent,
        "critical_percent": settings.critical_percent,
        "theme": settings.theme.value,
        "start_monitoring_on_launch": settings.start_monitoring_on_launch,
        "show_unconfigured_devices": settings.show_unconfigured_devices,
        "devices": [
            {
                "logical_id": device.logical_id,
                "alias": device.alias,
                "kind": device.kind,
                "host_powered": device.host_powered,
                "identities": [
                    {
                        "protocol": identity.protocol.value,
                        "identifier": identity.identifier,
                        "device_type": identity.device_type,
                        "transmission_type": identity.transmission_type,
                    }
                    for identity in device.identities
                ],
            }
            for device in settings.devices
        ],
    }


def _decode_settings(raw: object) -> AppSettings:
    root = _object_map(raw, "settings")
    schema_version = _integer(root.get("schema_version"), "schema_version")
    if schema_version not in {LEGACY_SETTINGS_SCHEMA_VERSION, SETTINGS_SCHEMA_VERSION}:
        msg = f"Unsupported settings schema version: {schema_version}."
        raise SettingsError(msg)
    raw_devices = _object_list(root.get("devices"), "devices")
    devices = _deduplicate_devices(tuple(_decode_device(item) for item in raw_devices))
    return AppSettings(
        devices=devices,
        refresh_seconds=_integer(root.get("refresh_seconds"), "refresh_seconds"),
        low_percent=_integer(root.get("low_percent"), "low_percent"),
        critical_percent=_integer(root.get("critical_percent"), "critical_percent"),
        theme=_decode_theme(root.get("theme", ThemeMode.DARK.value)),
        start_monitoring_on_launch=_boolean(
            root.get("start_monitoring_on_launch", False), "start_monitoring_on_launch"
        ),
        show_unconfigured_devices=_boolean(root.get("show_unconfigured_devices", True), "show_unconfigured_devices"),
    )


def _deduplicate_devices(devices: tuple[LogicalDevice, ...]) -> tuple[LogicalDevice, ...]:
    """Migrate legacy duplicate identities, preserving the user's latest mapping."""
    seen: set[str] = set()
    normalized_reversed: list[LogicalDevice] = []
    for device in reversed(devices):
        identities = tuple(identity for identity in device.identities if identity.key not in seen)
        seen.update(identity.key for identity in identities)
        if identities:
            normalized_reversed.append(replace(device, identities=identities))
    return tuple(reversed(normalized_reversed))


def _decode_theme(value: object) -> ThemeMode:
    theme_text = _text(value, "theme")
    try:
        return ThemeMode(theme_text)
    except ValueError as error:
        msg = f"Unsupported theme: {theme_text}."
        raise SettingsError(msg) from error


def _decode_device(raw: object) -> LogicalDevice:
    item = _object_map(raw, "device")
    identities = _object_list(item.get("identities"), "identities")
    return LogicalDevice(
        logical_id=_text(item.get("logical_id"), "logical_id"),
        alias=_text(item.get("alias"), "alias"),
        kind=_text(item.get("kind"), "kind"),
        host_powered=_boolean(item.get("host_powered"), "host_powered"),
        identities=tuple(_decode_identity(identity) for identity in identities),
    )


def _decode_identity(raw: object) -> DeviceIdentity:
    item = _object_map(raw, "identity")
    protocol_text = _text(item.get("protocol"), "protocol")
    try:
        protocol = ProtocolName(protocol_text)
    except ValueError as error:
        msg = f"Unsupported protocol: {protocol_text}."
        raise SettingsError(msg) from error
    return DeviceIdentity(
        protocol=protocol,
        identifier=_text(item.get("identifier"), "identifier"),
        device_type=_optional_integer(item.get("device_type"), "device_type"),
        transmission_type=_optional_integer(item.get("transmission_type"), "transmission_type"),
    )


def _object_map(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"{name} must be an object."
        raise SettingsError(msg)
    raw_map = cast("dict[object, object]", value)
    if not all(isinstance(key, str) for key in raw_map):
        msg = f"{name} contains a non-string key."
        raise SettingsError(msg)
    return {cast("str", key): item for key, item in raw_map.items()}


def _object_list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        msg = f"{name} must be an array."
        raise SettingsError(msg)
    return cast("list[object]", value)


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        msg = f"{name} must be a non-empty string."
        raise SettingsError(msg)
    return value


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        msg = f"{name} must be an integer."
        raise SettingsError(msg)
    return value


def _optional_integer(value: object, name: str) -> int | None:
    if value is None:
        return None
    return _integer(value, name)


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        msg = f"{name} must be a boolean."
        raise SettingsError(msg)
    return value
