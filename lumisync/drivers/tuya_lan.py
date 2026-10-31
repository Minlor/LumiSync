"""Tuya / LSC Smart Connect LAN driver.

LSC Smart Connect (Action's retail smart-home app) is a rebrand of the Tuya
"Smart Life" platform — confirmed by decompiling ``com.lscsmartconnection.smart``
v2.0.4, whose native libraries are Tuya's SDK (``libthingsmart.so``,
``libThingSmartLink.so``, ``libthing_security.so``). See
the account setup section in ``README.md``.

Tuya WiFi bulbs and strips speak the **Tuya local protocol** over TCP ``6668``:
every command is AES-encrypted with a per-device 16-byte *local key* that is
provisioned at pairing by the vendor account service. Unlike Govee LAN (open UDP
JSON) or iDotMatrix BLE (open GATT), a Tuya device is uncontrollable without
that key, and the key cannot be recovered by decompiling the app — the user
must authorize access to it (see the account setup section in ``README.md``).

The account integrations can obtain authorized local keys; this adapter itself
speaks only the local protocol using the OS credential store or a supplied key.

Two layers, mirroring the iDotMatrix driver:

* **Encoder** (pure, unit-tested): maps LumiSync's power/brightness/color/white
  operations onto Tuya light *data points* (DPs). This is the LumiSync-specific
  part and is verified with golden values, no hardware or network needed.
* **Transport** (:class:`TuyaLightAdapter`): drives the encrypted session via
  ``tinytuya`` (bundled with LumiSync). Tuya's five protocol versions plus the
  AES-GCM/HMAC session handshake are complex and security-sensitive, so LumiSync
  uses the mature ``tinytuya`` implementation for the wire/crypto rather than
  reimplementing it; the DP mapping above is ours.
"""

from __future__ import annotations

import base64
import binascii
import colorsys
import re
import select
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any, Dict, List, Optional

from ..device import DeviceLike
from .base import RGB, DeviceCapabilities, TransportAdapter
from . import tuya_aoi, tuya_presence
from ..accounts.secrets import local_key_for
from ..accounts.tuya import code_for, dp_layout, paint_dp, value_range
from ..utils.colors import resample_colors_to_count
from ..utils.logging import get_logger

logger = get_logger("lumisync_tuya")

# Standard Tuya lighting data points (category "dj").
#
# Modern data-point layout ("v2"), used by current LSC WiFi bulbs/strips *and*
# the LSC "dd"-family dreamlight strips (ids 18+, old code but new payloads):
#   20 switch_led (bool) | 21 work_mode (enum) | 22 bright_value (10-1000)
#   23 temp_value (0-1000) | 24 colour_data (HHHHSSSSVVVV hex, H 0-360 S/V 0-1000)
# Legacy layout ("v1"), used by older bulbs:
#   1 switch (bool) | 2 mode | 3 bright (25-255) | 4 temp (0-255)
#   5 colour_data (RRGGBBHHHHSSVV hex, S/V as 0-255)
DP_V2 = {
    "power": 20,
    "mode": 21,
    "brightness": 22,
    "temperature": 23,
    "colour": 24,
}
DP_V1 = {
    "power": 1,
    "mode": 2,
    "brightness": 3,
    "temperature": 4,
    "colour": 5,
}

_BRIGHT_V2_MIN, _BRIGHT_V2_MAX = 10, 1000
_BRIGHT_V1_MIN, _BRIGHT_V1_MAX = 25, 255


# ----------------------------------------------------------------- encoder

def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _colour_mode(schema: str) -> str:
    """Historic 'v1'/'v2' names map onto the internal 'legacy'/'hsv' formats."""
    return {"v1": "legacy", "v2": "hsv"}.get(str(schema).lower(), str(schema).lower())


def rgb_to_hsv_tuya(r: int, g: int, b: int) -> tuple[int, int, int]:
    """Convert 0-255 RGB to Tuya's HSV units: H in 0-360, S and V in 0-1000."""
    rf, gf, bf = (_clamp(r, 0, 255) / 255, _clamp(g, 0, 255) / 255, _clamp(b, 0, 255) / 255)
    h, s, v = colorsys.rgb_to_hsv(rf, gf, bf)
    return (round(h * 360), round(s * 1000), round(v * 1000))


def encode_colour(r: int, g: int, b: int, *, schema: str = "hsv") -> str:
    """Encode an RGB color as a Tuya ``colour_data`` hex string.

    ``hsv`` (default): ``HHHHSSSSVVVV`` — H 0-360, S/V 0-1000, each 4 hex
    digits. Every LSC Smart Connect strip uses this, including ones whose DP
    code is the legacy ``colour_data``.
    ``legacy``: ``RRGGBBHHHHSSVV`` — RGB prefix plus H/S/V (legacy bulb format,
    S/V 0-255). Historic names 'v1'/'v2' are accepted as aliases.
    """
    h, s, v = rgb_to_hsv_tuya(r, g, b)
    if _colour_mode(schema) == "legacy":
        s255 = round(s * 255 / 1000)
        v255 = round(v * 255 / 1000)
        return f"{_clamp(r, 0, 255):02x}{_clamp(g, 0, 255):02x}{_clamp(b, 0, 255):02x}{h:04x}{s255:02x}{v255:02x}"
    return f"{h:04x}{s:04x}{v:04x}"


def decode_colour(colour: str, *, schema: str = "hsv") -> RGB:
    """Decode a Tuya ``colour_data`` hex string back into 0-255 RGB."""
    if _colour_mode(schema) == "legacy":
        return (
            int(colour[0:2], 16),
            int(colour[2:4], 16),
            int(colour[4:6], 16),
        )
    h, s, v = (int(colour[i:i + 4], 16) for i in (0, 4, 8))
    red, green, blue = colorsys.hsv_to_rgb(h / 360, s / 1000, v / 1000)
    return (round(red * 255), round(green * 255), round(blue * 255))


def encode_brightness(percent: int, *, schema: str = "hsv") -> int:
    """Map a 0-100 percentage onto the Tuya brightness DP range for the schema."""
    pct = _clamp(percent, 0, 100)
    if _colour_mode(schema) == "legacy":
        lo, hi = _BRIGHT_V1_MIN, _BRIGHT_V1_MAX
    else:
        lo, hi = _BRIGHT_V2_MIN, _BRIGHT_V2_MAX
    return round(lo + (hi - lo) * pct / 100)


def encode_temperature(kelvin: int, min_k: int, max_k: int, *, schema: str = "hsv") -> int:
    """Map a color temperature (Kelvin) onto the Tuya temp DP (warm=0 → cool=top).

    The temp DP spans 0-255 on the legacy layout and 0-1000 on the modern one.
    """
    if max_k <= min_k:
        return 0
    frac = (_clamp(kelvin, min_k, max_k) - min_k) / (max_k - min_k)
    top = 255 if _colour_mode(schema) == "legacy" else 1000
    return round(frac * top)


