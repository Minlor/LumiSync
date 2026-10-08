"""Suggest an account country from local region settings, without a network call."""

from __future__ import annotations

import sys

from PySide6.QtCore import QLocale

from ...accounts.countries import COUNTRY_CALLING_CODES


def _windows_home_country() -> str:
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        get_country = ctypes.WinDLL("kernel32", use_last_error=True).GetUserDefaultGeoName
        get_country.argtypes = [wintypes.LPWSTR, ctypes.c_int]
        get_country.restype = ctypes.c_int
        buffer = ctypes.create_unicode_buffer(16)
        return buffer.value.upper() if get_country(buffer, len(buffer)) else ""
    except (AttributeError, OSError):
        return ""


def suggested_account_country() -> tuple[str, str]:
    supported = {iso for iso, _code in COUNTRY_CALLING_CODES}
    home = _windows_home_country()
    if home in supported:
        return home, "Windows region"
    locale = QLocale.territoryToCode(QLocale.system().territory())
    return (locale, "system region") if locale in supported else ("", "")
