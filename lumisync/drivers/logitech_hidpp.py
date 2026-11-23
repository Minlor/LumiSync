"""Logitech mouse lighting over HID++ 2.0, with no Logitech software API.

HID++ is Logitech's own protocol on a vendor HID collection of the mouse
(usage page ``0xFF00``). This driver uses the "long" collection (usage 2):
a request is a 20-byte output report and its answer a 20-byte input report.

Request: ``[0x11, device index, feature index, (function << 4) | software id,
16 parameter bytes]``. A reply repeats bytes 1-3. An error reply has feature
index ``0xFF`` and carries the request's feature index, function byte and an
error code (:data:`ERRORS`). Features are numbered per device: the root
feature (``0x0000``, always index 0) maps a feature ID to this mouse's index,
so the colour feature's index is looked up when the handle opens and never
assumed.

Lighting is feature ``0x8070`` (colour LED effects), per zone (the G502
HERO has two, the DPI lights and the logo). getZoneInfo (fn1) gives a
zone's location and effect count, getZoneEffectInfo (fn2) the effect id at
each index, and setZoneEffect (fn3) takes ``[zone, effect INDEX, 10 effect
bytes, persist]``. The second byte is the zone's index from fn2, not the
effect id (the G502 HERO lists off 0x0000, fixed 0x0001, breathing 0x000A
and colour cycle 0x0003 at indexes 0-3 on both zones and refuses the id
0x0A with "invalid argument"), so every zone's id -> index map is read when
the handle opens, kept on the controller, and an effect a zone did not
list is never sent. The effect bytes, each confirmed by eye:

* fixed: ``R G B 02``
* breathing: ``R G B periodHi periodLo 00 64`` (period in ms, waveform 0,
  intensity 0x64)
* colour cycle: ``00 00 00 00 00 periodHi periodLo 64``

the rest zero. The manual effect is a per-device option
(``lighting_effect``: ``fixed``, ``breathing`` or ``colour_cycle``, with
``lighting_effect_speed`` 1-5 as a period of 8, 5, 3, 2 or 1 s), and with
``lighting_logo_color_enabled`` the logo zone takes ``lighting_logo_color``
while the DPI lights keep the main colour. Zones run independently.

Sync frames are always fixed colours and read nothing. By default the whole
mouse is one colour, like a single lamp, and sync sends the frame's
average. With the descriptor field ``lighting_layout: "zones"`` a sync
frame is resized to the zones instead, one colour per zone in the mouse's
own zone order; manual lighting always uses the effect and colours above.
When a stream ends the manual lighting is shown again, if a manual command
was given since the app started. The zone count and each zone's location
are read when the handle opens; :data:`SUPPORTED_DEVICES` names the
locations and effects confirmed on each model. Power off is fixed black.
The mouse cannot be asked cheaply what it shows while another program also
drives it, so power, brightness and colour are tracked optimistically.

Colour: like the board (see :mod:`.led_color`), each colour is scaled by
brightness, put through the sRGB curve and this mouse's
:data:`LOGITECH_WHITE_BALANCE` before it is sent, unless the descriptor has
``color_correction: "none"``. Layout and colour correction are read from
the adapter's descriptor on every command, so :meth:`update_device` applies
an edited descriptor without reopening anything.

Safety: :data:`ALLOWED_FUNCTIONS` lists every (feature, function) this
driver may send, checked when a request is built and again just before it
is written. setZoneEffect is additionally checked whole: a known zone, an
effect index the zone listed for fixed, breathing or colour cycle, exactly
that effect's byte layout (periods 1000-20000 ms, waveform 0, intensity
0x64), and the "persist" byte at 0, so nothing is written to the mouse's
flash. Onboard
profiles, report rate, DPI and firmware update are never sent
(:data:`NEVER_SEND_FEATURES`); the onboard-profile mode is only read, for a
log line. Discovery only lists HID collections; it sends nothing.

Verified on a wired G502 HERO (``046D:C08B``) under Windows 11 with
Logitech G HUB running. G HUB may put its own colour back when its active
profile changes. Not yet run on Linux.

Connections: GUI controls and the sync engine share one :class:`_Controller`
per mouse, which owns the only handle and a lock held for each whole
request/reply exchange and each frame. The handle opens on the first command
and closes when the last adapter using it is closed.
"""

from __future__ import annotations

import dataclasses
import sys
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Dict, FrozenSet, List, NamedTuple, Optional, Set, Tuple

from ..device import DeviceLike
from ..utils.colors import resample_colors_to_count
from ..utils.logging import get_logger
from . import hid_feature, led_color
from .base import RGB, DeviceCapabilities, TransportAdapter
from .led_color import (
    COLOR_CORRECTION_FIELD,
    EffectChoice,
    EffectOptions,
    LightingOptions,
    SecondaryColorOption,
    average_color,
    color_correction,
)
from .led_color import descriptor_field as _field

logger = get_logger("lumisync_logitech")

# The HID layer in use. Tests replace it with a fake that scripts replies.
_hid: Any = hid_feature

# ------------------------------------------------------------------ protocol

REPORT_ID_SHORT = 0x10
REPORT_ID_LONG = 0x11
LONG_LENGTH = 20
PARAMS_LENGTH = LONG_LENGTH - 4
# Largest input report read back (Windows needs at least the collection's
# input report length; Linux hidraw may also deliver other collections').
READ_SIZE = 64

HIDPP_USAGE_PAGE = 0xFF00
HIDPP_LONG_USAGE = 0x0002

# A corded mouse answers as device 0xFF (receivers number their devices 1-6).
DEVICE_INDEX = 0xFF
# The 4-bit software id in every request; replies to other programs (G HUB)
# carry theirs, which is how they are told apart.
SOFTWARE_ID = 0x0B

ERROR_INDEX = 0xFF  # HID++ 2.0 error reply
ERROR_INDEX_V1 = 0x8F  # HID++ 1.0 error reply, in case an old firmware answers so

ERRORS: Dict[int, str] = {
    0x01: "unknown error",
    0x02: "invalid argument",
    0x03: "out of range",
    0x04: "hardware error",
    0x05: "internal error",
    0x06: "invalid feature index",
    0x07: "invalid function",
    0x08: "busy",
    0x09: "unsupported",
}
ERROR_BUSY = 0x08

FEATURE_ROOT = 0x0000
FEATURE_COLOR_LED_EFFECTS = 0x8070
FEATURE_ONBOARD_PROFILES = 0x8100

ROOT_GET_FEATURE = 0x0
ROOT_PING = 0x1
LED_GET_INFO = 0x0
LED_GET_ZONE_INFO = 0x1
LED_GET_ZONE_EFFECT_INFO = 0x2
LED_SET_ZONE_EFFECT = 0x3
LED_GET_ZONE_EFFECT = 0xE
ONBOARD_GET_MODE = 0x2

# Every (feature, function) this driver may send. Anything else is refused
# when the request is built and again at the send point.
ALLOWED_FUNCTIONS: Dict[int, FrozenSet[int]] = {
    FEATURE_ROOT: frozenset({ROOT_GET_FEATURE, ROOT_PING}),
    FEATURE_COLOR_LED_EFFECTS: frozenset(
        {LED_GET_INFO, LED_GET_ZONE_INFO, LED_GET_ZONE_EFFECT_INFO, LED_SET_ZONE_EFFECT, LED_GET_ZONE_EFFECT}
    ),
    FEATURE_ONBOARD_PROFILES: frozenset({ONBOARD_GET_MODE}),  # read the mode only
}

