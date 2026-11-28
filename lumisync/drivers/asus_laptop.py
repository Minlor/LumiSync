"""ASUS laptop keyboard lighting over ATKACPI.

Direct hardware lighting control for the built-in keyboard of ASUS gaming
laptops through the ASUS ATKACPI driver interface (``\\\\.\\ATKACPI``). The
only model verified on real hardware is the ASUS TUF Gaming A15 (FA506NCR);
other models are not enabled until someone tests them.

Protocol and Hardware Background
--------------------------------
On ASUS gaming laptops, keyboard backlight and Aura lighting control are
routed through the ASUS System Control Interface via ACPI/WMI. Commands are
sent to ``\\\\.\\ATKACPI`` using DeviceIoControl code ``0x0022240C``:
* Method ``DEVS`` (``0x53564544``): Set device configuration
* Method ``DSTS`` (``0x53545344``): Query device status and presence

Device IDs:
* Primary keyboard: ``0x00100056`` (``TUF_KB_DEVID``)
* Fallback keyboard: ``0x0010005A`` (``TUF_KB_DEVID_FALLBACK``)

The driver asks ``DSTS`` for the primary ID first, then the fallback, and sends
every ``DEVS`` packet to whichever ID reported presence.

The ``DEVS`` static color payload layout (Windows ATKACPI format):
* Argument length: 10 bytes (4-byte device ID + 6-byte setting buffer)
* Setting byte 0 (Opcode):
  - ``0xB3``: Immediate volatile setting (active EC/RAM state, used for
    live streaming, power-off, and dynamic color sync without flash wear).
  - ``0xB4``: Save to BIOS/firmware storage (used solely for explicit
    manual color saves by user action).
* Setting byte 1 (Mode): ``0x00`` (Static single-zone mode)
* Setting bytes 2-4: Red, Green, Blue channel levels (0-255)
* Setting byte 5 (Speed): ``0x00`` (Timing parameter for static mode)

Windows ATKACPI 10-byte Argument Packing:
The Linux kernel's ``asus-wmi`` driver packs arguments into a 12-byte WMI
package (dev_id followed by two 32-bit words). On Windows, ASUS's kernel driver
(``ATKACPI.sys`` / ASUS System Control Interface) unpacks IOCTL ``0x0022240C``
by reading the 32-bit magic, 32-bit arg length, 32-bit devid, and the 6-byte
raw setting array (10-byte payload total). This 10-byte layout was confirmed on
real hardware on the ASUS TUF Gaming A15 (FA506NCR).

Safety and Invariants
---------------------
- **Hardware Gating & Verified Models:** Discovery inspects the system BIOS
  manufacturer in the Windows registry (``HARDWARE\\DESCRIPTION\\System\\BIOS``)
  and checks against the table of verified models (:data:`VERIFIED_MODELS`).
  Non-ASUS machines and non-verified models are never probed and never open
  ``\\\\.\\ATKACPI``.
- **Read-Only DSTS Query:** Before registering a device, discovery evaluates
  the ``DSTS`` method to confirm ``ASUS_WMI_DSTS_PRESENCE_BIT`` (``0x00010000``).
  The controller repeats this query when it opens its handle, to pick the
  device ID its ``DEVS`` packets target; if neither ID is present it sends
  nothing and raises ``OSError``.
- **Firmware Status Verification:** Every ``DEVS`` call verifies that the
  firmware returned status 1 (success) in the output buffer, raising an
  ``OSError`` on rejection.
- **Flash Protection (No 0xB4 in Streams):** High-frequency streaming
  (Monitor and Music sync) strictly emits opcode ``0xB3`` (RAM/EC volatile).
  Opcode ``0xB4`` (BIOS save) is never emitted in any stream, power, or
  brightness update.
- **Stream Thresholding:** Identical frames or frames where no channel shifted
  by at least :data:`SYNC_COLOR_THRESHOLD` are dropped to minimize ACPI traffic.
- **Manual State Tracking:** If no manual color was configured, ending a
  stream leaves the hardware on the last frame without restoring a fake white.
  Power-on after power-off restores the manual color if one was set, otherwise
  the last color the keyboard showed (for example the last streamed frame).
- **Measured Latency:** Benchmarked on ASUS TUF Gaming A15 (FA506NCR) at
  average 0.63 ms per DeviceIoControl call (min 0.52 ms, max 1.24 ms over 50
  calls), yielding a light bus duty cycle (~1.9%) at 30 Hz.
"""

