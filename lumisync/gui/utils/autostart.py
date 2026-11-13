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

# Sign-in launches start hidden in the tray instead of opening the window.
_STARTUP_FLAG = "--minimized"


def _program_path() -> str:
    """The file that starts this copy of LumiSync.

    Inside an AppImage ``sys.executable`` lives in a temporary mount
    (``/tmp/.mount_XXXX``) that is gone after this run; ``$APPIMAGE`` is the
    stable path of the image itself.
    """
    if _SYSTEM == "Linux":
        appimage = os.environ.get("APPIMAGE", "").strip()
        if appimage:
            return appimage
    return sys.executable


def _launch_command() -> str:
    """Command the OS should run at login to start LumiSync."""
    if getattr(sys, "frozen", False):
        return f'"{_program_path()}" {_STARTUP_FLAG}'

    interpreter = Path(sys.executable)
    if _SYSTEM == "Windows":
        # pythonw.exe avoids flashing a console window.
        windowed = interpreter.with_name("pythonw.exe")
        if windowed.exists():
            interpreter = windowed
    return f'"{interpreter}" -m lumisync {_STARTUP_FLAG}'


def _command_executable(command: str) -> Path | None:
    """Return the program a stored autostart command launches."""
    command = command.strip()
    if not command:
        return None
    if command.startswith('"'):
        end = command.find('"', 1)
        target = command[1:end] if end > 0 else command[1:]
    else:
        target = command.split()[0]
    return Path(target) if target else None


def _should_repair(current: str) -> bool:
    """Decide whether an existing entry should point at this copy instead.

    Only packaged builds repair: a developer running from source must not take
    over the installed app's sign-in entry. Another copy that still exists
    keeps the entry (the installer decides which copy starts), but an entry
    for a deleted copy, or this copy with old arguments, is refreshed.
    """
    if not getattr(sys, "frozen", False):
        return False
    if current.strip() in (_launch_command(), _desktop_exec()):
        return False
    registered = _command_executable(current)
    if registered is None:
        return True
    try:
        here = Path(_program_path()).resolve()
        if registered.resolve() == here:
            return True
        return not registered.exists()
    except OSError:
        return False


# --------------------------------------------------------------------- Linux

def _autostart_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "autostart"


def _desktop_path() -> Path:
    return _autostart_dir() / "lumisync.desktop"


def _quote_exec_argument(value: str) -> str:
    """Quote one argument for a Desktop Entry ``Exec=`` key.

    Inside quotes the spec reserves ``"``, `` ` ``, ``$`` and ``\\``; ``%``
    starts a field code. The string-level escape doubles every backslash.
    """
    escaped = "".join("\\" + ch if ch in '"`$\\' else ch for ch in value)
    escaped = escaped.replace("\\", "\\\\").replace("%", "%%")
    return f'"{escaped}"'


def _desktop_exec() -> str:
    """The ``Exec=`` value for the Linux autostart entry."""
    if getattr(sys, "frozen", False):
        return f"{_quote_exec_argument(_program_path())} {_STARTUP_FLAG}"
    return f"{_quote_exec_argument(sys.executable)} -m lumisync {_STARTUP_FLAG}"


def _desktop_contents() -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={_APP_NAME}\n"
        "Comment=Sync your lights with your screen and audio\n"
        f"Exec={_desktop_exec()}\n"
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


def repair_if_stale() -> bool:
    """Re-point an existing autostart entry at the current executable.

    Moving from the portable onefile build to the installed onedir build changes
    ``sys.executable``. Without this repair, Windows keeps launching the old
    copy from Downloads after the user has installed the newer release.
    """
    if _SYSTEM == "Windows":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                _RUN_KEY,
                0,
                winreg.KEY_READ | winreg.KEY_SET_VALUE,
            ) as key:
                try:
                    current, _kind = winreg.QueryValueEx(key, _APP_NAME)
                except FileNotFoundError:
                    return False
                if not _should_repair(str(current)):
                    return False
                winreg.SetValueEx(key, _APP_NAME, 0, winreg.REG_SZ, _launch_command())
                return True
        except OSError:
            return False

    if _SYSTEM == "Linux":
        path = _desktop_path()
        if not path.exists():
            return False
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            desired = f"Exec={_desktop_exec()}"
            for index, line in enumerate(lines):
                if line.startswith("Exec="):
                    if not _should_repair(line[len("Exec="):]):
                        return False
                    lines[index] = desired
                    break
            else:
                lines.append(desired)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
        except OSError:
            return False

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


__all__ = ["is_supported", "is_enabled", "set_enabled", "repair_if_stale"]
