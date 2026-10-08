"""Tuya QR sharing authorization and signed OpenAPI for linked OEM accounts."""

from __future__ import annotations

import hashlib
import colorsys
import hmac
import json
import logging
import re
import threading
import time
from urllib.parse import quote, urlparse

import requests

from .errors import AccountError
from .http import request_json

# Public authorization registration used by Tuya's official sharing SDK and
# Home Assistant. The phone authorization screen names Home Assistant. This is
# configurable so a separately registered sharing client can also be used.
SHARING_CLIENT_ID = "HA_3y9q4ak7g4ephrvke"
SHARING_SCHEMA = "haauthorize"
REGIONS = {
    "eu": "https://openapi.tuyaeu.com",
    "eu-w": "https://openapi-weaz.tuyaeu.com",
    "us": "https://openapi.tuyaus.com",
    "us-e": "https://openapi-ueaz.tuyaus.com",
    "cn": "https://openapi.tuyacn.com",
    "in": "https://openapi.tuyain.com",
    "sg": "https://openapi-sg.iotbing.com",
}
ROLES = {
    "power": ("switch_led", "switch"),
    "mode": ("work_mode",),
    "brightness": ("bright_value_v2", "bright_value"),
    "temperature": ("temp_value_v2", "temp_value"),
    "colour": ("colour_data_v2", "colour_data"),
}


def functions_for(device: dict) -> dict:
    return device.get("tuya_functions", {})


def code_for(device: dict, role: str) -> str | None:
    functions = functions_for(device)
    if role == "power" and device.get("tuya_power_code") in functions:
        return device["tuya_power_code"]
    return next((code for code in ROLES[role] if code in functions), None)


def value_range(device: dict, role: str, default: tuple[int, int]) -> tuple[int, int]:
    specification = functions_for(device).get(code_for(device, role), {})
    values = specification.get("values", {})
    if isinstance(values, str):
        try:
            values = json.loads(values)
        except ValueError:
            values = {}
    if not isinstance(values, dict):
        return default
    try:
        low, high = int(values.get("min", default[0])), int(values.get("max", default[1]))
    except (ValueError, TypeError):
        return default
    return (low, high) if high > low else default


def colour_scale(device: dict) -> int:
    return 1000 if code_for(device, "colour") == "colour_data_v2" else 255


def colour_values(device: dict, value) -> tuple[int, int, int] | None:
    """Decode the cloud's HSV JSON without confusing it with local hex DPs."""
    try:
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, dict):
            return None
        scale = colour_scale(device)
        return (max(0, min(360, int(value["h"]))),
                max(0, min(scale, int(value["s"]))),
                max(0, min(scale, int(value["v"]))))
    except (ValueError, TypeError, KeyError):
        return None


