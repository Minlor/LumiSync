"""Gigabyte RGB Fusion 2 motherboard lighting over USB HID.

The board's lighting is driven by an ITE controller on an internal USB
port (IT5702, ``048D:5702``, on the verified B550 AORUS ELITE). It speaks
64-byte HID feature reports on its vendor collection (usage page ``0xFF89``,
usage ``0xCC``). Byte 0 is the report ID ``0xCC``, byte 1 the command; the
protocol is written up in ``docs/gigabyte-rgb-fusion2-research.md``.

Windows and Linux are supported (see :mod:`.hid_feature`). On Linux the
``/dev/hidraw`` node is root-only until the udev rule in :data:`UDEV_RULE`
is installed; discovery still lists the board from sysfs, and the first
command explains the rule.

By default the board is one light: every frame writes the same static colour
to all eight zone registers and then applies them. With the descriptor field
``rgb_fusion_layout: "zones"`` each zone of the board (see
:data:`BOARD_ZONES`) gets its own colour instead; the packets are the same,
one static-effect report per register and one apply. The controller has no
readback, so power, brightness and colour are tracked optimistically.

Colour: the LEDs are driven linearly while the app's colours are sRGB, so
each channel goes through the sRGB transfer function (:data:`SRGB_TO_LINEAR`,
shared with the other USB lighting in :mod:`.led_color`) and this board's
:data:`WHITE_BALANCE` as the last step before the packet is built, after
software brightness. ``color_correction: "none"`` sends the sRGB values
unchanged.

Safety: the driver only ever emits the commands in :data:`ALLOWED_COMMANDS`
(identify, audio mode off, addressable-header mode, zone effect, apply).
Calibration, LED-count, persistence, lamp-array and save-to-flash commands
are never built, and :func:`_send` refuses any packet outside the allow-list.

Connections: GUI controls and the sync engine can hold separate adapter
instances for the board. They share one :class:`_Controller` per physical
controller, which owns the only HID handle and a lock held for each whole
frame (eight zone packets plus apply), so frames never interleave. The
handle opens on the first command and closes when the last adapter using it
is closed. Layout and colour correction are read from the adapter's
descriptor on every frame; :meth:`GigabyteFusionAdapter.update_device`
swaps in an edited descriptor without reopening anything.
"""

from __future__ import annotations

import dataclasses
import sys
import threading
import time
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..device import DeviceLike
from ..utils.colors import resample_colors_to_count
from ..utils.logging import get_logger
from . import hid_feature, led_color
from .base import RGB, DeviceCapabilities, TransportAdapter
from .led_color import (
    COLOR_CORRECTION_FIELD,
    COLOR_CORRECTION_NONE,
    COLOR_CORRECTION_SRGB,
    SRGB_TO_LINEAR,
    LightingOptions,
    average_color,
    color_correction,
    scale_color,
)
from .led_color import channel as _channel
from .led_color import descriptor_field as _field

logger = get_logger("lumisync_rgb_fusion")

# The HID layer in use. Tests replace it with a fake that records reports.
_hid: Any = hid_feature

REPORT_ID = 0xCC
REPORT_LENGTH = 64
LIGHTING_USAGE_PAGE = 0xFF89
LIGHTING_USAGE = 0xCC

# (vendor id, product id) pairs verified on real hardware. Other ITE
# lighting controllers exist (other IT57xx/IT8297 product IDs), but they are
# deliberately left out until someone verifies them on a real board: an
# unverified controller may number its zones or commands differently.
SUPPORTED_CONTROLLERS: Tuple[Tuple[int, int], ...] = (
    (0x048D, 0x5702),  # ITE IT5702 (B550 AORUS ELITE, firmware 2.0.10.0)
)

CMD_ZONE_EFFECT = 0x20  # + zone number, 0..7
CMD_APPLY = 0x28
CMD_AUDIO_MODE = 0x31
CMD_ADDRESSABLE_MODE = 0x32
CMD_IDENTIFY = 0x60

ZONE_COUNT = 8
ALL_ZONES = 0xFF
EFFECT_STATIC = 0x01
PRODUCT_RGB_FUSION2 = 0x01
# The controller needs a moment after the addressable-mode change.
ADDRESSABLE_SETTLE_SECONDS = 0.05