# Features never written: (first id, last id, reason). Onboard profiles may
# only be read (getMode); every other function of it is refused.
NEVER_SEND_FEATURES: Tuple[Tuple[int, int, str], ...] = (
    (0x8100, 0x8100, "onboard profiles: writes switch host/onboard mode or rewrite profiles in flash"),
    (0x8060, 0x8060, "report rate: changes how the mouse reports movement"),
    (0x2201, 0x2201, "adjustable DPI: changes pointer speed"),
    (0x1300, 0x1300, "LED control: hands the lights between software and firmware"),
    (0x00C0, 0x00D0, "firmware update (DFU): can leave the mouse unusable"),
)

# Effect ids as getZoneEffectInfo (fn2) reports them. setZoneEffect does not
# take the id: it takes the zone's effect INDEX, the position fn2 listed the
# effect at, so the index of each id is read per zone when the handle opens.
EFFECT_ID_OFF = 0x0000  # listed by the mouse; never sent (power off is fixed black)
EFFECT_ID_FIXED = 0x0001
EFFECT_ID_COLOUR_CYCLE = 0x0003
EFFECT_ID_BREATHING = 0x000A
# The effects this driver may set.
ALLOWED_EFFECT_IDS = frozenset({EFFECT_ID_FIXED, EFFECT_ID_BREATHING, EFFECT_ID_COLOUR_CYCLE})
# Fixed colour's index on a zone whose effect list could not be read: the
# index every zone of the G502 HERO lists it at, and what this driver sent
# before it read the list.
FALLBACK_FIXED_INDEX = 1

# Packet offsets of setZoneEffect: [4] zone, [5] effect index, [6..15] the
# effect's 10 bytes, [16] the "persist" byte, [17..19] unused. The persist
# byte stays 0: the effect is shown, never saved to flash.
SET_EFFECT_ZONE_OFFSET = 4
SET_EFFECT_INDEX_OFFSET = 5
SET_EFFECT_DATA_OFFSET = 6
SET_EFFECT_DATA_LENGTH = 10
SET_EFFECT_PERSIST_OFFSET = 16
# Effect bytes, each layout confirmed by eye on a G502 HERO:
#   fixed:        R G B 02, then zeros
#   breathing:    R G B periodHi periodLo 00 64, then zeros (waveform 0 =
#                 default, intensity 0x64 = 100)
#   colour cycle: 00 00 00 00 00 periodHi periodLo 64, then zeros
FIXED_EFFECT_TAIL = 0x02
BREATHING_WAVEFORM = 0x00
EFFECT_INTENSITY = 0x64
MIN_PERIOD_MS = 1000
MAX_PERIOD_MS = 20000

PING_MARKER = 0x5A
MIN_PROTOCOL = (2, 0)

REPLY_TIMEOUT_SECONDS = 0.5
WRITE_TIMEOUT_SECONDS = 0.5
# Stale input reports read and dropped before each request at most.
DRAIN_LIMIT = 64

# Replaced in tests.
_monotonic = time.monotonic

# An identical frame is skipped only while it is this fresh; after that it is
# sent again, so a colour another program (G HUB) put back is overwritten
# within a second during sync.
REPEAT_AFTER_SECONDS = 1.0


def _feature_allowed(feature_id: int, function: int) -> bool:
    return function in ALLOWED_FUNCTIONS.get(feature_id, frozenset())


# Per zone (in zone order): effect id -> the zone's effect index, as read
# with getZoneEffectInfo when the handle opened.
EffectIndexes = Tuple[Mapping[int, int], ...]


def _period_allowed(period: int) -> bool:
    return MIN_PERIOD_MS <= period <= MAX_PERIOD_MS


def _check_set_zone_effect(packet: bytes, effect_indexes: Optional[EffectIndexes]) -> None:
    """Validate setZoneEffect's whole parameter block against the zone's effect list."""
    if packet[SET_EFFECT_PERSIST_OFFSET] != 0:
        raise ValueError("Refusing to write a Logitech lighting effect to flash.")
    if any(packet[SET_EFFECT_PERSIST_OFFSET + 1:]):
        raise ValueError("Refusing to send a Logitech lighting effect with data after the persist byte.")
    zone, index = packet[SET_EFFECT_ZONE_OFFSET], packet[SET_EFFECT_INDEX_OFFSET]
    if effect_indexes is None or zone >= len(effect_indexes):
        raise ValueError(f"Refusing to set a lighting effect on unknown zone {zone}.")
    effect_id = next(
        (eid for eid, listed in effect_indexes[zone].items() if listed == index and eid in ALLOWED_EFFECT_IDS),
        None,
    )
    if effect_id is None:
        raise ValueError(f"Effect index {index} of zone {zone} is not an allowed lighting effect.")
    data = packet[SET_EFFECT_DATA_OFFSET:SET_EFFECT_DATA_OFFSET + SET_EFFECT_DATA_LENGTH]
    if effect_id == EFFECT_ID_FIXED:
        valid = data[3] == FIXED_EFFECT_TAIL and not any(data[4:])
    elif effect_id == EFFECT_ID_BREATHING:
        valid = (
            _period_allowed(int.from_bytes(data[3:5], "big"))
            and data[5] == BREATHING_WAVEFORM
            and data[6] == EFFECT_INTENSITY
            and not any(data[7:])
        )
    else:  # colour cycle
        valid = (
            not any(data[:5])
            and _period_allowed(int.from_bytes(data[5:7], "big"))
            and data[7] == EFFECT_INTENSITY
            and not any(data[8:])
        )
    if not valid:
        raise ValueError(f"Refusing to send lighting effect 0x{effect_id:04X} with unknown effect bytes.")


def _check_packet(packet: bytes, feature_id: int, effect_indexes: Optional[EffectIndexes] = None) -> None:
    """Refuse any request outside the allow-list (builder and send point).

    ``effect_indexes`` is the mouse's per-zone effect list; setZoneEffect is
    refused without it.
    """
    if len(packet) != LONG_LENGTH or packet[0] != REPORT_ID_LONG or packet[1] != DEVICE_INDEX:
        raise ValueError("Refusing to send a malformed HID++ request.")
    function, software_id = packet[3] >> 4, packet[3] & 0x0F
    if software_id != SOFTWARE_ID:
        raise ValueError("Refusing to send a HID++ request with another software id.")
    if not _feature_allowed(feature_id, function):
        raise ValueError(
            f"HID++ feature 0x{feature_id:04X} function {function} is not allowed for Logitech lighting."
        )
    if feature_id == FEATURE_ROOT and packet[2] != 0:
        raise ValueError("The HID++ root feature is always at index 0.")
    if feature_id == FEATURE_COLOR_LED_EFFECTS and function == LED_SET_ZONE_EFFECT:
        _check_set_zone_effect(packet, effect_indexes)


@dataclass(frozen=True)
class Request:
    """One HID++ request, with the feature id its index stands for."""

    feature_id: int
    packet: bytes

    @property
    def function(self) -> int:
        return self.packet[3] >> 4


