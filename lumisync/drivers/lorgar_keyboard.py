"""Lorgar AZAR keyboard lighting over its vendor HID collection.

The keyboard (USB ``320F:5075``) takes 64-byte output reports on the HID
collection with usage page ``0xFF1C``, usage ``0x92``, and answers each with
a 64-byte input report on the same collection. The protocol was decoded
from a USB capture of the vendor app and confirmed on the keyboard:

* BEGIN ``04 02 00 01`` -> ``04 02 00 01 00 00 00 ff``
* SET ``04 a6 00 a5 19 00 00 00 00 MODE BRIGHT SPEED DIR MULTI R G B`` ->
  the keyboard echoes the request byte for byte
* END ``04 03 00 02`` -> ``04 03 00 02 00 00 00 ff``

each zero-padded to 64 bytes. Every change is one BEGIN, SET, END sequence;
END is sent whenever BEGIN was, even if SET failed.

SET carries one of the keyboard's 17 built-in effects (:data:`EFFECTS`; the
mode number is the effect's place in the vendor app's list, 17 is unused),
the brightness level 0-4, a speed 0-4 (0 is the fastest), a direction
(``0xFF`` the default, ``0x00`` the reverse) and a multicolour flag (1: the
effect's own colours, with R G B zero). The default effect is static, one
colour for the whole keyboard (mode ``0x06``, speed ``0x02``, ``FF 00``).
Effects that take a colour use the user's colour unless multicolour is
chosen; the others always go out multicolour. Power off is static black at
level 0 whatever the effect. Colours go out as given (raw sRGB; no
correction yet).

The effect and its speed, direction and multicolour setting are per-device
options in the descriptor (``lighting_effect``, ``lighting_effect_speed``
1-5, ``lighting_effect_reversed``, ``lighting_effect_multicolor``; see
:func:`effect_settings`), read when the adapter is built and in
:meth:`~LorgarKeyboardAdapter.update_device`, and shown on the PC page by
:func:`lighting_options`.

Safety: :func:`_check_packet` allows exactly these three packet shapes, with
every SET byte inside the ranges above, and is run when a packet is built
and again at the single send point (:func:`_send`). Nothing else (command
``0x08`` included) can be sent. Discovery only lists HID collections; it
never opens the keyboard.

Every SET is saved to the keyboard's internal memory (verified: it survives
a replug even without END), there is no command that is not saved, and that
memory has a limited number of writes. So Monitor and Music sync is off by
default: the capabilities say ``supports_streaming=False`` (device pickers,
groups, per-light levels and the sync start check all filter on that flag)
and the adapter refuses :meth:`~LorgarKeyboardAdapter.begin_stream` and
:meth:`~LorgarKeyboardAdapter.set_segments`, until the user turns on
"Monitor and Music sync" on the keyboard's card (``lighting_sync_enabled``,
after a warning about the wear). Then the whole keyboard follows one colour
(the frame's average, sent as static at the keyboard's brightness level),
and updates are limited to one per ``lighting_sync_interval`` (10 s by
default, 2 s, or 0.25 s; see :data:`SYNC_INTERVALS`) and only when the colour
moved by at least :data:`SYNC_COLOR_THRESHOLD` on some channel. A frame that
is not due is dropped at once, never queued or waited for. When the stream
ends the user's own lighting goes back with one write, but only when a
manual colour, brightness or power command was given since the app started
(before that the remembered state is only the white default, so the
keyboard keeps the last sync colour and nothing is written).

Manual writes are rate limited too: at most one sequence per
:data:`MIN_WRITE_INTERVAL_SECONDS`, a state equal to the last one sent
successfully is skipped, and the most recently requested state always goes
out (trailing edge). The limiter waits on the calling thread, which is a
background worker for every GUI control (the device controller runs adapters
through ``start_task``), never the GUI thread.
:meth:`~LorgarKeyboardAdapter.close` wakes a waiting writer at once.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Set, Tuple

from ..device import DeviceLike
from ..utils.logging import get_logger
from . import hid_feature, led_color
from .base import RGB, DeviceCapabilities, TransportAdapter
from .led_color import EffectChoice, EffectOptions, LightingOptions, SyncChoice, SyncOptions

logger = get_logger("lumisync_lorgar")

# The HID layer in use. Tests replace it with a fake keyboard.
_hid: Any = hid_feature

# ------------------------------------------------------------------ protocol

PACKET_LENGTH = 64
READ_SIZE = 64

LORGAR_USAGE_PAGE = 0xFF1C
LORGAR_USAGE = 0x92

REPORT_ID = 0x04
BEGIN_HEADER = bytes([0x04, 0x02, 0x00, 0x01])
END_HEADER = bytes([0x04, 0x03, 0x00, 0x02])
SET_HEADER = bytes([0x04, 0xA6, 0x00, 0xA5])
# Bytes 4-8 of a SET packet, always the same.
SET_FIXED = bytes([0x19, 0x00, 0x00, 0x00, 0x00])

MODE_STATIC = 0x06
# The speed byte of static and of every effect without a speed (the vendor
# app's value), and the fastest/slowest wire speeds: 0 is the fastest.
SPEED = 0x02
FASTEST_SPEED = 0x00
SLOWEST_SPEED = 0x04
# Direction byte: 0xFF is the default direction (Wave: left to right).
DIRECTION_FORWARD = 0xFF
DIRECTION_REVERSED = 0x00
MAX_BRIGHTNESS_LEVEL = 4

# Offsets inside a SET packet.
MODE_OFFSET = 9
BRIGHTNESS_OFFSET = 10
SPEED_OFFSET = 11
DIRECTION_OFFSET = 12
MULTICOLOR_OFFSET = 13
COLOR_OFFSET = 14
SET_TAIL_OFFSET = 17

# The GUI's speed steps: 1 slowest .. 5 fastest; 3 is the vendor app's speed.
SPEED_STEPS = 5
DEFAULT_SPEED_STEP = 3


class Effect(NamedTuple):
    """One built-in effect: its SET mode byte and what it takes.

    ``directions`` labels the default (``0xFF``) and the reversed (``0x00``)
    direction, or is empty when the effect has no direction.
    """

    mode: int
    key: str
    name: str
    has_color: bool
    has_speed: bool
    directions: Tuple[str, ...] = ()


# The vendor app's effect list, in its order (the mode number). Only Wave
# (mode 1, both directions, speed, multicolour) and Static (mode 6) were
# confirmed by eye. The direction labels of Vortex, Colour palette and
# Scrolling, and which effects take no colour (Colour palette, Stars,
# Firework, Scrolling, Acceleration) or no speed (Colour palette), are read
# from the vendor app's pages and not yet confirmed on the keyboard.
EFFECTS: Tuple[Effect, ...] = (
    Effect(1, "wave", "Wave", True, True, ("Left to right", "Right to left")),
    Effect(2, "cloud", "Cloud", True, True, ("Left to right", "Right to left")),
    Effect(3, "vortex", "Vortex", True, True, ("Counterclockwise", "Clockwise")),
    Effect(4, "colour_palette", "Colour palette", False, False, ("Outside", "Inside")),
    Effect(5, "breathing", "Breathing", True, True),
    Effect(6, "static", "Static", True, False),
    Effect(7, "reaction", "Reaction", True, True),
    Effect(8, "stone", "Stone", True, True),
    Effect(9, "traverse", "Traverse", True, True),
    Effect(10, "stars", "Stars", False, True),
    Effect(11, "firework", "Firework", False, True),
    Effect(12, "scrolling", "Scrolling", False, True, ("Bottom", "Top")),
    Effect(13, "wave_motion", "Wave motion", True, True),
    Effect(14, "cartoon", "Cartoon", True, True),
    Effect(15, "rain", "Rain", True, True),
    Effect(16, "scanning", "Scanning", True, True),
    Effect(18, "acceleration", "Acceleration", False, True),
)
EFFECT_BY_MODE: Dict[int, Effect] = {effect.mode: effect for effect in EFFECTS}
EFFECT_BY_KEY: Dict[str, Effect] = {effect.key: effect for effect in EFFECTS}
ALLOWED_MODES = frozenset(EFFECT_BY_MODE)
DEFAULT_EFFECT = EFFECT_BY_MODE[MODE_STATIC]

REPLY_TIMEOUT_SECONDS = 0.5
WRITE_TIMEOUT_SECONDS = 0.5
# Stale input reports read and dropped before each sequence at most.
DRAIN_LIMIT = 64

# At most one BEGIN/SET/END sequence per this many seconds.
MIN_WRITE_INTERVAL_SECONDS = 0.25

# Opt-in sync: seconds between stream writes per saved interval key. Every
# write wears the keyboard's memory, so the default is the slowest.
SYNC_INTERVALS: Dict[str, float] = {"10s": 10.0, "2s": 2.0, "fast": 0.25}
DEFAULT_SYNC_INTERVAL = "10s"
SYNC_INTERVAL_LABELS: Tuple[Tuple[str, str], ...] = (
    ("10s", "Every 10 seconds (least wear)"),
    ("2s", "Every 2 seconds"),
    ("fast", "Up to 4 a second (most wear)"),
)
# A stream frame is written only when some channel moved at least this much
# since the last colour the stream sent.
SYNC_COLOR_THRESHOLD = 24

SYNC_DESCRIPTION = (
    "Off by default. This keyboard saves every colour change to its internal memory, "
    "which wears out with use."
)
SYNC_CONFIRM_TITLE = "Allow sync for this keyboard?"
SYNC_CONFIRM_TEXT = (
    "This keyboard saves every colour change to its internal memory. That memory has a "
    "limited number of writes, and sync sends many changes. Over time this can wear the "
    "keyboard out, and it may stop saving its lighting and settings. LumiSync limits how "
    "often it updates the keyboard to slow this down. The whole keyboard follows one colour."
)


def _real_wait(stop: threading.Event, seconds: float) -> bool:
    """Wait ``seconds`` unless ``stop`` is set first; True when stopped."""
    return stop.wait(seconds)


# The clock and the interruptible wait used by the rate limiter (and the
# reply deadline). Tests replace both with a fake clock.
_monotonic: Callable[[], float] = time.monotonic
_wait: Callable[[threading.Event, float], bool] = _real_wait


def _check_packet(packet: bytes) -> None:
    """Refuse anything but the three known packets (builder and send point)."""
    packet = bytes(packet)
    if len(packet) != PACKET_LENGTH:
        raise ValueError(f"Refusing to send a {len(packet)}-byte Lorgar report; it must be {PACKET_LENGTH} bytes.")
    header = packet[:4]
    if header in (BEGIN_HEADER, END_HEADER):
        if any(packet[4:]):
            raise ValueError("Refusing to send a Lorgar begin/end report with data after its header.")
        return
    if header != SET_HEADER:
        raise ValueError(f"Lorgar command {packet[:4].hex(' ')} is not allowed.")
    if packet[4:9] != SET_FIXED:
        raise ValueError("Refusing to send a Lorgar lighting report with unknown header bytes.")
    if packet[MODE_OFFSET] not in ALLOWED_MODES:
        raise ValueError(f"Lorgar lighting effect {packet[MODE_OFFSET]} is not allowed.")
    if not 0 <= packet[BRIGHTNESS_OFFSET] <= MAX_BRIGHTNESS_LEVEL:
        raise ValueError(f"Lorgar brightness level {packet[BRIGHTNESS_OFFSET]} is out of range (0-4).")
    if not FASTEST_SPEED <= packet[SPEED_OFFSET] <= SLOWEST_SPEED:
        raise ValueError(f"Lorgar effect speed {packet[SPEED_OFFSET]} is out of range (0-4).")
    if packet[DIRECTION_OFFSET] not in (DIRECTION_FORWARD, DIRECTION_REVERSED):
        raise ValueError("Refusing to send a Lorgar lighting report with an unknown direction.")
    if packet[MULTICOLOR_OFFSET] not in (0, 1):
        raise ValueError("Refusing to send a Lorgar lighting report with an unknown multicolour flag.")
    if packet[MULTICOLOR_OFFSET] == 1 and any(packet[COLOR_OFFSET:SET_TAIL_OFFSET]):
        raise ValueError("Refusing to send a multicolour Lorgar lighting report with a colour.")
    if any(packet[SET_TAIL_OFFSET:]):
        raise ValueError("Refusing to send a Lorgar lighting report with data after the colour.")


def _padded(data: bytes) -> bytes:
    return bytes(data) + bytes(PACKET_LENGTH - len(data))


def _channel(value: Any) -> int:
    return max(0, min(255, int(value)))


def build_begin() -> bytes:
    packet = _padded(BEGIN_HEADER)
    _check_packet(packet)
    return packet


def build_end() -> bytes:
    packet = _padded(END_HEADER)
    _check_packet(packet)
    return packet


def build_set(mode: int, level: int, speed: int, direction: int, multicolor: Any, color: RGB) -> bytes:
    """A SET packet, given in wire values.

    ``mode`` one of :data:`EFFECTS`, ``level`` 0-4, ``speed`` 0 (fastest) to
    4, ``direction`` ``0xFF`` or ``0x00``, ``multicolor`` 0/1 (with 1 the
    colour must be black). Anything else is refused.
    """
    mode, level, speed, direction = int(mode), int(level), int(speed), int(direction)
    if not 0 <= level <= MAX_BRIGHTNESS_LEVEL:
        raise ValueError(f"Lorgar brightness level {level} is out of range (0-4).")
    for name, value in (("effect", mode), ("speed", speed), ("direction", direction)):
        if not 0 <= value <= 0xFF:
            raise ValueError(f"Lorgar {name} {value} is out of range.")
    flag = int(multicolor)
    if not 0 <= flag <= 0xFF:
        raise ValueError(f"Lorgar multicolour flag {flag} is out of range.")
    r, g, b = (_channel(value) for value in color)
    packet = _padded(
        SET_HEADER + SET_FIXED + bytes([mode, level, speed, direction, flag, r, g, b])
    )
    _check_packet(packet)
    return packet


def build_set_static(level: int, color: RGB) -> bytes:
    """The static-colour SET packet: brightness ``level`` 0-4, one colour."""
    return build_set(MODE_STATIC, level, SPEED, DIRECTION_FORWARD, 0, color)


def speed_byte(step: Any) -> int:
    """The wire speed for a GUI speed step (1 slowest .. 5 fastest): ``5 - step``."""
    step = max(1, min(SPEED_STEPS, int(step)))
    return SPEED_STEPS - step


def brightness_level(percent: Any) -> int:
    """The keyboard's level (0-4) for a 0-100% brightness: 0 only at 0%."""
    percent = max(0, min(100, int(percent)))
    if percent == 0:
        return 0
    return min(MAX_BRIGHTNESS_LEVEL, max(1, round(percent / 25)))


