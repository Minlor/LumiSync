"""
Utility modules for the LumiSync GUI.
"""

from .validators import IPAddressValidator, MACAddressValidator, PortValidator
from .window_effects import (
    apply_qt_translucent_background,
    enable_dark_titlebar,
    set_windows_titlebar_colors,
    enable_windows_rounded_corners,
    install_dark_titlebar_filter,
    enable_windows_dwm_blur,
    enable_windows_backdrop,
    disable_windows_backdrop,
    windows_system_backdrop_supported,
    apply_windows_mica,
    apply_windows_acrylic,
    WindowsBackdropType,
)

__all__ = [
    'IPAddressValidator',
    'MACAddressValidator',
    'PortValidator',
    'apply_qt_translucent_background',
    'enable_dark_titlebar',
    'set_windows_titlebar_colors',
    'enable_windows_rounded_corners',
    'install_dark_titlebar_filter',
    'enable_windows_dwm_blur',
    'enable_windows_backdrop',
    'disable_windows_backdrop',
    'windows_system_backdrop_supported',
    'apply_windows_mica',
    'apply_windows_acrylic',
    'WindowsBackdropType',
]