# Every command this driver may emit. Anything else is refused by _send().
ALLOWED_COMMANDS = frozenset(
    {CMD_IDENTIFY, CMD_AUDIO_MODE, CMD_ADDRESSABLE_MODE, CMD_APPLY}
    | set(range(CMD_ZONE_EFFECT, CMD_ZONE_EFFECT + ZONE_COUNT))
)
# Commands that change calibration, LED counts or what the controller keeps
# across power cycles (persistence, lamp array, save to flash). Never sent.
NEVER_SEND_COMMANDS = frozenset({0x33, 0x34, 0x47, 0x48, 0x5E})
assert not (ALLOWED_COMMANDS & NEVER_SEND_COMMANDS)

DEVICE_TYPE = "rgb_fusion2"
DEVICE_KIND = "motherboard"
DEVICE_MODEL = "RGB Fusion 2"
FALLBACK_NAME = "Gigabyte RGB Fusion lighting"

NOT_FOUND_MESSAGE = (
    "The Gigabyte RGB Fusion lighting controller was not found on USB. "
    "Check that the board's RGB lighting is enabled in the BIOS."
)
OPEN_FAILED_MESSAGE = (
    "Could not open the RGB Fusion lighting controller. Another RGB program "
    "may be using it; close it and try again."
)
NOT_FUSION_MESSAGE = (
    "The USB lighting controller did not identify as Gigabyte RGB Fusion 2, "
    "so LumiSync will not drive it."
)
LOST_MESSAGE = (
    "Lost the connection to the RGB Fusion lighting controller. LumiSync "
    "reconnects on the next command."
)
UNSUPPORTED_MESSAGE = "Gigabyte RGB Fusion lighting can only be controlled on Windows and Linux."

# Linux: /dev/hidraw* is root-only by default. This rule (shipped as
# packaging/linux/60-lumisync-pc-lighting.rules) gives the logged-in user
# access to the supported controller only.
UDEV_RULE = (
    'KERNEL=="hidraw*", SUBSYSTEM=="hidraw", ATTRS{idVendor}=="048d", '
    'ATTRS{idProduct}=="5702", TAG+="uaccess"'
)
UDEV_RULE_FILENAME = "60-lumisync-pc-lighting.rules"


def permission_denied_message(path: str) -> str:
    """The Linux "install the udev rule" explanation for ``path``."""
    return (
        f"LumiSync is not allowed to open {path}, the RGB Fusion lighting "
        "controller: on Linux only root can use it until a udev rule grants "
        f"access. Save this line as /etc/udev/rules.d/{UDEV_RULE_FILENAME}:\n"
        f"{UDEV_RULE}\n"
        "then run 'sudo udevadm control --reload' and 'sudo udevadm trigger' "
        "(or reboot), and try again."
    )
UNKNOWN_CONTROLLER_MESSAGE = "This RGB Fusion controller model has not been verified with LumiSync yet."

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

# ------------------------------------------------------- per-device options

# Colour correction uses the shared field and values (:mod:`.led_color`).

# Descriptor field and values for the lighting layout.
LAYOUT_FIELD = "rgb_fusion_layout"
LAYOUT_BOARD = "board"  # default: one colour on every register
LAYOUT_ZONES = "zones"  # one colour per zone of the board


@dataclass(frozen=True)
class Zone:
    """One lighting zone: the register it is written to and what it lights."""

    register: int
    name: str


def _zones(*names: str) -> Tuple[Zone, ...]:
    """Zones in sync order, on registers 0, 1, 2, ... in that order."""
    return tuple(Zone(register, name) for register, name in enumerate(names))


# Zones per board, keyed by the descriptor's ``motherboard`` name (compared
# without case). The order is the sync order: zone i takes the i-th colour of
# a frame. A board is added here once its zone map has been checked on real
# hardware. Registers a board does not list are written black in per-zone
# mode (nothing is attached to them).
BOARD_ZONES: Dict[str, Tuple[Zone, ...]] = {
    "B550 AORUS ELITE": _zones(
        "I/O cover",
        "LED_C1 header",
        "LED_CPU header",
        "PCIe accent",
        "LED_C2 header",
        "D_LED1 header",
        "D_LED2 header",
    ),  # register 7 is unused on this board
}
# Any other board: every register, by number.
GENERIC_ZONES: Tuple[Zone, ...] = tuple(Zone(register, f"Zone {register + 1}") for register in range(ZONE_COUNT))
assert all(0 <= zone.register < ZONE_COUNT for zones in BOARD_ZONES.values() for zone in zones)