from __future__ import annotations

import struct
import sys
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from ..device import DeviceLike
from ..utils.logging import get_logger
from .base import RGB, DeviceCapabilities, TransportAdapter
from .led_color import average_color, channel as _channel

logger = get_logger("lumisync_asus_laptop")

DEVICE_TYPE = "asus_laptop"
DEVICE_KIND = "keyboard"
DEVICE_MODEL = "ASUS Laptop Keyboard"
FALLBACK_NAME = "ASUS Laptop Keyboard"

NOT_FOUND_MESSAGE = (
    "The ASUS laptop keyboard lighting controller (\\\\.\\ATKACPI) was not found. "
    "Ensure that this is an ASUS gaming laptop and the ASUS System Control Interface is running."
)

# Win32 Constants
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3

CONTROL_CODE = 0x0022240C
DEVS_MAGIC = 0x53564544  # ASCII 'DEVS' in little-endian
DSTS_MAGIC = 0x53545344  # ASCII 'DSTS' in little-endian

TUF_KB_DEVID = 0x00100056
TUF_KB_DEVID_FALLBACK = 0x0010005A
DSTS_PRESENCE_BIT = 0x00010000

OPCODE_SET = 0xB3   # Live / volatile setting (RAM / EC)
OPCODE_SAVE = 0xB4  # Persistent setting (saved to BIOS / NVRAM)

DEVS_PACKET_LENGTH = 18
DEVS_PAYLOAD_LENGTH = 10  # 4 bytes devid + 6 bytes setting
DSTS_PACKET_LENGTH = 12
DSTS_PAYLOAD_LENGTH = 4   # 4 bytes devid

ALLOWED_OPCODES = frozenset({OPCODE_SET, OPCODE_SAVE})
SUPPORTED_DEVICES = frozenset({TUF_KB_DEVID, TUF_KB_DEVID_FALLBACK})

# Minimum channel difference to trigger a new ACPI write during streaming
SYNC_COLOR_THRESHOLD = 2

# Table of verified laptop model identifiers, matched as substrings of
# SystemProductName (e.g. "ASUS TUF Gaming A15 FA506NCR_FA506NCR"). Only models
# tested on real hardware belong here: other FA506 variants are not verified.
VERIFIED_MODELS: Tuple[str, ...] = (
    "FA506NCR",
)


# ----------------------------------------------------------- Packet Builders


def build_color_packet(r: int, g: int, b: int, save: bool = False, dev_id: int = TUF_KB_DEVID) -> bytes:
    """Build the 18-byte DEVS ACPI packet for setting static keyboard color.

    If ``save`` is True, opcode ``0xB4`` is used to persist the color to BIOS.
    If ``save`` is False (default), opcode ``0xB3`` is used for volatile live
    updates to EC/RAM without flash memory wear.
    """
    opcode = OPCODE_SAVE if save else OPCODE_SET
    r_val = _channel(r)
    g_val = _channel(g)
    b_val = _channel(b)

    # 6-byte setting: [opcode, mode=0, R, G, B, speed=0]
    setting = bytes([opcode, 0x00, r_val, g_val, b_val, 0x00])
    devs_args = struct.pack("<I", dev_id) + setting
    packet = struct.pack("<II", DEVS_MAGIC, len(devs_args)) + devs_args
    _check_packet(packet)
    return packet


def build_dsts_packet(dev_id: int = TUF_KB_DEVID) -> bytes:
    """Build the 12-byte DSTS ACPI query packet to test device presence."""
    dev_bytes = struct.pack("<I", dev_id)
    packet = struct.pack("<II", DSTS_MAGIC, len(dev_bytes)) + dev_bytes
    _check_packet(packet)
    return packet