def normalize_devices(raw: dict, functions: dict, dp_codes: dict | None = None,
                      status_functions: dict | None = None) -> list[dict]:
    """Import lamps, light switches and sockets, with one control per relay."""
    lighting = any(code in functions for code in ("switch_led", "colour_data", "colour_data_v2", "bright_value", "bright_value_v2", "temp_value", "temp_value_v2"))
    category = str(raw.get("category") or "").casefold()
    # A generic switch DP can also control an appliance. Require a known
    # electrical category and actual writable boolean relay controls.
    switching = category in ("kg", "kg_zigbee", "cjkg") or category.endswith("_kg")
    socket = category in ("cz", "pc", "cz_zigbee", "pc_zigbee") or category.endswith(("_cz", "_pc"))
    switches = sorted((code for code, spec in functions.items()
                       if re.fullmatch(r"switch(?:_[1-9][0-9]*)?", code)
                       and str(spec.get("type", "bool")).casefold() in ("bool", "boolean")),
                      key=lambda code: int(code.split("_")[-1]) if "_" in code else 0)
    if not lighting and not ((switching or socket) and switches):
        return []
    identity = str(raw.get("id", ""))
    if not identity:
        return []
    fallback = "Tuya light" if lighting else "Tuya smart plug" if socket else "Tuya light switch"
    result = {"device_id": identity, "mac": f"tuya:{identity}",
              "model": raw.get("name") or fallback,
              "name": raw.get("name") or fallback,
              "transport": "tuya_cloud", "tuya_functions": functions,
              "tuya_category": category,
              "dp_schema": "v2" if any(c.endswith("_v2") for c in functions) else "v1",
              "color_temp_min": 2700, "color_temp_max": 6500}
    if status_functions is not None:
        result["tuya_status_functions"] = status_functions
    if lighting and len(switches) == 1 and not code_for(result, "power"):
        result["tuya_power_code"] = switches[0]
    if raw.get("local_key"):
        result["local_key"] = raw["local_key"]
    result["local_supported"] = raw.get("support_local", True)
    codes = {str(dp): code for dp, code in (dp_codes or {}).items()}
    codes.update({str(spec["dp_id"]): code for code, spec in functions.items() if "dp_id" in spec})
    if codes:
        result["dp_map"] = {role: int(dp_id) for dp_id, code in codes.items() for role in ROLES
                            if dp_id.isdigit() and code == code_for(result, role)}
    if lighting:
        return [result]
    devices = []
    for code in switches:
        device = {**result, "device_kind": "smart_plug" if socket else "light_switch", "tuya_power_code": code,
                  "dp_map": dict(result.get("dp_map", {}))}
        dp_id = next((dp for dp, mapped in codes.items() if mapped == code and dp.isdigit()), None)
        if dp_id is not None:
            device["dp_map"]["power"] = int(dp_id)
        else:
            # Cloud commands can work without a numeric DP, local control cannot.
            device["local_supported"] = False
        if len(switches) > 1:
            device["tuya_channel"] = code
            device["mac"] += ":" + code
            device["name"] += (" · Outlet " if socket else " · Switch ") + (code.split("_")[-1] if "_" in code else "main")
            device["model"] = device["name"]
        devices.append(device)
    return devices


def normalize_device(raw: dict, functions: dict, dp_codes: dict | None = None) -> dict | None:
    """Compatibility helper for callers that need the first control."""
    devices = normalize_devices(raw, functions, dp_codes)
    return devices[0] if devices else None


class TuyaOpenApiClient:
    """Project credentials authorize only devices linked to that project."""

    def __init__(self, credentials: dict, session=None) -> None:
        self.credentials = dict(credentials)
        self.endpoint = REGIONS.get(credentials.get("region"))
        if self.endpoint is None:
            raise AccountError("Choose the data center used by your Tuya cloud project.", "validation")
        self.session = session or requests.Session()
        self._token = ""
        self._expires = 0.0
        self._lock = threading.RLock()

    def _request(self, method: str, path: str, body=None, *, token_request: bool = False) -> dict:
        with self._lock:
            if not token_request and time.monotonic() >= self._expires:
                info = self._request("GET", "/v1.0/token?grant_type=1", token_request=True)
                self._token = info["access_token"]
                self._expires = time.monotonic() + max(1, int(info["expire_time"]) - 60)
            content = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body is not None else ""
            timestamp = str(int(time.time() * 1000))
            access_id = self.credentials["access_id"]
            token = "" if token_request else self._token
            signing = method + "\n" + hashlib.sha256(content.encode()).hexdigest() + "\n\n" + path
            signature = hmac.new(self.credentials["access_secret"].encode(),
                                 (access_id + token + timestamp + signing).encode(), hashlib.sha256).hexdigest().upper()
            headers = {"client_id": access_id, "sign": signature, "t": timestamp,
                       "sign_method": "HMAC-SHA256", "Content-Type": "application/json"}
            if token:
                headers["access_token"] = token
            reply = request_json(self.session, method, self.endpoint + path, headers=headers,
                                 **({"data": content.encode()} if content else {}))
            if reply.get("success") is not True:
                code = str(reply.get("code", "unknown"))
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,24}", code):
                    code = "unknown"
                raise AccountError(f"Tuya rejected the request (code {code}). Check the project credentials, data center, linked account, and API subscription.", "authentication")
            return reply.get("result", {})

    def list_devices(self) -> list[dict]:
        seed = quote(str(self.credentials["linked_device_id"]), safe="")
        owner = self._request("GET", f"/v1.0/devices/{seed}").get("uid")
        if not owner:
            raise AccountError("The linked device did not identify an account. Verify that your project has access to this device and its owning account.", "authentication")
        devices = self._request("GET", f"/v1.0/users/{quote(str(owner), safe='')}/devices")
        if not isinstance(devices, list):
            raise AccountError("Tuya returned an unexpected device list.", "response")
        result = []
        for raw in devices:
            specification = self._request("GET", f"/v1.1/devices/{quote(raw['id'], safe='')}/specifications")
            functions = {item["code"]: item for item in specification.get("functions", [])}
            dp_codes = {item["dp_id"]: item["code"] for item in specification.get("functions", []) if "dp_id" in item}
            status_functions = {item["code"]: item for item in specification.get("status", [])}
            result.extend(normalize_devices(raw, functions, dp_codes, status_functions))
        return result

    def send_commands(self, identity: str, commands: list[dict]) -> None:
        result = self._request("POST", f"/v1.0/devices/{quote(identity, safe='')}/commands", {"commands": commands})
        if result is not True:
            raise AccountError("Tuya did not accept the device command.", "service")

    def query_status(self, device: dict) -> dict:
        status = self.query_raw_status(device)
        details = self._request("GET", f"/v1.0/devices/{quote(device['device_id'], safe='')}")
        return decode_status(device, status, details.get("online"))

    def query_raw_status(self, device: dict) -> dict:
        status = self._request("GET", f"/v1.0/devices/{quote(device['device_id'], safe='')}/status")
        if not isinstance(status, list):
            raise AccountError("Tuya did not return device state.", "response")
        return {item["code"]: item["value"] for item in status}

    def close(self) -> None:
        self.session.close()


class SafeSdkSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs["timeout"] = (5, 15)
        kwargs["allow_redirects"] = False
        return super().request(method, url, **kwargs)


class TuyaSharingClient:
    def __init__(self, credentials: dict | None = None) -> None:
        self.credentials = dict(credentials or {})
        self.credentials.setdefault("client_id", SHARING_CLIENT_ID)
        self.credentials.setdefault("schema", SHARING_SCHEMA)
        self._lock = threading.RLock()
        self._manager = None
        from tuya_sharing import LoginControl, logger

        # The SDK logs decrypted responses at DEBUG, including local keys.
        logger.setLevel(logging.CRITICAL)
        self._login = LoginControl()
        self._login.session.close()
        self._login.session = SafeSdkSession()

    def request_qr(self, user_code: str) -> str:
        user_code = user_code.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,80}", user_code):
            raise AccountError("Enter the user code shown under Account and Security in Tuya Smart or Smart Life.", "validation")
        self.credentials["user_code"] = user_code
        try:
            reply = self._login.qr_code(self.credentials["client_id"], self.credentials["schema"], user_code)
        except Exception:
            raise AccountError("Could not request a Tuya authorization code. Check your connection and user code.", "network") from None
        token = reply.get("result", {}).get("qrcode") if reply.get("success") else None
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", token):
            raise AccountError("Tuya could not authorize this user code. Check the code in Tuya Smart / Smart Life, or use email and password for your LSC account.", "authentication")
        self._qr_token = token
        return "tuyaSmart--qrLogin?token=" + token

    def finish_qr(self) -> bool:
        try:
            success, info = self._login.login_result(self._qr_token, self.credentials["client_id"], self.credentials["user_code"])
        except Exception:
            raise AccountError("Could not check Tuya authorization. Try again.", "network") from None
        if not success:
            # No authorization yet. The GUI keeps a bounded expiry and allows retry.
            return False
        if not all(info.get(k) for k in ("access_token", "refresh_token", "endpoint", "terminal_id", "uid")):
            raise AccountError("Tuya returned an incomplete authorization session.", "response")
        endpoint = urlparse(str(info["endpoint"]))
        if endpoint.scheme != "https" or endpoint.port not in (None, 443) or endpoint.username or endpoint.path not in ("", "/") or not re.fullmatch(r"apigw(?:-[a-z0-9]+)?\.(?:iotbing|tuya(?:eu|us|cn|in))\.com", endpoint.hostname or ""):
            raise AccountError("Tuya returned an unsupported account endpoint.", "response")
        self.credentials.update({"endpoint": info["endpoint"].rstrip("/"), "terminal_id": info["terminal_id"],
                                 "token_info": {k: info.get(k, 0) for k in ("t", "uid", "expire_time", "access_token", "refresh_token")}})
        return True

    def _handle(self):
        if self._manager is None:
            from tuya_sharing import Manager, SharingTokenListener

            outer = self

            class TokenListener(SharingTokenListener):
                def update_token(self, token_info):
                    outer.credentials["token_info"] = dict(token_info)
                    callback = getattr(outer, "credentials_updated", None)
                    if callback:
                        callback(outer.credentials)

            self._manager = Manager(self.credentials["client_id"], self.credentials["user_code"],
                                    self.credentials["terminal_id"], self.credentials["endpoint"],
                                    self.credentials["token_info"], TokenListener())
            self._manager.customer_api.session.close()
            self._manager.customer_api.session = SafeSdkSession()
        return self._manager

    def list_devices(self) -> list[dict]:
        with self._lock:
            manager = self._handle()
            manager.update_device_cache()
            result = []
            for raw in manager.device_map.values():
                functions = {code: vars(spec) for code, spec in raw.function.items()}
                strategy = getattr(raw, "local_strategy", {})
                status_functions = {code: vars(spec) for code, spec in getattr(raw, "status_range", {}).items()}
                for dp, spec in strategy.items():
                    if spec["status_code"] in status_functions:
                        status_functions[spec["status_code"]]["dp_id"] = int(dp)
                result.extend(normalize_devices(vars(raw), functions, {dp: spec["status_code"] for dp, spec in strategy.items()}, status_functions))
            return result

    def send_commands(self, identity: str, commands: list[dict]) -> None:
        with self._lock:
            # The SDK Manager discards command responses and filters duplicates.
            # Call its authenticated API so failures cannot look like success.
            result = self._handle().customer_api.post(f"/v1.1/m/thing/{quote(identity, safe='')}/commands", None, {"commands": commands})
            if not isinstance(result, dict) or not result.get("success") or result.get("result") is False:
                raise AccountError("Tuya did not accept the device command.", "service")

    def query_status(self, device: dict) -> dict | None:
        with self._lock:
            raw = self._query_details(device)
            return decode_status(device, self._raw_status(raw), raw.get("online"))

    def _query_details(self, device: dict) -> dict:
        try:
            reply = self._handle().customer_api.get("/v1.0/m/life/ha/devices/detail", {"devIds": device["device_id"]})
        except requests.RequestException:
            raise AccountError("Could not reach Tuya. Check your connection and try again.", "network") from None
        raw = reply.get("result", []) if reply.get("success") else []
        if not isinstance(raw, list) or not raw:
            raise AccountError("Tuya did not return device state. Reconnect the account if authorization has expired.", "authentication")
        return raw[0]

    @staticmethod
    def _raw_status(raw: dict) -> dict:
        return {item["code"]: item["value"] for item in raw.get("status", [])}

    def query_raw_status(self, device: dict) -> dict:
        with self._lock:
            return self._raw_status(self._query_details(device))

    def close(self) -> None:
        with self._lock:
            if self._manager is not None:
                self._manager.customer_api.session.close()
            self._login.session.close()


