"""Typed sync preferences passed explicitly into the sync engine.

The GUI, command line and sync workers previously shared mutable module-level
``SYNC`` and ``BRIGHTNESS`` namespaces.  Every frame read the globals, so a
value could change (or be reset by a test) halfway through a session.  A
``SyncSettings`` instance is now owned by the controller and passed to the
worker; live edits mutate that one object instead of a process-wide global.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, Mapping

from ..sync import audio
from .options import BRIGHTNESS, SYNC

# QSettings keys for the tunables exposed in the Settings page. Kept here so
# the loader, the controller and the UI share one source of truth.
SYNC_SETTINGS_KEYS = {
    "smoothing": "sync/smoothing",
    "saturation": "sync/saturation",
    "gamma_correct": "sync/gamma_correct",
    "monitor_fps": "sync/monitor_fps",
    "music_gain": "sync/music_gain",
    "music_auto_gain": "sync/music_auto_gain",
    "music_smoothing": "sync/music_smoothing",
    "music_palette": "sync/music_palette",
    "music_reaction": "sync/music_reaction",
    "monitor_brightness": "sync/monitor/brightness",
    "music_brightness": "sync/music/brightness",
    "monitor_display": "sync/monitor_display",
}


def _coerce_float(value: Any, fallback: float, low: float, high: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, result))


def _coerce_int(value: Any, fallback: int, low: int, high: int) -> int:
    return int(_coerce_float(value, float(fallback), float(low), float(high)))


def _coerce_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return fallback


@dataclass
class SyncSettings:
    """All tuning knobs for one monitor or music sync session."""

    # Monitor sync
    monitor_fps: int = 45
    smoothing: float = 0.55
    gamma_correct: bool = True
    saturation: float = 1.15
    delta_threshold: int = 3
    monitor_brightness: float = 0.75
    monitor_display: int = 0

    # Music sync
    music_fps: int = 60
    music_gain: float = 1.7
    music_auto_gain: bool = True
    music_smoothing: float = 0.6
    music_palette: str = audio.PALETTE_RGB
    music_reaction: str = audio.REACTION_FLOW
    music_brightness: float = 0.85

    @classmethod
    def from_options(cls) -> "SyncSettings":
        """Build defaults from the historical option namespaces."""
        return cls(
            monitor_fps=int(SYNC.monitor_fps),
            smoothing=float(SYNC.smoothing),
            gamma_correct=bool(SYNC.gamma_correct),
            saturation=float(SYNC.saturation),
            delta_threshold=int(SYNC.delta_threshold),
            monitor_brightness=float(BRIGHTNESS.monitor),
            music_fps=int(SYNC.music_fps),
            music_gain=float(SYNC.music_gain),
            music_auto_gain=bool(SYNC.music_auto_gain),
            music_smoothing=float(SYNC.music_smoothing),
            music_palette=str(SYNC.music_palette),
            music_reaction=str(SYNC.music_reaction),
            music_brightness=float(BRIGHTNESS.music),
        )

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any] | None,
        *,
        fallback: "SyncSettings | None" = None,
    ) -> "SyncSettings":
        """Read field names or legacy ``sync/*`` keys, coercing every value."""
        base = fallback or cls()
        values = values or {}

        def value(name: str) -> Any:
            if name in values:
                return values[name]
            return values.get(SYNC_SETTINGS_KEYS.get(name, name))

        return cls(
            monitor_fps=_coerce_int(value("monitor_fps"), base.monitor_fps, 10, 144),
            smoothing=_coerce_float(value("smoothing"), base.smoothing, 0.05, 1.0),
            gamma_correct=_coerce_bool(value("gamma_correct"), base.gamma_correct),
            saturation=_coerce_float(value("saturation"), base.saturation, 1.0, 2.0),
            delta_threshold=_coerce_int(value("delta_threshold"), base.delta_threshold, 0, 255),
            monitor_brightness=_coerce_float(
                value("monitor_brightness"), base.monitor_brightness, 0.1, 1.0
            ),
            monitor_display=max(0, _coerce_int(value("monitor_display"), base.monitor_display, 0, 16)),
            music_fps=_coerce_int(value("music_fps"), base.music_fps, 10, 144),
            music_gain=_coerce_float(value("music_gain"), base.music_gain, 0.5, 4.0),
            music_auto_gain=_coerce_bool(value("music_auto_gain"), base.music_auto_gain),
            music_smoothing=_coerce_float(
                value("music_smoothing"), base.music_smoothing, 0.05, 1.0
            ),
            music_palette=(
                str(value("music_palette"))
                if str(value("music_palette")) in audio.PALETTES
                else base.music_palette
            ),
            music_reaction=(
                str(value("music_reaction"))
                if str(value("music_reaction")) in audio.REACTIONS
                else base.music_reaction
            ),
            music_brightness=_coerce_float(
                value("music_brightness"), base.music_brightness, 0.1, 1.0
            ),
        )

    @classmethod
    def from_qsettings(cls, settings) -> "SyncSettings":
        """Load from Qt's ``QSettings`` (values often round-trip as strings)."""
        fallback = cls.from_options()
        return cls.from_mapping(
            {
                field_name: settings.value(key, getattr(fallback, field_name))
                for field_name, key in SYNC_SETTINGS_KEYS.items()
            },
            fallback=fallback,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_qsettings(self, settings) -> None:
        for name in SYNC_SETTINGS_KEYS:
            settings.setValue(SYNC_SETTINGS_KEYS[name], getattr(self, name))

    def copy(self) -> "SyncSettings":
        return SyncSettings(**self.to_dict())

    def update_from(self, other: "SyncSettings") -> None:
        """Copy values into this instance so running workers see live edits."""
        for item in fields(self):
            setattr(self, item.name, getattr(other, item.name))

    def brightness_for(self, mode: str) -> float:
        return self.music_brightness if mode == "music" else self.monitor_brightness

    def set_brightness(self, mode: str, value: float) -> None:
        value = _coerce_float(value, self.brightness_for(mode), 0.1, 1.0)
        if mode == "music":
            self.music_brightness = value
        else:
            self.monitor_brightness = value


def sync_settings_from_json(data: Mapping[str, Any] | None) -> SyncSettings:
    return SyncSettings.from_mapping(data)


__all__ = [
    "SyncSettings",
    "SYNC_SETTINGS_KEYS",
    "sync_settings_from_json",
]