# ------------------------------------------------------------------ devices


class SupportedDevice(NamedTuple):
    vendor_id: int
    product_id: int
    name: str
    kind: str


# Keyboards verified on real hardware.
SUPPORTED_DEVICES: Tuple[SupportedDevice, ...] = (
    SupportedDevice(0x320F, 0x5075, "Lorgar AZAR", "keyboard"),
)

DEVICE_TYPE = "lorgar_keyboard"

NOT_FOUND_MESSAGE = "The Lorgar keyboard was not found on USB. Check that it is plugged in."
OPEN_FAILED_MESSAGE = "Could not open the Lorgar keyboard's lighting interface. Unplug it, plug it back in and try again."
LOST_MESSAGE = "Lost the connection to the Lorgar keyboard. LumiSync reconnects on the next command."
NO_REPLY_MESSAGE = "The Lorgar keyboard did not answer. LumiSync reconnects on the next command."
MISMATCH_MESSAGE = "The Lorgar keyboard did not confirm the new lighting. LumiSync reconnects on the next command."
UNSUPPORTED_MESSAGE = "Lorgar keyboard lighting can only be controlled on Windows and Linux."
UNKNOWN_DEVICE_MESSAGE = "This Lorgar device has not been verified with LumiSync yet."
NO_SYNC_MESSAGE = (
    "Sync is off for the Lorgar keyboard. To use it, turn on \"Monitor and Music sync\" "
    "on the keyboard's card on the PC page."
)
CLOSED_MESSAGE = "The Lorgar keyboard's connection was closed before the change was sent."

