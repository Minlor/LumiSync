"""Colour helpers shared by the USB lighting drivers on this PC.

The LEDs on a motherboard or a mouse are driven linearly, while every colour
in the app is sRGB. :func:`correct_color` converts with the standard sRGB
transfer function (:data:`SRGB_TO_LINEAR`) and then a per-channel white
balance that each driver measures for its own LEDs. Software brightness is
applied before the conversion (:func:`wire_color`), so dimming keeps hues.

:class:`LightingOptions` is how a driver tells the GUI which per-device
lighting options a descriptor has (colour correction, whole/per-zone
layout, built-in effects as :class:`EffectOptions`, a second colour as
:class:`SecondaryColorOption`) and how to label them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional, Sequence, Tuple

from ..device import DeviceLike
from .base import RGB

# Descriptor field and values for colour correction. The default (sRGB) is
# stored as an absent field.
COLOR_CORRECTION_FIELD = "color_correction"
COLOR_CORRECTION_SRGB = "srgb"  # default: sRGB in, linear light out
COLOR_CORRECTION_NONE = "none"

# Gains that leave a colour unchanged after the curve.
NEUTRAL_WHITE_BALANCE: Tuple[float, float, float] = (1.0, 1.0, 1.0)


def channel(value: Any) -> int:
    """One colour channel clamped to 0-255."""
    return max(0, min(255, int(value)))


def descriptor_field(device: Optional[DeviceLike], name: str) -> str:
    """A descriptor's text field, stripped and lower case ("" when absent)."""
    if not isinstance(device, Mapping):
        return ""
    return str(device.get(name) or "").strip().lower()


def color_correction(device: Optional[DeviceLike]) -> str:
    """The descriptor's colour correction (sRGB unless switched off)."""
    if descriptor_field(device, COLOR_CORRECTION_FIELD) == COLOR_CORRECTION_NONE:
        return COLOR_CORRECTION_NONE
    return COLOR_CORRECTION_SRGB


def _srgb_to_linear(value: int) -> int:
    """The standard (piecewise) sRGB decoding of one 0-255 channel."""
    c = value / 255.0
    linear = c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return round(linear * 255.0)


# sRGB channel value -> linear-light channel value. 0 and 255 map to
# themselves; mid grey 128 becomes 55.
SRGB_TO_LINEAR: Tuple[int, ...] = tuple(_srgb_to_linear(value) for value in range(256))
assert SRGB_TO_LINEAR[0] == 0 and SRGB_TO_LINEAR[255] == 255


def correct_color(
    color: Sequence[int],
    correction: str = COLOR_CORRECTION_SRGB,
    white_balance: Sequence[float] = NEUTRAL_WHITE_BALANCE,
    keep_peak: bool = False,
) -> RGB:
    """Convert an sRGB colour to the LEDs' linear drive (unless correction is off).

    The curve comes first, then ``white_balance`` (one gain per channel).
    With ``keep_peak`` the result is then scaled so its strongest channel is
    as strong as the input's: the curve's colour mix at the input's
    brightness, for LEDs where the plain curve looks too dark.
    """
    r, g, b = (channel(value) for value in color)
    if correction == COLOR_CORRECTION_NONE:
        return (r, g, b)
    gained = (
        SRGB_TO_LINEAR[r] * white_balance[0],
        SRGB_TO_LINEAR[g] * white_balance[1],
        SRGB_TO_LINEAR[b] * white_balance[2],
    )
    peak = max(gained)
    scale = max(r, g, b) / peak if keep_peak and peak > 0 else 1.0
    return (
        min(255, round(gained[0] * scale)),
        min(255, round(gained[1] * scale)),
        min(255, round(gained[2] * scale)),
    )


def scale_color(color: Sequence[int], brightness: int) -> RGB:
    """Apply brightness (0-100) to a colour, in software."""
    level = max(0, min(100, int(brightness)))
    r, g, b = (channel(value) for value in color)
    return (round(r * level / 100), round(g * level / 100), round(b * level / 100))


def wire_color(
    color: Sequence[int],
    brightness: int,
    correction: str = COLOR_CORRECTION_SRGB,
    white_balance: Sequence[float] = NEUTRAL_WHITE_BALANCE,
    keep_peak: bool = False,
) -> RGB:
    """What goes in the packet: brightness first, then colour correction."""
    return correct_color(scale_color(color, brightness), correction, white_balance, keep_peak)


def average_color(colors: Sequence[Sequence[int]]) -> RGB:
    """Mean of a frame, for driving a device as one light."""
    count = len(colors)
    if not count:
        return (0, 0, 0)
    return (
        round(sum(channel(c[0]) for c in colors) / count),
        round(sum(channel(c[1]) for c in colors) / count),
        round(sum(channel(c[2]) for c in colors) / count),
    )


# Descriptor fields of a device's built-in lighting effect (devices whose
# driver declares :class:`EffectOptions`). Each default is stored as an
# absent field.
EFFECT_FIELD = "lighting_effect"  # the effect's key, e.g. "static", "wave"
EFFECT_SPEED_FIELD = "lighting_effect_speed"  # 1 (slowest) .. speed_steps (fastest)
EFFECT_REVERSED_FIELD = "lighting_effect_reversed"  # True: the effect's second direction
EFFECT_MULTICOLOR_FIELD = "lighting_effect_multicolor"  # True: multicolour instead of the colour