def layout(device: Optional[DeviceLike]) -> str:
    """The descriptor's layout (whole board unless per zone was chosen)."""
    return LAYOUT_ZONES if _field(device, LAYOUT_FIELD) == LAYOUT_ZONES else LAYOUT_BOARD


def zones_for(device: Optional[DeviceLike]) -> Tuple[Zone, ...]:
    """The zones of the descriptor's board, in sync order."""
    board = _field(device, "motherboard")
    for name, zones in BOARD_ZONES.items():
        if name.lower() == board:
            return zones
    return GENERIC_ZONES


def zone_names(device: Optional[DeviceLike]) -> Tuple[str, ...]:
    return tuple(zone.name for zone in zones_for(device))


def capabilities_for_layout(device: Optional[DeviceLike]) -> DeviceCapabilities:
    """Static capabilities: one segment per zone in per-zone mode, else one."""
    if layout(device) != LAYOUT_ZONES:
        return _CAPABILITIES
    return _zone_capabilities(len(zones_for(device)))


_zone_capability_cache: Dict[int, DeviceCapabilities] = {}


def _zone_capabilities(count: int) -> DeviceCapabilities:
    cap = _zone_capability_cache.get(count)
    if cap is None:
        cap = _zone_capability_cache[count] = dataclasses.replace(_CAPABILITIES, segment_count=count)
    return cap


def lighting_options(device: Optional[DeviceLike]) -> LightingOptions:
    """The board's per-device options (colour correction, whole/per-zone layout)."""
    names = zone_names(device)
    return LightingOptions(
        title="Board lighting",
        noun="board",
        correction_tip="Converts colours for the board's LEDs so dim and pastel colours look as on screen",
        layout_field=LAYOUT_FIELD,
        layout_whole=LAYOUT_BOARD,
        layout_zones=LAYOUT_ZONES,
        layout=layout(device),
        zone_names=names,
        whole_label="Whole board",
        whole_description="One colour on every zone",
        zones_description=f"{len(names)} zones, one colour each",
    )


# ---------------------------------------------------------- colour transfer
# The sRGB curve is shared (:mod:`.led_color`); the white balance is this
# board's own.

# Per-channel gain applied after the curve. The blue (and, less so, green)
# LEDs are stronger than the red ones, so equal drive looks icy blue and
# pastels wash out. Set by eye on a B550 AORUS ELITE: with these gains white
# is neutral, and hot pink and orange look like their screen colours.
WHITE_BALANCE: Tuple[float, float, float] = (1.0, 0.84, 0.59)


def correct_color(color: Sequence[int], correction: str = COLOR_CORRECTION_SRGB) -> RGB:
    """Convert an sRGB colour to the board LEDs' linear drive (unless correction is off)."""
    return led_color.correct_color(color, correction, WHITE_BALANCE)


def wire_color(color: Sequence[int], brightness: int, correction: str = COLOR_CORRECTION_SRGB) -> RGB:
    """What goes in the packet: brightness first, then colour correction."""
    return led_color.wire_color(color, brightness, correction, WHITE_BALANCE)


# A frame is the colour of each of the eight registers, as sent.
Frame = Tuple[RGB, ...]
BLACK: RGB = (0, 0, 0)
BLACK_FRAME: Frame = (BLACK,) * ZONE_COUNT


def board_frame(color: RGB) -> Frame:
    """Every register the same colour."""
    return (color,) * ZONE_COUNT


def zone_frame(colors: Sequence[RGB], zones: Sequence[Zone]) -> Frame:
    """Zone i's colour on its register; registers without a zone stay black."""
    registers = list(BLACK_FRAME)
    for zone, color in zip(zones, colors):
        registers[zone.register] = color
    return tuple(registers)

# Replaced in tests so the 50 ms settle does not slow the suite down.
_sleep = time.sleep