UDEV_RULE = (
    'KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="320f", '
    'ATTRS{idProduct}=="5075", TAG+="uaccess"'
)
UDEV_RULE_FILENAME = "60-lumisync-pc-lighting.rules"


def permission_denied_message(path: str) -> str:
    """The Linux "install the udev rule" explanation for ``path``."""
    return (
        f"LumiSync is not allowed to open {path}, the Lorgar keyboard's lighting "
        "interface: on Linux only root can use it until a udev rule grants "
        f"access. Save this line in /etc/udev/rules.d/{UDEV_RULE_FILENAME}:\n"
        f"{UDEV_RULE}\n"
        "then run 'sudo udevadm control --reload' and 'sudo udevadm trigger' "
        "(or unplug the keyboard and plug it back in), and try again."
    )


_CAPABILITIES = DeviceCapabilities(
    transport="usb",
    segment_count=1,
    supports_power=True,
    supports_brightness=True,
    supports_color=True,
    supports_segments=False,
    supports_white=False,
    # Manual only unless sync is turned on: each change is saved to the
    # keyboard's memory.
    supports_streaming=False,
    max_update_hz=1.0 / MIN_WRITE_INTERVAL_SECONDS,
)
# With sync turned on: one colour (the session averages the frame for a
# device without segments); the stream limits the writes itself.
_SYNC_CAPABILITIES = replace(_CAPABILITIES, supports_streaming=True)