def dp_schema(device: Mapping[str, Any]) -> Dict[str, int]:
    """Return the DP index map for a device (layout 'v1' | 'v2' by DP ids)."""
    defaults = DP_V1 if dp_layout(device) == "v1" else DP_V2
    overrides = device.get("dp_map")
    return {**defaults, **{key: int(value) for key, value in overrides.items() if key in defaults}} if isinstance(overrides, dict) else defaults


def colour_schema(device: Mapping[str, Any]) -> str:
    """'hsv' | 'legacy' colour encoding for this device.

    ``colour_format`` on the descriptor wins; modern DP-numbered devices (DP
    ids from 18 up, including all LSC strips) use the HSV format.
    """
    value = str(device.get("colour_format", "")).strip().lower()
    if value in ("hsv", "legacy"):
        return value
    return "legacy" if dp_layout(device) == "v1" else "hsv"


def build_color_command(device: Mapping[str, Any], r: int, g: int, b: int) -> Dict[int, Any]:
    """DP payload that switches a light to colour mode and sets ``(r,g,b)``."""
    dp = dp_schema(device)
    schema = colour_schema(device)
    result = {dp["colour"]: encode_colour(r, g, b, schema=schema)}
    if "tuya_functions" not in device or code_for(device, "mode"):
        result[dp["mode"]] = "colour"
    return result


# ----------------------------------------------------------------- paint (RGBIC)
#
# Per-zone colour writes use Tuya's "paint" ("涂抹", smear) DP — named
# ``paint_colour_data`` or ``paint_colour_1`` in the cloud spec, seen at DP 61
# on the Battletron/LSC wall-washer family and DP 103 on newer LSC RGB/IC
# strips. The payload grammar below is from Tuya's official tuya-panel-demo
# ``SmearFormater`` (lampLightString panel), byte for byte:
#
#   version(1B) dimmerMode(1B) effect(1B) ledNumber(1B) [mode-specific fields]
#     dimmerMode 1 (colour):  smearMode(1B; 0=all, 1=single, 2=clear), then
#                             hue(2B 0-360) saturation(2B 0-1000) value(2B 0-1000)
#     dimmerMode 3 (combination): combineType(1B), then N × HHHH SSSS VVVV
#
# A real Battletron controller reports DP 61 = "AAEAFAAAAAPoA+g=" — version 0,
# colour mode, one red (H0/S1000/V1000) block smeared over ledNumber 0x14 = 20
# zones — which is the golden frame reproduced by :func:`encode_paint_all`.
# Paint DPs accept at most 128 payload bytes per write.
#
# Byte-verified against the LSC app's own writes to a real Battletron strip
# (telemetry capture, 2026-10-29). The dd-family paint DP does NOT accept the
# combination/dimmerMode-3 grammar; the app paints with these frames:
#   all/colour:   00 01 00 <led> 00 HHHHSSSSVVVV                  (11 bytes)
#   single/paint: 00 01 <effect> <led> 01 HHHHSSSSVVVV 0x80|qty <idx 1B each>
#   clear:        same with smearMode 2 and hue/sat/val = 000000
# where led is the app's zone grid (0x14 = 20 zones on this strip — not the
# pixel count in DP 53), hue is 0-360 and sat/val 0-1000, and each index byte
# carries the 1-based zone number. One colour per write; LumiSync streams the
# sync frame as a series of single-strokes (adjacent zones with the same
# colour merge into one write).

PAINT_MAX_PAYLOAD = 128
PAINT_HEADER_BYTES = 5
PAINT_BLOCK_BYTES = 6
PAINT_CANDIDATES = (61, 103)
PAINT_ZONE_COUNT_MAX = 20  # the app's zone grid (ledNumber byte 0x14)
PAINT_STROKE_DELAY = 0.012  # between strokes of one frame; the MCU drains writes
PAINT_DELTA_TOLERANCE = 12  # summed per-channel change below this is invisible
PAINT_HEALTH_INTERVAL = 200  # frames between liveness pings (~25 s at 8 fps)
PAINT_UPDATE_HZ = 8.0  # sustainable frame rate: ~79 writes/s ceiling, 4-5
# strokes/frame plus the health ping puts 8 fps at roughly half the measured
# MCU limit; higher rates degrade the strip under sustained load.
PAINT_SLOW_FRAME_SECONDS = 0.35  # stroke burst slower than this = MCU stressed
PAINT_COOLDOWN_SECONDS = 0.4  # back off after a stressed frame


def paint_zone_capacity() -> int:
    """Independently colourable zones the strip renders (the app's ledNumber)."""
    return PAINT_ZONE_COUNT_MAX


def encode_paint_all(r: int, g: int, b: int, *, zones: int = 20) -> str:
    """Smear one colour across every paint zone (smear mode 'all')."""
    h, s, v = rgb_to_hsv_tuya(r, g, b)
    hexstr = f"000100{_clamp(zones, 1, 255):02x}00{h:04x}{s:04x}{v:04x}"
    return base64.b64encode(bytes.fromhex(hexstr)).decode("ascii")


def encode_paint_single(indexes: Sequence[int], r: int, g: int, b: int, *,
                        zones: int = 20, effect: int = 1) -> str:
    """Paint one colour across the given 0-based zone indexes (single mode).

    Matches the LSC app's captured payloads: smearMode 1, a ``singleData``
    byte ``0x80 | count`` (singleType = individual points), then one byte per
    1-based zone index.
    """
    body = []
    for index in indexes:
        try:
            if 0 <= int(index) <= 254 and int(index) < _clamp(zones, 1, 255):
                body.append(int(index) + 1)
        except (TypeError, ValueError):
            continue
    if not body:
        body = [1]
    body = body[: min(0x7F, PAINT_MAX_PAYLOAD - 12)]  # 11 header bytes + 1 singleData
    single_data = 0x80 | len(body)
    h, s, v = rgb_to_hsv_tuya(r, g, b)
    index_hex = "".join(f"{i:02x}" for i in body)
    hexstr = (f"0001{_clamp(effect, 0, 1):02x}{_clamp(zones, 1, 255):02x}01"
              f"{h:04x}{s:04x}{v:04x}{single_data:02x}{index_hex}")
    return base64.b64encode(bytes.fromhex(hexstr)).decode("ascii")


