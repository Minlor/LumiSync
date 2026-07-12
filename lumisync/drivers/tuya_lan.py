"""Tuya / LSC Smart Connect LAN driver.

LSC Smart Connect (Action's retail smart-home app) is a rebrand of the Tuya
"Smart Life" platform — confirmed by decompiling ``com.lscsmartconnection.smart``
v2.0.4, whose native libraries are Tuya's SDK (``libthingsmart.so``,
``libThingSmartLink.so``, ``libthing_security.so``). See
``docs/lsc-tuya-research.md``.

Tuya WiFi bulbs and strips speak the **Tuya local protocol** over TCP ``6668``:
every command is AES-encrypted with a per-device 16-byte *local key* that is
provisioned at pairing by the vendor account service. Unlike Govee LAN (open UDP
JSON) or iDotMatrix BLE (open GATT), a Tuya device is uncontrollable without
that key, and the key cannot be recovered by decompiling the app — the user
must supply it (``docs/lsc-tuya-research.md`` documents how to obtain it).

LumiSync itself makes no network calls to any vendor account service; it only
speaks the local protocol on the LAN, using credentials the user pastes in.

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

import colorsys
from typing import Any, Dict, List, Optional

from .base import RGB, DeviceCapabilities, TransportAdapter

# Standard Tuya lighting data points (category "dj").
#
# Modern string-code schema ("v2"), used by current LSC WiFi bulbs/strips:
#   20 switch_led (bool) | 21 work_mode (enum) | 22 bright_value_v2 (10-1000)
#   23 temp_value_v2 (0-1000) | 24 colour_data_v2 (HHHHSSSSVVVV hex)
# Legacy schema ("v1"), used by older bulbs:
#   1 switch (bool) | 2 mode | 3 bright (25-255) | 4 temp (0-255)
#   5 colour_data (HHHHSSVV hex, S/V as 0-255)
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


def rgb_to_hsv_tuya(r: int, g: int, b: int) -> tuple[int, int, int]:
    """Convert 0-255 RGB to Tuya's HSV units: H in 0-360, S and V in 0-1000."""
    rf, gf, bf = (_clamp(r, 0, 255) / 255, _clamp(g, 0, 255) / 255, _clamp(b, 0, 255) / 255)
    h, s, v = colorsys.rgb_to_hsv(rf, gf, bf)
    return (round(h * 360), round(s * 1000), round(v * 1000))


def encode_colour(r: int, g: int, b: int, *, schema: str = "v2") -> str:
    """Encode an RGB color as a Tuya ``colour_data`` hex string.

    ``v2`` (default): ``HHHHSSSSVVVV`` — H 0-360, S/V 0-1000, each 4 hex digits.
    ``v1``: ``HHHHSSVV`` — H 0-360 (4 hex), S/V scaled to 0-255 (2 hex each).
    """
    h, s, v = rgb_to_hsv_tuya(r, g, b)
    if schema == "v1":
        s255 = round(s * 255 / 1000)
        v255 = round(v * 255 / 1000)
        return f"{h:04x}{s255:02x}{v255:02x}"
    return f"{h:04x}{s:04x}{v:04x}"


def encode_brightness(percent: int, *, schema: str = "v2") -> int:
    """Map a 0-100 percentage onto the Tuya brightness DP range for the schema."""
    pct = _clamp(percent, 0, 100)
    if schema == "v1":
        lo, hi = _BRIGHT_V1_MIN, _BRIGHT_V1_MAX
    else:
        lo, hi = _BRIGHT_V2_MIN, _BRIGHT_V2_MAX
    return round(lo + (hi - lo) * pct / 100)


def encode_temperature(kelvin: int, min_k: int, max_k: int, *, schema: str = "v2") -> int:
    """Map a color temperature (Kelvin) onto the Tuya temp DP (warm=0 → cool=top).

    The temp DP spans 0-255 on the legacy schema and 0-1000 on the modern one.
    """
    if max_k <= min_k:
        return 0
    frac = (_clamp(kelvin, min_k, max_k) - min_k) / (max_k - min_k)
    top = 255 if schema == "v1" else 1000
    return round(frac * top)


def dp_schema(device: Dict[str, Any]) -> Dict[str, int]:
    """Return the DP index map for a device (``dp_schema`` = 'v1' | 'v2')."""
    return DP_V1 if str(device.get("dp_schema", "v2")).lower() == "v1" else DP_V2


def colour_schema(device: Dict[str, Any]) -> str:
    return "v1" if str(device.get("dp_schema", "v2")).lower() == "v1" else "v2"


