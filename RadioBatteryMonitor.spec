"""PyInstaller one-folder build for Radio Battery Monitor."""

analysis = Analysis(
    ["src/radio_battery_monitor/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=["usb.backend.libusb0"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="RadioBatteryMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    name="RadioBatteryMonitor",
)
