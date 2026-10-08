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

import colorsys
import threading
from typing import Any, Dict, List, Optional

from .base import RGB, DeviceCapabilities, TransportAdapter
from ..accounts.secrets import local_key_for
from ..accounts.tuya import code_for, value_range

# Standard Tuya lighting data points (category "dj").
#
# Modern string-code schema ("v2"), used by current LSC WiFi bulbs/strips:
#   20 switch_led (bool) | 21 work_mode (enum) | 22 bright_value_v2 (10-1000)
#   23 temp_value_v2 (0-1000) | 24 colour_data_v2 (HHHHSSSSVVVV hex)
# Legacy schema ("v1"), used by older bulbs:
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


def rgb_to_hsv_tuya(r: int, g: int, b: int) -> tuple[int, int, int]:
    """Convert 0-255 RGB to Tuya's HSV units: H in 0-360, S and V in 0-1000."""
    rf, gf, bf = (_clamp(r, 0, 255) / 255, _clamp(g, 0, 255) / 255, _clamp(b, 0, 255) / 255)
    h, s, v = colorsys.rgb_to_hsv(rf, gf, bf)
    return (round(h * 360), round(s * 1000), round(v * 1000))


def encode_colour(r: int, g: int, b: int, *, schema: str = "v2") -> str:
    """Encode an RGB color as a Tuya ``colour_data`` hex string.

    ``v2`` (default): ``HHHHSSSSVVVV`` — H 0-360, S/V 0-1000, each 4 hex digits.
    ``v1``: ``RRGGBBHHHHSSVV`` — RGB prefix plus H/S/V (legacy bulb format).
    """
    h, s, v = rgb_to_hsv_tuya(r, g, b)
    if schema == "v1":
        s255 = round(s * 255 / 1000)
        v255 = round(v * 255 / 1000)
        return f"{_clamp(r, 0, 255):02x}{_clamp(g, 0, 255):02x}{_clamp(b, 0, 255):02x}{h:04x}{s255:02x}{v255:02x}"
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
    defaults = DP_V1 if str(device.get("dp_schema", "v2")).lower() == "v1" else DP_V2
    overrides = device.get("dp_map")
    return {**defaults, **{key: int(value) for key, value in overrides.items() if key in defaults}} if isinstance(overrides, dict) else defaults


def colour_schema(device: Dict[str, Any]) -> str:
    return "v1" if str(device.get("dp_schema", "v2")).lower() == "v1" else "v2"