def build_request(
    feature_id: int, feature_index: int, function: int, params: bytes = b"",
    effect_indexes: Optional[EffectIndexes] = None,
) -> Request:
    """The one request builder: a 20-byte long report, checked against the allow-list.

    setZoneEffect also needs the mouse's per-zone ``effect_indexes``.
    """
    if not _feature_allowed(feature_id, function):
        raise ValueError(
            f"HID++ feature 0x{feature_id:04X} function {function} is not allowed for Logitech lighting."
        )
    if not 0 <= int(feature_index) < ERROR_INDEX_V1 or not 0 <= int(function) <= 0x0F:
        raise ValueError("HID++ feature index or function out of range.")
    params = bytes(params)
    if len(params) > PARAMS_LENGTH:
        raise ValueError(f"A HID++ long request has {PARAMS_LENGTH} parameter bytes, not {len(params)}.")
    packet = bytes(
        [REPORT_ID_LONG, DEVICE_INDEX, int(feature_index), (int(function) << 4) | SOFTWARE_ID]
    ) + params.ljust(PARAMS_LENGTH, b"\0")
    _check_packet(packet, feature_id, effect_indexes)
    return Request(feature_id, packet)


def _channel(value: Any) -> int:
    return max(0, min(255, int(value)))


def build_ping() -> Request:
    return build_request(FEATURE_ROOT, 0, ROOT_PING, bytes([0, 0, PING_MARKER]))


def build_get_feature(feature_id: int) -> Request:
    return build_request(FEATURE_ROOT, 0, ROOT_GET_FEATURE, int(feature_id).to_bytes(2, "big"))


def build_get_led_info(led_index: int) -> Request:
    return build_request(FEATURE_COLOR_LED_EFFECTS, led_index, LED_GET_INFO)


def build_get_zone_info(led_index: int, zone: int) -> Request:
    """``getZoneInfo``: a zone's location and effect count (read only)."""
    return build_request(FEATURE_COLOR_LED_EFFECTS, led_index, LED_GET_ZONE_INFO, bytes([zone & 0xFF]))


def build_get_zone_effect_info(led_index: int, zone: int, index: int) -> Request:
    """``getZoneEffectInfo``: the id of a zone's effect at ``index`` (read only)."""
    return build_request(
        FEATURE_COLOR_LED_EFFECTS, led_index, LED_GET_ZONE_EFFECT_INFO, bytes([zone & 0xFF, index & 0xFF])
    )


def build_get_zone_effect(led_index: int, zone: int) -> Request:
    return build_request(FEATURE_COLOR_LED_EFFECTS, led_index, LED_GET_ZONE_EFFECT, bytes([zone & 0xFF]))


def build_get_onboard_mode(onboard_index: int) -> Request:
    return build_request(FEATURE_ONBOARD_PROFILES, onboard_index, ONBOARD_GET_MODE)


def _period_bytes(period_ms: int) -> bytes:
    period = int(period_ms)
    if not _period_allowed(period):
        raise ValueError(f"Effect period {period} ms is out of range ({MIN_PERIOD_MS}-{MAX_PERIOD_MS}).")
    return period.to_bytes(2, "big")


def build_set_zone_effect(
    led_index: int, zone: int, effect_id: int, data: bytes, effect_indexes: EffectIndexes
) -> Request:
    """``setZoneEffect``: one zone to an effect, shown now and not saved.

    Packet bytes: [4] zone, [5] the zone's index of ``effect_id``, [6..15]
    ``data`` (zero padded), the rest 0 (byte [16], the persist flag,
    included). An effect the zone did not list is refused.
    """
    zone = int(zone)
    if not 0 <= zone < len(effect_indexes) or effect_id not in effect_indexes[zone]:
        raise ValueError(f"Zone {zone} does not list lighting effect 0x{effect_id:04X}.")
    data = bytes(data)
    if len(data) > SET_EFFECT_DATA_LENGTH:
        raise ValueError("Lighting effect data is too long.")
    return build_request(
        FEATURE_COLOR_LED_EFFECTS, led_index, LED_SET_ZONE_EFFECT,
        bytes([zone, effect_indexes[zone][effect_id]]) + data.ljust(SET_EFFECT_DATA_LENGTH, b"\0"),
        effect_indexes,
    )


def build_set_zone_color(led_index: int, zone: int, color: RGB, effect_indexes: EffectIndexes) -> Request:
    """One zone to a fixed colour: effect bytes ``R G B 02``."""
    r, g, b = (_channel(value) for value in color)
    return build_set_zone_effect(
        led_index, zone, EFFECT_ID_FIXED, bytes([r, g, b, FIXED_EFFECT_TAIL]), effect_indexes
    )


def build_set_zone_breathing(
    led_index: int, zone: int, color: RGB, period_ms: int, effect_indexes: EffectIndexes
) -> Request:
    """One zone breathing in ``color``: ``R G B periodHi periodLo 00 64``."""
    r, g, b = (_channel(value) for value in color)
    data = bytes([r, g, b]) + _period_bytes(period_ms) + bytes([BREATHING_WAVEFORM, EFFECT_INTENSITY])
    return build_set_zone_effect(led_index, zone, EFFECT_ID_BREATHING, data, effect_indexes)


def build_set_zone_colour_cycle(led_index: int, zone: int, period_ms: int, effect_indexes: EffectIndexes) -> Request:
    """One zone cycling through the colours: ``00 00 00 00 00 periodHi periodLo 64``."""
    data = bytes(5) + _period_bytes(period_ms) + bytes([EFFECT_INTENSITY])
    return build_set_zone_effect(led_index, zone, EFFECT_ID_COLOUR_CYCLE, data, effect_indexes)


# ------------------------------------------------------------------ devices


class SupportedDevice(NamedTuple):
    """One model verified on real hardware.

    ``zones`` is what each zone lights, as (location the mouse reports,
    name), in the mouse's zone order. The location codes are only confirmed
    per model, so a location not listed here is shown as "Zone N".
    ``effects`` are the keys of the :data:`EFFECTS` confirmed on the model.
    """

    vendor_id: int
    product_id: int
    name: str
    kind: str
    zones: Tuple[Tuple[int, str], ...]
    effects: Tuple[str, ...] = ("fixed",)


# The mice verified on real hardware. Others speak the same protocol but may
# number zones or effects differently, so they are added once checked.
SUPPORTED_DEVICES: Tuple[SupportedDevice, ...] = (
    # Zone 0 reports location 0x0001 and lights the DPI indicators, zone 1
    # reports 0x0002 and lights the logo (checked by eye on a G502 HERO).
    # Both zones list off, fixed, breathing and colour cycle, in that order.
    SupportedDevice(
        0x046D, 0xC08B, "G502 HERO", "mouse", ((0x0001, "DPI lights"), (0x0002, "Logo")),
        ("fixed", "breathing", "colour_cycle"),
    ),
)
# The zone name whose zone takes the separate logo colour.
LOGO_ZONE_NAME = "Logo"


class Effect(NamedTuple):
    """One lighting effect the driver can set, as the GUI offers it."""

    key: str
    name: str
    effect_id: int
    has_speed: bool
    has_color: bool


