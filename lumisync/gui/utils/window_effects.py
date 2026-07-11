"""Window effects (dark titlebar / translucency / blur) utilities.

Design goals:
- Safe: never crash if a platform API isn't available.
- Optional: callers can try enabling an effect and ignore failures.

Current implementation:
- Cross-platform: Qt translucent background helper.
- Windows: dark titlebar via DwmSetWindowAttribute, applied app-wide
  through an event filter so dialogs get it too.
- Windows: best-effort DWM blur-behind (legacy) using ctypes.
- Windows 11+: best-effort DWM backdrop types (Mica / Tabbed).

Notes
-----
Windows backdrop types require Windows 11 (or some late Windows 10 builds).
We always fall back gracefully.
"""

from __future__ import annotations

import platform

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QWidget

_IS_WINDOWS = platform.system() == "Windows"

if _IS_WINDOWS:
    from ctypes import Structure, byref, c_int, c_void_p, sizeof, windll
    from ctypes.wintypes import BOOL, DWORD, HWND

    class _DWM_BLURBEHIND(Structure):
        _fields_ = [
            ("dwFlags", DWORD),
            ("fEnable", BOOL),
            ("hRgnBlur", c_void_p),
            ("fTransitionOnMaximized", BOOL),
        ]


def apply_qt_translucent_background(window: QWidget) -> None:
    """Enable Qt translucent background on a top-level window."""

    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    # Flicker reduction (best effort)
    window.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)


# ---- Windows dark titlebar ----

# DwmSetWindowAttribute: https://learn.microsoft.com/windows/win32/api/dwmapi/nf-dwmapi-dwmsetwindowattribute
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19  # pre-20H1 Windows 10 builds


def enable_dark_titlebar(hwnd: int) -> bool:
    """Ask DWM to draw this window's titlebar in dark mode."""

    if not _IS_WINDOWS:
        return False

    try:
        dwmapi = windll.dwmapi
    except Exception:
        return False

    value = c_int(1)
    for attribute in (_DWMWA_USE_IMMERSIVE_DARK_MODE, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
        try:
            hr = dwmapi.DwmSetWindowAttribute(
                HWND(hwnd),
                DWORD(attribute),
                byref(value),
                DWORD(sizeof(value)),
            )
            if int(hr) == 0:
                return True
        except Exception:
            return False
    return False


class _DarkTitlebarFilter(QObject):
    """Applies the dark titlebar to every top-level window as it shows.

    Catches dialogs (QMessageBox, QInputDialog, QColorDialog) as well as the
    main window, so no native white titlebar ever flashes into the dark UI.
    """

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.Show
            and isinstance(obj, QWidget)
            and obj.isWindow()
        ):
            try:
                enable_dark_titlebar(int(obj.winId()))
            except Exception:
                pass
        return super().eventFilter(obj, event)


def install_dark_titlebar_filter(app: QApplication) -> None:
    """Install an app-wide filter that dark-titlebars all top-level windows."""

    if not _IS_WINDOWS:
        return
    filter_ = _DarkTitlebarFilter(app)
    app.installEventFilter(filter_)


# ---- Windows DWM blur-behind (legacy) ----

_DWM_BB_ENABLE = 0x00000001


def enable_windows_dwm_blur(hwnd: int) -> bool:
    """Try to enable legacy DWM blur behind a window."""

    if not _IS_WINDOWS:
        return False

    try:
        dwmapi = windll.dwmapi
    except Exception:
        return False

    try:
        bb = _DWM_BLURBEHIND()
        bb.dwFlags = _DWM_BB_ENABLE
        bb.fEnable = True
        bb.hRgnBlur = None
        bb.fTransitionOnMaximized = False

        hr = dwmapi.DwmEnableBlurBehindWindow(HWND(hwnd), bb)
        return int(hr) == 0
    except Exception:
        return False


# ---- Windows 11 backdrop types (Mica/Tabbed) ----

_DWMWA_SYSTEMBACKDROP_TYPE = 38


class WindowsBackdropType:
    AUTO = 0
    NONE = 1
    MICA = 2
    ACRYLIC = 3  # May map to transient acrylic on some builds
    TABBED = 4


def enable_windows_backdrop(hwnd: int, backdrop: int = WindowsBackdropType.MICA) -> bool:
    """Try to enable a Windows system backdrop type (Mica/Tabbed).

    Returns True if the call succeeded.
    """

    if not _IS_WINDOWS:
        return False

    try:
        dwmapi = windll.dwmapi
    except Exception:
        return False

    try:
        value = c_int(int(backdrop))
        hr = dwmapi.DwmSetWindowAttribute(
            HWND(hwnd),
            DWORD(_DWMWA_SYSTEMBACKDROP_TYPE),
            byref(value),
            DWORD(sizeof(value)),
        )
        return int(hr) == 0
    except Exception:
        return False


__all__ = [
    "apply_qt_translucent_background",
    "enable_dark_titlebar",
    "install_dark_titlebar_filter",
    "enable_windows_dwm_blur",
    "enable_windows_backdrop",
    "WindowsBackdropType",
]