def encode_paint_clear(indexes: Sequence[int], *, zones: int = 20) -> str:
    """Clear painting on the given zone indexes (smear mode 'clear')."""
    body = []
    for index in indexes:
        try:
            if 0 <= int(index) <= 254 and int(index) < _clamp(zones, 1, 255):
                body.append(int(index) + 1)
        except (TypeError, ValueError):
            continue
    if not body:
        body = [1]
    body = body[: min(0x7F, PAINT_MAX_PAYLOAD - 12)]  # 11 header bytes + 1 singleData
    single_data = 0x80 | len(body)
    index_hex = "".join(f"{i:02x}" for i in body)
    hexstr = (f"000101{_clamp(zones, 1, 255):02x}02"
              f"000000000000{single_data:02x}{index_hex}")
    return base64.b64encode(bytes.fromhex(hexstr)).decode("ascii")


def encode_paint_strokes(colors: List[RGB], *, zones: Optional[int] = None,
                         previous: Optional[List[RGB]] = None,
                         tolerance: int = 0) -> List[str]:
    """Base64 DP values painting one colour per zone (single-stroke mode).

    Consecutive zones that share a colour merge into one write, matching the
    LSC app's own protocol: the strips take one colour per paint write, so a
    monitor frame becomes a series of compact single strokes.

    Streaming details from live testing:
    * Black zones cannot be painted (the LSC app erases with clear frames) —
      zero-value strokes were left on-screen, so black runs become clear
      frames instead.
    * ``previous`` (the last painted frame) lets zones that are already close
      enough be skipped; ``tolerance`` is the summed per-channel difference
      still treated as unchanged (a few units are invisible, especially while
      the smoothing EMA drifts slowly).
    """
    colors = list(colors) or [(0, 0, 0)]
    count = max(1, min(int(zones or len(colors)), paint_zone_capacity()))
    if len(colors) != count:
        colors = resample_colors_to_count(colors, count)
    previous_colors = None
    if previous:
        previous_colors = resample_colors_to_count(list(previous), count)

    def unchanged(index: int) -> bool:
        if previous_colors is None:
            return False
        target = tuple(int(c) for c in colors[index])
        held = tuple(int(c) for c in previous_colors[index])
        if tolerance <= 0:
            return target == held
        return sum(abs(a - b) for a, b in zip(target, held)) <= tolerance

    frames = []
    start = 0
    while start < count:
        color = tuple(int(c) for c in colors[start])
        skip = unchanged(start)
        black = color == (0, 0, 0)
        end = start + 1
        while end < count:
            candidate = tuple(int(c) for c in colors[end])
            same_skip = unchanged(end)
            if (previous_colors is not None and same_skip != skip) \
                    or (not skip and candidate != color):
                break
            end += 1
        if not skip:
            if black:
                frames.append(encode_paint_clear(range(start, end), zones=count))
            else:
                frames.append(encode_paint_single(range(start, end), *color, zones=count))
        start = end
    return frames


