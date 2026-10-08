"""Personal Tuya Smart and LSC accounts using their signed mobile protocol."""

from __future__ import annotations

import base64
import colorsys
import copy
import hashlib
import hmac
import json
import re
import threading
import time
from urllib.parse import urlsplit
import uuid

import requests

from .errors import AccountError
from .http import request_json
from .mobile_profile import PACKAGES, profile_for
from .tuya import decode_status, normalize_devices

MOBILE_REGIONS = {"eu": "https://a1.tuyaeu.com/api.json", "us": "https://a1.tuyaus.com/api.json",
                  "cn": "https://a1.tuyacn.com/api.json", "in": "https://a1.tuyain.com/api.json"}
_SIGNED = frozenset(("a", "v", "lat", "lon", "lang", "deviceId", "appVersion", "ttid", "isH5",
                     "h5Token", "os", "clientId", "postData", "time", "requestId", "et", "n4h5", "sid", "chKey", "sp"))
_AMERICA_CODES = frozenset("591 1 56 57 593 594 502 62 81 82 60 52 95 64 595 51 63 1787 597 66 598 58 84 54 55 852 853 886".split())


def mobile_signature(params: dict, key: str) -> str:
    parts = []
    for name, value in sorted(params.items()):
        if name not in _SIGNED or not value:
            continue
        if name == "postData":
            digest = hashlib.md5(value.encode()).hexdigest()
            value = digest[8:16] + digest[:8] + digest[24:32] + digest[16:24]
        parts.append(f"{name}={value}")
    return hmac.new(key.encode(), "||".join(parts).encode(), hashlib.sha256).hexdigest()


def _endpoint(value: str) -> str:
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.query or parsed.fragment or parsed.path not in ("", "/", "/api.json")
                or not any(host.endswith("." + domain) for domain in ("tuyaeu.com", "tuyaus.com", "tuyacn.com", "tuyain.com", "iotbing.com"))):
            raise ValueError
        return f"https://{host}/api.json"
    except (ValueError, TypeError):
        raise AccountError("The vendor returned an unsupported account server.", "response") from None