def _supported(vendor_id: int, product_id: int) -> Optional[SupportedDevice]:
    for entry in SUPPORTED_DEVICES:
        if (entry.vendor_id, entry.product_id) == (vendor_id, product_id):
            return entry
    return None


def device_id_for(vendor_id: int, product_id: int) -> str:
    return f"lorgar-{vendor_id:04x}-{product_id:04x}"


def descriptor_for(vendor_id: int, product_id: int) -> Dict[str, Any]:
    """The saved device descriptor of one supported keyboard (from the table only)."""
    entry = _supported(vendor_id, product_id)
    if entry is None:
        raise ValueError(f"USB device {vendor_id:04X}:{product_id:04X} is not a supported Lorgar keyboard.")
    return {
        "device_id": device_id_for(vendor_id, product_id),
        "name": entry.name,
        "model": entry.name,
        "transport": "usb",
        "type": DEVICE_TYPE,
        "device_kind": entry.kind,
        "usb_vendor_id": vendor_id,
        "usb_product_id": product_id,
    }


def _lighting_collection(infos: Any) -> Optional[Any]:
    """The vendor lighting collection among a device's HID collections."""
    for info in infos:
        if int(info.usage_page) == LORGAR_USAGE_PAGE and int(info.usage) == LORGAR_USAGE:
            return info
    return None


def discover() -> List[Dict[str, Any]]:
    """Descriptors of the supported Lorgar keyboards on this PC. Never raises.

    Lists HID collections only: nothing is opened and no report is sent.
    """
    found: List[Dict[str, Any]] = []
    for entry in SUPPORTED_DEVICES:
        vendor_id, product_id = entry.vendor_id, entry.product_id
        try:
            if _lighting_collection(_hid.enumerate(vendor_id, product_id)) is None:
                continue
            found.append(descriptor_for(vendor_id, product_id))
        except Exception as exc:
            logger.warning("Could not list Lorgar device %04X:%04X (%s)", vendor_id, product_id, type(exc).__name__)
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
    """Whether the keyboard is attached. Lists HID collections only."""
    vendor_id, product_id = _device_ids(device)
    if _supported(vendor_id, product_id) is None:
        return False
    try:
        return _lighting_collection(_hid.enumerate(vendor_id, product_id)) is not None
    except Exception:
        return False


# ------------------------------------------------------- per-device options


class EffectSettings(NamedTuple):
    """The effect chosen in a descriptor, with its speed step and switches."""

    mode: int = MODE_STATIC
    speed: int = DEFAULT_SPEED_STEP
    reversed: bool = False
    multicolor: bool = False


DEFAULT_EFFECT_SETTINGS = EffectSettings()