def decode_status(device: dict, status: dict, online=None) -> dict:
    from .tuya_energy import decode_metering
    result = {}
    if online is not None:
        result["online"] = bool(online)
    power = status.get(code_for(device, "power"))
    if isinstance(power, bool):
        result["power_on"] = power
    mode = status.get(code_for(device, "mode"))
    hsv = colour_values(device, status.get(code_for(device, "colour")))
    if hsv is not None and mode != "white":
        h, s, v = hsv
        scale = colour_scale(device)
        result["color"] = tuple(round(c * 255) for c in colorsys.hsv_to_rgb(h / 360, s / scale, v / scale))
        result["brightness"] = round(v * 100 / scale)
        result["color_temp"] = 0
    brightness = status.get(code_for(device, "brightness"))
    if isinstance(brightness, (int, float)) and "brightness" not in result:
        low, high = value_range(device, "brightness", (10, 1000) if device.get("dp_schema", "v2") == "v2" else (25, 255))
        if high > low:
            result["brightness"] = max(0, min(100, round((brightness - low) * 100 / (high - low))))
    temperature = status.get(code_for(device, "temperature"))
    if mode == "white" and isinstance(temperature, (int, float)):
        low, high = value_range(device, "temperature", (0, 1000) if device.get("dp_schema", "v2") == "v2" else (0, 255))
        warm, cool = int(device.get("color_temp_min", 2700)), int(device.get("color_temp_max", 6500))
        result["color_temp"] = round(warm + max(0, min(1, (temperature - low) / (high - low))) * (cool - warm))
    result.update(decode_metering(device, status))
    return result
