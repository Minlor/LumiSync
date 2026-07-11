"""Start-with-Windows support via the HKCU Run registry key.

Per-user only (no admin rights needed), and a no-op on other platforms.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "LumiSync"


def is_supported() -> bool:
    return platform.system() == "Windows"


def _launch_command() -> str:
    """Command Windows should run at login to start LumiSync."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    # Dev / pip install: prefer pythonw.exe so no console window appears.
    interpreter = Path(sys.executable)
    windowed = interpreter.with_name("pythonw.exe")
    if windowed.exists():
        interpreter = windowed
    return f'"{interpreter}" -m lumisync'


def is_enabled() -> bool:
    if not is_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
        return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    """Enable/disable autostart. Returns True if the change was applied."""
    if not is_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(
                    key, _VALUE_NAME, 0, winreg.REG_SZ, _launch_command()
                )
            else:
                try:
                    winreg.DeleteValue(key, _VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


__all__ = ["is_supported", "is_enabled", "set_enabled"]