def _speed_step(value: Any) -> int:
    """A saved speed step 1-5; anything else is the default."""
    if isinstance(value, bool):
        return DEFAULT_SPEED_STEP
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value.strip())
    if isinstance(value, int) and 1 <= value <= SPEED_STEPS:
        return value
    return DEFAULT_SPEED_STEP


def effect_settings(device: Optional[DeviceLike]) -> EffectSettings:
    """The descriptor's effect settings; missing or invalid values are the defaults."""
    if not isinstance(device, Mapping):
        return DEFAULT_EFFECT_SETTINGS
    effect = EFFECT_BY_KEY.get(led_color.descriptor_field(device, led_color.EFFECT_FIELD), DEFAULT_EFFECT)
    return EffectSettings(
        mode=effect.mode,
        speed=_speed_step(device.get(led_color.EFFECT_SPEED_FIELD)),
        reversed=device.get(led_color.EFFECT_REVERSED_FIELD) is True,
        multicolor=device.get(led_color.EFFECT_MULTICOLOR_FIELD) is True,
    )


def sync_enabled(device: Optional[DeviceLike]) -> bool:
    """Whether the user turned on Monitor and Music sync for this keyboard."""
    return isinstance(device, Mapping) and device.get(led_color.SYNC_ENABLED_FIELD) is True


def sync_interval_key(device: Optional[DeviceLike]) -> str:
    """The saved sync interval key; unknown or missing values are the default."""
    key = led_color.descriptor_field(device, led_color.SYNC_INTERVAL_FIELD)
    return key if key in SYNC_INTERVALS else DEFAULT_SYNC_INTERVAL


def sync_interval_seconds(device: Optional[DeviceLike]) -> float:
    """Seconds between stream writes for the descriptor (never below the minimum)."""
    return max(MIN_WRITE_INTERVAL_SECONDS, SYNC_INTERVALS[sync_interval_key(device)])


def sync_options(device: Optional[DeviceLike]) -> SyncOptions:
    """The opt-in sync switch and intervals, with the wear warning."""
    return SyncOptions(
        enabled=sync_enabled(device),
        interval=sync_interval_key(device),
        choices=tuple(SyncChoice(key, label) for key, label in SYNC_INTERVAL_LABELS),
        default_interval=DEFAULT_SYNC_INTERVAL,
        description=SYNC_DESCRIPTION,
        confirm_title=SYNC_CONFIRM_TITLE,
        confirm_text=SYNC_CONFIRM_TEXT,
    )


def offers_multicolor(effect: Effect) -> bool:
    """Whether the GUI offers a multicolour switch: colour effects except static."""
    return effect.has_color and effect.mode != MODE_STATIC


def lighting_options(device: Optional[DeviceLike]) -> LightingOptions:
    """The keyboard's per-device options: built-in effects and opt-in sync (no colour correction)."""
    settings = effect_settings(device)
    effect = EFFECT_BY_MODE[settings.mode]
    return LightingOptions(
        title="Keyboard lighting",
        noun="keyboard",
        color_correction=False,
        effects=EffectOptions(
            choices=tuple(
                EffectChoice(
                    key=entry.key,
                    name=entry.name,
                    has_speed=entry.has_speed,
                    directions=entry.directions,
                    multicolor=offers_multicolor(entry),
                )
                for entry in EFFECTS
            ),
            effect=effect.key,
            speed=settings.speed,
            reversed=settings.reversed,
            multicolor=settings.multicolor,
            default_effect=DEFAULT_EFFECT.key,
            default_speed=DEFAULT_SPEED_STEP,
            speed_steps=SPEED_STEPS,
        ),
        sync=sync_options(device),
    )


# ----------------------------------------------------------- shared handles

# What the keyboard shows, as it goes on the wire: (mode, brightness level
# 0-4, speed 0-4, direction byte, multicolour 0/1, colour).
WireState = Tuple[int, int, int, int, int, RGB]
OFF_STATE: WireState = (MODE_STATIC, 0, SPEED, DIRECTION_FORWARD, 0, (0, 0, 0))


def effect_wire_state(settings: EffectSettings, level: int, color: RGB) -> WireState:
    """The SET values for an effect at a brightness level with the user's colour.

    Static keeps today's bytes (speed ``0x02``, ``FF``, never multicolour).
    An effect without speed sends ``0x02``, one without direction ``0xFF``;
    an effect without colour, or with multicolour on, sends 1 and black.
    """
    effect = EFFECT_BY_MODE.get(settings.mode, DEFAULT_EFFECT)
    if effect.mode == MODE_STATIC:
        return (MODE_STATIC, level, SPEED, DIRECTION_FORWARD, 0, color)
    speed = speed_byte(settings.speed) if effect.has_speed else SPEED
    direction = DIRECTION_REVERSED if settings.reversed and effect.directions else DIRECTION_FORWARD
    if not effect.has_color or settings.multicolor:
        return (effect.mode, level, speed, direction, 1, (0, 0, 0))
    return (effect.mode, level, speed, direction, 0, color)


