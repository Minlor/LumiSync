"""Capability-aware manual controls for authenticated Wi-Fi cloud devices."""

from __future__ import annotations

import colorsys
import json

from .base import DeviceCapabilities, TransportAdapter
from ..accounts.errors import AccountError
from ..accounts.manager import account_manager
from ..accounts.tuya import code_for, colour_scale, colour_values, value_range
from ..sku_catalog import capabilities_for


def bounded(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


class CloudLightAdapter(TransportAdapter):
    @property
    def capabilities(self) -> DeviceCapabilities:
        transport = self.device["transport"]
        if transport == "tuya_cloud":
            return DeviceCapabilities(
                transport="cloud", segment_count=1, supports_segments=False,
                supports_streaming=False,
                supports_power=bool(code_for(self.device, "power")),
                supports_brightness=bool(code_for(self.device, "brightness") or code_for(self.device, "colour")),
                supports_color=bool(code_for(self.device, "colour")),
                supports_white=bool(code_for(self.device, "temperature")),
                color_temp_min=int(self.device.get("color_temp_min", 2700)),
                color_temp_max=int(self.device.get("color_temp_max", 6500)),
            )
        if transport == "govee_cloud":
            instances = {c["instance"]: c for c in self.device.get("cloud_capabilities", [])}
            temperature = instances.get("colorTemperatureK", {}).get("parameters", {}).get("range", {})
            return DeviceCapabilities(
                transport="cloud", segment_count=1, supports_segments=False, supports_streaming=False,
                supports_power="powerSwitch" in instances, supports_brightness="brightness" in instances,
                supports_color="colorRgb" in instances, supports_white="colorTemperatureK" in instances,
                color_temp_min=int(temperature.get("min", 0)), color_temp_max=int(temperature.get("max", 0)),
            )
        cap = capabilities_for(self.device.get("sku") or self.device.get("model"))
        return DeviceCapabilities(
            transport="cloud", segment_count=1, supports_segments=False, supports_streaming=False,
            supports_color=cap.supports_color if cap else True,
            supports_white=bool(cap and cap.color_temp_max > 0),
            color_temp_min=cap.color_temp_min if cap else 0,
            color_temp_max=cap.color_temp_max if cap else 0,
        )

    def _send(self, role: str, value) -> None:
        cap = self.capabilities
        flag = {"power": cap.supports_power, "brightness": cap.supports_brightness,
                "colour": cap.supports_color, "temperature": cap.supports_white}[role]
        if not flag:
            raise AccountError("This control is not supported by this device.", "unsupported")
        client = account_manager.client_for(self.device)
        transport = self.device["transport"]
        if transport == "tuya_cloud":
            code = code_for(self.device, role)
            commands = [{"code": code, "value": value}]
            mode = code_for(self.device, "mode")
            if mode and role in ("colour", "temperature"):
                commands.insert(0, {"code": mode, "value": "colour" if role == "colour" else "white"})
            client.send_commands(self.device["device_id"], commands)
        elif transport == "govee_cloud":
            instance = {"power": "powerSwitch", "brightness": "brightness", "colour": "colorRgb", "temperature": "colorTemperatureK"}[role]
            client.control(self.device, instance, value)
        else:
            command = {"power": "turn", "brightness": "brightness", "colour": "colorwc", "temperature": "colorwc"}[role]
            client.control(self.device, command, value)

    def set_power(self, on: bool) -> None:
        transport = self.device["transport"]
        self._send("power", bool(on) if transport == "tuya_cloud" else int(bool(on))
                   if transport == "govee_cloud" else {"val": int(bool(on))})

    def set_brightness(self, percent: int) -> None:
        percent = bounded(percent, 0, 100)
        transport = self.device["transport"]
        if transport == "tuya_cloud":
            client = account_manager.client_for(self.device)
            # The white brightness DP does not affect Tuya colour mode. Read
            # the current hue/saturation before changing its HSV value instead.
            status = client.query_raw_status(self.device)
            hsv = colour_values(self.device, status.get(code_for(self.device, "colour")))
            mode = status.get(code_for(self.device, "mode"))
            if hsv is not None and mode != "white":
                h, s, _v = hsv
                self._send("colour", json.dumps({"h": h, "s": s, "v": round(percent * colour_scale(self.device) / 100)}, separators=(",", ":")))
                return
            if not code_for(self.device, "brightness"):
                raise AccountError("Set a colour before changing this light's brightness.", "unsupported")
            low, high = value_range(self.device, "brightness", (10, 1000) if self.device.get("dp_schema", "v2") == "v2" else (25, 255))
            value = round(low + (high - low) * percent / 100)
        elif transport == "govee_cloud":
            capability = next((c for c in self.device.get("cloud_capabilities", []) if c.get("instance") == "brightness"), {})
            limits = capability.get("parameters", {}).get("range", {})
            value = bounded(percent, int(limits.get("min", 0)), int(limits.get("max", 100)))
        else:
            value = {"val": percent}
        self._send("brightness", value)

    def set_color(self, r: int, g: int, b: int) -> None:
        r, g, b = (bounded(c, 0, 255) for c in (r, g, b))
        transport = self.device["transport"]
        if transport == "tuya_cloud":
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            scale = colour_scale(self.device)
            value = json.dumps({"h": round(h * 360), "s": round(s * scale), "v": round(v * scale)}, separators=(",", ":"))
        elif transport == "govee_cloud":
            value = (r << 16) | (g << 8) | b
        else:
            value = {"color": {"r": r, "g": g, "b": b}, "colorTemInKelvin": 0}
        self._send("colour", value)

    def set_color_temperature(self, kelvin: int) -> None:
        cap = self.capabilities
        kelvin = bounded(kelvin, cap.color_temp_min, cap.color_temp_max)
        transport = self.device["transport"]
        if transport == "tuya_cloud":
            low, high = value_range(self.device, "temperature", (0, 1000) if self.device.get("dp_schema", "v2") == "v2" else (0, 255))
            value = round(low + (high - low) * (kelvin - cap.color_temp_min) / max(1, cap.color_temp_max - cap.color_temp_min))
        else:
            value = kelvin if transport == "govee_cloud" else {"color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": kelvin}
        self._send("temperature", value)

    def set_segments(self, colors) -> None:
        raise AccountError("Screen and music sync require local LAN or Bluetooth control. Choose a local connection for this device.", "unsupported")

    def begin_stream(self) -> None:
        self.set_segments([])

    def query_status(self):
        return account_manager.client_for(self.device).query_status(self.device)

    def close(self) -> None:
        # The account owns and shares its session across all of its devices.
        pass