# An identical frame is skipped only while it is this fresh. After that it is
# sent again: the controller cannot be read back, so a repeat is the only way
# to put the colour back after the board reset its lighting (sleep/resume) or
# another program changed it. During sync this costs one frame a second on a
# static scene instead of thirty.
REPEAT_AFTER_SECONDS = 1.0
_monotonic = time.monotonic


# ------------------------------------------------------------------ packets


def _report(command: int, fields: Optional[Mapping[int, int]] = None) -> bytes:
    if command not in ALLOWED_COMMANDS:
        raise ValueError(f"Command 0x{command:02X} is not allowed for RGB Fusion 2.")
    packet = bytearray(REPORT_LENGTH)
    packet[0] = REPORT_ID
    packet[1] = command
    for offset, value in (fields or {}).items():
        packet[offset] = int(value) & 0xFF
    return bytes(packet)


def build_identify_query() -> bytes:
    """``CC 60``: ask the controller to describe itself."""
    return _report(CMD_IDENTIFY)


def build_audio_mode_off() -> bytes:
    """``CC 31 00``: switch the audio "beat" mode off."""
    return _report(CMD_AUDIO_MODE)


def build_addressable_mode(header_mask: int = 0) -> bytes:
    """``CC 32 <mask>``: headers in the mask go per-LED; the rest follow zones."""
    return _report(CMD_ADDRESSABLE_MODE, {2: header_mask})


def build_zone_static(zone: int, color: Sequence[int]) -> bytes:
    """One zone's static colour at full packet brightness.

    The colour bytes are blue, green, red. Brightness is applied to the
    colour before this point (see :func:`scale_color`), so the max/min
    brightness bytes stay at 255/0.
    """
    if not 0 <= int(zone) < ZONE_COUNT:
        raise ValueError(f"Zone {zone} is outside 0..{ZONE_COUNT - 1}.")
    zone = int(zone)
    r, g, b = (_channel(value) for value in color)
    mask = (1 << zone).to_bytes(4, "little")
    fields = {2: mask[0], 3: mask[1], 4: mask[2], 5: mask[3],
              11: EFFECT_STATIC, 12: 0xFF, 13: 0x00, 14: b, 15: g, 16: r}
    return _report(CMD_ZONE_EFFECT + zone, fields)


def build_apply(zone_mask: int = ALL_ZONES) -> bytes:
    """``CC 28 <mask>``: show the zone effects written so far."""
    return _report(CMD_APPLY, {2: zone_mask})


def build_frame(registers: Sequence[Sequence[int]]) -> List[bytes]:
    """One frame: register i set to ``registers[i]`` (all eight), then apply."""
    if len(registers) != ZONE_COUNT:
        raise ValueError(f"A frame has {ZONE_COUNT} registers, not {len(registers)}.")
    return [build_zone_static(zone, color) for zone, color in enumerate(registers)] + [build_apply()]


def build_static_frame(color: Sequence[int]) -> List[bytes]:
    """One whole-board frame: every zone set to ``color``, then apply."""
    return build_frame([color] * ZONE_COUNT)


# Brightness is applied in software (:func:`scale_color`) rather than with
# the packet's brightness byte: public references disagree on that byte's
# scale on this controller, and scaling the colour ourselves does not depend
# on it.


# ----------------------------------------------------------------- identity


class NotRgbFusionController(ValueError):
    """An identify reply that does not come from an RGB Fusion 2 controller."""


@dataclass(frozen=True)
class ControllerIdentity:
    """What the controller reports about itself (the ``CC 60`` reply)."""

    product: int
    device_number: int
    strip_detect: int
    firmware: str
    led_count_codes: Tuple[int, int, int]
    support_flags: int
    product_string: str
    spare_calibration: bytes
    strip0_calibration: bytes
    strip1_calibration: bytes
    mainboard_calibration: bytes
    chip_id: int
    spare_calibration_2: bytes


