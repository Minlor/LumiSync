"""iDotMatrix Bluetooth-LE driver — LumiSync's own protocol implementation.

Pixel-matrix displays (16x16, 16x32, 16x64, 32x32) controlled over BLE GATT.
The transport constants and framing here were derived by inspecting the official
app (``com.tech.idotmatrix`` v2.1.2); the encoder below is LumiSync's own code,
not a port of any third-party library.

Two layers:

* **Encoder** (pure, unit-tested): frame construction, command builders, and
  screen->matrix pixel packing. This is transport-independent and verifiable
  without hardware.
* **Transport** (:class:`IDotMatrixBleAdapter`): a ``bleak`` GATT client driven
  from a private asyncio loop so the sync (Qt) threads can call it synchronously.
  ``bleak`` is an optional dependency; the adapter raises a clear error if it is
  missing.

PROVISIONAL opcodes are marked below. They match the app's structure but should
be confirmed against a BLE capture on a real device before shipping; each is a
named constant so correcting it is a one-line change.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple

from .base import RGB, DeviceCapabilities, TransportAdapter

# --- Confirmed from the app (com.tech.idotmatrix 2.1.2) ---
# Service + write characteristic (com.heaton.baselib.ble.BleManager UUID_*).
SERVICE_UUID = "000000fa-0000-1000-8000-00805f9b34fb"
WRITE_CHAR_UUID = "0000fa02-0000-1000-8000-00805f9b34fb"
# The app subscribes to this "read/version" characteristic for responses.
NOTIFY_CHAR_UUID = "d44bc439-abfd-45a2-b575-925416129602"
OTA_SERVICE_UUID = "0000ae00-0000-1000-8000-00805f9b34fb"

# Matrix resolutions the app ships assets for, as (cols, rows).
KNOWN_SIZES: Dict[str, Tuple[int, int]] = {
    "16x16": (16, 16),
    "16x32": (16, 32),
    "16x64": (16, 64),
    "32x32": (32, 32),
}
DEFAULT_SIZE = (32, 32)

# Command headers, confirmed from the decompiled app (BleProtocolN). The body is
# the header + args; frame() prepends the 2-byte little-endian total length, so
# e.g. brightness => [5,0,4,0x80,val], color => [7,0,2,2,r,g,b].
_OP_BRIGHTNESS = (0x04, 0x80)   # setLight(value)          -> [5,0,4,0x80,val]
_OP_POWER = (0x07, 0x01)        # sendSwitchplate(on)      -> [5,0,7,1,on]
_OP_COLOR = (0x02, 0x02)        # sendColor(r,g,b)         -> [7,0,2,2,r,g,b]


# ----------------------------------------------------------------- encoder

def frame(body: bytes) -> bytes:
    """Wrap a command body with iDotMatrix's little-endian total-length prefix.

    Frame = [total_lo, total_hi, <body>], where total counts the two length
    bytes plus the body. Matches the app: every command byte array begins with
    ``[len & 0xFF, (len >> 8) & 0xFF]``.
    """
    total = len(body) + 2
    return bytes([total & 0xFF, (total >> 8) & 0xFF]) + bytes(body)


def build_brightness_frame(percent: int) -> bytes:
    value = max(0, min(100, int(percent)))
    return frame(bytes([*_OP_BRIGHTNESS, value]))


def build_power_frame(on: bool) -> bytes:
    return frame(bytes([*_OP_POWER, 0x01 if on else 0x00]))


def build_color_frame(r: int, g: int, b: int) -> bytes:
    """Solid-color command (whole panel), matching the app's ``sendColor``."""
    return frame(bytes([*_OP_COLOR, int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF]))


# --- DIY pixel drawing (confirmed from SendCore.payload type 5 + BleProtocolN) ---
# A drawing is sent as one packet per distinct color:
#   [len_lo, len_hi, 5, 1, moveType, r, g, b, x0, y0, x1, y1, ...]
# framed by frame(). moveType 0 = static (NO_EFFECT). Enter/quit DIY mode wrap it.
_DIY_TYPE = (0x05, 0x01)
_MOVE_NONE = 0
# DiyImageFun modes for the enter/quit command ([5,0,4,1,mode]).
DIY_ENTER_CLEAR = 1
DIY_QUIT_KEEP = 2
DIY_ENTER_NOCLEAR = 3


def build_diy_mode_frame(mode: int) -> bytes:
    """Enter/quit the panel's DIY drawing mode (enterDiy/setDiyFunMode)."""
    return frame(bytes([0x04, 0x01, int(mode) & 0xFF]))


