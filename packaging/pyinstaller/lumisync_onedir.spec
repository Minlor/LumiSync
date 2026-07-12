# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_all,
    collect_dynamic_libs,
    collect_submodules,
)


ROOT = Path(SPECPATH).resolve().parents[1]


def safe_collect_submodules(package_name):
    try:
        return collect_submodules(package_name)
    except Exception:
        return []


def safe_collect_dynamic_libs(package_name):
    try:
        return collect_dynamic_libs(package_name)
    except Exception:
        return []


def safe_collect_all(package_name):
    """Return (datas, binaries, hiddenimports) for a package, or empty lists."""
    try:
        return collect_all(package_name)
    except Exception:
        return [], [], []


datas = [
    (
        str(ROOT / "lumisync" / "gui" / "resources" / "icons"),
        "lumisync/gui/resources/icons",
    ),
    (str(ROOT / "lumisync" / "gui" / "theme" / "dark.qss"), "lumisync/gui/theme"),
    (str(ROOT / "lumisync" / "config" / "presets.toml"), "lumisync/config"),
    (str(ROOT / "LICENSE"), "."),
]

binaries = []
binaries += safe_collect_dynamic_libs("cv2")
binaries += safe_collect_dynamic_libs("dxcam")
binaries += safe_collect_dynamic_libs("soundcard")

hiddenimports = []
hiddenimports += safe_collect_submodules("cv2")
hiddenimports += safe_collect_submodules("lumisync")
hiddenimports += safe_collect_submodules("dxcam")
hiddenimports += [
    "cv2",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtNetwork",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtWidgets",
    "pythoncom",
    "pywintypes",
    "soundcard",
    "win32timezone",
]

# Device transports are bundled so the frozen app controls every supported
# device family. bleak's Windows backend lives in the separate winrt.* packages,
# and tinytuya pulls its own crypto deps — collect_all grabs their submodules,
# data files, and dynamic libs so nothing is missing at runtime.
for _pkg in ("bleak", "winrt", "tinytuya"):
    _d, _b, _h = safe_collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

a = Analysis(
    [str(ROOT / "lumisync" / "lumisync.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LumiSync",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=str(ROOT / "lumisync" / "gui" / "resources" / "icons" / "app.ico"),
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="LumiSync",
)