def parse_identity(reply: bytes) -> ControllerIdentity:
    """Parse an identify reply, refusing anything that is not RGB Fusion 2."""
    data = bytes(reply)
    if len(data) < REPORT_LENGTH:
        raise NotRgbFusionController(f"The identify reply is {len(data)} bytes, expected {REPORT_LENGTH}.")
    if data[0] != REPORT_ID:
        raise NotRgbFusionController(f"The identify reply has report ID 0x{data[0]:02X}.")
    if data[1] != PRODUCT_RGB_FUSION2:
        raise NotRgbFusionController(f"The identify reply names product 0x{data[1]:02X}.")
    product_string = data[12:40].split(b"\0", 1)[0].decode("ascii", "replace").strip()
    return ControllerIdentity(
        product=data[1],
        device_number=data[2],
        strip_detect=data[3],
        firmware=f"{data[4]}.{data[5]}.{data[6]}.{data[7]}",
        led_count_codes=(data[8], data[9], data[10]),
        support_flags=data[11],
        product_string=product_string,
        spare_calibration=data[40:44],
        strip0_calibration=data[44:48],
        strip1_calibration=data[48:52],
        mainboard_calibration=data[52:56],
        chip_id=int.from_bytes(data[56:60], "little"),
        spare_calibration_2=data[60:64],
    )


# ---------------------------------------------------------- shared handles


class _Controller:
    """One physical controller, shared by every adapter that drives it.

    ``lock`` is held for the open sequence and for each whole frame, so two
    adapters (GUI and sync) never interleave their packets. The lighting
    state lives here too: it describes the board, not one adapter, and
    survives the handle being reopened.
    """

    def __init__(self, vendor_id: int, product_id: int) -> None:
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.lock = threading.RLock()
        self.device: Any = None
        self.identity: Optional[ControllerIdentity] = None
        # Register colours last sent on the current handle, exactly as sent
        # (after brightness and colour correction).
        self.last_frame: Optional[Frame] = None
        self.last_frame_at = 0.0
        self.users: Set[int] = set()
        # Optimistic lighting state; the controller cannot be read back.
        self.color: RGB = (255, 255, 255)
        self.brightness = 100
        self.power_on: Optional[bool] = None


_controllers: Dict[Tuple[int, int], _Controller] = {}
_controllers_lock = threading.Lock()


def _controller_for(vendor_id: int, product_id: int) -> _Controller:
    key = (vendor_id, product_id)
    with _controllers_lock:
        controller = _controllers.get(key)
        if controller is None:
            controller = _controllers[key] = _Controller(vendor_id, product_id)
        return controller


def _lighting_collection(infos: Sequence[Any]) -> Optional[Any]:
    """The vendor lighting collection among a controller's HID collections."""
    for info in infos:
        if (
            int(info.usage_page) == LIGHTING_USAGE_PAGE
            and int(info.usage) == LIGHTING_USAGE
            and int(info.feature_report_length) == REPORT_LENGTH
        ):
            return info
    return None


def _send(device: Any, packet: bytes) -> None:
    """Send one report, refusing any command outside the allow-list."""
    if len(packet) != REPORT_LENGTH or packet[0] != REPORT_ID or packet[1] not in ALLOWED_COMMANDS:
        raise ValueError("Refusing to send a report outside the RGB Fusion 2 allow-list.")
    device.send_feature_report(packet)


def _identify(device: Any) -> ControllerIdentity:
    _send(device, build_identify_query())
    return parse_identity(device.get_feature_report(REPORT_ID, REPORT_LENGTH))


def _drop_locked(controller: _Controller) -> None:
    """Release the handle (the caller holds ``controller.lock``)."""
    device, controller.device = controller.device, None
    controller.last_frame = None
    if device is not None:
        try:
            device.close()
        except Exception:
            logger.debug("Closing the RGB Fusion handle failed", exc_info=True)


def _open_locked(controller: _Controller) -> None:
    """Find, open, identify and prepare the controller once per connection."""
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
    device = _hid.HidDevice(info.path)
    try:
        device.open()
    except hid_feature.HidUnsupportedPlatform as exc:
        raise RuntimeError(UNSUPPORTED_MESSAGE) from exc
    except PermissionError as exc:
        # On Windows "access denied" means another program holds the
        # controller; on Linux it means the udev rule is missing.
        if sys.platform == "linux":
            raise RuntimeError(permission_denied_message(info.path)) from exc
        raise RuntimeError(OPEN_FAILED_MESSAGE) from exc
    except OSError as exc:
        raise RuntimeError(OPEN_FAILED_MESSAGE) from exc
    try:
        identity = _identify(device)
        _send(device, build_audio_mode_off())
        _send(device, build_addressable_mode(0))
        _sleep(ADDRESSABLE_SETTLE_SECONDS)
    except NotRgbFusionController as exc:
        _close_quietly(device)
        raise RuntimeError(NOT_FUSION_MESSAGE) from exc
    except OSError as exc:
        _close_quietly(device)
        raise RuntimeError(LOST_MESSAGE) from exc
    except BaseException:
        _close_quietly(device)
        raise
    controller.device = device
    controller.identity = identity
    controller.last_frame = None