def _check_packet(packet: bytes) -> None:
    """Validate that the packet matches strict ASUS ACPI specifications."""
    if len(packet) == DEVS_PACKET_LENGTH:
        magic, args_len, devid, opcode = struct.unpack("<IIIB", packet[:13])
        if magic != DEVS_MAGIC:
            raise ValueError(f"Invalid ASUS ACPI DEVS magic: expected 0x{DEVS_MAGIC:08X}, got 0x{magic:08X}")
        if args_len != DEVS_PAYLOAD_LENGTH:
            raise ValueError(f"Invalid ASUS ACPI DEVS payload length: expected {DEVS_PAYLOAD_LENGTH}, got {args_len}")
        if devid not in SUPPORTED_DEVICES:
            raise ValueError(f"Invalid ASUS ACPI target device: 0x{devid:08X}")
        if opcode not in ALLOWED_OPCODES:
            raise ValueError(f"Disallowed ASUS ACPI opcode: 0x{opcode:02X}")
    elif len(packet) == DSTS_PACKET_LENGTH:
        magic, args_len, devid = struct.unpack("<III", packet)
        if magic != DSTS_MAGIC:
            raise ValueError(f"Invalid ASUS ACPI DSTS magic: expected 0x{DSTS_MAGIC:08X}, got 0x{magic:08X}")
        if args_len != DSTS_PAYLOAD_LENGTH:
            raise ValueError(f"Invalid ASUS ACPI DSTS payload length: expected {DSTS_PAYLOAD_LENGTH}, got {args_len}")
        if devid not in SUPPORTED_DEVICES:
            raise ValueError(f"Invalid ASUS ACPI target device: 0x{devid:08X}")
    else:
        raise ValueError(f"Invalid ASUS ACPI packet length: got {len(packet)}")


# ----------------------------------------------------- Low-Level Win32 Layer