class _Controller:
    """One physical keyboard, shared by every adapter that drives it.

    ``lock`` is held for the rate-limit wait and the whole BEGIN/SET/END
    sequence; ``state_lock`` only guards the requested state, so a newer
    request can replace it while an older one waits, and the waiting writer
    then sends the newest.
    """

    def __init__(self, vendor_id: int, product_id: int) -> None:
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.lock = threading.RLock()
        self.state_lock = threading.Lock()
        self.stop = threading.Event()
        self.device: Any = None
        self.users: Set[int] = set()
        # Requested lighting state (the user's sRGB colour and 0-100%).
        self.color: RGB = (255, 255, 255)
        self.brightness = 100
        self.power_on: Optional[bool] = None
        # Whether a manual colour, brightness or power command was requested
        # since this controller was made. Until then the state above is only
        # the default (white, 100%), so a stream's end leaves the keyboard on
        # the last sync colour instead of "restoring" white.
        self.manual_known = False
        # The requested effect (static by default) and its settings.
        self.effect: EffectSettings = DEFAULT_EFFECT_SETTINGS
        # The state the keyboard confirmed last (None: unknown).
        self.last_sent: Optional[WireState] = None
        # When the last sequence started (None: never).
        self.last_attempt_at: Optional[float] = None
        # Adapters streaming sync frames now; while any is, manual changes
        # are remembered but only power off is written.
        self.streams: Set[int] = set()
        # The colour the current stream last wrote (None: nothing yet) and
        # how many sequences the stream started.
        self.stream_color: Optional[RGB] = None
        self.stream_writes = 0

    @property
    def streaming(self) -> bool:
        return bool(self.streams)

    def wire_state(self) -> WireState:
        with self.state_lock:
            if self.power_on is False:
                return OFF_STATE
            return effect_wire_state(self.effect, brightness_level(self.brightness), self.color)


_controllers: Dict[Tuple[int, int], _Controller] = {}
_controllers_lock = threading.Lock()


def _controller_for(vendor_id: int, product_id: int) -> _Controller:
    key = (vendor_id, product_id)
    with _controllers_lock:
        controller = _controllers.get(key)
        if controller is None:
            controller = _controllers[key] = _Controller(vendor_id, product_id)
        return controller


class _NoReply(Exception):
    """No matching reply within the timeout."""


def _send(controller: _Controller, packet: bytes) -> None:
    """The single send point: re-check the packet, then write it."""
    packet = bytes(packet)
    _check_packet(packet)
    controller.device.write(packet, WRITE_TIMEOUT_SECONDS)


def _drain(controller: _Controller) -> None:
    """Drop input reports already waiting (late replies, other reports)."""
    for _ in range(DRAIN_LIMIT):
        if controller.device.read(READ_SIZE, 0.0) is None:
            return


def _await_reply(controller: _Controller, request: bytes) -> bytes:
    """The reply to ``request`` (same report id, command and sub-command)."""
    deadline = _monotonic() + REPLY_TIMEOUT_SECONDS
    while True:
        remaining = deadline - _monotonic()
        if remaining <= 0:
            raise _NoReply()
        report = controller.device.read(READ_SIZE, remaining)
        if report is None:
            raise _NoReply()
        report = bytes(report)
        if len(report) >= 4 and report[0] == request[0] and report[1] == request[1] and report[3] == request[3]:
            return report


def _drop_locked(controller: _Controller) -> None:
    device, controller.device = controller.device, None
    controller.last_sent = None
    if device is not None:
        try:
            device.close()
        except Exception:
            logger.debug("Closing the Lorgar handle failed", exc_info=True)


def _open_locked(controller: _Controller) -> None:
    """Find and open the keyboard's lighting collection."""
    if controller.device is not None:
        return
    try:
        info = _lighting_collection(_hid.enumerate(controller.vendor_id, controller.product_id))
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
    controller.last_sent = None
    logger.info("Lorgar %04X:%04X: lighting collection opened", controller.vendor_id, controller.product_id)


def _end_locked(controller: _Controller) -> bool:
    """Send END after a BEGIN; False when it could not be written."""
    end = build_end()
    try:
        _send(controller, end)
        try:
            _await_reply(controller, end)
        except _NoReply:
            logger.warning("The Lorgar keyboard did not answer the end of a lighting change")
    except OSError:
        logger.warning("Could not end a Lorgar lighting change", exc_info=True)
        return False
    return True


def _write_state_locked(controller: _Controller, state: WireState) -> None:
    """One BEGIN/SET/END sequence; success only when SET is echoed exactly."""
    set_packet = build_set(*state)
    _open_locked(controller)
    controller.last_sent = None
    controller.last_attempt_at = _monotonic()
    try:
        _drain(controller)
        _send(controller, build_begin())
        # BEGIN went out, so END always follows, whatever happens to SET.
        try:
            try:
                _await_reply(controller, build_begin())
                _send(controller, set_packet)
                reply = _await_reply(controller, set_packet)
            except _NoReply:
                raise RuntimeError(NO_REPLY_MESSAGE) from None
            if reply[:PACKET_LENGTH] != set_packet:
                logger.warning(
                    "The Lorgar keyboard answered a lighting change with %s instead of an echo",
                    reply[:PACKET_LENGTH].hex(" "),
                )
                raise RuntimeError(MISMATCH_MESSAGE)
        finally:
            ended = _end_locked(controller)
        if not ended:
            raise RuntimeError(LOST_MESSAGE)
    except OSError as exc:
        logger.warning("Lorgar lighting change failed (%s)", type(exc).__name__)
        _drop_locked(controller)
        raise RuntimeError(LOST_MESSAGE) from exc
    except RuntimeError as exc:
        logger.warning("Lorgar lighting change failed: %s", exc)
        _drop_locked(controller)
        raise
    controller.last_sent = state