def _close_quietly(device: Any) -> None:
    try:
        device.close()
    except Exception:
        pass


def _write_frame_locked(controller: _Controller, frame: Frame) -> None:
    """Send the register colours ``frame``, unless they were sent a moment ago.

    ``frame`` holds the bytes that go on the wire, so a change of layout or
    colour correction always counts as a new frame.
    """
    frame = tuple((_channel(c[0]), _channel(c[1]), _channel(c[2])) for c in frame)
    _open_locked(controller)
    now = _monotonic()
    if controller.last_frame == frame and now - controller.last_frame_at < REPEAT_AFTER_SECONDS:
        return
    try:
        for packet in build_frame(frame):
            _send(controller.device, packet)
    except OSError as exc:
        _drop_locked(controller)
        raise RuntimeError(LOST_MESSAGE) from exc
    controller.last_frame = frame
    controller.last_frame_at = now


def close_all() -> None:
    """Close every shared handle (tests, shutdown). Lighting is left as is."""
    with _controllers_lock:
        controllers = list(_controllers.values())
    for controller in controllers:
        with controller.lock:
            controller.users.clear()
            _drop_locked(controller)


# ---------------------------------------------------------------- discovery


# Linux DMI board name; patched in tests. Missing under WSL and in VMs
# without DMI data, which reads as "unknown".
_DMI_BOARD_NAME = "/sys/class/dmi/id/board_name"