def _init_kernel32() -> Any:
    """Initialize kernel32 function signatures with explicit 64-bit types."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    k32.CreateFileW.restype = wintypes.HANDLE

    k32.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    k32.DeviceIoControl.restype = wintypes.BOOL

    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    return k32


class _Win32Acpi:
    """Real Win32 ACPI communication via kernel32.dll."""

    def __init__(self) -> None:
        self._k32: Any = None

    def _get_k32(self) -> Any:
        if self._k32 is None:
            self._k32 = _init_kernel32()
        return self._k32

    def open_device(self) -> int:
        if sys.platform != "win32":
            raise OSError("ASUS ATKACPI is only available on Windows")
        import ctypes
        from ctypes import wintypes

        k32 = self._get_k32()
        invalid_handle = wintypes.HANDLE(-1).value

        handle = k32.CreateFileW(
            r"\\.\ATKACPI",
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if handle == invalid_handle or not handle:
            code = ctypes.get_last_error()
            raise OSError(f"Could not open \\\\.\\ATKACPI (Win32 error {code})")
        return int(handle)

    def send_control(self, handle: int, packet: bytes) -> None:
        _check_packet(packet)
        import ctypes
        from ctypes import wintypes

        k32 = self._get_k32()
        out_buf = ctypes.create_string_buffer(16)
        bytes_returned = wintypes.DWORD(0)

        ok = k32.DeviceIoControl(
            handle,
            CONTROL_CODE,
            packet,
            len(packet),
            out_buf,
            16,
            ctypes.byref(bytes_returned),
            None,
        )
        if not ok:
            code = ctypes.get_last_error()
            raise OSError(f"DeviceIoControl to \\\\.\\ATKACPI failed (Win32 error {code})")
        if bytes_returned.value < 4:
            raise OSError("ASUS ACPI call failed: firmware returned no status data")
        status = struct.unpack("<I", out_buf.raw[:4])[0]
        if status != 1:
            raise OSError(f"ASUS ACPI call rejected by firmware (status 0x{status:08X})")

    def query_status(self, handle: int, dev_id: int) -> int:
        packet = build_dsts_packet(dev_id)
        _check_packet(packet)
        import ctypes
        from ctypes import wintypes

        k32 = self._get_k32()
        out_buf = ctypes.create_string_buffer(16)
        bytes_returned = wintypes.DWORD(0)

        ok = k32.DeviceIoControl(
            handle,
            CONTROL_CODE,
            packet,
            len(packet),
            out_buf,
            16,
            ctypes.byref(bytes_returned),
            None,
        )
        if not ok:
            code = ctypes.get_last_error()
            raise OSError(f"DeviceIoControl (DSTS) to \\\\.\\ATKACPI failed (Win32 error {code})")
        if bytes_returned.value < 4:
            raise OSError("ASUS ACPI DSTS query returned no status data")
        return struct.unpack("<I", out_buf.raw[:4])[0]

    def close_device(self, handle: int) -> None:
        if handle:
            k32 = self._get_k32()
            k32.CloseHandle(handle)


# Global ACPI provider. Tests replace this with a mock implementation.
_acpi: Any = _Win32Acpi()


# ------------------------------------------------ System Detection & Discovery


def is_verified_model(model_name: str) -> bool:
    """Check whether a system product name matches verified ASUS laptop models."""
    upper = model_name.upper()
    return any(v.upper() in upper for v in VERIFIED_MODELS)


def _get_system_info() -> Tuple[bool, str]:
    """Inspect system manufacturer and model safely via Windows registry."""
    if sys.platform != "win32":
        return False, ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS") as key:
            sys_mfr = str(winreg.QueryValueEx(key, "SystemManufacturer")[0] or "").strip().upper()
            base_mfr = str(winreg.QueryValueEx(key, "BaseBoardManufacturer")[0] or "").strip().upper()
            try:
                prod_name = str(winreg.QueryValueEx(key, "SystemProductName")[0] or "").strip()
            except OSError:
                prod_name = ""

            if "ASUS" in sys_mfr or "ASUSTEK" in sys_mfr or "ASUS" in base_mfr or "ASUSTEK" in base_mfr:
                return True, prod_name or DEVICE_MODEL
    except Exception as exc:
        logger.debug("Failed to read system BIOS manufacturer: %s", exc)
    return False, ""


def _query_device_id(handle: int) -> Optional[int]:
    """Return the keyboard device ID whose DSTS reports presence on ``handle``.

    Asks the primary ID (0x00100056) first, then the fallback (0x0010005A).
    Returns None when neither is present. Query errors propagate.
    """
    for dev_id in (TUF_KB_DEVID, TUF_KB_DEVID_FALLBACK):
        status = _acpi.query_status(handle, dev_id)
        if (status & DSTS_PRESENCE_BIT) != 0:
            return dev_id
    return None


def _probe_device_id() -> Optional[int]:
    """Open ATKACPI, find the present keyboard device ID, and close it again.

    Does no system gating: callers check manufacturer and model first.
    Returns None on any failure.
    """
    try:
        handle = _acpi.open_device()
        try:
            return _query_device_id(handle)
        finally:
            _acpi.close_device(handle)
    except Exception:
        return None


def _is_supported_system(is_asus: bool, model: str) -> bool:
    return is_asus and is_verified_model(model)


def detect_device_id() -> Optional[int]:
    """Return the ATKACPI keyboard device ID this laptop answers on, if any.

    Verifies manufacturer, checks against the verified models table, opens
    ATKACPI, and queries firmware presence via DSTS (devid 0x00100056 with
    0x0010005A fallback). Returns None when the machine is not supported or
    neither ID reports presence.
    """
    is_asus, model = _get_system_info()
    if not _is_supported_system(is_asus, model):
        return None
    return _probe_device_id()


def device_present(device: Optional[DeviceLike] = None) -> bool:
    """Return whether an ASUS laptop with ATKACPI lighting support is present."""
    return detect_device_id() is not None


def descriptor_for(model_name: str) -> Dict[str, Any]:
    """Build the LumiSync device descriptor for the ASUS laptop keyboard.

    Note on transport: The physical communication is ACPI/WMI via the ASUS
    embedded controller. We report transport="usb" here because LumiSync's
    transport adapter registry and validation currently require "usb" for local
    hardware until a dedicated ACPI transport is introduced.
    """
    clean_name = model_name if model_name else DEVICE_MODEL
    return {
        "device_id": "asus_laptop_keyboard",
        "name": f"{clean_name} Lighting",
        "model": clean_name,
        "transport": "usb",
        "type": DEVICE_TYPE,
        "device_kind": DEVICE_KIND,
        "vendor": "asus",
    }


def discover() -> List[Dict[str, Any]]:
    """Enumerate-only discovery for ASUS laptop keyboards.

    Verifies system manufacturer and model against verified hardware first.
    If the machine matches and DSTS presence confirms lighting hardware,
    returns its device descriptor.
    """
    # Read the registry once and probe ACPI directly, rather than through
    # device_present(), which would read the registry a second time.
    is_asus, model = _get_system_info()
    if not _is_supported_system(is_asus, model):
        return []

    dev_id = _probe_device_id()
    if dev_id is not None:
        logger.info(
            "Found ASUS laptop keyboard lighting on %s (device 0x%08X)", model or "ASUS laptop", dev_id
        )
        return [descriptor_for(model)]
    return []


def lighting_options(device: Any) -> None:
    """Return lighting options for this driver (None for single-zone static)."""
    return None


# ---------------------------------------------------- Shared Controller State


class _Controller:
    """Shared hardware controller managing the ATKACPI device handle."""

    def __init__(self) -> None:
        self.handle: Optional[int] = None
        self.lock = threading.Lock()
        self.users: Set[int] = set()
        self.color: RGB = (255, 255, 255)
        self.brightness: int = 100
        self.power_on: Optional[bool] = None
        # Track whether a manual color or brightness was set by the user.
        # Until then, manual state is unknown, so ending a stream or reapplying
        # does not force white onto the keyboard.
        self.manual_known = False
        self.last_stream_color: Optional[RGB] = None
        # The last non-black RGB actually sent to the keyboard while it was on
        # (brightness already applied). Power-on restores it when no manual
        # color is known, so the keyboard does not stay black after sync + off.
        self.last_shown: Optional[RGB] = None
        # The keyboard device ID that answered DSTS when the handle was opened.
        self.dev_id: Optional[int] = None

    def open_handle(self) -> None:
        if self.handle is not None:
            return
        handle = _acpi.open_device()
        try:
            dev_id = _query_device_id(handle)
        except BaseException:
            _acpi.close_device(handle)
            raise
        if dev_id is None:
            _acpi.close_device(handle)
            raise OSError(
                "ASUS ATKACPI keyboard lighting is not present: DSTS reported neither "
                f"device 0x{TUF_KB_DEVID:08X} nor 0x{TUF_KB_DEVID_FALLBACK:08X}"
            )
        self.handle = handle
        self.dev_id = dev_id
        logger.info("ASUS ATKACPI lighting interface opened (device 0x%08X)", dev_id)

    def send_color(self, r: int, g: int, b: int, save: bool = False) -> None:
        """Send a static color DEVS packet to the detected keyboard device ID."""
        self.open_handle()
        assert self.handle is not None and self.dev_id is not None
        packet = build_color_packet(r, g, b, save=save, dev_id=self.dev_id)
        _acpi.send_control(self.handle, packet)
        if (r, g, b) != (0, 0, 0):
            self.last_shown = (r, g, b)

    def close_handle(self) -> None:
        if self.handle is not None:
            try:
                _acpi.close_device(self.handle)
            finally:
                self.handle = None
                self.dev_id = None
            logger.info("ASUS ATKACPI lighting interface closed")


_controller_instance = _Controller()
_controller_lock = threading.Lock()


def _get_shared_controller() -> _Controller:
    return _controller_instance


# ----------------------------------------------------------- Transport Adapter


class AsusLaptopAdapter(TransportAdapter):
    """LumiSync TransportAdapter for the ASUS laptop keyboard (verified on the TUF Gaming A15 FA506NCR)."""

    def __init__(self, device: DeviceLike) -> None:
        super().__init__(device)
        self._controller = _get_shared_controller()
        self._registered = False
        with _controller_lock:
            self._controller.users.add(id(self))
            self._registered = True

    @classmethod
    def capabilities_for(cls, device: DeviceLike) -> DeviceCapabilities:
        return DeviceCapabilities(
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

    @property
    def capabilities(self) -> DeviceCapabilities:
        return self.capabilities_for(self.device)

    def _apply_color(self, color: RGB, brightness: int, save: bool = False) -> None:
        level = max(0, min(100, brightness))
        factor = level / 100.0
        r = int(color[0] * factor)
        g = int(color[1] * factor)
        b = int(color[2] * factor)
        self._controller.send_color(r, g, b, save=save)

    def set_power(self, on: bool) -> None:
        with self._controller.lock:
            ctrl = self._controller
            if on:
                # Restore with 0xB3 only: the manual color if one is known,
                # else the last color the keyboard showed (e.g. a stream frame).
                # The default white is used only after our own power-off when
                # nothing was ever shown.
                if ctrl.manual_known:
                    self._apply_color(ctrl.color, ctrl.brightness, save=False)
                elif ctrl.last_shown is not None:
                    ctrl.send_color(*ctrl.last_shown, save=False)
                elif ctrl.power_on is False:
                    self._apply_color(ctrl.color, ctrl.brightness, save=False)
                ctrl.power_on = True
            else:
                # Power-off writes black (0, 0, 0) via 0xB3 (RAM only).
                # Never 0xB4, so black does not persist across reboots.
                ctrl.send_color(0, 0, 0, save=False)
                ctrl.power_on = False

    def set_brightness(self, percent: int) -> None:
        level = max(0, min(100, int(percent)))
        with self._controller.lock:
            self._controller.manual_known = True
            if self._controller.power_on is not False:
                self._apply_color(self._controller.color, level, save=False)
                self._controller.power_on = True
            self._controller.brightness = level

    def set_color(self, r: int, g: int, b: int) -> None:
        color = (_channel(r), _channel(g), _channel(b))
        with self._controller.lock:
            self._controller.manual_known = True
            if self._controller.power_on is not False:
                # User explicit manual color change: save=True (0xB4) so it persists across reboots.
                # One 0xB4 packet per manual color change. The saved color includes the
                # brightness at this moment, since brightness itself is only sent with 0xB3.
                self._apply_color(color, self._controller.brightness, save=True)
                self._controller.power_on = True
            self._controller.color = color

    def set_segments(self, colors: List[RGB]) -> None:
        if not colors:
            return
        color = average_color(colors)
        with self._controller.lock:
            # Drop identical frames or changes below threshold to reduce ACPI bus traffic
            if self._controller.last_stream_color is not None:
                diff = max(abs(int(a) - int(b)) for a, b in zip(self._controller.last_stream_color, color))
                if diff < SYNC_COLOR_THRESHOLD:
                    return
            self._controller.last_stream_color = color
            # ALWAYS save=False (0xB3), NEVER 0xB4 in streams
            self._apply_color(color, self._controller.brightness, save=False)
            self._controller.power_on = True

    def begin_stream(self) -> None:
        with self._controller.lock:
            self._controller.open_handle()
            self._controller.last_stream_color = None

    def end_stream(self) -> None:
        with self._controller.lock:
            self._controller.last_stream_color = None
            # Only restore when a manual color was explicitly requested; otherwise leave last frame
            if self._controller.manual_known and self._controller.power_on is not False:
                self._apply_color(self._controller.color, self._controller.brightness, save=False)

    def reapply(self) -> None:
        with self._controller.lock:
            if self._controller.manual_known and self._controller.power_on is not False:
                self._apply_color(self._controller.color, self._controller.brightness, save=False)

    def close(self) -> None:
        if not self._registered:
            return
        self._registered = False
        with _controller_lock:
            self._controller.users.discard(id(self))
            if not self._controller.users:
                with self._controller.lock:
                    self._controller.close_handle()


__all__ = [
    "ALLOWED_OPCODES",
    "CONTROL_CODE",
    "DEVS_MAGIC",
    "DEVS_PACKET_LENGTH",
    "DEVICE_KIND",
    "DEVICE_MODEL",
    "DEVICE_TYPE",
    "DSTS_MAGIC",
    "DSTS_PACKET_LENGTH",
    "DSTS_PRESENCE_BIT",
    "FALLBACK_NAME",
    "NOT_FOUND_MESSAGE",
    "OPCODE_SAVE",
    "OPCODE_SET",
    "SUPPORTED_DEVICES",
    "SYNC_COLOR_THRESHOLD",
    "TUF_KB_DEVID",
    "TUF_KB_DEVID_FALLBACK",
    "VERIFIED_MODELS",
    "AsusLaptopAdapter",
    "build_color_packet",
    "build_dsts_packet",
    "descriptor_for",
    "detect_device_id",
    "device_present",
    "discover",
    "is_verified_model",
    "lighting_options",
]