def build_color_command(device: Dict[str, Any], r: int, g: int, b: int) -> Dict[int, Any]:
    """DP payload that switches a light to colour mode and sets ``(r,g,b)``."""
    dp = dp_schema(device)
    schema = colour_schema(device)
    return {
        dp["mode"]: "colour",
        dp["colour"]: encode_colour(r, g, b, schema=schema),
    }


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


class TuyaLightAdapter(TransportAdapter):
    """Control one Tuya/LSC WiFi light over the local network.

    Requires ``ip``, ``device_id`` and ``local_key`` on the device descriptor.
    ``protocol_version`` defaults to 3.3 (the most common); 3.4/3.5 devices need
    the matching value. ``dp_schema`` ('v1'|'v2') selects the data-point layout.
    """

    def __init__(self, device: Dict[str, Any]) -> None:
        super().__init__(device)
        self._dev = None  # lazily-created tinytuya handle

    # -- transport plumbing -------------------------------------------------

    def _handle(self):
        if self._dev is not None:
            return self._dev

        ip = self.device.get("ip")
        dev_id = self.device.get("device_id") or self.device.get("devId")
        local_key = self.device.get("local_key") or self.device.get("localKey")
        if not (ip and dev_id and local_key):
            raise RuntimeError(
                "Tuya device needs ip, device_id and local_key. See "
                "docs/lsc-tuya-research.md for how to obtain the local key."
            )

        tinytuya = _require_tinytuya()
        dev = tinytuya.Device(dev_id, address=ip, local_key=local_key)
        try:
            dev.set_version(float(self.device.get("protocol_version", 3.3)))
        except (TypeError, ValueError):
            dev.set_version(3.3)
        dev.set_socketPersistent(True)
        self._dev = dev
        return dev

    # -- capabilities -------------------------------------------------------

    @property
    def capabilities(self) -> DeviceCapabilities:
        min_k = int(self.device.get("color_temp_min", 2700))
        max_k = int(self.device.get("color_temp_max", 6500))
        return DeviceCapabilities(
            transport="lan",
            segment_count=1,  # driven as a single ambient color
            supports_power=True,
            supports_brightness=True,
            supports_color=True,
            supports_segments=True,  # via averaging, see set_segments
            supports_white=max_k > min_k,
            color_temp_min=min_k,
            color_temp_max=max_k,
        )

    # -- control ------------------------------------------------------------

    def set_power(self, on: bool) -> None:
        dp = dp_schema(self.device)
        self._handle().set_value(dp["power"], bool(on))

    def set_brightness(self, percent: int) -> None:
        dp = dp_schema(self.device)
        schema = colour_schema(self.device)
        self._handle().set_value(dp["brightness"], encode_brightness(percent, schema=schema))

    def set_color(self, r: int, g: int, b: int) -> None:
        self._handle().set_multiple_values(build_color_command(self.device, r, g, b))

    def set_color_temperature(self, kelvin: int) -> None:
        cap = self.capabilities
        if not cap.supports_white:
            super().set_color_temperature(kelvin)
            return
        dp = dp_schema(self.device)
        schema = colour_schema(self.device)
        value = encode_temperature(kelvin, cap.color_temp_min, cap.color_temp_max, schema=schema)
        self._handle().set_multiple_values({dp["mode"]: "white", dp["temperature"]: value})

    def set_segments(self, colors: List[RGB]) -> None:
        """Tuya lights have no fast per-segment stream, so sync drives them as one
        ambient light: average the frame to a single color."""
        if not colors:
            return
        n = len(colors)
        r = sum(int(c[0]) for c in colors) // n
        g = sum(int(c[1]) for c in colors) // n
        b = sum(int(c[2]) for c in colors) // n
        self.set_color(r, g, b)

    def begin_stream(self) -> None:
        # Ensure the light is in colour mode before a run of color frames.
        dp = dp_schema(self.device)
        try:
            self._handle().set_value(dp["mode"], "colour")
        except Exception:
            pass

    def query_status(self) -> Optional[Dict[str, Any]]:
        try:
            status = self._handle().status()
        except Exception:
            return None
        if not isinstance(status, dict):
            return None
        dps = status.get("dps") if "dps" in status else status
        if not isinstance(dps, dict):
            return None
        schema = dp_schema(self.device)
        power = dps.get(str(schema["power"])) or dps.get(schema["power"])
        return {"online": True, "power_on": bool(power) if power is not None else None}

    def close(self) -> None:
        dev, self._dev = self._dev, None
        if dev is not None:
            try:
                dev.close()
            except Exception:
                pass


__all__ = [
    "TuyaLightAdapter",
    "DP_V1",
    "DP_V2",
    "rgb_to_hsv_tuya",
    "encode_colour",
    "encode_brightness",
    "encode_temperature",
    "build_color_command",
    "dp_schema",
    "colour_schema",
]