def _json_value(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _schema_functions(raw, *, writable_only: bool = True) -> tuple[dict, dict]:
    raw = _json_value(raw)
    if isinstance(raw, dict):
        raw = _json_value(raw.get("schema", raw.get("schemaInfo", [])))
        if isinstance(raw, dict):
            raw = _json_value(raw.get("schema", []))
    if not isinstance(raw, list):
        return {}, {}
    functions, codes = {}, {}
    for item in raw:
        if not isinstance(item, dict) or not item.get("code") or not str(item.get("id", "")).isdigit():
            continue
        if writable_only and "w" not in item.get("mode", "rw"):
            continue
        prop = _json_value(item.get("property", item.get("values", {})))
        prop = prop if isinstance(prop, dict) else {}
        code, dp_id = item["code"], str(item["id"])
        functions[code] = {"code": code, "dp_id": int(dp_id), "type": prop.get("type", ""), "values": prop}
        codes[dp_id] = code
    return functions, codes


def _colour_to_dp(code, value):
    if code not in ("colour_data", "colour_data_v2"):
        return value
    hsv = _json_value(value)
    if not isinstance(hsv, dict):
        return value
    scale = 1000 if code.endswith("_v2") else 255
    h, s, v = (int(hsv[k]) for k in ("h", "s", "v"))
    if not 0 <= h <= 360 or not 0 <= s <= scale or not 0 <= v <= scale:
        raise AccountError("Invalid light colour.", "validation")
    if scale == 1000:
        return f"{h:04x}{s:04x}{v:04x}"
    rgb = tuple(round(x * 255) for x in colorsys.hsv_to_rgb(h / 360, s / scale, v / scale))
    return "".join(f"{x:02x}" for x in rgb) + f"{h:04x}{s:02x}{v:02x}"


def _colour_from_dp(code, value):
    if not isinstance(value, str):
        return value
    if code == "colour_data_v2" and re.fullmatch(r"[a-fA-F0-9]{12}", value):
        h, s, v = (int(value[i:i + 4], 16) for i in (0, 4, 8))
    elif code == "colour_data" and re.fullmatch(r"[a-fA-F0-9]{14}", value):
        h, s, v = int(value[6:10], 16), int(value[10:12], 16), int(value[12:14], 16)
    else:
        return value
    return json.dumps({"h": h, "s": s, "v": v}, separators=(",", ":"))


class TuyaMobileClient:
    def __init__(self, brand: str, credentials: dict | None = None, *, profile: dict | None = None, session=None):
        if brand not in PACKAGES:
            raise AccountError("Unknown vendor account.", "validation")
        self.brand = brand
        self.label = "LSC" if brand == "lsc" else "Tuya"
        allowed = ("profile", "sid", "device_id", "endpoint", "email", "country_code")
        self.credentials = {k: copy.deepcopy(v) for k, v in (credentials or {}).items() if k in allowed}
        if profile is not None:
            self.credentials["profile"] = copy.deepcopy(profile)
        self.credentials.setdefault("device_id", uuid.uuid4().hex)
        self.endpoint = _endpoint(self.credentials.get("endpoint", MOBILE_REGIONS["eu"]))
        self.session = session or requests.Session()
        self.credentials_updated = None
        self._lock = threading.RLock()
        self._verified = False
        self._clock_offset = 0.0
        self._devices = {}

    def _changed(self):
        if self.credentials_updated:
            self.credentials_updated(copy.deepcopy(self.credentials))

    def _request(self, action, version="1.0", body=None, *, authenticated=True, gid=None):
        with self._lock:
            profile = self.credentials.get("profile", {})
            if (not isinstance(profile, dict) or profile.get("package") != PACKAGES[self.brand]
                    or not all(isinstance(profile.get(k), str) and profile[k] for k in ("app_key", "signing_key", "ch_key", "ttid", "app_version"))):
                raise AccountError("Select the matching vendor app package before signing in.", "app_profile")
            if authenticated and not self.credentials.get("sid"):
                raise AccountError(f"Sign in to your {self.label} account first.", "authentication")
            params = {"a": action, "v": version, "clientId": profile["app_key"],
                      "time": str(int(time.time() + self._clock_offset)), "requestId": str(uuid.uuid4()),
                      "deviceId": self.credentials["device_id"], "appVersion": profile["app_version"],
                      "ttid": profile["ttid"], "chKey": profile["ch_key"], "os": "Android", "lang": "en_US", "et": "0.0.1", "nd": "1"}
            if authenticated:
                params["sid"] = self.credentials["sid"]
            if gid is not None:
                params["gid"] = str(gid)
            if body is not None:
                params["postData"] = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
            params["sign"] = mobile_signature(params, profile["signing_key"])
            reply = request_json(self.session, "POST", self.endpoint, data=params,
                                 headers={"User-Agent": f"Thing-UA=APP/Android/{profile['app_version']}/SDK/{profile.get('sdk_version', profile['app_version'])}"})
            timestamp = reply.get("t")
            if isinstance(timestamp, (int, float)) and timestamp > 1_000_000_000:
                server_time = timestamp / 1000 if timestamp > 10_000_000_000 else timestamp
                self._clock_offset = server_time - time.time()
            if reply.get("success") is not True:
                self._raise_error(reply.get("errorCode"))
            return reply.get("result")

    def _raise_error(self, raw_code):
        code = str(raw_code or "UNKNOWN")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", code):
            code = "UNKNOWN"
        if code == "MFA_NEED_SEND_CODE":
            raise AccountError("Email verification is required.", "mfa_send")
        if "MFA" in code:
            raise AccountError("Check your verification code, or clear it to request a new one.", "verification")
        if code in ("ILLEGAL_CLIENT_ID", "SING_VALIDATE_FALED", "SIGN_VALIDATE_FAILED"):
            raise AccountError(f"{self.label} rejected the app login configuration. Select a current matching app package and try again.", "app_profile")
        if code in ("USER_SESSION_INVALID", "SESSION_INVALID", "USER_SESSION_EXPIRED"):
            self.credentials.pop("sid", None)
            self._changed()
            raise AccountError(f"Your {self.label} session has expired. Sign in again from Devices → Accounts.", "authentication")
        if any(word in code for word in ("LIMIT", "FREQUENT", "TOO_MANY")):
            raise AccountError("The vendor rate limit was reached. Wait before trying again.", "rate_limit")
        if any(word in code for word in ("CAPTCHA", "RISK", "VALIDATE_CODE")):
            raise AccountError(f"Complete the requested security check in the {self.label} app, then sign in again here.", "verification_external")
        if any(word in code for word in ("PASSWD", "PASSWORD", "USER_NOT_EXIST", "USER_LOGIN", "EMAIL_ERROR", "COUNTRY_CODE")):
            raise AccountError(f"{self.label} did not accept your sign-in details (code {code}). Check your email, password and account country.", "authentication")
        raise AccountError(f"{self.label} could not complete this request (code {code}).", "service")

    def verify_profile(self):
        self._request("smartlife.p.time.get", authenticated=False)
        self._verified = True

    def load_profile(self, path=""):
        self.credentials["profile"] = profile_for(self.brand, path)
        self._verified = False

    def _password_body(self, email, password, country_code, mfa_code):
        token = self._request("thing.m.user.username.token.get", "2.0",
                              {"countryCode": country_code, "username": email, "isUid": False}, authenticated=False)
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import padding, rsa
            if token.get("publicKey") and token.get("exponent"):
                key = rsa.RSAPublicNumbers(int(token["exponent"]), int(token["publicKey"])).public_key()
            else:
                key = serialization.load_der_public_key(base64.b64decode(token["pbKey"]))
            if not isinstance(key, rsa.RSAPublicKey) or not 1024 <= key.key_size <= 8192 or not token.get("token"):
                raise ValueError
            # This MD5-then-RSA format is required by the vendor protocol. HTTPS
            # remains verified, and plaintext/hash-only fallback is forbidden.
            encrypted = key.encrypt(hashlib.md5(password.encode()).hexdigest().encode(), padding.PKCS1v15()).hex()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise AccountError("The vendor returned an invalid password encryption challenge.", "response") from None
        return {"countryCode": country_code, "passwd": encrypted, "token": token["token"], "ifencrypt": 1,
                "options": json.dumps({"group": 1, "mfaCode": mfa_code}, separators=(",", ":"))}

    def login(self, email: str, password: str, country_code: str, *, mfa_code="", region="auto"):
        with self._lock:
            email, country_code = email.strip(), country_code.strip().lstrip("+")
            if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or not password or not re.fullmatch(r"[1-9][0-9]{0,4}", country_code):
                raise AccountError("Enter your account email, password and country calling code.", "validation")
            if mfa_code and not re.fullmatch(r"[0-9]{4,10}", mfa_code):
                raise AccountError("Enter the verification code sent by your vendor.", "validation")
            if region == "auto":
                region = "cn" if country_code == "86" else "us" if country_code in _AMERICA_CODES else "in" if country_code == "91" and self.brand == "tuya" else "eu"
            if region not in MOBILE_REGIONS:
                raise AccountError("Choose your account's data center.", "validation")
            self.endpoint = MOBILE_REGIONS[region]
            if not self._verified:
                self.verify_profile()
            body = self._password_body(email, password, country_code, mfa_code)
            body["email"] = email
            try:
                result = self._request("thing.m.user.email.password.login", "3.0", body, authenticated=False)
            except AccountError as exc:
                if exc.code == "mfa_send" and mfa_code:
                    raise AccountError("Check the latest verification code, or clear it to request a new one.", "verification") from None
                if exc.code != "mfa_send":
                    raise
                # Tokens are single-use: sending a code needs a fresh challenge.
                verification = self._password_body(email, password, country_code, "null")
                verification["username"] = email
                self._request("thing.m.user.username.mfa.code.get", "1.0", verification, authenticated=False)
                raise AccountError("A verification code was sent to your email. Enter it here to finish signing in.", "verification_required") from None
            if not isinstance(result, dict) or not isinstance(result.get("sid"), str) or not result["sid"]:
                raise AccountError("The vendor did not return an account session.", "response")
            domain = result.get("domain") or {}
            if not isinstance(domain, dict):
                raise AccountError("The vendor returned invalid account routing information.", "response")
            endpoint = _endpoint(domain["mobileApiUrl"]) if domain.get("mobileApiUrl") else self.endpoint
            self.endpoint = endpoint
            self.credentials.update(sid=result["sid"], endpoint=endpoint, email=email, country_code=country_code)
            self._changed()

    def list_devices(self):
        with self._lock:
            homes = self._request("m.life.home.space.list")
            if not isinstance(homes, list) or any(not isinstance(home, dict) for home in homes):
                raise AccountError("The vendor returned an unexpected home list.", "response")
            devices, mappings = {}, {}
            for home in homes:
                gid = home.get("gid", home.get("homeId", home.get("groupId", home.get("id"))))
                if gid is None:
                    raise AccountError("The vendor returned a home without an identifier.", "response")
                raw_devices = self._request("m.life.my.group.device.list", "2.2", {"gid": gid}, gid=gid)
                products = self._request("m.life.device.ref.info.my.list", "7.2", {"gid": gid, "zigbeeGroup": True}, gid=gid)
                if (not isinstance(raw_devices, list) or not isinstance(products, list)
                        or any(not isinstance(raw, dict) for raw in raw_devices)
                        or any(not isinstance(product, dict) for product in products)):
                    raise AccountError("The vendor returned an unexpected device schema list.", "response")
                product_info = {str(p.get("productId", p.get("id", ""))): p for p in products}
                for raw in raw_devices:
                    identity = str(raw.get("devId", raw.get("id", "")))
                    product = product_info.get(str(raw.get("productId", "")), {})
                    schema = raw.get("schema", product.get("schemaInfo", product.get("schema", [])))
                    functions, _ = _schema_functions(schema)
                    all_functions, codes = _schema_functions(schema, writable_only=False)
                    topology = raw.get("deviceTopo") or {}
                    communication = raw.get("communication") or {}
                    if not isinstance(topology, dict) or not isinstance(communication, dict):
                        raise AccountError("The vendor returned invalid device routing information.", "response")
                    gateway = str(raw.get("gwId") or raw.get("parentId") or topology.get("parentDevId") or communication.get("communicationNode") or identity)
                    local = not (raw.get("nodeId") or topology.get("nodeId") or raw.get("isSubDev") or gateway != identity)
                    info = raw.get("productInfo") or {}
                    category = raw.get("category") or product.get("category") or (info.get("category") if isinstance(info, dict) else None) or product.get("categoryCode")
                    normalized = normalize_devices({"id": identity, "name": raw.get("name"), "local_key": raw.get("localKey"),
                                                    "category": category, "support_local": bool(local and raw.get("localKey"))}, functions, codes, all_functions)
                    for device in normalized:
                        device["vendor_account"] = self.brand
                        device["tuya_home_id"] = gid
                        device["tuya_gateway_id"] = gateway
                        devices[device["mac"]] = device
                        mappings[identity] = {"dp_codes": {code: dp for dp, code in codes.items()}, "writable_codes": set(functions), "gw_id": gateway, "home_id": gid}
            self._devices = mappings
            return list(devices.values())

    def bind_device(self, device):
        """Restore non-secret routing/schema data from the saved descriptor."""
        from .tuya_energy import status_functions
        with self._lock:
            self._devices[device["device_id"]] = {
                "dp_codes": {code: str(f["dp_id"]) for code, f in status_functions(device).items() if "dp_id" in f},
                "writable_codes": set(device.get("tuya_functions", {})),
                "gw_id": device.get("tuya_gateway_id", device["device_id"]), "home_id": device.get("tuya_home_id")}

    def send_commands(self, identity, commands):
        with self._lock:
            mapping = self._devices.get(identity, {})
            codes = mapping.get("dp_codes", {})
            writable = mapping.get("writable_codes", set())
            if not commands or any(command.get("code") not in codes or command.get("code") not in writable for command in commands):
                raise AccountError("Refresh this account's devices before using this control.", "unsupported")
            dps = {str(codes[c["code"]]): _colour_to_dp(c["code"], c["value"]) for c in commands}
            result = self._request("thing.m.device.dp.publish", "1.0",
                                   {"devId": identity, "gwId": mapping.get("gw_id", identity), "dps": json.dumps(dps, separators=(",", ":"))},
                                   gid=mapping.get("home_id"))
            if result is not True:
                raise AccountError("The vendor did not accept the device command.", "service")

    def _device_state(self, device):
        identity = device["device_id"]
        mapping = self._devices.get(identity, {})
        raw = self._request("thing.m.device.get", "1.0", {"devId": identity}, gid=mapping.get("home_id"))
        if not isinstance(raw, dict):
            raise AccountError("The vendor did not return device state.", "response")
        dp_info = raw.get("dataPointInfo") or {}
        if not isinstance(dp_info, dict):
            raise AccountError("The vendor returned invalid device state.", "response")
        dps = _json_value(raw.get("dps", dp_info.get("dps", {})))
        if not isinstance(dps, dict):
            raise AccountError("The vendor returned invalid device state.", "response")
        codes = mapping.get("dp_codes", {}) or {code: str(f["dp_id"]) for code, f in device.get("tuya_functions", {}).items() if "dp_id" in f}
        state = {code: _colour_from_dp(code, dps[str(dp)]) for code, dp in codes.items() if str(dp) in dps}
        online = raw.get("isOnline", raw.get("online", raw.get("cloudOnline")))
        return state, online if isinstance(online, bool) else None

    def query_raw_status(self, device):
        return self._device_state(device)[0]

    def query_status(self, device):
        state, online = self._device_state(device)
        return decode_status(device, state, online)

    def query_energy_history(self, device, month):
        from .tuya_energy import decode_energy_history, energy_history_spec, month_bounds
        spec = energy_history_spec(device)
        if spec is None:
            raise AccountError("This device does not report energy usage history.", "unsupported")
        start, end = month_bounds(month)
        with self._lock:
            raw = self._request("tuya.m.dp.rang.stat.day.list", "2.0", {
                "devId": device["device_id"], "dpId": str(spec["dp_id"]), "type": "sum",
                "startDay": start.strftime("%Y%m%d"), "endDay": end.strftime("%Y%m%d"), "auto": 2,
            }, gid=device.get("tuya_home_id"))
        return decode_energy_history(raw, month, spec)

    def close(self):
        self.session.close()
