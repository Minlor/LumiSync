"""Icon registry for the LumiSync GUI.

Why this exists
--------------
The GUI previously referenced icon filenames directly (e.g. ``\"refresh.png\"``).
That approach doesn't scale well as the UI grows.

This module provides:
- A typed key (``IconKey``) so UI code doesn't hard-code filenames.
- Central mapping of keys -> icon filenames.
- Simple caching of ``QIcon`` / ``QPixmap`` instances.

Icons are loaded from package resources (works in dev, installed, and frozen).

Implementation notes
--------------------
We prefer ``.svg`` for crisp scaling. If an SVG isn't present (or can't be
resolved), we fall back to the older PNG with the same base name.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, Optional, Tuple, Union

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QIcon, QPixmap

from . import ResourceManager


class IconKey(str, Enum):
    # General
    APP = "app"
    SETTINGS = "settings"

    # Devices
    REFRESH = "refresh"
    NETWORK = "network"
    POWER = "power"

    # Modes
    SCREEN = "screen"
    MUSIC = "music"
    PLAY = "play"
    STOP = "stop"


_ICON_FILES: Dict[IconKey, str] = {
    IconKey.APP: "lightbulb-on.svg",
    IconKey.SETTINGS: "settings.svg",
    IconKey.REFRESH: "refresh.svg",
    IconKey.NETWORK: "network.svg",
    IconKey.POWER: "power.svg",
    IconKey.SCREEN: "screen.svg",
    IconKey.MUSIC: "music.svg",
    IconKey.PLAY: "play.svg",
    IconKey.STOP: "stop.svg",
}


_SizeLike = Union[None, int, QSize]


def _normalize_size(size: _SizeLike) -> Optional[QSize]:
    if size is None:
        return None
    if isinstance(size, QSize):
        return size
    return QSize(int(size), int(size))


def _fallback_png(filename: str) -> str:
    if filename.lower().endswith(".svg"):
        return filename[:-4] + ".png"
    return filename


# Cache keys: (IconKey, width, height)
_icon_cache: Dict[Tuple[IconKey, int, int], QIcon] = {}
_pixmap_cache: Dict[Tuple[IconKey, int, int], QPixmap] = {}


def icon(key: IconKey, size: _SizeLike = None) -> QIcon:
    """Return a QIcon for the provided key."""

    qsize = _normalize_size(size)
    w = int(qsize.width()) if qsize else 0
    h = int(qsize.height()) if qsize else 0
    cache_key = (key, w, h)

    cached = _icon_cache.get(cache_key)
    if cached is not None:
        return cached

    filename = _ICON_FILES.get(key)
    if not filename:
        ico = QIcon()
        _icon_cache[cache_key] = ico
        return ico

    ico = ResourceManager.get_icon(filename)
    if ico.isNull():
        ico = ResourceManager.get_icon(_fallback_png(filename))

    _icon_cache[cache_key] = ico
    return ico


def pixmap(key: IconKey, size: _SizeLike = None) -> QPixmap:
    """Return a QPixmap for the provided key."""

    qsize = _normalize_size(size)
    w = int(qsize.width()) if qsize else 0
    h = int(qsize.height()) if qsize else 0
    cache_key = (key, w, h)

    cached = _pixmap_cache.get(cache_key)
    if cached is not None:
        return cached

    filename = _ICON_FILES.get(key)
    if not filename:
        pm = QPixmap()
        _pixmap_cache[cache_key] = pm
        return pm

    # ResourceManager.get_pixmap doesn't currently handle SVG rendering.
    # Use QIcon -> pixmap for SVGs.
    ico = icon(key)
    if qsize:
        pm = ico.pixmap(qsize)
    else:
        pm = ico.pixmap(QSize(256, 256))

    _pixmap_cache[cache_key] = pm
    return pm


def register_icon(key: IconKey, filename: str) -> None:
    """Override or add a mapping from IconKey -> filename."""

    _ICON_FILES[key] = filename
    # Clear caches for this key
    for k in list(_icon_cache.keys()):
        if k[0] == key:
            _icon_cache.pop(k, None)
    for k in list(_pixmap_cache.keys()):
        if k[0] == key:
            _pixmap_cache.pop(k, None)


__all__ = ["IconKey", "icon", "pixmap", "register_icon"]