EFFECTS: Tuple[Effect, ...] = (
    Effect("fixed", "Fixed colour", EFFECT_ID_FIXED, False, True),
    Effect("breathing", "Breathing", EFFECT_ID_BREATHING, True, True),
    Effect("colour_cycle", "Colour cycle", EFFECT_ID_COLOUR_CYCLE, True, False),
)
EFFECT_BY_KEY: Dict[str, Effect] = {effect.key: effect for effect in EFFECTS}
DEFAULT_EFFECT = EFFECTS[0]

# The GUI's speed steps (1 slowest .. 5 fastest) and the period each one
# sends, for both animated effects.
SPEED_STEPS = 5
DEFAULT_SPEED_STEP = 3
SPEED_PERIODS_MS: Dict[int, int] = {1: 8000, 2: 5000, 3: 3000, 4: 2000, 5: 1000}

# Descriptor fields of the separate logo colour (both default to absent:
# off, and the main colour).
LOGO_COLOR_ENABLED_FIELD = "lighting_logo_color_enabled"
LOGO_COLOR_FIELD = "lighting_logo_color"
LOGO_COLOR_TITLE = "Separate logo colour"
LOGO_COLOR_DESCRIPTION = "Give the logo its own colour. The DPI lights keep the main colour."
LOGO_COLOR_ROW_TITLE = "Logo colour"

# Per-channel gain applied after the sRGB curve. Set by eye on a G502 HERO:
# raw white has a slight green tint, which green at 88% takes out, and hot
# pink then looks like its screen colour.
LOGITECH_WHITE_BALANCE: Tuple[float, float, float] = (1.0, 0.88, 1.0)

# Descriptor field and values for the lighting layout. The default (whole)
# is stored as an absent field.
LAYOUT_FIELD = "lighting_layout"
LAYOUT_WHOLE = "whole"  # default: one colour on every zone
LAYOUT_ZONES = "zones"  # one colour per zone during sync

DEVICE_TYPE = "logitech_hidpp"
VENDOR_NAME = "Logitech"

NOT_FOUND_MESSAGE = "The Logitech mouse was not found on USB. Check that it is plugged in."
OPEN_FAILED_MESSAGE = "Could not open the Logitech mouse's lighting interface. Unplug it, plug it back in and try again."
LOST_MESSAGE = "Lost the connection to the Logitech mouse. LumiSync reconnects on the next command."
NO_REPLY_MESSAGE = "The Logitech mouse did not answer. LumiSync reconnects on the next command."
NO_LIGHTING_MESSAGE = "This mouse has no supported lighting feature."
UNSUPPORTED_MESSAGE = "Logitech mouse lighting can only be controlled on Windows and Linux."
UNKNOWN_DEVICE_MESSAGE = "This Logitech device has not been verified with LumiSync yet."

# Linux: /dev/hidraw* is root-only by default. This rule (shipped in
# packaging/linux/60-lumisync-pc-lighting.rules) gives the logged-in user
# access to the supported mouse only.
UDEV_RULE = (
    'KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="046d", '
    'ATTRS{idProduct}=="c08b", TAG+="uaccess"'
)
UDEV_RULE_FILENAME = "60-lumisync-pc-lighting.rules"


def permission_denied_message(path: str) -> str:
    """The Linux "install the udev rule" explanation for ``path``."""
    return (
        f"LumiSync is not allowed to open {path}, the Logitech mouse's lighting "
        "interface: on Linux only root can use it until a udev rule grants "
        f"access. Save this line in /etc/udev/rules.d/{UDEV_RULE_FILENAME}:\n"
        f"{UDEV_RULE}\n"
        "then run 'sudo udevadm control --reload' and 'sudo udevadm trigger' "
        "(or unplug the mouse and plug it back in), and try again."
    )


_CAPABILITIES = DeviceCapabilities(
    transport="usb",
    segment_count=1,
    supports_power=True,
    supports_brightness=True,
    supports_color=True,
    supports_segments=True,
    supports_white=False,
    supports_streaming=True,
    max_update_hz=30.0,
)


def _supported(vendor_id: int, product_id: int) -> Optional[SupportedDevice]:
    for entry in SUPPORTED_DEVICES:
        if (entry.vendor_id, entry.product_id) == (vendor_id, product_id):
            return entry
    return None


def device_id_for(vendor_id: int, product_id: int) -> str:
    return f"hidpp-{vendor_id:04x}-{product_id:04x}"


def descriptor_for(vendor_id: int, product_id: int) -> Dict[str, Any]:
    """The saved device descriptor of one supported mouse (from the table only)."""
    entry = _supported(vendor_id, product_id)
    if entry is None:
        raise ValueError(f"USB device {vendor_id:04X}:{product_id:04X} is not a supported Logitech mouse.")
    return {
        "device_id": device_id_for(vendor_id, product_id),
        "name": entry.name,
        "model": f"{VENDOR_NAME} {entry.name}",
        "transport": "usb",
        "type": DEVICE_TYPE,
        "device_kind": entry.kind,
        "usb_vendor_id": vendor_id,
        "usb_product_id": product_id,
    }


def _hidpp_collection(infos: Any) -> Optional[Any]:
    """The HID++ long collection among a device's HID collections."""
    for info in infos:
        if int(info.usage_page) == HIDPP_USAGE_PAGE and int(info.usage) == HIDPP_LONG_USAGE:
            return info
    return None


def discover() -> List[Dict[str, Any]]:
    """Descriptors of the supported Logitech mice on this PC. Never raises.

    Lists HID collections only: nothing is opened for IO and no report is sent.
    """
    found: List[Dict[str, Any]] = []
    for entry in SUPPORTED_DEVICES:
        vendor_id, product_id = entry.vendor_id, entry.product_id
        try:
            if _hidpp_collection(_hid.enumerate(vendor_id, product_id)) is None:
                continue
            found.append(descriptor_for(vendor_id, product_id))
        except Exception as exc:
            logger.warning("Could not list Logitech device %04X:%04X (%s)", vendor_id, product_id, type(exc).__name__)
    return found


def _device_ids(device: Any) -> Tuple[int, int]:
    """The vendor/product pair a descriptor names (the first supported one by default)."""
    default = SUPPORTED_DEVICES[0][:2]
    if not isinstance(device, Mapping):
        return default
    try:
        return int(device.get("usb_vendor_id", default[0])), int(device.get("usb_product_id", default[1]))
    except (TypeError, ValueError):
        return default


def device_present(device: Optional[DeviceLike] = None) -> bool:
    """Whether the mouse is attached. Lists HID collections only."""
    vendor_id, product_id = _device_ids(device)
    if _supported(vendor_id, product_id) is None:
        return False
    try:
        return _hidpp_collection(_hid.enumerate(vendor_id, product_id)) is not None
    except Exception:
        return False


# ----------------------------------------------------------- shared handles


class HidppError(RuntimeError):
    """The mouse answered a request with a HID++ error code."""

    def __init__(self, code: int, request: Request) -> None:
        self.code = code
        self.request = request
        name = ERRORS.get(code, f"error 0x{code:02X}")
        super().__init__(
            f"The Logitech mouse refused a lighting command ({name}, feature "
            f"0x{request.feature_id:04X} function {request.function})."
        )