def build_color_command(device: Dict[str, Any], r: int, g: int, b: int) -> Dict[int, Any]:
    """DP payload that switches a light to colour mode and sets ``(r,g,b)``."""
    dp = dp_schema(device)
    schema = colour_schema(device)
    result = {dp["colour"]: encode_colour(r, g, b, schema=schema)}
    if "tuya_functions" not in device or code_for(device, "mode"):
        result[dp["mode"]] = "colour"
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
    the matching value. ``dp_schema`` ('v1'|'v2') selects the data-point layout.
    """

    def __init__(self, device: Dict[str, Any]) -> None:
        super().__init__(device)
        self._connection = _TuyaConnection()
        self._lock = self._connection.lock

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

    def _handle(self):
        if self._dev is not None:
            return self._dev

        ip = self.device.get("ip")
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
        dev.set_socketRetryLimit(1)
        self._dev = dev
        return dev

    # -- capabilities -------------------------------------------------------

    @property
    def capabilities(self) -> DeviceCapabilities:
        min_k = int(self.device.get("color_temp_min", 2700))
        max_k = int(self.device.get("color_temp_max", 6500))
        known = "tuya_functions" in self.device
        colour = not known or bool(code_for(self.device, "colour"))
        return DeviceCapabilities(
            transport="lan",
            segment_count=1,  # driven as a single ambient color
            supports_power=not known or bool(code_for(self.device, "power")),
            supports_brightness=not known or bool(code_for(self.device, "brightness") or colour),
            supports_color=colour,
            supports_segments=colour,  # via averaging, see set_segments
            supports_streaming=colour,
            supports_white=max_k > min_k and (not known or bool(code_for(self.device, "temperature"))),
            max_update_hz=5.0,
            color_temp_min=min_k,
            color_temp_max=max_k,
        )

    # -- control ------------------------------------------------------------

    def set_power(self, on: bool) -> None:
        self._require_control("supports_power")
        dp = dp_schema(self.device)
        with self._lock:
            self._check_result(self._handle().set_value(dp["power"], bool(on)))

    def set_brightness(self, percent: int) -> None:
        self._require_control("supports_brightness")
        dp = dp_schema(self.device)
        schema = colour_schema(self.device)
        with self._lock:
            handle = self._handle()
            dps = self._status_dps() if hasattr(handle, "status") else None
            colour = dps.get(str(dp["colour"]), dps.get(dp["colour"])) if dps else None
            mode = dps.get(str(dp["mode"]), dps.get(dp["mode"])) if dps else None
            if mode != "white" and isinstance(colour, str) and (mode == "colour" or "tuya_functions" in self.device and not code_for(self.device, "brightness")):
                try:
                    if schema == "v1":
                        h, s = int(colour[6:10], 16), int(colour[10:12], 16) / 255
                    else:
                        h, s = int(colour[:4], 16), int(colour[4:8], 16) / 1000
                    r, g, b = colorsys.hsv_to_rgb(h / 360, s, _clamp(percent, 0, 100) / 100)
                    self.set_color(round(r * 255), round(g * 255), round(b * 255))
                    return
                except ValueError:
                    raise RuntimeError("Tuya returned an invalid color; set a color before changing brightness.") from None
            if "tuya_functions" in self.device and not code_for(self.device, "brightness"):
                raise RuntimeError("Set a color before changing this light's brightness.")
            low, high = value_range(self.device, "brightness", (25, 255) if schema == "v1" else (10, 1000))
            self._check_result(handle.set_value(dp["brightness"], round(low + (high - low) * _clamp(percent, 0, 100) / 100)))

    def set_color(self, r: int, g: int, b: int) -> None:
        self._require_control("supports_color")
        with self._lock:
            self._check_result(self._handle().set_multiple_values(build_color_command(self.device, r, g, b)))

    def set_color_temperature(self, kelvin: int) -> None:
        cap = self.capabilities
        if not cap.supports_white:
            raise RuntimeError("This light has no tunable white control.")
        dp = dp_schema(self.device)
        schema = colour_schema(self.device)
        low, high = value_range(self.device, "temperature", (0, 255) if schema == "v1" else (0, 1000))
        value = round(low + (high - low) * (_clamp(kelvin, cap.color_temp_min, cap.color_temp_max) - cap.color_temp_min) / (cap.color_temp_max - cap.color_temp_min))
        command = {dp["temperature"]: value}
        if "tuya_functions" not in self.device or code_for(self.device, "mode"):
            command[dp["mode"]] = "white"
        with self._lock:
            self._check_result(self._handle().set_multiple_values(command))

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
        self._require_control("supports_streaming")
        # Ensure the light is in colour mode before a run of color frames.
        dp = dp_schema(self.device)
        with self._lock:
            if "tuya_functions" not in self.device or code_for(self.device, "mode"):
                self._check_result(self._handle().set_value(dp["mode"], "colour"))

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

    def query_status(self) -> Optional[Dict[str, Any]]:
        try:
            with self._lock:
                dps = self._status_dps()
        except Exception:
            return None
        if not dps:
            return None
        schema = dp_schema(self.device)
        def value(role):
            if "tuya_functions" in self.device and not code_for(self.device, role):
                return None
            return dps.get(str(schema[role]), dps.get(schema[role]))
        power = value("power")
        result = {"online": True, "power_on": power if isinstance(power, bool) else None}
        brightness = value("brightness")
        mode = value("mode")
        colour = value("colour")
        try:
            if mode == "colour" and isinstance(colour, str):
                if colour_schema(self.device) == "v1":
                    result["color"] = tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))
                    brightness = round(int(colour[12:14], 16) * 100 / 255)
                else:
                    h, s, v = (int(colour[i:i + 4], 16) for i in (0, 4, 8))
                    result["color"] = tuple(round(c * 255) for c in colorsys.hsv_to_rgb(h / 360, s / 1000, v / 1000))
                    brightness = round(v / 10)
                result["brightness"] = _clamp(brightness, 0, 100)
            elif isinstance(brightness, (int, float)):
                low, high = value_range(self.device, "brightness", (25, 255) if colour_schema(self.device) == "v1" else (10, 1000))
                result["brightness"] = _clamp(round((brightness - low) * 100 / (high - low)), 0, 100)
            temperature = value("temperature")
            if mode == "white" and isinstance(temperature, (int, float)):
                cap = self.capabilities
                low, high = value_range(self.device, "temperature", (0, 255) if colour_schema(self.device) == "v1" else (0, 1000))
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
    "build_color_command",
    "dp_schema",
    "colour_schema",
]
