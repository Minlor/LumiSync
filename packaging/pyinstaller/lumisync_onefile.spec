# -*- mode: python ; coding: utf-8 -*-

import sys
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

# Pillow's PyInstaller hook drags in tkinter (and the tcl/tk runtime) via
# PIL.ImageTk even though the GUI is pure Qt, and its AVIF codec is an 8 MB
# binary — the app only builds PIL images from in-memory capture arrays.
excludes = [
    "tkinter",
    "_tkinter",
    "PIL.ImageTk",
    "PIL.AvifImagePlugin",
    "PIL._avif",
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
    excludes=excludes,
    noarchive=False,
    # Strip docstrings from the frozen bytecode (equivalent to python -OO).
    optimize=2,
)


# Bundled files the app never loads at runtime. Windows-only names are kept
# separate: the same trims are unverified against Linux's Qt platform plugins,
# so the AppImage stays conservative.
_BLOAT_MARKERS = (
    "_avif.",  # PIL AVIF codec (belt and braces with the module exclude)
)
_WINDOWS_BLOAT_MARKERS = (
    # Software-OpenGL fallback; the widgets UI renders with Qt's raster
    # engine and never requests OpenGL.
    "opengl32sw.dll",
    # cv2's video-file codecs; cv2 only processes in-memory screen captures.
    "opencv_videoio_ffmpeg",
    # Qt Quick/QML stack, pulled in only by the virtual-keyboard input
    # plugin — a desktop widgets app uses neither.
    "qt6qml",
    "qt6quick",
    "qt6opengl.dll",
    "qt6virtualkeyboard",
    "qtvirtualkeyboardplugin",
    # Qt6Pdf exists only to back the PDF imageformat plugin.
    "qt6pdf.dll",
    "qpdf.dll",
    # Alternative platform plugin; Qt uses qwindows unless direct2d is
    # explicitly requested.
    "qdirect2d.dll",
)
if sys.platform == "win32":
    _BLOAT_MARKERS += _WINDOWS_BLOAT_MARKERS


def _is_bundle_bloat(entry):
    dest = entry[0].replace("\\", "/").lower()
    if "/translations/" in dest and dest.startswith("pyside6/"):
        return True  # Qt translations: the UI is English-only
    basename = dest.rsplit("/", 1)[-1]
    return any(marker in basename for marker in _BLOAT_MARKERS)


a.binaries = [entry for entry in a.binaries if not _is_bundle_bloat(entry)]
a.datas = [entry for entry in a.datas if not _is_bundle_bloat(entry)]

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
    icon=str(ROOT / "lumisync" / "gui" / "resources" / "icons" / "app.ico"),
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