class _NoReply(Exception):
    """No matching reply within the timeout."""


Frame = Tuple[RGB, ...]


class _Controller:
    """One physical mouse, shared by every adapter that drives it."""

    def __init__(self, vendor_id: int, product_id: int) -> None:
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.lock = threading.RLock()
        self.device: Any = None
        # Feature index -> feature id, for the send-point check. Index 0 is root.
        self.feature_ids: Dict[int, int] = {0: FEATURE_ROOT}
        self.led_index = 0
        self.zone_count = 0
        # Each zone's reported location (0 where it could not be read).
        self.zone_locations: Tuple[int, ...] = ()
        # Each zone's effect id -> effect index, read with getZoneEffectInfo
        # when the handle opens (never while streaming).
        self.effect_indexes: EffectIndexes = ()
        # Effects already warned about as missing (logged once each).
        self.warned_effects: Set[str] = set()
        self.protocol: Tuple[int, int] = (0, 0)
        self.onboard_mode: Optional[int] = None
        self.last_frame: Optional[Frame] = None
        self.last_frame_at = 0.0
        self.users: Set[int] = set()
        # Optimistic lighting state.
        self.color: RGB = (255, 255, 255)
        self.brightness = 100
        self.power_on: Optional[bool] = None
        # Whether a manual colour, brightness or power command was applied
        # since this controller was made; until then the state above is only
        # the default (white, 100%) and is never "restored" after sync.
        self.manual_known = False
        # Adapters streaming sync frames now.
        self.streams: Set[int] = set()


_controllers: Dict[Tuple[int, int], _Controller] = {}
_controllers_lock = threading.Lock()


def _controller_for(vendor_id: int, product_id: int) -> _Controller:
    key = (vendor_id, product_id)
    with _controllers_lock:
        controller = _controllers.get(key)
        if controller is None:
            controller = _controllers[key] = _Controller(vendor_id, product_id)
        return controller


# ------------------------------------------------------- per-device options


def layout(device: Optional[DeviceLike]) -> str:
    """The descriptor's layout (whole mouse unless per zone was chosen)."""
    return LAYOUT_ZONES if _field(device, LAYOUT_FIELD) == LAYOUT_ZONES else LAYOUT_WHOLE


def _opened_zones(device: Optional[DeviceLike]) -> Optional[Tuple[int, Tuple[int, ...]]]:
    """Zone count and locations read from the mouse, if its handle was opened."""
    with _controllers_lock:
        controller = _controllers.get(_device_ids(device))
    if controller is None or not controller.zone_count:
        return None
    return controller.zone_count, controller.zone_locations


def zone_names(device: Optional[DeviceLike]) -> Tuple[str, ...]:
    """What each zone lights, in the mouse's zone order.

    After the mouse was opened the count and locations are its own; before
    that, the model's known zones from :data:`SUPPORTED_DEVICES`.
    """
    entry = _supported(*_device_ids(device))
    known = dict(entry.zones) if entry is not None else {}
    opened = _opened_zones(device)
    if opened is None:
        if entry is not None and entry.zones:
            return tuple(name for _location, name in entry.zones)
        return ("Zone 1",)
    count, locations = opened
    return tuple(
        known.get(locations[zone], f"Zone {zone + 1}") if zone < len(locations) else f"Zone {zone + 1}"
        for zone in range(count)
    )


def capabilities_for_layout(device: Optional[DeviceLike]) -> DeviceCapabilities:
    """Static capabilities: one segment per zone in per-zone mode, else one."""
    if layout(device) != LAYOUT_ZONES:
        return _CAPABILITIES
    return _zone_capabilities(len(zone_names(device)))


_zone_capability_cache: Dict[int, DeviceCapabilities] = {}


def _zone_capabilities(count: int) -> DeviceCapabilities:
    cap = _zone_capability_cache.get(count)
    if cap is None:
        cap = _zone_capability_cache[count] = dataclasses.replace(_CAPABILITIES, segment_count=count)
    return cap


def _name_list(names: Tuple[str, ...]) -> str:
    """``DPI lights and logo``: names after the first start lower case unless an acronym."""
    def tail(name: str) -> str:
        return name if name[1:2].isupper() else name[:1].lower() + name[1:]

    parts = [names[0]] + [tail(name) for name in names[1:]]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def wire_color(color: RGB, brightness: int, correction: str) -> RGB:
    """What goes in the packet: brightness first, then colour correction.

    The corrected colour keeps the picked colour's peak level: by eye on a
    G502 HERO the plain curve had the right colour but looked dark next to
    G HUB, and no curve was bright but washed out.
    """
    return led_color.wire_color(color, brightness, correction, LOGITECH_WHITE_BALANCE, keep_peak=True)


def _speed_step(value: Any) -> int:
    """A saved speed step 1-5; anything else is the default."""
    if isinstance(value, bool):
        return DEFAULT_SPEED_STEP
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, int) and 1 <= value <= SPEED_STEPS:
        return value
    return DEFAULT_SPEED_STEP


def effect_speed(device: Optional[DeviceLike]) -> int:
    """The descriptor's effect speed step (1 slowest .. 5 fastest; 3 by default)."""
    return _speed_step(device.get(led_color.EFFECT_SPEED_FIELD)) if isinstance(device, Mapping) else DEFAULT_SPEED_STEP


def effect_period_ms(device: Optional[DeviceLike]) -> int:
    """The period the animated effects run at for the descriptor's speed."""
    return SPEED_PERIODS_MS[effect_speed(device)]


def _opened_effect_ids(device: Optional[DeviceLike]) -> Optional[FrozenSet[int]]:
    """The effect ids every zone listed, if the mouse's handle was opened."""
    with _controllers_lock:
        controller = _controllers.get(_device_ids(device))
    if controller is None or not controller.effect_indexes:
        return None
    return _common_effect_ids(controller.effect_indexes)


def _common_effect_ids(effect_indexes: EffectIndexes) -> FrozenSet[int]:
    common = frozenset(effect_indexes[0])
    for listed in effect_indexes[1:]:
        common &= frozenset(listed)
    return common


def offered_effects(device: Optional[DeviceLike]) -> Tuple[Effect, ...]:
    """The effects offered for the mouse: the model's confirmed ones.

    Once the mouse was opened, an effect some zone did not list is left out
    (fixed colour always stays, as the fallback).
    """
    entry = _supported(*_device_ids(device))
    keys = entry.effects if entry is not None else (DEFAULT_EFFECT.key,)
    listed = _opened_effect_ids(device)
    return tuple(
        effect for effect in EFFECTS
        if effect is DEFAULT_EFFECT
        or (effect.key in keys and (listed is None or effect.effect_id in listed))
    )


def effect_key(device: Optional[DeviceLike]) -> str:
    """The descriptor's effect key, when the model has it (fixed colour otherwise)."""
    key = _field(device, led_color.EFFECT_FIELD)
    entry = _supported(*_device_ids(device))
    keys = entry.effects if entry is not None else (DEFAULT_EFFECT.key,)
    return key if key in EFFECT_BY_KEY and key in keys else DEFAULT_EFFECT.key


