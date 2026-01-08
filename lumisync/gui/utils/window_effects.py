"""Window effects (translucency / blur) utilities.

Design goals:
- Safe: never crash if a platform API isn't available.
- Optional: callers can try enabling an effect and ignore failures.

Current implementation:
- Cross-platform: Qt translucent background helper.
- Windows: best-effort DWM blur-behind (legacy) using ctypes.
- Windows 11+: best-effort DWM backdrop types (Mica / Tabbed).

Notes
-----
Windows backdrop types require Windows 11 (or some late Windows 10 builds).
We always fall back gracefully.
"""

from __future__ import annotations

import platform
from ctypes import Structure, c_int, c_void_p, windll
from ctypes.wintypes import BOOL, DWORD, HWND

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget


def apply_qt_translucent_background(window: QWidget) -> None:
    """Enable Qt translucent background on a top-level window."""

    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    # Flicker reduction (best effort)
    window.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)


# ---- Windows DWM blur-behind (legacy) ----


class _DWM_BLURBEHIND(Structure):
    _fields_ = [
        ("dwFlags", DWORD),
        ("fEnable", BOOL),
        ("hRgnBlur", c_void_p),
        ("fTransitionOnMaximized", BOOL),
    ]


_DWM_BB_ENABLE = 0x00000001


def enable_windows_dwm_blur(hwnd: int) -> bool:
    """Try to enable legacy DWM blur behind a window."""

    if platform.system() != "Windows":
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

# DwmSetWindowAttribute: https://learn.microsoft.com/windows/win32/api/dwmapi/nf-dwmapi-dwmsetwindowattribute
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

    if platform.system() != "Windows":
        return False

    try:
        dwmapi = windll.dwmapi
    except Exception:
        return False

    try:
        from ctypes import byref, sizeof

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
    "enable_windows_dwm_blur",
    "enable_windows_backdrop",
    "WindowsBackdropType",
]