def _flush_locked(controller: _Controller) -> None:
    """Send the newest requested state, at most once per interval.

    Waits (interruptibly, on the calling thread) until the interval since
    the last sequence has passed, then sends whatever state is requested by
    then. A state the keyboard already confirmed is not sent again.
    """
    while True:
        if controller.stop.is_set():
            raise RuntimeError(CLOSED_MESSAGE)
        state = controller.wire_state()
        if state == controller.last_sent:
            return
        if controller.last_attempt_at is not None:
            remaining = controller.last_attempt_at + MIN_WRITE_INTERVAL_SECONDS - _monotonic()
            if remaining > 0:
                if _wait(controller.stop, remaining):
                    raise RuntimeError(CLOSED_MESSAGE)
                continue  # A newer request may have arrived meanwhile.
        _write_state_locked(controller, state)
        return


def _color_moved(previous: RGB, color: RGB) -> bool:
    """Whether some channel moved by at least :data:`SYNC_COLOR_THRESHOLD`."""
    return max(abs(int(a) - int(b)) for a, b in zip(previous, color)) >= SYNC_COLOR_THRESHOLD


def _stream_frame_locked(controller: _Controller, color: RGB, interval: float) -> None:
    """Write one sync colour if it is due; otherwise drop it at once.

    Due means both: ``interval`` (never below the minimum) has passed since
    the last sequence started, and the colour differs from what the stream
    last sent by :data:`SYNC_COLOR_THRESHOLD` (the first frame of a stream:
    differs at all from what the keyboard shows). Never waits.
    """
    if controller.stop.is_set():
        return
    with controller.state_lock:
        if controller.power_on is False:
            return  # Off: frames do nothing until the keyboard is turned on.
        level = brightness_level(controller.brightness)
    state: WireState = (MODE_STATIC, level, SPEED, DIRECTION_FORWARD, 0, color)
    if controller.last_attempt_at is not None:
        if _monotonic() - controller.last_attempt_at < max(MIN_WRITE_INTERVAL_SECONDS, interval):
            return
    if state == controller.last_sent:
        return
    last = controller.last_sent
    if (
        controller.stream_color is not None
        and last is not None
        and last[:5] == state[:5]
        and not _color_moved(controller.stream_color, color)
    ):
        return
    # The attempt counts from here, so a failure (opening included) is not
    # retried before the interval either.
    controller.last_attempt_at = _monotonic()
    controller.stream_writes += 1
    _write_state_locked(controller, state)
    controller.stream_color = color


def close_all() -> None:
    """Close every shared handle (tests, shutdown). Lighting is left as is."""
    with _controllers_lock:
        controllers = list(_controllers.values())
    for controller in controllers:
        controller.stop.set()
        with controller.lock:
            controller.users.clear()
            _drop_locked(controller)


# ------------------------------------------------------------------ adapter


