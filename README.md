# Radio Battery Monitor

Radio Battery Monitor is a local Windows 11 dashboard for battery telemetry from selected Bluetooth Low Energy (BLE)
and ANT+ fitness devices. It checks the Windows Bluetooth radio and a Dynastream ANT USB Stick 2 (`0FCF:1008`),
discovers nearby devices, lets several radio identities be linked to one physical device, and refreshes reported
battery values every five minutes.

The dashboard uses a high-contrast dark theme by default, with status-colored adapter cards, a device table, and a
separate activity view. A light theme remains available from **Settings**.

The app is deliberately conservative: it displays only values actually reported by a device. The PC-powered
Bluetooth and USB radios show **N/A — host powered**. A device that does not publish the applicable standard battery
page shows **Not reported**; voltage is never converted into a guessed percentage.

## Supported telemetry

| Radio | Data read | Displayed values |
| --- | --- | --- |
| BLE | Battery Service, Battery Level characteristic `0x2A19` | Percentage from 0–100 |
| ANT+ | Common Data Page 82 | Qualitative status, voltage, battery count/ID, and operating time |

ANT+ Page 82 is optional and is not continuously transmitted by every profile or product. A connected device can
therefore remain **Not reported** even when its own vendor app knows the battery state. This is a protocol/device
limitation, not a reason to invent a value.

## Requirements

- Windows 11 and Python 3.12 for development.
- A Windows Bluetooth radio for BLE monitoring.
- For ANT+, a Dynastream ANT USB Stick 2 using its existing libusb-win32-compatible driver.
- Close Garmin Express, Zwift, TrainerRoad, or other software that may own the ANT stick before starting a session.
  USB radios are exclusive-use devices; the app reports a response timeout as **In use** and does not change drivers.

## Run from source

Install [uv](https://docs.astral.sh/uv/), then run from this directory:

```powershell
uv sync --frozen
uv run radio-battery-monitor
```

The initial hardware check only enumerates adapters; it does not claim them. Choose **Discover devices** to begin a
15-second BLE/ANT+ discovery window. Wake battery-powered sensors first. Select a discovered row, choose **Add / link
selected**, give it an alias and device kind, and optionally link it to an existing logical device that has another
radio identity. Mark mains/USB-powered equipment as externally powered so the app displays N/A and skips battery
polling.

**Start monitoring** claims the radios until **Stop** or window close. **Refresh now** triggers an immediate scan and
battery request. The normal refresh interval is five minutes.

Saving, linking, or removing a device while monitoring no longer closes and reopens the radios. The mapping is saved
immediately and begins monitoring after the next deliberate **Stop** / **Start** cycle. This avoids rapid libusb
handle churn on Windows.

## Settings

The **Settings** dialog controls:

- Dark or light appearance; dark is the default.
- Automatic refresh interval from 30 to 3600 seconds.
- Percentage thresholds used for low and critical highlighting.
- Optional monitoring at application launch.
- Whether unconfigured discovery results remain visible.

Changes that affect an active radio session are queued for the next Stop / Start cycle. Appearance and table-filter
changes apply immediately. Existing schema-v1 settings are migrated in memory and saved as schema v2 on the next
settings or device edit.

## Local data

Settings are versioned JSON and written atomically to:

```text
%LOCALAPPDATA%\RadioBatteryMonitor\settings.json
```

Bounded diagnostic logs are stored below `%LOCALAPPDATA%\RadioBatteryMonitor\logs`. One 1 MiB active log and three
rotated backups are retained. Raw BLE addresses and ANT device numbers are intentionally omitted from logs.

To reset the device mappings, close the app and move or delete `settings.json`. Removing that file is recoverable only
if you keep your own copy.

## Quality checks

```powershell
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run pyright
uv run pytest
uv run python -m compileall -q src tests
```

The radio backends are isolated behind typed interfaces, so unit tests use fakes and do not require or claim hardware.

## Build the Windows app

Create the reproducible one-folder distribution with:

```powershell
uv run pyinstaller --noconfirm --clean RadioBatteryMonitor.spec
```

Launch `dist\RadioBatteryMonitor\RadioBatteryMonitor.exe`. A one-folder build is intentional: it starts faster and is
less prone to native BLE/USB dependency extraction issues than a one-file executable. The build is unsigned, so
Windows reputation warnings are possible when it is copied to another PC.

## Troubleshooting

- **ANT stick ready, then in use:** ANT USB access is exclusive. Close every ANT-aware app, including Zwift and
  background Garmin Express processes, then retry. The app performs three bounded open attempts to tolerate Windows
  USB re-enumeration. Unplug and reconnect the stick only if it remains unavailable. Do not install or swap drivers
  merely to clear an ownership issue.
- **BLE device sleeping/out of range:** wake it, keep it close to the PC, and refresh. Some devices stop advertising
  while connected to another app.
- **BLE Battery Service unavailable:** the product does not expose standard characteristic `0x2A19`; vendor-specific
  protocols are outside this app's scope.
- **ANT+ Not reported:** the device did not send Page 82 during the session. Leave it awake through another refresh;
  absence after that is still a valid result.
- **Duplicate rows:** link the BLE and ANT+ observations to the same existing logical device rather than creating a
  second device. One product may legitimately advertise several ANT+ profiles; for example, fitness-equipment and
  bicycle-power rows with the same ANT device number should normally be linked to the same trainer.
