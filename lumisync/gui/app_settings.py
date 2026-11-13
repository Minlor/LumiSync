"""The single place that opens LumiSync's QSettings store.

On Windows ``QSettings("Minlor", "LumiSync")`` is the registry key
``HKCU\\Software\\Minlor\\LumiSync`` no matter what default format is set, so
the release self-check (``--check-gui``) and the test suite used to read and
overwrite the user's real preferences: target selections, the captured
display, window geometry. Setting ``LUMISYNC_SETTINGS_INI`` to a file path
redirects every caller to a throwaway INI file instead.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings

ORGANIZATION = "Minlor"
APPLICATION = "LumiSync"
OVERRIDE_ENV = "LUMISYNC_SETTINGS_INI"


def app_settings() -> QSettings:
    """Open the app's preference store (or the isolated one, when set)."""
    override = os.environ.get(OVERRIDE_ENV, "").strip()
    if override:
        return QSettings(override, QSettings.Format.IniFormat)
    return QSettings(ORGANIZATION, APPLICATION)


def is_isolated() -> bool:
    return bool(os.environ.get(OVERRIDE_ENV, "").strip())


__all__ = ["APPLICATION", "ORGANIZATION", "OVERRIDE_ENV", "app_settings", "is_isolated"]