def logo_color_enabled(device: Optional[DeviceLike]) -> bool:
    """Whether the logo has its own colour (only a true flag turns it on)."""
    return isinstance(device, Mapping) and device.get(LOGO_COLOR_ENABLED_FIELD) is True


def logo_color(device: Optional[DeviceLike]) -> Optional[RGB]:
    """The saved logo colour, or None (the main colour) when absent or invalid."""
    if not isinstance(device, Mapping):
        return None
    value = device.get(LOGO_COLOR_FIELD)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    if not all(isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255 for c in value):
        return None
    return (int(value[0]), int(value[1]), int(value[2]))


def _has_logo_zone(entry: Optional[SupportedDevice]) -> bool:
    return entry is not None and any(name == LOGO_ZONE_NAME for _location, name in entry.zones)


def lighting_options(device: Optional[DeviceLike]) -> LightingOptions:
    """The mouse's per-device options: colour correction, layout, effects, logo colour."""
    entry = _supported(*_device_ids(device))
    noun = entry.kind if entry is not None else "mouse"
    names = zone_names(device)
    offered = offered_effects(device)
    current = effect_key(device)
    if current not in {effect.key for effect in offered}:
        current = DEFAULT_EFFECT.key
    effects = EffectOptions(
        choices=tuple(
            EffectChoice(key=effect.key, name=effect.name, has_speed=effect.has_speed, has_color=effect.has_color)
            for effect in offered
        ),
        effect=current,
        speed=effect_speed(device),
        default_effect=DEFAULT_EFFECT.key,
        default_speed=DEFAULT_SPEED_STEP,
        speed_steps=SPEED_STEPS,
    )
    secondary = None
    if _has_logo_zone(entry):
        secondary = SecondaryColorOption(
            enabled_field=LOGO_COLOR_ENABLED_FIELD,
            color_field=LOGO_COLOR_FIELD,
            enabled=logo_color_enabled(device),
            color=logo_color(device),
            title=LOGO_COLOR_TITLE,
            description=LOGO_COLOR_DESCRIPTION,
            color_title=LOGO_COLOR_ROW_TITLE,
        )
    return LightingOptions(
        title=f"{noun.capitalize()} lighting",
        noun=noun,
        correction_tip=f"Converts colours for the {noun}'s LEDs so white is neutral and colours look as on screen",
        layout_field=LAYOUT_FIELD,
        layout_whole=LAYOUT_WHOLE,
        layout_zones=LAYOUT_ZONES,
        layout=layout(device),
        zone_names=names,
        whole_label=f"Whole {noun}",
        whole_description="One colour on both lights" if len(names) == 2 else "One colour on every light",
        zones_description=f"{_name_list(names)}, one colour each",
        effects=effects,
        secondary_color=secondary,
    )


def _send(controller: _Controller, request: Request) -> None:
    """Write one request, re-checking it against the allow-list first."""
    packet = request.packet
    if len(packet) < 3 or controller.feature_ids.get(packet[2]) != request.feature_id:
        raise ValueError("Refusing to send a HID++ request to an unresolved feature index.")
    _check_packet(packet, request.feature_id, controller.effect_indexes)
    controller.device.write(packet, WRITE_TIMEOUT_SECONDS)


def _drain(controller: _Controller) -> None:
    """Drop input reports already waiting (late replies, notifications)."""
    for _ in range(DRAIN_LIMIT):
        if controller.device.read(READ_SIZE, 0.0) is None:
            return


def _await_reply(controller: _Controller, request: Request) -> bytes:
    """The reply to ``request``, skipping every other input report."""
    packet = request.packet
    deadline = _monotonic() + REPLY_TIMEOUT_SECONDS
    while True:
        remaining = deadline - _monotonic()
        if remaining <= 0:
            raise _NoReply()
        report = controller.device.read(READ_SIZE, remaining)
        if report is None:
            raise _NoReply()
        report = bytes(report)
        if len(report) < 6 or report[0] not in (REPORT_ID_SHORT, REPORT_ID_LONG) or report[1] != packet[1]:
            continue
        if report[2] in (ERROR_INDEX, ERROR_INDEX_V1) and report[3] == packet[2] and report[4] == packet[3]:
            raise HidppError(report[5], request)
        if report[0] == REPORT_ID_LONG and report[2] == packet[2] and report[3] == packet[3]:
            return report


def _exchange(controller: _Controller, request: Request) -> bytes:
    """Send a request and wait for its reply; one retry on timeout or "busy".

    Raises RuntimeError when the mouse stays silent or busy, :class:`HidppError`
    for any other error reply, and :class:`OSError` from the HID layer.
    """
    for attempt in (1, 2):
        _drain(controller)
        _send(controller, request)
        try:
            return _await_reply(controller, request)
        except _NoReply:
            if attempt == 2:
                raise RuntimeError(NO_REPLY_MESSAGE) from None
        except HidppError as exc:
            if exc.code != ERROR_BUSY or attempt == 2:
                raise
    raise AssertionError("unreachable")


def _drop_locked(controller: _Controller) -> None:
    device, controller.device = controller.device, None
    controller.last_frame = None
    if device is not None:
        try:
            device.close()
        except Exception:
            logger.debug("Closing the Logitech handle failed", exc_info=True)


def _resolve(controller: _Controller, feature_id: int) -> int:
    """This mouse's index of ``feature_id`` (0 if absent), remembered for the send check."""
    reply = _exchange(controller, build_get_feature(feature_id))
    index = reply[4]
    if index:
        controller.feature_ids[index] = feature_id
    return index


def _open_locked(controller: _Controller) -> None:
    """Find and open the mouse, check its protocol and find its lighting feature."""
    if controller.device is not None:
        return
    try:
        info = _hidpp_collection(_hid.enumerate(controller.vendor_id, controller.product_id))
    except hid_feature.HidUnsupportedPlatform as exc:
        raise RuntimeError(UNSUPPORTED_MESSAGE) from exc
    except OSError as exc:
        raise RuntimeError(NOT_FOUND_MESSAGE) from exc
    if info is None:
        raise RuntimeError(NOT_FOUND_MESSAGE)
    stream = _hid.HidStream(info.path)
    try:
        stream.open()
    except hid_feature.HidUnsupportedPlatform as exc:
        raise RuntimeError(UNSUPPORTED_MESSAGE) from exc
    except PermissionError as exc:
        if sys.platform == "linux":
            raise RuntimeError(permission_denied_message(info.path)) from exc
        raise RuntimeError(OPEN_FAILED_MESSAGE) from exc
    except OSError as exc:
        raise RuntimeError(OPEN_FAILED_MESSAGE) from exc
    controller.device = stream
    controller.feature_ids = {0: FEATURE_ROOT}
    controller.effect_indexes = ()  # Read again below; nothing is set before.
    controller.last_frame = None
    try:
        reply = _exchange(controller, build_ping())
        protocol = (reply[4], reply[5])
        if protocol < MIN_PROTOCOL or reply[6] != PING_MARKER:
            raise RuntimeError(
                f"This Logitech mouse speaks HID++ {protocol[0]}.{protocol[1]}; LumiSync needs HID++ 2.0 or later."
            )
        led_index = _resolve(controller, FEATURE_COLOR_LED_EFFECTS)
        if not led_index:
            raise RuntimeError(NO_LIGHTING_MESSAGE)
        zone_count = _exchange(controller, build_get_led_info(led_index))[4]
        if not zone_count:
            raise RuntimeError(NO_LIGHTING_MESSAGE)
        zone_locations, effect_indexes = _read_zones(controller, led_index, zone_count)
        onboard_mode = _read_onboard_mode(controller)
    except OSError as exc:
        _drop_locked(controller)
        raise RuntimeError(LOST_MESSAGE) from exc
    except BaseException:
        _drop_locked(controller)
        raise
    controller.protocol = protocol
    controller.led_index = led_index
    controller.zone_count = zone_count
    controller.zone_locations = zone_locations
    controller.effect_indexes = effect_indexes
    controller.onboard_mode = onboard_mode
    logger.info(
        "Logitech %04X:%04X: HID++ %d.%d, lighting feature at index %d, %d zone(s) at %s, effects %s, "
        "onboard mode %s",
        controller.vendor_id, controller.product_id, protocol[0], protocol[1], led_index, zone_count,
        ", ".join(f"0x{location:04X}" for location in zone_locations),
        "; ".join(
            ", ".join(f"0x{effect_id:04X}@{index}" for effect_id, index in sorted(listed.items(), key=lambda i: i[1]))
            for listed in effect_indexes
        ),
        "unknown" if onboard_mode is None else onboard_mode,
    )