def reduce_palette(colors: List[RGB], limit: int = 5) -> List[RGB]:
    """Posterise a frame to at most ``limit`` colours (k-means, 3 passes).

    The strip takes one write per distinct colour run, so a gradient frame of
    20 distinct colours costs 20 sequential writes (~250 ms) — slower than the
    panel can look smooth. The LSC app's own Razer path streams just four
    colour columns, and quantising to a handful of bands gives the same
    fast, continuous motion on the paint DP. Colours always come from the
    input frame, so palettes stay true to the screen content.
    """
    palette: List[RGB] = [(int(c[0]), int(c[1]), int(c[2])) for c in colors]
    limit = max(1, min(int(limit), len(palette)))
    distinct: List[RGB] = list(dict.fromkeys(palette))
    if len(distinct) <= limit:
        return palette
    # Seed centres with an even sample across the run (ordered by luminance).
    ordered = sorted(distinct, key=lambda c: c[0] * 299 + c[1] * 587 + c[2] * 114)
    step = max(1, len(ordered) // limit)
    centres: List[RGB] = [
        ordered[min(len(ordered) - 1, i * step)] for i in range(limit)
    ]
    for _ in range(3):
        buckets: List[List[RGB]] = [[] for _ in centres]
        for colour in ordered:
            best_index = min(
                range(len(centres)),
                key=lambda i: sum(
                    (a - b) ** 2 for a, b in zip(colour, centres[i])
                ),
            )
            buckets[best_index].append(colour)
        centres = [
            (
                sum(c[0] for c in bucket) // len(bucket),
                sum(c[1] for c in bucket) // len(bucket),
                sum(c[2] for c in bucket) // len(bucket),
            )
            if bucket else centres[i]
            for i, bucket in enumerate(buckets)
        ]
    # Snap each zone to the nearest centre, then to the frame colour nearest
    # that centre so painted colours stay true RGB values from the screen.
    snapped: List[RGB] = []
    for colour in colors:
        best = min(centres, key=lambda c: sum((a - b) ** 2 for a, b in zip(colour, c)))
        candidate = min(distinct, key=lambda c: sum((a - b) ** 2 for a, b in zip(c, best)))
        snapped.append(candidate)
    return snapped


def advance_painted(colors, previous, *, zones: Optional[int] = None,
                    tolerance: int = 0):
    """State of what the strip is actually showing after a streamed frame.

    Zones skipped inside :func:`encode_paint_strokes` keep their older colour,
    so the next frame must compare against that painted state, not the target.
    """
    colors = list(colors) or [(0, 0, 0)]
    count = max(1, min(int(zones or len(colors)), paint_zone_capacity()))
    if len(colors) != count:
        colors = resample_colors_to_count(colors, count)
    if not previous:
        return [(int(c[0]), int(c[1]), int(c[2])) for c in colors]
    held = resample_colors_to_count(list(previous), count)
    state = []
    for index in range(count):
        target = (int(colors[index][0]), int(colors[index][1]), int(colors[index][2]))
        prior = (int(held[index][0]), int(held[index][1]), int(held[index][2]))
        if tolerance > 0 and sum(abs(a - b) for a, b in zip(target, prior)) <= tolerance:
            state.append(prior)
        else:
            state.append(target)
    return state


# ----------------------------------------------------------------- discovery
#
# Tuya devices volunteer every readable data point — with its current value —
# in a status reply, which is the device's own self-description. That snapshot
# answers the questions cloud specs often bury or lack: which DP ids the
# firmware really owns, whether the colour payload is the modern HSV string or
# the legacy one, and where the RGBIC paint DP lives (its current value carries
# a recognizable header). :func:`status_enrichment` reads those signals so the
# device's knowledge is learned from the device itself — no product tables —
# and is then stamped onto its descriptor, which the app saves, so every later
# session starts with it (this is automatic for all users on add or refresh).


def paint_zone_hint(value) -> int | None:
    """Zone count a paint DP's current value advertises, else ``None``.

    Recognizes the dd-family paint payloads (byte-verified against the LSC
    app): version byte 0, dimmerMode 1 with smearMode 0/1/2 (all / single /
    clear — single carries ``0x80|count`` plus one 1-based index byte per
    zone), or the dimmerMode 3 combination grammar used by other RGBIC
    families. Everything else — colour hex strings, scene/music blobs,
    nonzero versions — fails the shape.
    """
    if not isinstance(value, str):
        return None
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(data) < 10 or data[0] != 0 or data[1] not in (1, 3):
        return None
    if data[1] == 1:
        if data[2] > 1 or data[4] not in (0, 1, 2):
            return None
        if data[4] == 0 and len(data) not in (10, 11):
            return None  # all-frame colour
        if data[4] in (1, 2):
            if not 12 <= len(data) <= 12 + 0x7F:
                return None
            single = data[11]
            if single < 0x80 or len(data) - 12 != (single & 0x7F):
                return None  # single/clear must carry exactly qty index bytes
    if data[1] == 3 and (data[2] > 1 or (len(data) - PAINT_HEADER_BYTES) % PAINT_BLOCK_BYTES != 0):
        return None
    zones = data[3]
    return zones if 1 <= zones <= 255 else None


def status_enrichment(dps: Dict[Any, Any]) -> Dict[str, Any]:
    """Descriptor fields derivable from one status snapshot of a device.

    Returns a small dict such as ``{"dp_schema": "v2", "colour_format": "hsv",
    "tuya_paint_dp": 61, "tuya_paint_zones": 20}`` — only fields the snapshot
    speaks to, empty when the snapshot carries nothing recognizable.
    """
    if not isinstance(dps, dict):
        return {}
    ids = sorted(int(i) for i in dps if str(i).isdigit())
    if not ids:
        return {}
    result: Dict[str, Any] = {}
    # Layout from DP numbering: modern firmware numbers from 18 up (ids 20-27),
    # legacy from 1-8. The ids in the status reply are what the firmware owns,
    # which beats any code names a product spec may use.
    if any(dp >= 18 for dp in ids):
        result["dp_schema"] = "v2"
    elif any(1 <= dp <= 8 for dp in ids):
        result["dp_schema"] = "v1"
    # Colour format from the device's own colour value: 14 hex chars is the
    # legacy ``RRGGBBHHHHSSVV`` payload, 12 is ``HHHHSSSSVVVV``.
    legacy = any(isinstance(v, str) and re.fullmatch(r"[0-9a-fA-F]{14}", v) for v in dps.values())
    modern = any(isinstance(v, str) and re.fullmatch(r"[0-9a-fA-F]{12}", v) for v in dps.values())
    if legacy and not modern:
        result["colour_format"] = "legacy"
    elif modern and not legacy:
        result["colour_format"] = "hsv"
    # RGBIC paint DP: known candidates first, then any other DP whose current
    # value has the paint shape — a product numbering its paint DP differently
    # still reports that DP in status every time.
    paint_dp_id, zones = None, None
    for candidate in PAINT_CANDIDATES:
        value = dps.get(str(candidate), dps.get(candidate))
        if value is not None:
            paint_dp_id = candidate
            zones = paint_zone_hint(value)
            break
    else:
        matches = [(int(dp), v) for dp, v in dps.items()
                   if str(dp).isdigit() and isinstance(v, str)
                   and paint_zone_hint(v) is not None]
        for dp, value in sorted(matches):
            paint_dp_id, zones = dp, paint_zone_hint(value)
            break
    if paint_dp_id is not None:
        result["tuya_paint_dp"] = paint_dp_id
        if zones:
            result["tuya_paint_zones"] = zones
    return result


# ----------------------------------------------------------------- transport

def _require_tinytuya():
    try:
        import tinytuya  # noqa: F401
    except ImportError as exc:  # pragma: no cover - tinytuya ships with lumisync
        raise RuntimeError(
            "The 'tinytuya' package is missing — it normally ships with LumiSync. "
            "Reinstall LumiSync ('pip install --force-reinstall lumisync') or run "
            "'pip install tinytuya' to restore Tuya/LSC support."
        ) from exc
    return tinytuya


class _TuyaConnection:
    def __init__(self):
        self.handle = None
        self.lock = threading.RLock()


class TuyaLightAdapter(TransportAdapter):
    """Control one Tuya/LSC WiFi light over the local network.

    Requires ``ip``, ``device_id`` and ``local_key`` on the device descriptor.
    ``protocol_version`` defaults to 3.3 (the most common); 3.4/3.5 devices need
    the matching value. The data-point layout (DP ids 1-8 vs 18+) and the
    colour format are detected from the device's DP ids (see
    :func:`~lumisync.accounts.tuya.dp_layout`); ``dp_schema``/``colour_format``
    overrides work as before.

    RGBIC strips (Battletron, LSC wall washers, newer LSC RGB/IC strips) expose
    a paint DP that takes one colour per zone; it is probed once per session
    and stamped onto the descriptor as ``tuya_paint_dp`` so later sync runs
    start streaming zones without probing again. ``segment_count_override``
    picks the zone count (max 20 per paint payload).
    """

    def __init__(self, device: DeviceLike) -> None:
        super().__init__(device)
        self._connection = _TuyaConnection()
        self._lock = self._connection.lock
        self._paint_dp = paint_dp(device)
        self._paint_probed = self._paint_dp is not None
        self._last_frame: Optional[List[RGB]] = None  # skip unchanged zones
        self._frames_since_check = 0
        # AoI fast channel state: one bounded negotiation try per stream.
        self._aoi_failed = False
        self._aoi_thread: Optional[threading.Thread] = None
        self._closing_aoi = False
        # Last visible colour/brightness and switch state. Tuya strips answer
        # status queries with change-only echoes (often just the paint DP or
        # the switch), so every field needs a remembered value: otherwise the
        # UI flips to "unknown" or 0% after each write.
        self._last_colour: Optional[RGB] = None
        self._last_brightness: Optional[int] = None
        self._power_on: Optional[bool] = None
        self._streaming = False
        # Whether the solid base colour is currently black. Paint streaming
        # anchors it to black so cleared zones stay dark; a power-on must
        # restore a visible colour instead of lighting nothing.
        self._base_black: Optional[bool] = None
        self._resume_colour: Optional[str] = None

    @property
    def _dev(self):
        return self._connection.handle

    @_dev.setter
    def _dev(self, value):
        self._connection.handle = value

    def share_connection(self, other: TuyaLightAdapter) -> None:
        """Gangs on the same switch share one serialized physical TCP session."""
        self._connection = other._connection
        self._lock = self._connection.lock

    # -- transport plumbing -------------------------------------------------

    def _address(self) -> Optional[str]:
        """Where the device lives now: a fresh presence beat beats the saved IP.

        Routers hand LSC strips new DHCP leases, and the strip keeps announcing
        itself on UDP 6667 from wherever it landed. Following the beat keeps
        commands and sync frames from timing out against the old address.
        """
        saved = self.device.get("ip")
        identity = self._aoi_identity()
        seen = tuya_presence.presence_address(identity) if identity else None
        if not seen or seen == saved:
            return saved
        logger.info("%s now announces from %s (saved %s)", identity, seen, saved)
        try:
            self.device["ip"] = seen
        except TypeError:
            pass
        return seen

    def _handle(self):
        ip = self._address()
        if self._dev is not None:
            current = getattr(self._dev, "address", None)
            if not ip or not isinstance(current, str) or current == ip:
                return self._dev
            # The device moved: drop the session to the old address and repaint
            # every zone on the new one.
            self.close()
            self._last_frame = None

        dev_id = self.device.get("device_id") or self.device.get("devId")
        local_key = local_key_for(self.device)
        if not (ip and dev_id and local_key):
            raise RuntimeError(
                "Tuya device needs ip, device_id and local_key. See "
                "https://github.com/Minlor/LumiSync#account-setup for local-key setup."
            )

        tinytuya = _require_tinytuya()
        dev = tinytuya.Device(dev_id, address=ip, local_key=local_key)
        try:
            dev.set_version(float(self.device.get("protocol_version", 3.3)))
        except (TypeError, ValueError):
            dev.set_version(3.3)
        dev.set_socketPersistent(True)
        dev.set_socketTimeout(2.0)
        # Nagle/delayed-ACK otherwise adds ~12 ms to every paint stroke; the
        # RGBIC stream needs every write to leave immediately.
        try:
            dev.set_socketNODELAY(True)
        except Exception:
            pass
        dev.set_socketRetryLimit(1)
        self._dev = dev
        return dev

    # -- capabilities -------------------------------------------------------

    def _zone_count(self) -> int:
        """Usable zone count: overrides win, then the device's own answer."""
        if paint_dp(self.device) is None and self._paint_dp is None:
            return 1
        for key in ("segment_count_override", "segmentCountOverride"):
            raw_count = self.device.get(key)
            if raw_count is None:
                continue
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                continue
            if count > 0:
                return min(count, paint_zone_capacity())
        # The device reports its zone count in the paint data point's own
        # status frame (ledNumber); fall back to a conservative 10.
        try:
            default = int(self.device.get("tuya_paint_zones") or 10)
        except (TypeError, ValueError):
            default = 10
        return min(max(1, default), paint_zone_capacity())

    @property
    def capabilities(self) -> DeviceCapabilities:
        min_k = int(self.device.get("color_temp_min", 2700))
        max_k = int(self.device.get("color_temp_max", 6500))
        known = "tuya_functions" in self.device
        colour = not known or bool(code_for(self.device, "colour"))
        return DeviceCapabilities(
            transport="lan",
            segment_count=self._zone_count(),  # paint devices stream zones
            supports_power=not known or bool(code_for(self.device, "power")),
            supports_brightness=not known or bool(code_for(self.device, "brightness") or colour),
            supports_color=colour,
            supports_segments=colour,  # paint DP if available, else averaging
            supports_streaming=colour,
            supports_white=max_k > min_k and (not known or bool(code_for(self.device, "temperature"))),
            # AoI fast-channel ceiling once it renders; the TCP paint path
            # stays at its proven sustainable rate for now.
            max_update_hz=PAINT_UPDATE_HZ,
            color_temp_min=min_k,
            color_temp_max=max_k,
        )

    # -- control ------------------------------------------------------------

    def set_power(self, on: bool) -> None:
        self._require_control("supports_power")
        dp = dp_schema(self.device)
        with self._lock:
            handle = self._handle()
            if on:
                self._ensure_visible_base(handle)
            self._check_result(handle.set_value(dp["power"], bool(on)))
            self._power_on = bool(on)

    def _ensure_visible_base(self, handle) -> None:
        """Light the strip visibly when paint streaming left the base black.

        Screen/music sync anchors DP 24 to black so cleared paint zones stay
        dark. If the strip is powered on afterwards nothing lights up, which
        reads as a broken power button. Restore the last visible colour (or a
        neutral white) in that case only — a black the user chose themselves is
        left alone.
        """
        if self._paint_dp is None:
            return
        if self._base_black is None and hasattr(handle, "status"):
            with self._lock:
                dps = self._status_snapshot(2)
            colour = dps.get(str(dp_schema(self.device)["colour"]), dps.get(dp_schema(self.device)["colour"])) if dps else None
            if isinstance(colour, str):
                self._base_black = self._colour_value(colour) == 0
        if not self._base_black:
            return
        # A stale paint frame can also reappear on power-on (for example after
        # a crash ended sync without clearing it); clear the layer so the solid
        # colour below is what actually lights up.
        try:
            count = self._zone_count()
            handle.set_value(
                self._paint_dp,
                encode_paint_clear(list(range(count)), zones=count),
                nowait=True,
            )
        except (RuntimeError, OSError, ValueError, TypeError):
            pass
        colour = self._last_colour
        if colour is None or max(colour) == 0:
            percent = self._last_brightness if self._last_brightness else 100
            r, g, b = colorsys.hsv_to_rgb(0, 0, _clamp(percent, 1, 100) / 100)
            colour = (round(r * 255), round(g * 255), round(b * 255))
        try:
            self._check_result(
                handle.set_multiple_values(
                    build_color_command(self.device, *colour)
                )
            )
        except (RuntimeError, OSError, TypeError, ValueError):
            return  # Power-on still has to be attempted.
        self._remember_colour(colour)
        self._base_black = False

    def _colour_value(self, colour: str) -> int:
        """Value channel of a colour_data string (0 = black)."""
        try:
            if colour_schema(self.device) == "legacy":
                return int(colour[12:14], 16)
            return int(colour[8:12], 16)
        except (ValueError, TypeError, IndexError):
            return 0

    def set_brightness(self, percent: int) -> None:
        self._require_control("supports_brightness")
        dp = dp_schema(self.device)
        schema = colour_schema(self.device)
        target = _clamp(percent, 0, 100)
        with self._lock:
            handle = self._handle()
            rides = "tuya_functions" in self.device and not code_for(self.device, "brightness")
            colour = self._last_colour
            mode = None
            if colour is None and hasattr(handle, "status"):
                dps = self._status_snapshot(1)
                raw = dps.get(str(dp["colour"]), dps.get(dp["colour"])) if dps else None
                if isinstance(raw, str):
                    try:
                        colour = decode_colour(raw, schema=schema)
                    except (ValueError, TypeError):
                        colour = None
                mode = dps.get(str(dp["mode"]), dps.get(dp["mode"])) if dps else None
            if colour is not None and (rides or mode == "colour"):
                # The LSC dd-family strips carry no brightness DP at all (their
                # cloud spec is switch/mode/colour + config only): brightness
                # lives in the payload's value channel, in every work mode.
                h, s, _ = colorsys.rgb_to_hsv(*(channel / 255 for channel in colour))
                r, g, b = colorsys.hsv_to_rgb(h, s, target / 100)
                self.set_color(round(r * 255), round(g * 255), round(b * 255))
                return
            if rides:
                # Nothing colour-shaped to ride yet. Quietly succeed: a later
                # colour button or sync frame carries the value channel, and an
                # error toast here would just spam the status view.
                return
            low, high = value_range(self.device, "brightness", (25, 255) if schema == "legacy" else (10, 1000))
            self._check_result(handle.set_value(dp["brightness"], round(low + (high - low) * target / 100)))
            self._last_brightness = target

    def set_color(self, r: int, g: int, b: int) -> None:
        self._require_control("supports_color")
        r, g, b = (_clamp(channel, 0, 255) for channel in (r, g, b))
        with self._lock:
            self._check_result(self._handle().set_multiple_values(build_color_command(self.device, r, g, b)))
        self._remember_colour((r, g, b))

    def set_color_temperature(self, kelvin: int) -> None:
        cap = self.capabilities
        if not cap.supports_white:
            raise RuntimeError("This light has no tunable white control.")
        dp = dp_schema(self.device)
        schema = colour_schema(self.device)
        low, high = value_range(self.device, "temperature", (0, 255) if schema == "legacy" else (0, 1000))
        value = round(low + (high - low) * (_clamp(kelvin, cap.color_temp_min, cap.color_temp_max) - cap.color_temp_min) / (cap.color_temp_max - cap.color_temp_min))
        command: Dict[int, Any] = {dp["temperature"]: value}
        if "tuya_functions" not in self.device or code_for(self.device, "mode"):
            command[dp["mode"]] = "white"
        with self._lock:
            self._check_result(self._handle().set_multiple_values(command))

    def _aoi_identity(self) -> Optional[str]:
        return str(self.device.get("device_id") or self.device.get("devId") or "") or None

    def _stream_via_aoi(self, colors: List[RGB]) -> bool:
        """Stream one frame through the AoI UDP fast channel when possible.

        Returns True when the frame was handled over UDP (or is legitimately
        pending there); False means the caller must use the TCP paint path.
        """
        if not (tuya_aoi.AOI_FAST_STREAM and self._paint_dp is not None) or self._aoi_failed:
            return False
        identity = self._aoi_identity()
        if not identity:
            return False
        client = tuya_aoi.aoi()
        if not client.started.is_set():
            client.learn_local_key(identity, local_key_for(self.device), self.device.get("ip"))
            if not client.start():
                return False
        if not client.has_session(identity):
            # Mirrors the vendor app: business frames are skipped until the
            # consult succeeds; one bounded negotiation starts from here and
            # frames resume as soon as the session lands.
            client.learn_local_key(identity, local_key_for(self.device), self.device.get("ip"))
            self._kick_aoi_negotiation(identity)
            return True
        count = self._zone_count()
        banded = reduce_palette(resample_colors_to_count(list(colors), count), 4)
        bands = list(dict.fromkeys(banded))
        zone_bands = [bands.index(colour) + 1 for colour in banded]
        try:
            value = tuya_aoi.rgbic_business_value(bands, zone_bands, count)
        except (ValueError, TypeError):
            return False
        if not client.send_business(identity, value):
            return False
        self._last_frame = list(banded)
        return True

    def _kick_aoi_negotiation(self, identity: str) -> None:
        """Run one bounded negotiation off-thread; fall back after failures."""
        if any(thread is self._aoi_thread for thread in (self._aoi_thread,) if thread is not None):
            return

        def negotiate(client, who, adapter) -> None:
            try:
                success = client.ensure_session(who)
            except Exception as exc:
                logger.info("AoI negotiation failed for %s: %s", who, exc)
                success = False
            if not success and not adapter._closing_aoi:
                adapter._aoi_failed = True
                logger.info("%s: falling back to the TCP paint transport", who)

        self._closing_aoi = False
        self._aoi_thread = threading.Thread(
            target=negotiate,
            args=(tuya_aoi.aoi(), identity, self),
            name=f"lumisync-aoi-{identity}",
            daemon=True,
        )
        self._aoi_thread.start()

    def set_segments(self, colors: List[RGB]) -> None:
        """Stream one colour per zone via the RGBIC paint DP when the device
        owns one; otherwise (older bulbs, no paint DP) average the frame into a
        single ambient colour.

        The paint DP is probed once per session — it varies per product (DP 61
        on the Battletron/wall-washer family, 103 on newer LSC strips) and is
        often unnamed in the cloud spec. A frame written to a DP the device
        does not own is silently ignored, so the probe looks for the write to
        echo back via status. Successful probes are stamped onto the device
        descriptor (``tuya_paint_dp``) so later sync runs skip the probe.
        """
        if not colors:
            return
        with self._lock:
            if self._stream_via_aoi(colors):
                return
            handle = self._handle()
            if not self._paint_probed:
                self._paint_probed = True
                self._probe_paint_dp(handle, colors)
            if self._paint_dp is not None:
                count = self._zone_count()
                try:
                    palette_limit = int(self.device.get("tuya_paint_palette") or 4)
                except (TypeError, ValueError):
                    palette_limit = 4
                frame = resample_colors_to_count(list(colors), count)
                # One write per distinct colour run: quantising to a handful of
                # bands turns a 20-stroke gradient frame into ~4 strokes.
                frame = reduce_palette(frame, max(1, palette_limit))
                strokes = encode_paint_strokes(
                    frame, zones=count, previous=self._last_frame,
                    tolerance=PAINT_DELTA_TOLERANCE,
                )
                started = time.monotonic()
                starting = self._last_frame is None
                for stroke in strokes:
                    # nowait: strokes stream back-to-back; each still ACKs but
                    # waiting per write costs ~150 ms and lagged frames behind.
                    # The gap lets the MCU drain writes.
                    handle.set_value(self._paint_dp, stroke, nowait=True)
                    time.sleep(PAINT_STROKE_DELAY)
                elapsed = time.monotonic() - started
                if elapsed > PAINT_SLOW_FRAME_SECONDS:
                    # Writes stalled (session churn or an overloaded MCU):
                    # back off instead of stacking more pressure on it.
                    time.sleep(PAINT_COOLDOWN_SECONDS)
                self._last_frame = advance_painted(
                    frame, self._last_frame, zones=count,
                    tolerance=PAINT_DELTA_TOLERANCE,
                )
                if starting:
                    # Catch a dead session on the very first frame instead of
                    # waiting for the periodic ping; a reconnect here forces a
                    # full repaint on the next frame. begin_stream just set the
                    # mode, so this pass only needs to check liveness.
                    self._ensure_link_alive(handle, reassert_mode=False)
                self._frames_since_check += 1
                if self._frames_since_check >= PAINT_HEALTH_INTERVAL:
                    self._frames_since_check = 0
                    self._ensure_link_alive(handle)
                return
            n = len(colors)
            r = sum(int(c[0]) for c in colors) // n
            g = sum(int(c[1]) for c in colors) // n
            b = sum(int(c[2]) for c in colors) // n
            self.set_color(r, g, b)

    def _ensure_link_alive(self, handle, *, reassert_mode: bool = True) -> None:
        """Ping the strip periodically and recover a silently dropped session.

        ``nowait`` paint writes never report a dead socket: the device closes
        our TCP session when another app connects (the phone app or the
        Battletron desktop tool), and the sync would keep painting into
        nothing. A status ping catches it; we reconnect, re-assert colour mode
        and force a full repaint of every zone.
        """
        try:
            with self._lock:
                alive = self._status_dps() is not None
        except Exception:
            alive = False
        dp = dp_schema(self.device)
        mode_known = "tuya_functions" not in self.device or code_for(self.device, "mode")
        if not alive:
            try:
                self.close()
            except Exception:
                pass
            try:
                fresh = self._handle()
                self._last_frame = None  # repaint every zone after a reconnect
                if mode_known:
                    fresh.set_value(dp["mode"], "colour", nowait=True)
            except Exception:
                pass
            return
        # Session alive: re-assert colour mode in case another app switched it.
        try:
            if mode_known and reassert_mode:
                handle.set_value(dp["mode"], "colour", nowait=True)
        except Exception:
            pass

    def _probe_paint_dp(self, handle, colors: List[RGB]) -> None:
        """Find and remember the per-zone paint DP, if this device has one."""
        declared = paint_dp(self.device)
        if declared:
            self._paint_dp = declared
            return
        # Probe with the app-verified per-zone frame (one stroke, one zone):
        # it is compact, and accepted paints mirror back in status changes.
        probe = encode_paint_single([0], *colors[len(colors) // 2 or 0], zones=20)
        # Cheap first pass: the status snapshot is the device's own DP list,
        # and a live paint DP usually shows paint-shaped state in it — no
        # writes needed at all.
        known = status_enrichment(self._peek_status(handle) or {})
        if known.get("tuya_paint_dp"):
            self._stamp_paint(known["tuya_paint_dp"], known.get("tuya_paint_zones"))
            return
        for candidate in PAINT_CANDIDATES:
            try:
                self._check_result(handle.set_value(candidate, probe, nowait=True))
            except (RuntimeError, OSError):
                continue  # Rejected out of hand; try the next candidate.
            if not hasattr(handle, "status"):
                # Minimal handles (tests): accept the first candidate that
                # accepted the write.
                self._stamp_paint(candidate)
                return
            if self._paint_echoes(handle, candidate):
                self._stamp_paint(candidate)
                return

    def _peek_status(self, handle):
        try:
            return self._status_dps() if hasattr(handle, "status") else None
        except Exception:
            return None

    def _paint_echoes(self, handle, dp: int) -> bool:
        # Measured on a real Battletron strip: the paint write mirrors into
        # status within ~1 s, as a change-only reply. Poll patiently.
        for _ in range(8):
            dps = self._peek_status(handle)
            if dps and (str(dp) in dps or dp in dps):
                return True
            time.sleep(0.25)
        return False

    def _stamp_paint(self, dp: int, zones: Optional[int] = None) -> None:
        self._paint_dp = dp
        try:
            self.device["tuya_paint_dp"] = dp  # persists for later sync runs
            if zones and not self.device.get("tuya_paint_zones"):
                self.device["tuya_paint_zones"] = int(zones)
        except TypeError:
            pass

    def _apply_enrichment(self, fields: Dict[str, Any]) -> None:
        """Stamp status-learned fields onto the descriptor without overwriting.

        Discovery is a one-time upgrade per device: once a value is known
        (recognized or saved), the device's own snapshot may not overrule it.
        """
        for key, value in fields.items():
            if value is None or self.device.get(key) is not None:
                continue
            try:
                self.device[key] = value
            except TypeError:
                pass

    def begin_stream(self) -> None:
        self._require_control("supports_streaming")
        # Ensure the light is in colour mode before a run of color frames. A
        # rejected command here (914: session churn / an overloaded MCU) must
        # not abort the whole sync: drop the dead session and retry once, then
        # carry on best-effort — the stream health watchdog keeps recovering.
        dp = dp_schema(self.device)
        schema = colour_schema(self.device)
        mode_known = "tuya_functions" not in self.device or code_for(self.device, "mode")
        power_known = "tuya_functions" not in self.device or code_for(self.device, "power")
        base_black = encode_colour(0, 0, 0, schema=schema)
        self._streaming = True

        def setup(handle) -> None:
            # Remember the solid colour so end_stream can restore it after the
            # paint layer is cleared, instead of leaving the strip black.
            if self._resume_colour is None and hasattr(handle, "status"):
                try:
                    dps = self._status_snapshot(1)
                except Exception:
                    dps = None
                raw = dps.get(str(dp["colour"]), dps.get(dp["colour"])) if dps else None
                if isinstance(raw, str):
                    self._resume_colour = raw
            if self._resume_colour is None and self._last_colour is not None:
                self._resume_colour = encode_colour(*self._last_colour, schema=schema)
            if power_known:
                # Sync output needs the strip on; the LSC app does the same
                # when screen sync starts. Best-effort so a flaky MCU does not
                # abort the run.
                try:
                    handle.set_value(dp["power"], True)
                except (RuntimeError, OSError, TypeError):
                    pass
            if mode_known:
                self._check_result(handle.set_value(dp["mode"], "colour"))
            if self._paint_dp is not None:
                # The paint layer sits on top of the strip's solid colour:
                # cleared (screen-black) zones reveal whatever DP 24 holds, so
                # anchor the base to black while sync owns the strip.
                self._check_result(handle.set_value(dp["colour"], base_black, nowait=True))
                self._base_black = True

        with self._lock:
            try:
                setup(self._handle())
                return
            except RuntimeError:
                pass
            try:
                self.close()
            except Exception:
                pass
            try:
                setup(self._handle())
            except Exception:
                pass

    def end_stream(self) -> None:
        """Clear the paint layer and restore the colour shown before sync."""
        self._streaming = False
        self._closing_aoi = True
        try:
            with self._lock:
                handle = self._handle()
                dp = dp_schema(self.device)
                if self._paint_dp is not None:
                    try:
                        count = self._zone_count()
                        handle.set_value(
                            self._paint_dp,
                            encode_paint_clear(list(range(count)), zones=count),
                            nowait=True,
                        )
                    except (RuntimeError, OSError, ValueError, TypeError):
                        pass
                colour = self._resume_colour
                if colour is None and self._last_colour is not None:
                    colour = encode_colour(
                        *self._last_colour, schema=colour_schema(self.device)
                    )
                if colour is not None:
                    try:
                        self._check_result(handle.set_value(dp["colour"], colour))
                    except (RuntimeError, OSError, TypeError, ValueError):
                        colour = None
                    else:
                        try:
                            self._remember_colour(
                                decode_colour(
                                    colour, schema=colour_schema(self.device)
                                )
                            )
                        except (ValueError, TypeError):
                            pass
                        self._base_black = self._colour_value(colour) == 0
                self._last_frame = None
                self._resume_colour = None
        except Exception:
            self._last_frame = None
            self._resume_colour = None

    def _require_control(self, flag: str) -> None:
        if not getattr(self.capabilities, flag):
            raise RuntimeError("This control is not supported by this light.")

    @staticmethod
    def _check_result(result) -> None:
        if isinstance(result, dict) and ("Error" in result or "Err" in result or result.get("success") is False):
            raise RuntimeError("Tuya did not accept the command. Check the local key, IP address, and protocol version.")

    def _status_dps(self):
        status = self._handle().status()
        if not isinstance(status, dict) or any(k in status for k in ("Error", "Err")):
            return None
        dps = status.get("dps")
        return dps if isinstance(dps, dict) and dps else None

    def _status_snapshot(self, tries: int = 2):
        """Read the fullest status reply the device has available.

        Persistent-socket strips also push their own change echoes (a paint
        stroke, a switch flip), and tinytuya returns the next queued packet in
        place of the reply to our DP query. That made status read one command
        behind — power looked wrong right after a toggle, and a paint echo was
        parsed as the whole state. Drain the queue first, then ask for a full
        snapshot; a complete reply always carries the switch DP.
        """
        power_ids = {
            str(DP_V1["power"]), str(DP_V2["power"]),
            DP_V1["power"], DP_V2["power"],
        }
        fallback = None
        for _ in range(max(1, tries)):
            self._drain_echoes(self._handle())
            dps = self._status_dps()
            if not dps:
                continue
            if fallback is None:
                fallback = dps
            if any(key in dps for key in power_ids):
                return dps
        return fallback

    @staticmethod
    def _drain_echoes(handle) -> None:
        """Consume queued change notifications without blocking."""
        sock = getattr(handle, "socket", None)
        try:
            if sock is None or sock.fileno() < 0:
                return
        except (AttributeError, OSError, ValueError, TypeError):
            return
        for _ in range(16):
            try:
                readable, _, _ = select.select([sock], [], [], 0)
            except (OSError, ValueError, TypeError):
                return
            if not readable:
                return
            try:
                handle.receive()
            except (OSError, RuntimeError, ValueError):
                return

    def _remember_colour(self, colour) -> None:
        """Keep the last visible colour; partial replies must not blank the UI."""
        try:
            rgb = tuple(_clamp(int(c), 0, 255) for c in list(colour)[:3])
        except (TypeError, ValueError):
            return
        if len(rgb) != 3:
            return
        self._last_colour = rgb
        self._last_brightness = max(rgb) * 100 // 255

    def query_status(self) -> Optional[Dict[str, Any]]:
        try:
            with self._lock:
                dps = self._status_snapshot()
        except Exception:
            return None
        if not dps:
            return None
        # The snapshot doubles as the device's self-description; learn anything
        # new (layout, colour format, RGBIC paint DP) before decoding.
        self._apply_enrichment(status_enrichment(dps))
        schema = dp_schema(self.device)
        def value(role):
            if "tuya_functions" in self.device and not code_for(self.device, role):
                return None
            return dps.get(str(schema[role]), dps.get(schema[role]))
        power = value("power")
        if isinstance(power, bool):
            self._power_on = power
        result: Dict[str, Any] = {"online": True}
        if self._power_on is not None:
            # Change-only replies may omit the switch DP; keep the last real
            # answer instead of flashing "Power unknown" after every write.
            result["power_on"] = self._power_on
        brightness = value("brightness")
        mode = value("mode")
        colour = value("colour")
        try:
            if mode == "colour" and isinstance(colour, str):
                try:
                    rgb = decode_colour(colour, schema=colour_schema(self.device))
                except (ValueError, TypeError):
                    rgb = None
                if rgb is not None and (self._colour_value(colour) > 0 or not self._streaming):
                    if colour_schema(self.device) == "legacy":
                        # Legacy strings carry an explicit value byte; the RGB
                        # prefix is not always in sync with it.
                        percentage = _clamp(round(int(colour[12:14], 16) * 100 / 255), 0, 100)
                    else:
                        percentage = _clamp(round(max(rgb) * 100 / 255), 0, 100)
                    result["color"] = rgb
                    result["brightness"] = percentage
                    self._remember_colour(rgb)
                    self._last_brightness = percentage
            elif isinstance(brightness, (int, float)):
                low, high = value_range(self.device, "brightness", (25, 255) if colour_schema(self.device) == "legacy" else (10, 1000))
                percentage = _clamp(round((brightness - low) * 100 / (high - low)), 0, 100)
                result["brightness"] = percentage
                self._last_brightness = percentage
            temperature = value("temperature")
            if mode == "white" and isinstance(temperature, (int, float)):
                cap = self.capabilities
                low, high = value_range(self.device, "temperature", (0, 255) if colour_schema(self.device) == "legacy" else (0, 1000))
                result["color_temp"] = round(cap.color_temp_min + max(0, min(1, (temperature - low) / (high - low))) * (cap.color_temp_max - cap.color_temp_min))
        except (ValueError, TypeError):
            pass  # A malformed optional DP does not invalidate a valid power reply.
        from ..accounts.tuya_energy import decode_metering, status_functions
        readings = {code: dps.get(str(spec["dp_id"]), dps.get(spec["dp_id"]))
                    for code, spec in status_functions(self.device).items() if "dp_id" in spec}
        result.update(decode_metering(self.device, readings))
        return result

    def close(self) -> None:
        with self._lock:
            dev, self._dev = self._dev, None
            if dev is not None:
                dev.close()


__all__ = [
    "TuyaLightAdapter",
    "DP_V1",
    "DP_V2",
    "rgb_to_hsv_tuya",
    "encode_colour",
    "encode_brightness",
    "encode_temperature",
    "encode_paint_all",
    "encode_paint_single",
    "encode_paint_clear",
    "encode_paint_strokes",
    "advance_painted",
    "reduce_palette",
    "paint_zone_capacity",
    "paint_zone_hint",
    "status_enrichment",
    "build_color_command",
    "dp_schema",
    "colour_schema",
]