class LorgarKeyboardAdapter(TransportAdapter):
    """Effect, colour, brightness and power for a Lorgar keyboard.

    Construction and :attr:`capabilities` never touch the hardware; the
    shared handle opens on the first command. The effect settings come from
    the descriptor (at construction and in :meth:`update_device`) and are
    handed to the shared controller with every command. A sync target only
    once the descriptor turns sync on (:func:`sync_enabled`).
    """

    def __init__(self, device: DeviceLike) -> None:
        super().__init__(device)
        self._ids = _device_ids(device)
        self._registered = False
        self._effect = effect_settings(device)
        self._sync_enabled = sync_enabled(device)
        self._sync_interval = sync_interval_seconds(device)
        self._streaming = False

    @staticmethod
    def capabilities_for(device: Optional[DeviceLike] = None) -> DeviceCapabilities:
        return _SYNC_CAPABILITIES if sync_enabled(device) else _CAPABILITIES

    @property
    def capabilities(self) -> DeviceCapabilities:
        return _SYNC_CAPABILITIES if self._sync_enabled else _CAPABILITIES

    def update_device(self, device: DeviceLike) -> None:
        """Take an edited descriptor (effect and sync settings) for the same keyboard.

        Nothing is sent; the next command or :meth:`reapply` shows the
        change. A descriptor for another keyboard is ignored.
        """
        if _device_ids(device) == self._ids:
            self.device = device
            self._effect = effect_settings(device)
            self._sync_enabled = sync_enabled(device)
            self._sync_interval = sync_interval_seconds(device)

    def _controller(self) -> _Controller:
        if _supported(*self._ids) is None:
            raise RuntimeError(UNKNOWN_DEVICE_MESSAGE)
        controller = _controller_for(*self._ids)
        if not self._registered:
            with controller.state_lock:
                if not controller.users:
                    controller.stop.clear()
                controller.users.add(id(self))
            self._registered = True
        return controller

    def _apply(self, update: Callable[[_Controller], None]) -> None:
        controller = self._controller()
        with controller.state_lock:
            controller.effect = self._effect
            update(controller)
            controller.manual_known = True
            # During a stream a manual change is only remembered (it shows
            # when the stream ends); power off is the exception.
            held = controller.streaming and controller.power_on is not False
        if held:
            return
        with controller.lock:
            _flush_locked(controller)

    def set_power(self, on: bool) -> None:
        def update(controller: _Controller) -> None:
            controller.power_on = bool(on)

        self._apply(update)

    def set_brightness(self, percent: int) -> None:
        level = max(0, min(100, int(percent)))

        def update(controller: _Controller) -> None:
            controller.brightness = level
            if controller.power_on is None:
                controller.power_on = True

        self._apply(update)

    def set_color(self, r: int, g: int, b: int) -> None:
        color = (_channel(r), _channel(g), _channel(b))

        def update(controller: _Controller) -> None:
            controller.color = color
            if controller.power_on is None:
                controller.power_on = True

        self._apply(update)

    def begin_stream(self) -> None:
        """Start taking sync frames (only with sync turned on). Sends nothing."""
        if not self._sync_enabled:
            raise RuntimeError(NO_SYNC_MESSAGE)
        controller = self._controller()
        with controller.state_lock:
            if not controller.streams:
                controller.stream_color = None
                controller.stream_writes = 0
            controller.streams.add(id(self))
        self._streaming = True

    def set_segments(self, colors: List[RGB]) -> None:
        """One sync frame: the whole keyboard shows its average colour, when due.

        Returns at once when the write is not due (see
        :func:`_stream_frame_locked`) or another write is running; a failed
        write raises like any other command.
        """
        if not self._sync_enabled:
            if self._streaming:
                # Sync was turned off mid-stream: give the keyboard back now.
                self._finish_stream()
            raise RuntimeError(NO_SYNC_MESSAGE)
        if not self._streaming:
            self.begin_stream()
        color = led_color.average_color(colors)
        controller = self._controller()
        if not controller.lock.acquire(blocking=False):
            return  # Another write is running; this frame is dropped.
        try:
            _stream_frame_locked(controller, color, self._sync_interval)
        finally:
            controller.lock.release()

    def end_stream(self) -> None:
        """Stop the stream and show the user's own lighting again (one write)."""
        if self._streaming:
            self._finish_stream()

    def _finish_stream(self) -> None:
        self._streaming = False
        controller = _controller_for(*self._ids)
        with controller.state_lock:
            controller.streams.discard(id(self))
            if controller.streams:
                return
            writes, controller.stream_writes = controller.stream_writes, 0
            controller.stream_color = None
            manual_known = controller.manual_known
        logger.info("Lorgar keyboard: %d lighting writes during sync", writes)
        if not manual_known:
            # No manual state since the app started: the remembered state is
            # only the default (white), so the last sync colour stays.
            return
        with controller.lock:
            if controller.device is None and not writes:
                return  # The stream never touched the keyboard.
            try:
                _flush_locked(controller)
            except RuntimeError as exc:
                logger.warning("Could not restore the Lorgar keyboard's lighting after sync: %s", exc)

    def reapply(self) -> None:
        """Send the requested state if it differs from what the keyboard shows.

        Used after an effect option changed. The adapter's effect settings
        become the requested ones either way; nothing is sent unless the
        handle is already open and the keyboard is on, so an option change
        never opens the keyboard by itself.
        """
        controller = self._controller()
        with controller.state_lock:
            controller.effect = self._effect
            if controller.streaming:
                return  # Shown when the stream ends.
        with controller.lock:
            if controller.device is None or controller.power_on is not True:
                return
            _flush_locked(controller)

    def close(self) -> None:
        """Stop using the shared handle; the last user closes it. Lighting is left as is."""
        if not self._registered:
            return
        self._registered = False
        self._streaming = False
        controller = _controller_for(*self._ids)
        with controller.state_lock:
            controller.users.discard(id(self))
            controller.streams.discard(id(self))
            last = not controller.users
            if last:
                controller.stop.set()  # Wake a writer waiting on the rate limit.
        if last:
            with controller.lock:
                if not controller.users:
                    _drop_locked(controller)


__all__ = [
    "DEVICE_TYPE",
    "EFFECTS",
    "MIN_WRITE_INTERVAL_SECONDS",
    "SUPPORTED_DEVICES",
    "SYNC_COLOR_THRESHOLD",
    "SYNC_INTERVALS",
    "UDEV_RULE",
    "UDEV_RULE_FILENAME",
    "Effect",
    "EffectSettings",
    "LorgarKeyboardAdapter",
    "brightness_level",
    "build_begin",
    "build_end",
    "build_set",
    "build_set_static",
    "descriptor_for",
    "device_present",
    "discover",
    "effect_settings",
    "effect_wire_state",
    "lighting_options",
    "permission_denied_message",
    "speed_byte",
    "sync_enabled",
    "sync_interval_key",
    "sync_interval_seconds",
    "sync_options",
]
