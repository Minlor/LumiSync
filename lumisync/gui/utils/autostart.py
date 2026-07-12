"""Start-with-the-session support.

Per-user, no admin/root needed:

* **Windows** — an ``HKCU\\...\\Run`` registry value.
* **Linux** — an XDG ``~/.config/autostart/lumisync.desktop`` entry (GNOME,
  KDE, and most desktops honor it).

A no-op with ``is_supported() == False`` on any other platform (e.g. macOS).
"""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

_SYSTEM = platform.system()
_APP_NAME = "LumiSync"

# Windows registry location.
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def is_supported() -> bool:
    return _SYSTEM in ("Windows", "Linux")


# --------------------------------------------------------------------- launch

def _launch_command() -> str:
    """Command the OS should run at login to start LumiSync."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'

    interpreter = Path(sys.executable)
    if _SYSTEM == "Windows":
        # pythonw.exe avoids flashing a console window.
        windowed = interpreter.with_name("pythonw.exe")
        if windowed.exists():
            interpreter = windowed
    return f'"{interpreter}" -m lumisync'


# --------------------------------------------------------------------- Linux

def _autostart_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart"


def _desktop_path() -> Path:
    return _autostart_dir() / "lumisync.desktop"


def _desktop_contents() -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={_APP_NAME}\n"
        "Comment=Sync your lights with your screen and audio\n"
        f"Exec={_launch_command()}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


# --------------------------------------------------------------------- Windows

def _win_is_enabled() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _APP_NAME)
        return True
    except OSError:
        return False


def _win_set_enabled(enabled: bool) -> bool:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, _launch_command())
            else:
                try:
                    winreg.DeleteValue(key, _APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


# --------------------------------------------------------------------- public

def is_enabled() -> bool:
    if _SYSTEM == "Windows":
        return _win_is_enabled()
    if _SYSTEM == "Linux":
        return _desktop_path().exists()
    return False


def set_enabled(enabled: bool) -> bool:
    """Enable/disable autostart. Returns True if the change was applied."""
    if _SYSTEM == "Windows":
        return _win_set_enabled(enabled)
    if _SYSTEM == "Linux":
        try:
            path = _desktop_path()
            if enabled:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(_desktop_contents(), encoding="utf-8")
            elif path.exists():
                path.unlink()
            return True
        except OSError:
            return False
    return False


__all__ = ["is_supported", "is_enabled", "set_enabled"]