def motherboard_product() -> str:
    """The board's product name ("" if unknown).

    Windows: the BIOS registry key, read with :mod:`winreg` rather than
    WMI/PowerShell, which take seconds. Linux: the DMI ``board_name`` file.
    """
    if sys.platform == "linux":
        try:
            with open(_DMI_BOARD_NAME, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read().strip()
        except OSError:
            return ""
    if sys.platform != "win32":
        return ""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS") as key:
            value, _kind = winreg.QueryValueEx(key, "BaseBoardProduct")
    except OSError:
        return ""
    return value.strip() if isinstance(value, str) else ""


def device_id_for(vendor_id: int, product_id: int) -> str:
    return f"rgbfusion2-{vendor_id:04x}-{product_id:04x}"


def descriptor_for(
    vendor_id: int, product_id: int, identity: Optional[ControllerIdentity], board: str = "",
) -> Dict[str, Any]:
    """The saved device descriptor for one controller.

    ``identity`` is None when the controller was found but could not be
    opened (Linux without the udev rule); the descriptor then lacks the
    firmware, product string and chip id.
    """
    descriptor: Dict[str, Any] = {
        "device_id": device_id_for(vendor_id, product_id),
        "name": f"{board} lighting" if board else FALLBACK_NAME,
        "model": DEVICE_MODEL,
        "transport": "usb",
        "type": DEVICE_TYPE,
        "device_kind": DEVICE_KIND,
        "usb_vendor_id": vendor_id,
        "usb_product_id": product_id,
    }
    if identity is not None:
        descriptor["rgb_fusion_firmware"] = identity.firmware
        descriptor["rgb_fusion_product"] = identity.product_string
        descriptor["rgb_fusion_chip_id"] = f"0x{identity.chip_id:08X}"
    if board:
        descriptor["motherboard"] = board
    return descriptor


def _discovery_identity(vendor_id: int, product_id: int, info: Any) -> Optional[ControllerIdentity]:
    """Identify a controller without disturbing an open connection.

    Runs under the controller lock so the identify exchange can never land
    between the packets of a frame. A connection that is already open was
    identified when it opened, so it is not asked again. Discovery sends the
    identify query only; it never prepares the controller for colour.

    Returns None on Linux when the node may not be opened (no udev rule
    yet): the sysfs evidence (IDs and the lighting collection) is enough to
    list the board, and the first command explains the rule. The identity
    check still runs before any colour is sent (:func:`_open_locked`).
    """
    controller = _controller_for(vendor_id, product_id)
    with controller.lock:
        if controller.device is not None and controller.identity is not None:
            return controller.identity
        device = _hid.HidDevice(info.path)
        try:
            device.open()
        except PermissionError:
            if sys.platform == "linux":
                logger.info("No permission to open %s; listing the board without identifying it", info.path)
                return None
            raise
        try:
            return _identify(device)
        finally:
            _close_quietly(device)


def discover() -> List[Dict[str, Any]]:
    """Descriptors of the RGB Fusion 2 controllers on this PC. Never raises."""
    found: List[Dict[str, Any]] = []
    try:
        board: Optional[str] = None
        for vendor_id, product_id in SUPPORTED_CONTROLLERS:
            try:
                info = _lighting_collection(_hid.enumerate(vendor_id, product_id))
                if info is None:
                    continue
                identity = _discovery_identity(vendor_id, product_id, info)
            except NotRgbFusionController as exc:
                logger.info("Skipping USB lighting controller %04X:%04X: %s", vendor_id, product_id, exc)
                continue
            except Exception as exc:
                logger.warning(
                    "Could not identify USB lighting controller %04X:%04X (%s)",
                    vendor_id, product_id, type(exc).__name__,
                )
                continue
            if board is None:
                try:
                    board = motherboard_product()
                except Exception:
                    board = ""
            found.append(descriptor_for(vendor_id, product_id, identity, board))
    except Exception:
        logger.warning("RGB Fusion discovery failed", exc_info=True)
        return []
    return found


def _controller_ids(device: Any) -> Tuple[int, int]:
    """The vendor/product pair a descriptor names (the verified one by default)."""
    default = SUPPORTED_CONTROLLERS[0]
    if not isinstance(device, Mapping):
        return default
    try:
        vendor_id = int(device.get("usb_vendor_id", default[0]))
        product_id = int(device.get("usb_product_id", default[1]))
    except (TypeError, ValueError):
        return default
    return vendor_id, product_id


def controller_present(device: Optional[DeviceLike] = None) -> bool:
    """Whether the controller is attached. Lists HID collections only."""
    vendor_id, product_id = _controller_ids(device)
    if (vendor_id, product_id) not in SUPPORTED_CONTROLLERS:
        return False
    try:
        return _lighting_collection(_hid.enumerate(vendor_id, product_id)) is not None
    except Exception:
        return False


# ------------------------------------------------------------------ adapter


class GigabyteFusionAdapter(TransportAdapter):
    """Whole-board or per-zone colour for Gigabyte RGB Fusion 2 motherboards.

    Construction and :attr:`capabilities` never touch the hardware; the
    shared HID handle opens on the first command. There is no readback, so
    the adapter tracks power, brightness and colour as it sends them. The
    remembered colour and brightness are the user's (sRGB) values; colour
    correction only changes the bytes sent.
    """

    def __init__(self, device: DeviceLike) -> None:
        super().__init__(device)
        self._ids = _controller_ids(device)
        self._registered = False

    @staticmethod
    def capabilities_for(device: Optional[DeviceLike] = None) -> DeviceCapabilities:
        return capabilities_for_layout(device)

    @property
    def capabilities(self) -> DeviceCapabilities:
        return capabilities_for_layout(self.device)

    def update_device(self, device: DeviceLike) -> None:
        """Use an edited descriptor (layout, colour correction) from the next frame.

        The shared handle and lighting state are kept. A descriptor for a
        different controller is ignored; that needs a new adapter.
        """
        if _controller_ids(device) == self._ids:
            self.device = device

    def _zones(self) -> Optional[Tuple[Zone, ...]]:
        """The zones in per-zone mode, None for the whole board."""
        return zones_for(self.device) if layout(self.device) == LAYOUT_ZONES else None

    def _paint(self, color: RGB, brightness: int) -> Frame:
        """One colour on the whole board (every zone in per-zone mode)."""
        wire = wire_color(color, brightness, color_correction(self.device))
        zones = self._zones()
        return board_frame(wire) if zones is None else zone_frame([wire] * len(zones), zones)

    def _controller(self) -> _Controller:
        if self._ids not in SUPPORTED_CONTROLLERS:
            raise RuntimeError(UNKNOWN_CONTROLLER_MESSAGE)
        controller = _controller_for(*self._ids)
        if not self._registered:
            with controller.lock:
                controller.users.add(id(self))
            self._registered = True
        return controller

    def set_power(self, on: bool) -> None:
        controller = self._controller()
        with controller.lock:
            if on:
                _write_frame_locked(controller, self._paint(controller.color, controller.brightness))
            else:
                _write_frame_locked(controller, BLACK_FRAME)
            controller.power_on = bool(on)

    def set_brightness(self, percent: int) -> None:
        level = max(0, min(100, int(percent)))
        controller = self._controller()
        with controller.lock:
            if controller.power_on is not False:
                _write_frame_locked(controller, self._paint(controller.color, level))
                controller.power_on = True
            controller.brightness = level

    def set_color(self, r: int, g: int, b: int) -> None:
        color = (_channel(r), _channel(g), _channel(b))
        controller = self._controller()
        with controller.lock:
            if controller.power_on is not False:
                _write_frame_locked(controller, self._paint(color, controller.brightness))
                controller.power_on = True
            controller.color = color

    def set_segments(self, colors: List[RGB]) -> None:
        # Whole board: the frame's average, like the other single-light
        # devices. Per zone: the frame resized to the zone count, one colour
        # per zone. The manual colour is left alone either way, so turning
        # the board on after sync brings it back.
        if not colors:
            return
        correction = color_correction(self.device)
        zones = self._zones()
        controller = self._controller()
        with controller.lock:
            brightness = controller.brightness
            if zones is None:
                frame = board_frame(wire_color(average_color(colors), brightness, correction))
            else:
                resized = resample_colors_to_count(list(colors), len(zones))
                frame = zone_frame([wire_color(c, brightness, correction) for c in resized], zones)
            _write_frame_locked(controller, frame)
            controller.power_on = True

    def reapply(self) -> None:
        """Show the remembered colour again with the current options.

        Used after a layout or colour-correction change so it is visible at
        once. Sends nothing unless the connection is already open and the
        board is on: an option change never opens the controller by itself.
        """
        controller = self._controller()
        with controller.lock:
            if controller.device is None or controller.power_on is not True:
                return
            _write_frame_locked(controller, self._paint(controller.color, controller.brightness))

    def begin_stream(self) -> None:
        # Open (and identify) now, so an absent controller fails the sync
        # start with a clear message instead of on the first frame.
        controller = self._controller()
        with controller.lock:
            _open_locked(controller)

    def close(self) -> None:
        """Stop using the shared handle; the last user closes it.

        The lighting is left as it is: the controller cannot report its
        previous state, so there is nothing to restore.
        """
        if not self._registered:
            return
        self._registered = False
        controller = _controller_for(*self._ids)
        with controller.lock:
            controller.users.discard(id(self))
            if not controller.users:
                _drop_locked(controller)


__all__ = [
    "ALLOWED_COMMANDS",
    "BOARD_ZONES",
    "COLOR_CORRECTION_FIELD",
    "COLOR_CORRECTION_NONE",
    "COLOR_CORRECTION_SRGB",
    "GENERIC_ZONES",
    "LAYOUT_BOARD",
    "LAYOUT_FIELD",
    "LAYOUT_ZONES",
    "NEVER_SEND_COMMANDS",
    "SRGB_TO_LINEAR",
    "SUPPORTED_CONTROLLERS",
    "UDEV_RULE",
    "UDEV_RULE_FILENAME",
    "ControllerIdentity",
    "GigabyteFusionAdapter",
    "NotRgbFusionController",
    "Zone",
    "board_frame",
    "build_addressable_mode",
    "build_apply",
    "build_audio_mode_off",
    "build_frame",
    "build_identify_query",
    "build_static_frame",
    "build_zone_static",
    "capabilities_for_layout",
    "color_correction",
    "controller_present",
    "correct_color",
    "descriptor_for",
    "discover",
    "layout",
    "lighting_options",
    "motherboard_product",
    "parse_identity",
    "permission_denied_message",
    "scale_color",
    "wire_color",
    "zone_frame",
    "zone_names",
    "zones_for",
]
