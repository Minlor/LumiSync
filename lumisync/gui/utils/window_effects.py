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
import sys

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QPalette
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

    class _MARGINS(Structure):
        _fields_ = [
            ("cxLeftWidth", c_int),
            ("cxRightWidth", c_int),
            ("cyTopHeight", c_int),
            ("cyBottomHeight", c_int),
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
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_BORDER_COLOR = 34
_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36

_TITLEBAR_CAPTION = QColor("#242430")
_TITLEBAR_TEXT = QColor("#F3F4F8")
_TITLEBAR_BORDER = QColor("#555267")


def _colorref(color: QColor | str) -> int:
    """Convert a Qt color to the Win32 COLORREF byte order."""

    value = QColor(color)
    return value.red() | (value.green() << 8) | (value.blue() << 16)


def set_windows_titlebar_colors(
    hwnd: int,
    *,
    caption: QColor | str = _TITLEBAR_CAPTION,
    text: QColor | str = _TITLEBAR_TEXT,
    border: QColor | str = _TITLEBAR_BORDER,
) -> bool:
    """Make native Windows chrome part of LumiSync's visual foundation."""

    if not _IS_WINDOWS:
        return False
    try:
        dwmapi = windll.dwmapi
        results = []
        for attribute, color in (
            (_DWMWA_CAPTION_COLOR, caption),
            (_DWMWA_TEXT_COLOR, text),
            (_DWMWA_BORDER_COLOR, border),
        ):
            value = DWORD(_colorref(color))
            hr = dwmapi.DwmSetWindowAttribute(
                HWND(hwnd),
                DWORD(attribute),
                byref(value),
                DWORD(sizeof(value)),
            )
            results.append(int(hr) == 0)
        return all(results)
    except Exception:
        return False


def enable_windows_rounded_corners(hwnd: int) -> bool:
    """Request Windows 11's normal rounded top-level window corners."""

    if not _IS_WINDOWS:
        return False
    try:
        value = c_int(2)  # DWMWCP_ROUND
        hr = windll.dwmapi.DwmSetWindowAttribute(
            HWND(hwnd),
            DWORD(_DWMWA_WINDOW_CORNER_PREFERENCE),
            byref(value),
            DWORD(sizeof(value)),
        )
        return int(hr) == 0
    except Exception:
        return False


def enable_dark_titlebar(hwnd: int) -> bool:
    """Ask DWM to draw this window's titlebar in dark mode."""

    if not _IS_WINDOWS:
        return False

    try:
        dwmapi = windll.dwmapi
    except Exception:
        return False

    value = c_int(1)
    applied = False
    for attribute in (_DWMWA_USE_IMMERSIVE_DARK_MODE, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
        try:
            hr = dwmapi.DwmSetWindowAttribute(
                HWND(hwnd),
                DWORD(attribute),
                byref(value),
                DWORD(sizeof(value)),
            )
            if int(hr) == 0:
                applied = True
                break
        except Exception:
            return False
    if applied:
        set_windows_titlebar_colors(hwnd)
        enable_windows_rounded_corners(hwnd)
    return applied


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


# ---- Windows 11 system backdrop types ----

_DWMWA_SYSTEMBACKDROP_TYPE = 38
_MIN_SYSTEM_BACKDROP_BUILD = 22621


class WindowsBackdropType:
    AUTO = 0
    NONE = 1
    MICA = 2
    ACRYLIC = 3  # Desktop Acrylic / transient-window system backdrop
    TABBED = 4


def enable_windows_backdrop(hwnd: int, backdrop: int = WindowsBackdropType.MICA) -> bool:
    """Try to enable a Windows system backdrop type (Mica/Acrylic/Tabbed).

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


def extend_windows_frame_into_client(hwnd: int) -> bool:
    """Extend the DWM frame through the complete client area.

    Negative margins are the documented way to make the entire window a DWM
    composition surface. Qt can then paint translucent controls over the
    system backdrop instead of limiting the material to the title bar.
    """

    if not _IS_WINDOWS:
        return False

    try:
        margins = _MARGINS(-1, -1, -1, -1)
        hr = windll.dwmapi.DwmExtendFrameIntoClientArea(
            HWND(hwnd),
            byref(margins),
        )
        return int(hr) == 0
    except Exception:
        return False


def reset_windows_frame_client_area(hwnd: int) -> bool:
    """Stop extending the DWM frame through the client area."""

    if not _IS_WINDOWS:
        return False

    try:
        margins = _MARGINS(0, 0, 0, 0)
        hr = windll.dwmapi.DwmExtendFrameIntoClientArea(
            HWND(hwnd),
            byref(margins),
        )
        return int(hr) == 0
    except Exception:
        return False


def windows_system_backdrop_supported() -> bool:
    """Return whether this OS supports ``DWMWA_SYSTEMBACKDROP_TYPE``.

    Microsoft documents the attribute for Windows 11 build 22621 and newer.
    Keeping the version gate here prevents a transparent client area on
    systems where DWM cannot supply the requested material.
    """

    if not _IS_WINDOWS:
        return False
    try:
        return int(sys.getwindowsversion().build) >= _MIN_SYSTEM_BACKDROP_BUILD
    except Exception:
        return False


def _opaque_window_palette(window: QWidget) -> QPalette:
    app = QApplication.instance()
    if app is not None:
        return QPalette(app.palette())
    return QPalette(window.palette())


def _refresh_backdrop_style(window: QWidget) -> None:
    # Dynamic-property selectors are evaluated during polishing. Refresh
    # descendants because the selectors target content through the top-level
    # window property.
    for widget in (window, *window.findChildren(QWidget)):
        widget.style().unpolish(widget)
        widget.style().polish(widget)
    window.update()


def _restore_solid_background(
    window: QWidget,
    palette: QPalette,
    auto_fill: bool,
) -> None:
    window.setProperty("backdrop", "")
    window.setPalette(palette)
    window.setAutoFillBackground(auto_fill)
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    _refresh_backdrop_style(window)


def disable_windows_backdrop(window: QWidget) -> bool:
    """Use LumiSync's opaque blue theme without a system backdrop.

    This also works on non-Windows platforms; the native DWM cleanup is simply
    skipped there.
    """

    palette = _opaque_window_palette(window)
    auto_fill = window.autoFillBackground()
    native_ok = True
    try:
        if _IS_WINDOWS:
            hwnd = int(window.winId())
            native_ok = enable_windows_backdrop(
                hwnd,
                WindowsBackdropType.NONE,
            )
            reset_windows_frame_client_area(hwnd)
        _restore_solid_background(window, palette, auto_fill)
        return native_ok
    except Exception:
        try:
            _restore_solid_background(window, palette, auto_fill)
        except Exception:
            pass
        return False


def _apply_windows_system_backdrop(
    window: QWidget,
    backdrop: int,
    style_name: str,
) -> bool:
    """Apply a DWM material and activate its matching QSS layers.

    The ``backdrop`` property activates translucent foundation layers. If DWM
    rejects the backdrop request, every change is rolled back and the normal
    opaque dark theme remains in place.
    """

    if not windows_system_backdrop_supported():
        return False

    original_palette = _opaque_window_palette(window)
    original_auto_fill = window.autoFillBackground()

    def restore_opaque_fallback() -> None:
        _restore_solid_background(
            window,
            original_palette,
            original_auto_fill,
        )

    try:
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        transparent_palette = QPalette(original_palette)
        transparent_palette.setColor(
            QPalette.ColorRole.Window,
            QColor(0, 0, 0, 0),
        )
        window.setPalette(transparent_palette)
        window.setAutoFillBackground(False)

        hwnd = int(window.winId())
        enable_dark_titlebar(hwnd)
        if not enable_windows_backdrop(hwnd, backdrop):
            restore_opaque_fallback()
            return False
        if not extend_windows_frame_into_client(hwnd):
            enable_windows_backdrop(hwnd, WindowsBackdropType.NONE)
            restore_opaque_fallback()
            return False

        window.setProperty("backdrop", style_name)
        _refresh_backdrop_style(window)
        return True
    except Exception:
        try:
            restore_opaque_fallback()
        except Exception:
            pass
        return False


def apply_windows_mica(window: QWidget) -> bool:
    """Apply Mica as a subtle, wallpaper-derived window foundation."""

    return _apply_windows_system_backdrop(
        window,
        WindowsBackdropType.MICA,
        "mica",
    )


def apply_windows_acrylic(window: QWidget) -> bool:
    """Apply Desktop Acrylic as a continuously blurred window foundation."""

    return _apply_windows_system_backdrop(
        window,
        WindowsBackdropType.ACRYLIC,
        "acrylic",
    )

__all__ = [
    "apply_qt_translucent_background",
    "enable_dark_titlebar",
    "set_windows_titlebar_colors",
    "enable_windows_rounded_corners",
    "install_dark_titlebar_filter",
    "enable_windows_dwm_blur",
    "enable_windows_backdrop",
    "extend_windows_frame_into_client",
    "reset_windows_frame_client_area",
    "windows_system_backdrop_supported",
    "disable_windows_backdrop",
    "apply_windows_mica",
    "apply_windows_acrylic",
    "WindowsBackdropType",
]
