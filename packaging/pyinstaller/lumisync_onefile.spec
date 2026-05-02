# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules


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
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtSvg",
    "PyQt6.QtSvgWidgets",
    "PyQt6.QtWidgets",
    "pythoncom",
    "pywintypes",
    "soundcard",
    "win32timezone",
]

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
    a.binaries,
    a.datas,
    [],
    name="LumiSync-Windows-x64-onefile",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