def build_diy_color_group(r: int, g: int, b: int, coords, move: int = _MOVE_NONE) -> bytes:
    """One DIY packet: paint every ``(x, y)`` in ``coords`` with one color."""
    body = bytearray([*_DIY_TYPE, int(move) & 0xFF, int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF])
    for x, y in coords:
        body += bytes([int(x) & 0xFF, int(y) & 0xFF])
    return frame(bytes(body))


def build_diy_frames(grid: List[List[RGB]], skip_black: bool = True) -> List[bytes]:
    """Encode a 2D pixel grid (rows of RGB) into DIY color-group packets.

    Pixels are grouped by color so each color is one packet. Pure black is
    skipped by default (a freshly-cleared panel is already black).
    """
    groups: Dict[RGB, list] = {}
    for y, row in enumerate(grid):
        for x, color in enumerate(row):
            rgb = (int(color[0]) & 0xFF, int(color[1]) & 0xFF, int(color[2]) & 0xFF)
            if skip_black and rgb == (0, 0, 0):
                continue
            groups.setdefault(rgb, []).append((x, y))
    return [
        build_diy_color_group(rgb[0], rgb[1], rgb[2], coords)
        for rgb, coords in groups.items()
    ]


def pack_pixels(pixels: List[RGB]) -> bytes:
    """Pack RGB tuples into a flat R,G,B byte stream (row-major)."""
    out = bytearray()
    for r, g, b in pixels:
        out += bytes((int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF))
    return bytes(out)


def average_color(colors: List[RGB]) -> RGB:
    """Mean color of a frame — used to drive the panel as one ambient light."""
    if not colors:
        return (0, 0, 0)
    n = len(colors)
    r = sum(int(c[0]) for c in colors) // n
    g = sum(int(c[1]) for c in colors) // n
    b = sum(int(c[2]) for c in colors) // n
    return (r, g, b)


def chunk(data: bytes, size: int) -> List[bytes]:
    """Split ``data`` into BLE-write-sized chunks (last may be shorter)."""
    size = max(1, int(size))
    return [data[i : i + size] for i in range(0, len(data), size)]


def screen_to_pixels(frame_rgb, size: Tuple[int, int]) -> List[RGB]:
    """Downscale an ``(H, W, 3)`` RGB array to one color per matrix cell.

    Row-major, reusing the strip pipeline's zone averaging.
    """
    import numpy as np

    from ..sync import processing

    cols, rows = size
    mapping = [
        {"x": col / cols, "y": row / rows, "w": 1.0 / cols, "h": 1.0 / rows}
        for row in range(rows)
        for col in range(cols)
    ]
    return processing.average_zone_colors(np.asarray(frame_rgb), mapping)


# --------------------------------------------------------------- transport

class _BleLoop:
    """A private asyncio event loop on a daemon thread.

    Lets synchronous callers (the sync engine, Qt slots) drive ``bleak``'s async
    API via :meth:`run`. One loop is shared by all iDotMatrix adapters.
    """

    _instance: Optional["_BleLoop"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        import asyncio

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="lumisync-ble", daemon=True
        )
        self._thread.start()

    @classmethod
    def instance(cls) -> "_BleLoop":
        with cls._lock:
            if cls._instance is None:
                cls._instance = _BleLoop()
            return cls._instance

    def run(self, coro, timeout: float = 10.0):
        import asyncio

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout)


def looks_like_idotmatrix(name: Optional[str]) -> bool:
    """Heuristic match on a BLE advertised name for an iDotMatrix panel."""
    n = (name or "").strip().lower()
    if not n:
        return False
    return (
        n.startswith("idm")
        or "idotmatrix" in n
        or "idot" in n
        or "dotmatrix" in n
    )


def _require_bleak():
    try:
        import bleak  # noqa: F401
        return bleak
    except ImportError as exc:  # pragma: no cover - depends on optional dep
        raise RuntimeError(
            "iDotMatrix support needs the 'bleak' package. Install it with "
            "'pip install bleak' (or 'pip install lumisync[ble]')."
        ) from exc