def _read_zones(
    controller: _Controller, led_index: int, zone_count: int
) -> Tuple[Tuple[int, ...], EffectIndexes]:
    """Each zone's location (for its name) and its effect id -> index map (read only).

    getZoneInfo gives the location and the effect count; getZoneEffectInfo
    then gives the id at each index. A location the mouse does not give is
    0. A zone whose effect list cannot be read at all keeps fixed colour at
    :data:`FALLBACK_FIXED_INDEX` (what was sent before the list was read) and
    nothing else.
    """
    locations: List[int] = []
    indexes: List[Dict[int, int]] = []
    for zone in range(zone_count):
        listed: Dict[int, int] = {}
        location = 0
        try:
            reply: Optional[bytes] = _exchange(controller, build_get_zone_info(led_index, zone))
        except HidppError:
            reply = None
        if reply is not None and reply[4] == zone:
            location = int.from_bytes(reply[5:7], "big")
            for index in range(reply[7]):
                try:
                    info = _exchange(controller, build_get_zone_effect_info(led_index, zone, index))
                except HidppError:
                    continue
                if info[4] == zone and info[5] == index:
                    listed.setdefault(int.from_bytes(info[6:8], "big"), index)
        if not listed:
            logger.warning(
                "Logitech %04X:%04X: could not read zone %d's lighting effects; only fixed colour is used there",
                controller.vendor_id, controller.product_id, zone,
            )
            listed = {EFFECT_ID_FIXED: FALLBACK_FIXED_INDEX}
        locations.append(location)
        indexes.append(listed)
    return tuple(locations), tuple(indexes)


def _read_onboard_mode(controller: _Controller) -> Optional[int]:
    """The onboard-profile mode (read only; 1 onboard, 2 host), for the log."""
    try:
        index = _resolve(controller, FEATURE_ONBOARD_PROFILES)
        if not index:
            return None
        return _exchange(controller, build_get_onboard_mode(index))[4]
    except (HidppError, RuntimeError):
        return None


def _write_frame_locked(controller: _Controller, frame: Frame) -> None:
    """Send one colour per zone, unless the same colours went out a moment ago."""
    _open_locked(controller)
    frame = tuple((_channel(c[0]), _channel(c[1]), _channel(c[2])) for c in frame)
    now = _monotonic()
    if controller.last_frame == frame and now - controller.last_frame_at < REPEAT_AFTER_SECONDS:
        return
    controller.last_frame = None
    _exchange_all_locked(
        controller,
        (build_set_zone_color(controller.led_index, zone, color, controller.effect_indexes)
         for zone, color in enumerate(frame)),
    )
    controller.last_frame = frame
    controller.last_frame_at = now


def _exchange_all_locked(controller: _Controller, requests: Iterable[Request]) -> None:
    """Send each request in turn; a lost or silent mouse drops the handle."""
    try:
        for request in requests:
            _exchange(controller, request)
    except OSError as exc:
        _drop_locked(controller)
        raise RuntimeError(LOST_MESSAGE) from exc
    except HidppError:
        raise
    except RuntimeError:
        # No reply even after the retry: start from a fresh handle next time.
        _drop_locked(controller)
        raise


def _whole_frame(controller: _Controller, color: RGB) -> Frame:
    return (color,) * controller.zone_count


def _resolved_effect(controller: _Controller, device: Optional[DeviceLike]) -> Effect:
    """The descriptor's effect if every zone listed it; else fixed colour (warned once)."""
    effect = EFFECT_BY_KEY[effect_key(device)]
    if effect is DEFAULT_EFFECT or effect.effect_id in _common_effect_ids(controller.effect_indexes):
        return effect
    if effect.key not in controller.warned_effects:
        controller.warned_effects.add(effect.key)
        logger.warning(
            "Logitech %04X:%04X: not every zone lists the %s effect; showing a fixed colour instead",
            controller.vendor_id, controller.product_id, effect.name.lower(),
        )
    return DEFAULT_EFFECT


def _logo_zones(controller: _Controller) -> Tuple[int, ...]:
    """The zones whose reported location the model table names the logo."""
    entry = _supported(controller.vendor_id, controller.product_id)
    if entry is None:
        return ()
    logo = {location for location, name in entry.zones if name == LOGO_ZONE_NAME}
    return tuple(zone for zone, location in enumerate(controller.zone_locations) if location in logo)


def _show_manual_locked(controller: _Controller, device: Optional[DeviceLike], color: RGB, brightness: int) -> None:
    """The user's manual lighting: the chosen effect with the main (and logo) colour.

    Brightness scales the colour sent, for fixed colour and breathing alike.
    Colour cycle takes no colour and its intensity byte stays 0x64 (the
    allow-list's only value), so brightness does not change it.
    """
    _open_locked(controller)
    effect = _resolved_effect(controller, device)
    correction = color_correction(device)
    zone_colors = list(_whole_frame(controller, wire_color(color, brightness, correction)))
    if effect.has_color and logo_color_enabled(device):
        logo = wire_color(logo_color(device) or color, brightness, correction)
        for zone in _logo_zones(controller):
            zone_colors[zone] = logo
    if effect.effect_id == EFFECT_ID_FIXED:
        _write_frame_locked(controller, tuple(zone_colors))
        return
    period = effect_period_ms(device)
    led_index, indexes = controller.led_index, controller.effect_indexes
    if effect.effect_id == EFFECT_ID_BREATHING:
        requests = (
            build_set_zone_breathing(led_index, zone, zone_color, period, indexes)
            for zone, zone_color in enumerate(zone_colors)
        )
    else:
        requests = (build_set_zone_colour_cycle(led_index, zone, period, indexes) for zone in range(len(zone_colors)))
    # An animated effect is not a frame: the next fixed colour always goes out.
    controller.last_frame = None
    _exchange_all_locked(controller, requests)