@dataclass(frozen=True)
class EffectChoice:
    """One built-in effect as the GUI offers it.

    ``directions`` holds the labels of the default and the reversed
    direction, or nothing when the effect has no direction. ``multicolor``
    says whether the effect offers a multicolour switch. ``has_color`` is
    False for an effect that shows no chosen colour (a colour cycle), so
    colour options such as :class:`SecondaryColorOption` are hidden for it.
    """

    key: str
    name: str
    has_speed: bool = False
    directions: Tuple[str, ...] = ()
    multicolor: bool = False
    has_color: bool = True

    @property
    def has_direction(self) -> bool:
        return len(self.directions) == 2


@dataclass(frozen=True)
class EffectOptions:
    """A device's built-in effects and the chosen one, with its settings."""

    choices: Tuple[EffectChoice, ...]
    effect: str
    speed: int
    reversed: bool = False
    multicolor: bool = False
    default_effect: str = ""
    default_speed: int = 1
    speed_steps: int = 5

    def choice(self, key: str) -> Optional[EffectChoice]:
        for choice in self.choices:
            if choice.key == key:
                return choice
        return None

    @property
    def current(self) -> EffectChoice:
        return self.choice(self.effect) or self.choices[0]


# Descriptor fields of the opt-in Monitor/Music sync of a device whose every
# change wears it (devices whose driver declares :class:`SyncOptions`). The
# defaults (off, the slowest interval) are stored as absent fields.
SYNC_ENABLED_FIELD = "lighting_sync_enabled"  # True: the device may be a sync target
SYNC_INTERVAL_FIELD = "lighting_sync_interval"  # one of the driver's interval keys


@dataclass(frozen=True)
class SyncChoice:
    """One sync update interval as the GUI offers it."""

    key: str
    label: str


@dataclass(frozen=True)
class SyncOptions:
    """An opt-in Monitor/Music sync switch with its update intervals.

    The driver supplies every label: the row's subtitle, the confirmation
    asked before turning sync on, and the interval names.
    """

    enabled: bool
    interval: str
    choices: Tuple[SyncChoice, ...]
    default_interval: str
    title: str = "Monitor and Music sync"
    description: str = ""
    interval_title: str = "Sync updates"
    confirm_title: str = ""
    confirm_text: str = ""
    confirm_accept: str = "Allow sync"
    confirm_reject: str = "Cancel"

    def choice(self, key: str) -> Optional[SyncChoice]:
        for choice in self.choices:
            if choice.key == key:
                return choice
        return None


@dataclass(frozen=True)
class SecondaryColorOption:
    """A second colour for part of a device (a mouse's logo), off by default.

    ``enabled_field`` and ``color_field`` are the descriptor fields (the
    colour saved as ``[r, g, b]``); ``color`` is None until one is chosen,
    which means the main colour. The driver supplies every label.
    """

    enabled_field: str
    color_field: str
    enabled: bool
    color: Optional[RGB]
    title: str
    description: str
    color_title: str


@dataclass(frozen=True)
class LightingOptions:
    """The per-device lighting options a descriptor has, with their labels.

    ``layout_field`` is the descriptor field of the whole/per-zone choice
    ("" when the device has no layout option); ``layout_whole`` is its
    default value, stored as an absent field. ``effects`` lists the
    device's built-in effects (None when it has none); ``sync`` its opt-in
    Monitor/Music sync (None when sync needs no opt-in or is not offered);
    ``secondary_color`` an optional second colour for part of the device.
    """

    title: str
    noun: str
    color_correction: bool = True
    correction_tip: str = ""
    layout_field: str = ""
    layout_whole: str = ""
    layout_zones: str = "zones"
    layout: str = ""
    zone_names: Tuple[str, ...] = ()
    whole_label: str = ""
    whole_description: str = ""
    zones_description: str = ""
    effects: Optional[EffectOptions] = None
    sync: Optional[SyncOptions] = None
    secondary_color: Optional[SecondaryColorOption] = None

    @property
    def has_secondary_color(self) -> bool:
        return self.secondary_color is not None

    @property
    def has_layout(self) -> bool:
        return bool(self.layout_field)

    @property
    def has_effects(self) -> bool:
        return self.effects is not None

    @property
    def has_sync(self) -> bool:
        return self.sync is not None

    @property
    def per_zone(self) -> bool:
        return self.has_layout and self.layout == self.layout_zones

    @property
    def zone_count(self) -> int:
        return len(self.zone_names)

    @property
    def zones_label(self) -> str:
        return f"Per zone ({self.zone_count})"


__all__ = [
    "COLOR_CORRECTION_FIELD",
    "COLOR_CORRECTION_NONE",
    "COLOR_CORRECTION_SRGB",
    "EFFECT_FIELD",
    "EFFECT_MULTICOLOR_FIELD",
    "EFFECT_REVERSED_FIELD",
    "EFFECT_SPEED_FIELD",
    "NEUTRAL_WHITE_BALANCE",
    "SRGB_TO_LINEAR",
    "SYNC_ENABLED_FIELD",
    "SYNC_INTERVAL_FIELD",
    "EffectChoice",
    "EffectOptions",
    "LightingOptions",
    "SecondaryColorOption",
    "SyncChoice",
    "SyncOptions",
    "average_color",
    "channel",
    "color_correction",
    "correct_color",
    "descriptor_field",
    "scale_color",
    "wire_color",
]