class IDotMatrixBleAdapter(TransportAdapter):
    """Control an iDotMatrix pixel display over BLE.

    ``device`` should carry ``ble_address`` (or ``mac``) and optionally
    ``matrix_size`` (e.g. "32x32"). The GATT connection is opened lazily on first
    use and reused for subsequent frames.
    """

    def __init__(self, device: Dict[str, Any]) -> None:
        super().__init__(device)
        size_key = str(device.get("matrix_size", "")).lower()
        self._size = KNOWN_SIZES.get(size_key, DEFAULT_SIZE)
        self._address = device.get("ble_address") or device.get("mac")
        self._client = None
        self._mtu = 512
        self._last_response = None

    @property
    def capabilities(self) -> DeviceCapabilities:
        cols, rows = self._size
        return DeviceCapabilities(
            transport="ble",
            segment_count=cols * rows,
            supports_power=True,
            supports_brightness=True,
            supports_color=True,
            supports_segments=True,
            matrix_size=self._size,
        )

    # --- connection ---
    def _ensure_client(self):
        if self._client is not None:
            return self._client
        bleak = _require_bleak()
        if not self._address:
            raise RuntimeError("iDotMatrix device has no BLE address to connect to.")

        async def _connect():
            client = bleak.BleakClient(self._address)
            await client.connect()
            # The app subscribes for responses right after connecting; some
            # firmware only act on commands once notifications are enabled.
            try:
                await client.start_notify(NOTIFY_CHAR_UUID, self._on_notify)
            except Exception:
                pass
            return client

        self._client = _BleLoop.instance().run(_connect(), timeout=20.0)
        return self._client

    def _on_notify(self, _sender, data: bytes) -> None:
        # Responses are logged for debugging / future status parsing.
        self._last_response = bytes(data)

    def _write(self, data: bytes) -> None:
        client = self._ensure_client()

        async def _send():
            # The app writes with response (Android default write type), and the
            # panel ignores no-response writes — so we must request a response.
            for piece in chunk(data, self._mtu):
                await client.write_gatt_char(WRITE_CHAR_UUID, piece, response=True)

        _BleLoop.instance().run(_send(), timeout=10.0)

    # --- control surface ---
    def set_power(self, on: bool) -> None:
        self._write(build_power_frame(on))

    def set_brightness(self, percent: int) -> None:
        self._write(build_brightness_frame(percent))

    def set_color(self, r: int, g: int, b: int) -> None:
        self._write(build_color_frame(r, g, b))

    def set_segments(self, colors: List[RGB]) -> None:
        # For real-time sync, drive the panel as one ambient light (averaging the
        # frame). Per-pixel DIY frames are too many BLE packets for high FPS;
        # use draw_grid() for static drawings/animations instead.
        self.set_color(*average_color(list(colors)))

    # --- DIY pixel drawing / animation ---
    def draw_grid(self, grid: List[List[RGB]], clear: bool = True) -> None:
        """Draw a full 2D pixel grid (rows of RGB) on the panel via DIY mode."""
        self._write(build_diy_mode_frame(DIY_ENTER_CLEAR if clear else DIY_ENTER_NOCLEAR))
        for packet in build_diy_frames(grid):
            self._write(packet)
        self._write(build_diy_mode_frame(DIY_QUIT_KEEP))

    def show_image(self, frame_rgb) -> None:
        """Downscale an ``(H, W, 3)`` RGB array to the matrix and draw it."""
        cols, rows = self._size
        pixels = screen_to_pixels(frame_rgb, self._size)
        grid = [[pixels[y * cols + x] for x in range(cols)] for y in range(rows)]
        self.draw_grid(grid)

    def play_animation(self, frames: List[List[List[RGB]]], frame_delay: float = 0.2) -> None:
        """Play a sequence of pixel grids as an animation.

        Blocking; intended to run on a worker thread. Each frame is drawn without
        clearing black-to-black so motion looks continuous.
        """
        import time

        for grid in frames:
            self.draw_grid(grid, clear=True)
            time.sleep(max(0.0, frame_delay))

    @staticmethod
    def discover(timeout: float = 8.0) -> List[Dict[str, Any]]:
        """Scan for nearby BLE devices and flag likely iDotMatrix panels.

        We do NOT pre-filter by service UUID: iDotMatrix panels advertise by name
        (e.g. ``IDM-...``) and only expose the ``000000fa`` service after
        connecting, so a service-UUID filter hides them. Instead we return every
        named device with a ``likely`` flag (name heuristic OR the service showing
        up in the advertisement), and let the caller decide.

        Note: a BLE panel usually stops advertising while the phone app is
        connected to it — disconnect the app first, or it will be invisible.
        """
        bleak = _require_bleak()

        async def _scan():
            found = await bleak.BleakScanner.discover(timeout=timeout, return_adv=True)
            results = []
            for address, (device, adv) in found.items():
                name = device.name or getattr(adv, "local_name", None) or ""
                service_uuids = [
                    str(u).lower() for u in (getattr(adv, "service_uuids", None) or [])
                ]
                likely = looks_like_idotmatrix(name) or SERVICE_UUID.lower() in service_uuids
                results.append(
                    {
                        "ble_address": address,
                        "model": name or "Unknown",
                        "transport": "ble",
                        "likely": likely,
                    }
                )
            return results

        return _BleLoop.instance().run(_scan(), timeout=timeout + 8.0)

    def close(self) -> None:
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            _BleLoop.instance().run(client.disconnect(), timeout=5.0)
        except Exception:
            pass