def close_all() -> None:
    """Close every shared handle (tests, shutdown). Lighting is left as is."""
    with _controllers_lock:
        controllers = list(_controllers.values())
    for controller in controllers:
        with controller.lock:
            controller.users.clear()
            _drop_locked(controller)


# ------------------------------------------------------------------ adapter


class LogitechHidppAdapter(TransportAdapter):
    """Whole-mouse or per-zone colour for Logitech HID++ 2.0 mice.

    Construction and :attr:`capabilities` never touch the hardware; the
    shared handle opens on the first command. The remembered colour and
    brightness are the user's (sRGB) values; colour correction only changes
    the bytes sent.
    """

    def __init__(self, device: DeviceLike) -> None:
        super().__init__(device)
        self._ids = _device_ids(device)
        self._registered = False

    @staticmethod
    def capabilities_for(device: Optional[DeviceLike] = None) -> DeviceCapabilities:
        return capabilities_for_layout(device)

    @property
    def capabilities(self) -> DeviceCapabilities:
        return capabilities_for_layout(self.device)

    def update_device(self, device: DeviceLike) -> None:
        """Use an edited descriptor (layout, colour correction, effect, logo colour) from the next command.

        The shared handle and lighting state are kept. A descriptor for
        another mouse is ignored; that needs a new adapter.
        """
        if _device_ids(device) == self._ids:
            self.device = device

    def _controller(self) -> _Controller:
        if _supported(*self._ids) is None:
            raise RuntimeError(UNKNOWN_DEVICE_MESSAGE)
        controller = _controller_for(*self._ids)
        if not self._registered:
            with controller.lock:
                controller.users.add(id(self))
            self._registered = True
        return controller

    def _paint(self, controller: _Controller, color: RGB, brightness: int) -> None:
        """One colour on every zone (in either layout)."""
        _open_locked(controller)
        wire = wire_color(color, brightness, color_correction(self.device))
        _write_frame_locked(controller, _whole_frame(controller, wire))

    def _show_manual(self, controller: _Controller, color: RGB, brightness: int) -> None:
        """The manual effect and colours from this adapter's descriptor."""
        _show_manual_locked(controller, self.device, color, brightness)

    def set_power(self, on: bool) -> None:
        controller = self._controller()
        with controller.lock:
            if on:
                self._show_manual(controller, controller.color, controller.brightness)
            else:
                self._paint(controller, (0, 0, 0), 100)
            controller.power_on = bool(on)
            controller.manual_known = True

    def set_brightness(self, percent: int) -> None:
        level = max(0, min(100, int(percent)))
        controller = self._controller()
        with controller.lock:
            if controller.power_on is not False:
                self._show_manual(controller, controller.color, level)
                controller.power_on = True
            controller.brightness = level
            controller.manual_known = True

    def set_color(self, r: int, g: int, b: int) -> None:
        color = (_channel(r), _channel(g), _channel(b))
        controller = self._controller()
        with controller.lock:
            if controller.power_on is not False:
                self._show_manual(controller, color, controller.brightness)
                controller.power_on = True
            controller.color = color
            controller.manual_known = True

    def set_segments(self, colors: List[RGB]) -> None:
        # Sync frames are always fixed colours (whatever the manual effect),
        # with no effect-list reads: the zone effect indexes were read when
        # the handle opened.
        # Whole mouse: the frame's average. Per zone: the frame resized to
        # the zone count, one colour per zone. The manual colour is kept
        # either way, so turning the mouse on after sync brings it back.
        if not colors:
            return
        controller = self._controller()
        with controller.lock:
            if layout(self.device) != LAYOUT_ZONES:
                self._paint(controller, average_color(colors), controller.brightness)
            else:
                _open_locked(controller)
                correction = color_correction(self.device)
                resized = resample_colors_to_count(list(colors), controller.zone_count)
                frame = tuple(wire_color(color, controller.brightness, correction) for color in resized)
                _write_frame_locked(controller, frame)
            controller.power_on = True

    def reapply(self) -> None:
        """Show the remembered lighting again with the current options.

        Used after a layout, colour-correction, effect or logo-colour change
        so it is visible at once. Sends nothing unless the handle is already
        open and the mouse is on (an option change never opens the mouse by
        itself), nor while a sync stream runs (the stream's end shows it).
        """
        controller = self._controller()
        with controller.lock:
            if controller.device is None or controller.power_on is not True or controller.streams:
                return
            self._show_manual(controller, controller.color, controller.brightness)

    def begin_stream(self) -> None:
        # Open now, so a missing mouse fails the sync start with a clear message.
        controller = self._controller()
        with controller.lock:
            _open_locked(controller)
            controller.streams.add(id(self))

    def end_stream(self) -> None:
        """Show the manual effect and colours again after sync.

        Only when a manual colour, brightness or power command was applied
        since the app started (before that the remembered state is only the
        white default, so the mouse keeps the last sync colour) and the
        mouse is open and not turned off. Sends nothing otherwise.
        """
        controller = _controller_for(*self._ids)
        with controller.lock:
            if id(self) not in controller.streams:
                return
            controller.streams.discard(id(self))
            if controller.streams or not controller.manual_known:
                return
            if controller.device is None or controller.power_on is False:
                return
            try:
                self._show_manual(controller, controller.color, controller.brightness)
            except (RuntimeError, ValueError) as exc:
                logger.warning("Could not restore the Logitech mouse's lighting after sync: %s", exc)

    def close(self) -> None:
        """Stop using the shared handle; the last user closes it. Lighting is left as is."""
        if not self._registered:
            return
        self._registered = False
        controller = _controller_for(*self._ids)
        with controller.lock:
            controller.users.discard(id(self))
            controller.streams.discard(id(self))
            if not controller.users:
                _drop_locked(controller)


__all__ = [
    "ALLOWED_EFFECT_IDS",
    "ALLOWED_FUNCTIONS",
    "COLOR_CORRECTION_FIELD",
    "DEVICE_TYPE",
    "EFFECTS",
    "ERRORS",
    "LAYOUT_FIELD",
    "LAYOUT_WHOLE",
    "LAYOUT_ZONES",
    "LOGITECH_WHITE_BALANCE",
    "LOGO_COLOR_ENABLED_FIELD",
    "LOGO_COLOR_FIELD",
    "SPEED_PERIODS_MS",
    "NEVER_SEND_FEATURES",
    "SUPPORTED_DEVICES",
    "UDEV_RULE",
    "UDEV_RULE_FILENAME",
    "Effect",
    "HidppError",
    "LogitechHidppAdapter",
    "Request",
    "SupportedDevice",
    "build_get_feature",
    "build_get_led_info",
    "build_get_onboard_mode",
    "build_get_zone_effect",
    "build_get_zone_effect_info",
    "build_get_zone_info",
    "build_ping",
    "build_request",
    "build_set_zone_breathing",
    "build_set_zone_color",
    "build_set_zone_colour_cycle",
    "build_set_zone_effect",
    "capabilities_for_layout",
    "descriptor_for",
    "device_present",
    "discover",
    "effect_key",
    "effect_period_ms",
    "effect_speed",
    "layout",
    "lighting_options",
    "logo_color",
    "logo_color_enabled",
    "offered_effects",
    "permission_denied_message",
    "wire_color",
    "zone_names",
]
