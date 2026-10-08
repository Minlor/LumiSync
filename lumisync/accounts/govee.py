"""Govee Platform API and the account-issued MQTT channel used by the apps.

Account endpoints are private and may change. No embedded vendor credentials or
certificate-validation bypasses are used. A password is needed only at sign-in.
"""

from __future__ import annotations

import base64
import json
import os
import re
import ssl
import tempfile
import threading
import time
import uuid

import requests

from .errors import AccountError
from .http import request_json

PLATFORM_URL = "https://openapi.api.govee.com/router/api/v1"
APP_URL = "https://app2.govee.com"
APP_VERSION = "7.4.10"


def object_value(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


class GoveePlatformClient:
    def __init__(self, credentials: dict, session=None) -> None:
        self.credentials = dict(credentials)
        self.session = session or requests.Session()
        self._lock = threading.RLock()
        self._last_request = {}

    def _pace(self, key: str, interval: float) -> None:
        remaining = interval - (time.monotonic() - self._last_request.get(key, float("-inf")))
        if remaining > 0:
            time.sleep(remaining)
        self._last_request[key] = time.monotonic()

    def _request(self, method: str, path: str, body=None) -> dict:
        with self._lock:
            key = path + "/" + str((body or {}).get("payload", {}).get("device", "account"))
            self._pace(key, 0.5 if path == "/device/control" else 2.0)
            self._pace("account", 1 / 12)
            reply = request_json(
                self.session, method, PLATFORM_URL + path,
                headers={"Govee-API-Key": self.credentials["api_key"]},
                **({"json": body} if body is not None else {}),
            )
        if reply.get("code", 200) != 200:
            raise AccountError("Govee could not complete this request. Check the API key and device support.", "service")
        return reply

    def list_devices(self) -> list[dict]:
        result = self._request("GET", "/user/devices").get("data")
        if not isinstance(result, list):
            raise AccountError("Govee returned an unexpected device list.", "response")
        devices = []
        for raw in result:
            capabilities = raw.get("capabilities", [])
            # Import lights, leaving appliances with unrelated controls out of the UI.
            if not any(c.get("instance") in ("colorRgb", "brightness", "colorTemperatureK") for c in capabilities):
                continue
            devices.append({
                "device_id": raw["device"], "mac": raw["device"],
                "sku": raw["sku"], "model": raw["sku"],
                "name": raw.get("deviceName", raw["sku"]),
                "transport": "govee_cloud", "cloud_capabilities": capabilities,
            })
        return devices

    def control(self, device: dict, instance: str, value) -> None:
        capability = next((c for c in device.get("cloud_capabilities", []) if c.get("instance") == instance), None)
        if not capability:
            raise AccountError("This control is not supported by this device.", "unsupported")
        self._request("POST", "/device/control", {
            "requestId": str(uuid.uuid4()),
            "payload": {"sku": device["sku"], "device": device["device_id"],
                        "capability": {"type": capability["type"], "instance": instance, "value": value}},
        })

    def query_status(self, device: dict) -> dict:
        reply = self._request("POST", "/device/state", {
            "requestId": str(uuid.uuid4()),
            "payload": {"sku": device["sku"], "device": device["device_id"]},
        })
        payload = reply.get("payload", {})
        result = {}
        for item in payload.get("capabilities", []):
            value = object_value(item.get("state")).get("value")
            instance = item.get("instance")
            if instance == "online":
                result["online"] = value is True or value == 1
            elif instance == "powerSwitch":
                result["power_on"] = value == 1
            elif instance == "brightness" and isinstance(value, (int, float)):
                result["brightness"] = int(value)
            elif instance == "colorRgb" and isinstance(value, int):
                result["color"] = ((value >> 16) & 255, (value >> 8) & 255, value & 255)
            elif instance == "colorTemperatureK":
                result["color_temp"] = value
        if not result:
            raise AccountError("Govee did not return device state.", "response")
        return result

    def close(self) -> None:
        self.session.close()


class GoveeAccountClient:
    def __init__(self, credentials: dict | None = None, session=None) -> None:
        self.credentials = {key: value for key, value in (credentials or {}).items()
                            if key in ("client_id", "token", "account_id", "topic", "email")}
        self.credentials.setdefault("client_id", uuid.uuid4().hex)
        self.session = session or requests.Session()
        self._lock = threading.RLock()
        self._mqtt = None
        self._connected = threading.Event()
        self._states: dict[str, dict] = {}
        self._last_commands = {}
        self._received = threading.Condition()
        self._verification_email = ""
        self._verification_sent_at = float("-inf")

    def _request(self, method: str, path: str, body=None, *, authenticated=True, accepted=()) -> dict:
        headers = {"appVersion": APP_VERSION, "clientId": self.credentials["client_id"],
                   "clientType": "1", "iotVersion": "0", "timestamp": str(int(time.time() * 1000)),
                   "User-Agent": f"GoveeHome/{APP_VERSION} (com.ihoment.GoVeeSensor; build:8; iOS 26.5.0) Alamofire/5.11.0"}
        if authenticated and self.credentials.get("token"):
            headers["Authorization"] = "Bearer " + self.credentials["token"]
        reply = request_json(self.session, method, APP_URL + path, headers=headers,
                             **({"json": body} if body is not None else {}))
        try:
            status = int(reply.get("status", 200))
        except (ValueError, TypeError):
            raise AccountError("Govee returned an unexpected response status.", "response") from None
        if status != 200 and status not in accepted:
            # Classify known failures without displaying arbitrary response text,
            # which can contain account information or HTML.
            message = str(reply.get("message", "")).casefold()
            if "version" in message and any(word in message for word in ("low", "upgrade", "old")):
                raise AccountError("Govee requires a newer app protocol. Update LumiSync and try again.", "app_version")
            if status == 429:
                raise AccountError("Govee's rate limit was reached. Wait before trying again.", "rate_limit")
            if not authenticated and status == 451:
                raise AccountError("Govee Home does not recognize this email address. Check the account email shown in Govee Home.", "authentication")
            if status == 401 or any(word in message.replace(" ", "") for word in ("incorrectpassword", "invalidpassword", "wrongpassword")):
                if not authenticated:
                    raise AccountError("Govee did not accept this email and password. Use the credentials from Govee Home.", "authentication")
                raise AccountError("Your Govee session has expired. Sign in again from Devices → Accounts.", "authentication")
            code = str(status) if 0 <= status <= 999999 else "unknown"
            raise AccountError(f"Govee could not complete the request (code {code}). Try again or check your account in Govee Home.", "service")
        return reply

    def request_verification(self, email: str) -> None:
        email = email.strip()
        if self._verification_email != email:
            raise AccountError("Start signing in before requesting a verification code.", "validation")
        if time.monotonic() - self._verification_sent_at < 60:
            raise AccountError("A code was just sent. Wait a minute before requesting another.", "rate_limit")
        self._request("POST", "/account/rest/account/v1/verification", {"type": 8, "email": email}, authenticated=False)
        self._verification_sent_at = time.monotonic()

    def login(self, email: str, password: str, *, mfa_code: str = "") -> None:
        email, mfa_code = email.strip(), mfa_code.strip()
        if not email or not password:
            raise AccountError("Enter your Govee email and password.", "validation")
        if mfa_code and not re.fullmatch(r"[0-9]{4,10}", mfa_code):
            raise AccountError("Enter the verification code from your email.", "verification")
        body = {"email": email, "password": password, "client": self.credentials["client_id"]}
        if mfa_code:
            body["code"] = mfa_code
        with self._lock:
            reply = self._request("POST", "/account/rest/account/v2/login", body, authenticated=False, accepted=(454,))
            if str(reply.get("status")) == "454":
                if mfa_code:
                    raise AccountError("Govee did not accept that verification code. Check the latest email or request a new code.", "verification")
                self._verification_email = email
                if time.monotonic() - self._verification_sent_at >= 60:
                    self.request_verification(email)
                raise AccountError("Govee sent a verification code to your email. Enter it here to finish signing in.", "verification_required")
        account = object_value(reply.get("client"))
        if not all(account.get(k) for k in ("token", "accountId", "topic")):
            raise AccountError("Govee returned an incomplete sign-in session. Check your account in Govee Home and try again.", "response")
        self._close_mqtt()
        self.credentials.update({"token": account["token"], "account_id": str(account["accountId"]),
                                 "topic": account["topic"], "email": email})
        self._verification_email = ""
        callback = getattr(self, "credentials_updated", None)
        if callback:
            callback(self.credentials)

    def list_devices(self) -> list[dict]:
        with self._lock:
            reply = self._request("GET", "/bff-app/v1/device/list")
        raw_devices = object_value(reply.get("data")).get("devices")
        if not isinstance(raw_devices, list):
            raise AccountError("Govee returned an unexpected account device list.", "response")
        result = []
        for raw in raw_devices:
            if not isinstance(raw, dict):
                raise AccountError("Govee returned an unexpected device entry.", "response")
            ext = object_value(raw.get("deviceExt"))
            settings = object_value(ext.get("deviceSettings"))
            sku = str(raw.get("sku", ""))
            if not isinstance(settings.get("topic"), str) or not sku.startswith(("H6", "H70")):
                continue
            result.append({"device_id": raw["device"], "mac": raw["device"], "sku": sku,
                           "model": sku, "name": raw.get("deviceName", sku),
                           "transport": "govee_account", "iot_topic": settings["topic"]})
        return result

    def _ensure_mqtt(self) -> None:
        if self._mqtt is not None and self._connected.is_set():
            return
        self._close_mqtt()
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
        import paho.mqtt.client as mqtt

        info = object_value(self._request("GET", "/app/v1/account/iot/key").get("data"))
        endpoint = str(info.get("endpoint", ""))
        if not re.fullmatch(r"[a-zA-Z0-9-]+\.iot\.[a-z0-9-]+\.amazonaws\.com(?:\.cn)?", endpoint):
            raise AccountError("Govee returned an unsupported IoT endpoint.", "response")
        try:
            key, certificate, _chain = load_key_and_certificates(
                base64.b64decode(info["p12"], validate=True), str(info.get("p12Pass", "")).encode()
            )
            if key is None or certificate is None:
                raise ValueError("missing certificate")
            context = ssl.create_default_context(cafile=requests.certs.where())
            # SSLContext needs filenames. mkstemp creates private files; delete
            # them immediately once the context has loaded the certificate.
            paths = []
            try:
                for content in (certificate.public_bytes(serialization.Encoding.PEM),
                                key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())):
                    fd, path = tempfile.mkstemp(prefix="lumisync-tls-")
                    paths.append(path)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(content)
                context.load_cert_chain(paths[0], paths[1])
            finally:
                for path in paths:
                    os.unlink(path)
        except (ValueError, KeyError, OSError):
            raise AccountError("Could not load the IoT certificate issued by Govee. Sign in again.", "authentication") from None
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"AP/{self.credentials['account_id']}/{uuid.uuid4().hex}")
        client.tls_set_context(context)
        client.on_connect = self._on_connect
        client.on_disconnect = lambda *_: self._connected.clear()
        client.on_message = self._on_message
        client.on_subscribe = self._on_subscribe
        self._mqtt = client
        try:
            client.connect_async(endpoint, 8883, 60)
            client.loop_start()
            if not self._connected.wait(12):
                raise AccountError("Could not connect to Govee Wi-Fi control. Sign in again or try an API key.", "network")
        except Exception:
            self._close_mqtt()
            raise AccountError("Could not connect to Govee Wi-Fi control. Sign in again or try an API key.", "network") from None

    def _on_connect(self, client, _userdata, _flags, reason_code, _properties) -> None:
        if reason_code == 0:
            client.subscribe(self.credentials["topic"], qos=0)

    def _on_subscribe(self, _client, _userdata, _mid, reason_codes, _properties) -> None:
        if reason_codes and all(code == 0 for code in reason_codes):
            self._connected.set()

    def _on_message(self, _client, _userdata, message) -> None:
        try:
            packet = object_value(json.loads(message.payload))
            state = object_value(packet.get("state"))
            identity = packet.get("device") or state.get("device")
            if not identity or not state:
                return
            result = {"online": True, "received_at": time.monotonic()}
            if "onOff" in state:
                if state["onOff"] in (0, 1, False, True):
                    result["power_on"] = state["onOff"] in (1, True)
            if "brightness" in state:
                result["brightness"] = int(state["brightness"])
            color = object_value(state.get("color"))
            if all(k in color for k in ("r", "g", "b")):
                result["color"] = tuple(int(color[k]) for k in ("r", "g", "b"))
            if "colorTemInKelvin" in state:
                result["color_temp"] = int(state["colorTemInKelvin"])
            with self._received:
                self._states.setdefault(str(identity), {}).update(result)
                self._received.notify_all()
        except (ValueError, TypeError, AttributeError, KeyError):
            return  # Ignore malformed network packets, leaving state unconfirmed.

    def control(self, device: dict, command: str, data: dict | None = None) -> None:
        topic = device.get("iot_topic")
        if not topic or "+" in topic or "#" in topic:
            raise AccountError("This device has no supported Wi-Fi control topic.", "unsupported")
        with self._lock:
            self._ensure_mqtt()
            remaining = 0.5 - (time.monotonic() - self._last_commands.get(topic, float("-inf")))
            if remaining > 0:
                time.sleep(remaining)
            self._last_commands[topic] = time.monotonic()
            message = {"cmd": command, "cmdVersion": 2 if command == "status" else 0,
                       "transaction": f"v_{time.time_ns() // 1000}", "type": 0 if command == "status" else 1}
            if data is not None:
                message["data"] = data
            info = self._mqtt.publish(topic, json.dumps({"msg": message}), qos=0, retain=False)
            info.wait_for_publish(timeout=5)
            if info.rc != 0 or not info.is_published():
                raise AccountError("Govee did not accept the Wi-Fi command.", "network")

    def query_status(self, device: dict) -> dict | None:
        started = time.monotonic()
        self.control(device, "status")
        identity = device["device_id"]
        with self._received:
            self._received.wait_for(lambda: self._states.get(identity, {}).get("received_at", 0) >= started, timeout=3)
            state = self._states.get(identity, {})
            if state.get("received_at", 0) < started:
                return None
            return {k: v for k, v in state.items() if k != "received_at"}

    def _close_mqtt(self) -> None:
        client, self._mqtt = self._mqtt, None
        if client is not None:
            client.disconnect()
            client.loop_stop()
        self._connected.clear()

    def close(self) -> None:
        with self._lock:
            self._close_mqtt()
            self.session.close()
