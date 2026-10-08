"""Account protocol fixtures and credential boundaries; no live vendor calls."""

import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from lumisync.accounts.errors import AccountError
from lumisync.accounts.govee import GoveeAccountClient, GoveePlatformClient
from lumisync.accounts.http import request_json
from lumisync.accounts.manager import AccountManager
from lumisync.accounts.privacy import mask_account_label, mask_email
from lumisync.accounts.secrets import CredentialVault, local_key_for, protect_device
from lumisync.accounts.tuya import TuyaOpenApiClient, TuyaSharingClient, decode_status, normalize_device, normalize_devices
from lumisync.drivers.cloud import CloudLightAdapter


FUNCTIONS = {code: {"code": code, "values": json.dumps(values)} for code, values in (
    ("switch_led", {}), ("work_mode", {"range": ["white", "colour"]}),
    ("bright_value_v2", {"min": 10, "max": 1000}),
    ("temp_value_v2", {"min": 0, "max": 1000}), ("colour_data_v2", {}),
)}


def light(**extra):
    return {"transport": "tuya_cloud", "device_id": "light-1", "account_id": "owner",
            "dp_schema": "v2", "tuya_functions": FUNCTIONS, **extra}


def response(data, status=200):
    return SimpleNamespace(status_code=status, json=lambda: data)


class HttpTests(unittest.TestCase):
    def test_credentials_do_not_follow_redirects_and_requests_are_bounded(self):
        session = Mock()
        session.request.return_value = response({"ok": True})
        request_json(session, "POST", "https://vendor.example/login", json={"password": "test-password"})
        self.assertEqual(session.request.call_args.kwargs["timeout"], (5, 15))
        self.assertFalse(session.request.call_args.kwargs["allow_redirects"])
        self.assertNotIn("verify", session.request.call_args.kwargs)

    def test_vendor_errors_do_not_expose_response_bodies_or_credentials(self):
        for status, code in ((401, "authentication"), (403, "authentication"), (429, "rate_limit"), (302, "service"), (500, "service")):
            session = Mock()
            session.request.return_value = response({"token": "private-value"}, status)
            with self.subTest(status=status), self.assertRaises(AccountError) as caught:
                request_json(session, "GET", "https://vendor.example")
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("private-value", str(caught.exception))

    def test_network_exception_is_sanitized(self):
        session = Mock()
        session.request.side_effect = requests.RequestException("Authorization: private-value")
        with self.assertRaises(AccountError) as caught:
            request_json(session, "GET", "https://vendor.example")
        self.assertNotIn("private-value", str(caught.exception))


class VaultTests(unittest.TestCase):
    def test_session_mode_never_needs_a_system_keyring_even_when_disconnecting(self):
        store = CredentialVault()
        with patch.object(store, "_backend", side_effect=AssertionError("No backend in session mode")):
            store.put("connection", {"token": "private-value"}, remember=False)
            self.assertEqual(store.get("connection")["token"], "private-value")
            store.delete("connection")
        self.assertNotIn("connection", store._session)

    def test_failed_persistence_does_not_create_a_false_saved_session(self):
        store = CredentialVault()
        backend = Mock()
        backend.set_password.side_effect = OSError("private-value")
        with patch.object(store, "_backend", return_value=backend), self.assertRaises(AccountError):
            store.put("connection", {"token": "private-value"})
        self.assertNotIn("connection", store._session)

    def test_local_key_is_replaced_by_an_os_vault_reference(self):
        store = CredentialVault()
        with patch("lumisync.accounts.secrets.vault", store):
            protected = protect_device({"device_id": "bulb", "localKey": "abcdefghijklmnop"}, remember=False)
            self.assertEqual(local_key_for(protected), "abcdefghijklmnop")
        self.assertNotIn("local_key", protected)
        self.assertNotIn("localKey", protected)
        self.assertEqual(protected["local_key_ref"], "tuya-local/bulb")

    def test_plaintext_backend_and_plaintext_only_chain_are_rejected(self):
        PlainBackend = type("PlainBackend", (), {"priority": 1, "__module__": "keyrings.alt.file"})
        for backend in (PlainBackend(), SimpleNamespace(backends=[PlainBackend()])):
            with patch("keyring.get_keyring", return_value=backend), self.assertRaises(AccountError):
                CredentialVault._backend()

    def test_restoring_session_account_preserves_session_storage_policy(self):
        store = CredentialVault()
        manager = AccountManager()
        client = SimpleNamespace(credentials={"api_key": "test-key"}, close=Mock())
        with patch("lumisync.accounts.manager.vault", store):
            metadata = manager.register("govee_api", client, remember=False)
            manager.close_all()
            with patch("lumisync.accounts.manager.make_client", return_value=client):
                self.assertIs(manager.client_for({"account_id": metadata["id"]}), client)
            manager.disconnect(metadata["id"])


class GoveeTests(unittest.TestCase):
    def test_personal_login_discards_password_and_lists_only_lighting_topics(self):
        session = Mock()
        session.request.side_effect = [
            response({"status": 200, "client": {"token": "test-token", "accountId": 42, "topic": "account/topic"}}),
            response({"status": 200, "data": {"devices": [
                {"device": "AA:BB", "sku": "H619C", "deviceName": "Desk", "deviceExt": {"deviceSettings": json.dumps({"topic": "device/topic"})}},
                {"device": "heater", "sku": "H7130", "deviceExt": {"deviceSettings": json.dumps({"topic": "heater/topic"})}},
            ]}}),
        ]
        client = GoveeAccountClient(session=session)
        client.login("me@example.com", "test-password")
        self.assertTrue(session.request.call_args.args[1].endswith("/account/rest/account/v2/login"))
        login_headers = session.request.call_args.kwargs["headers"]
        self.assertIn("GoveeHome/" + login_headers["appVersion"], login_headers["User-Agent"])
        self.assertEqual(client.credentials["account_id"], "42")
        self.assertNotIn("password", client.credentials)
        self.assertEqual(client.list_devices()[0]["iot_topic"], "device/topic")
        self.assertEqual(session.request.call_args.args, ("GET", "https://app2.govee.com/bff-app/v1/device/list"))
        self.assertEqual(session.request.call_args.kwargs["headers"]["Authorization"], "Bearer test-token")

    def test_incomplete_account_session_requires_verification(self):
        session = Mock()
        session.request.return_value = response({"status": 200, "client": {"token": "test-token"}})
        with self.assertRaises(AccountError):
            GoveeAccountClient(session=session).login("me@example.com", "test-password")

    def test_email_verification_reuses_the_client_and_never_saves_login_secrets(self):
        session = Mock()
        session.request.side_effect = [response({"status": 454}), response({"status": 200}),
                                       response({"status": 200, "client": {"token": "test-token", "accountId": 42, "topic": "account/topic"}})]
        client = GoveeAccountClient({"client_id": "stable-client", "password": "legacy-password", "code": "111111"}, session)
        with self.assertRaises(AccountError) as caught:
            client.login("me@example.com", " password with spaces ")
        self.assertEqual(caught.exception.code, "verification_required")
        verification = session.request.call_args
        self.assertTrue(verification.args[1].endswith("/account/rest/account/v1/verification"))
        self.assertEqual(verification.kwargs["json"], {"type": 8, "email": "me@example.com"})
        client.login("me@example.com", " password with spaces ", mfa_code="123456")
        retry = session.request.call_args.kwargs
        self.assertEqual(retry["json"], {"email": "me@example.com", "password": " password with spaces ", "client": "stable-client", "code": "123456"})
        for call in session.request.call_args_list:
            self.assertEqual(call.kwargs["headers"]["clientId"], "stable-client")
            self.assertNotIn("Authorization", call.kwargs["headers"])
        self.assertEqual(set(client.credentials), {"client_id", "email", "account_id", "token", "topic"})
        client.close()

    def test_wrong_verification_code_is_distinct_and_does_not_send_another_email(self):
        session = Mock()
        session.request.return_value = response({"status": 454})
        with self.assertRaises(AccountError) as caught:
            GoveeAccountClient(session=session).login("me@example.com", "test-password", mfa_code="123456")
        self.assertEqual(caught.exception.code, "verification")
        self.assertEqual(session.request.call_count, 1)

    def test_repeated_challenge_and_resend_respect_the_email_cooldown(self):
        session = Mock()
        session.request.side_effect = [response({"status": 454}), response({"status": 200}), response({"status": 454})]
        client = GoveeAccountClient(session=session)
        for _ in range(2):
            with self.assertRaises(AccountError) as caught:
                client.login("me@example.com", "test-password")
            self.assertEqual(caught.exception.code, "verification_required")
        self.assertEqual(session.request.call_count, 3)
        with self.assertRaises(AccountError) as caught:
            client.request_verification("me@example.com")
        self.assertEqual(caught.exception.code, "rate_limit")
        with self.assertRaises(AccountError) as caught:
            client.request_verification("different@example.com")
        self.assertEqual(caught.exception.code, "validation")

    def test_sign_in_errors_identify_credentials_version_or_service_without_raw_text(self):
        for data, code in (({"status": 401, "message": "private-response"}, "authentication"),
                           ({"status": 451, "message": "private-response"}, "authentication"),
                           ({"status": 400, "message": "The app version is too low, please upgrade the version!"}, "app_version"),
                           ({"status": 429}, "rate_limit"), ({"status": 503, "message": "private-response"}, "service")):
            session = Mock()
            session.request.return_value = response(data)
            with self.subTest(data=data), self.assertRaises(AccountError) as caught:
                GoveeAccountClient(session=session).login("me@example.com", "test-password")
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("private-response", str(caught.exception))
            self.assertEqual(session.request.call_count, 1)

    def test_platform_capabilities_and_command_use_the_device_schema(self):
        session = Mock()
        session.request.side_effect = [response({"code": 200, "data": [
            {"device": "AA:BB", "sku": "H6008", "capabilities": [
                {"type": "devices.capabilities.range", "instance": "brightness", "parameters": {"range": {"min": 1, "max": 100}}},
            ]},
        ]}), response({"code": 200})]
        client = GoveePlatformClient({"api_key": "test-key"}, session)
        with patch.object(client, "_pace"):
            device = client.list_devices()[0]
            client.control(device, "brightness", 50)
        payload = session.request.call_args.kwargs["json"]["payload"]
        self.assertEqual(payload["capability"], {"type": "devices.capabilities.range", "instance": "brightness", "value": 50})
        cap = CloudLightAdapter(device).capabilities
        self.assertTrue(cap.supports_brightness)
        self.assertFalse(cap.supports_color)
        self.assertFalse(cap.supports_streaming)

    def test_account_mqtt_payload_and_readback_preserve_power_off(self):
        client = GoveeAccountClient({"account_id": "42", "topic": "account/topic"})
        info = Mock(rc=0)
        info.is_published.return_value = True
        client._mqtt = Mock()
        client._mqtt.publish.return_value = info
        with patch.object(client, "_ensure_mqtt"):
            client.control({"iot_topic": "device/topic"}, "turn", {"val": 0})
        packet = json.loads(client._mqtt.publish.call_args.args[1])
        self.assertEqual(packet["msg"]["data"], {"val": 0})
        self.assertFalse(client._mqtt.publish.call_args.kwargs["retain"])
        client._on_message(None, None, SimpleNamespace(payload=json.dumps({"state": {"device": "AA:BB", "onOff": 0, "brightness": 30}}).encode()))
        self.assertFalse(client._states["AA:BB"]["power_on"])
        client.close()

    def test_platform_brightness_respects_the_advertised_minimum(self):
        client = Mock()
        device = {"transport": "govee_cloud", "cloud_capabilities": [
            {"instance": "brightness", "parameters": {"range": {"min": 1, "max": 100}}},
        ]}
        with patch("lumisync.drivers.cloud.account_manager.client_for", return_value=client):
            CloudLightAdapter(device).set_brightness(0)
        client.control.assert_called_once_with(device, "brightness", 1)

    def test_mqtt_subscription_must_be_confirmed(self):
        client = GoveeAccountClient({"topic": "account/topic"})
        mqtt = Mock()
        client._on_connect(mqtt, None, None, 0, None)
        self.assertFalse(client._connected.is_set())
        client._on_subscribe(mqtt, None, 1, [128], None)
        self.assertFalse(client._connected.is_set())
        client._on_subscribe(mqtt, None, 1, [0], None)
        self.assertTrue(client._connected.is_set())
        client.close()


class TuyaTests(unittest.TestCase):
    def test_light_switch_uses_its_relay_and_excludes_unrelated_controls(self):
        functions = {"switch_1": {"type": "Boolean", "dp_id": 1}, "backlight_switch": {"type": "Boolean", "dp_id": 16}}
        device = normalize_device({"id": "wall", "category": "kg", "name": "Wall switch"}, functions)
        self.assertEqual(device["dp_map"], {"power": 1})
        self.assertEqual(device["tuya_power_code"], "switch_1")
        adapter = CloudLightAdapter(device)
        self.assertTrue(adapter.capabilities.supports_power)
        self.assertFalse(adapter.capabilities.supports_brightness)
        self.assertFalse(adapter.capabilities.supports_color)
        self.assertFalse(adapter.capabilities.supports_streaming)
        client = Mock()
        with patch("lumisync.drivers.cloud.account_manager.client_for", return_value=client):
            adapter.set_power(False)
        client.send_commands.assert_called_once_with("wall", [{"code": "switch_1", "value": False}])
        self.assertEqual(decode_status(device, {"switch_1": False, "backlight_switch": True}, True), {"online": True, "power_on": False})
        for category in ("clkg", "heater", ""):
            self.assertEqual(normalize_devices({"id": "other", "category": category}, functions), [])
        self.assertEqual(normalize_devices({"id": "button", "category": "kg"}, {"switch_1": {"type": "Enum"}}), [])

    def test_each_switch_gang_controls_and_reads_only_its_own_relay(self):
        functions = {"switch_2": {"type": "bool", "dp_id": 2}, "switch_1": {"type": "bool", "dp_id": 1}}
        devices = normalize_devices({"id": "wall", "name": "Wall", "category": "kg"}, functions)
        self.assertEqual([d["name"] for d in devices], ["Wall · Switch 1", "Wall · Switch 2"])
        self.assertEqual([d["model"] for d in devices], [d["name"] for d in devices])
        self.assertEqual([d["dp_map"]["power"] for d in devices], [1, 2])
        self.assertEqual(len({d["mac"] for d in devices}), 2)
        client = Mock()
        with patch("lumisync.drivers.cloud.account_manager.client_for", return_value=client):
            for device in devices:
                CloudLightAdapter(device).set_power(True)
        self.assertEqual([call.args for call in client.send_commands.call_args_list], [
            ("wall", [{"code": "switch_1", "value": True}]), ("wall", [{"code": "switch_2", "value": True}])])
        states = [decode_status(d, {"switch_1": False, "switch_2": True}) for d in devices]
        self.assertEqual(states, [{"power_on": False}, {"power_on": True}])

    def test_dimmable_light_with_numbered_power_dp_keeps_both_controls(self):
        device = normalize_device({"id": "dimmer", "category": "kg"}, {
            "switch_1": {"type": "bool", "dp_id": 1}, "bright_value_v2": {"type": "Integer", "dp_id": 2}})
        cap = CloudLightAdapter(device).capabilities
        self.assertTrue(cap.supports_power)
        self.assertTrue(cap.supports_brightness)
        self.assertEqual(device["dp_map"], {"power": 1, "brightness": 2})

    def test_signed_token_and_command_match_tuya_hmac_contract(self):
        session = Mock()
        session.request.side_effect = [response({"success": True, "result": {"access_token": "test-token", "expire_time": 7200}}), response({"success": True, "result": True})]
        client = TuyaOpenApiClient({"region": "eu", "access_id": "access-id", "access_secret": "test-secret"}, session)
        with patch("lumisync.accounts.tuya.time.time", return_value=1):
            client.send_commands("device", [{"code": "switch_led", "value": False}])
        token_headers = session.request.call_args_list[0].kwargs["headers"]
        token_string = "GET\n" + hashlib.sha256(b"").hexdigest() + "\n\n/v1.0/token?grant_type=1"
        expected = hmac.new(b"test-secret", ("access-id1000" + token_string).encode(), hashlib.sha256).hexdigest().upper()
        self.assertEqual(token_headers["sign"], expected)
        request = session.request.call_args_list[1]
        self.assertEqual(request.kwargs["headers"]["access_token"], "test-token")
        self.assertEqual(json.loads(request.kwargs["data"])["commands"][0]["value"], False)
        self.assertFalse(request.kwargs["allow_redirects"])

    def test_light_normalization_rejects_sockets_and_keeps_custom_dp_map(self):
        self.assertIsNone(normalize_device({"id": "plug"}, {"switch": {}}))
        result = normalize_device({"id": "bulb", "local_key": "abcdefghijklmnop"}, FUNCTIONS, {101: "switch_led", 105: "colour_data_v2"})
        self.assertEqual(result["dp_map"], {"power": 101, "colour": 105})

    def test_cloud_readback_uses_hsv_brightness_and_white_temperature(self):
        colour = decode_status(light(), {"switch_led": False, "work_mode": "colour", "colour_data_v2": '{"h":120,"s":1000,"v":500}', "bright_value_v2": 1000}, True)
        self.assertFalse(colour["power_on"])
        self.assertEqual(colour["color"], (0, 128, 0))
        self.assertEqual(colour["brightness"], 50)
        white = decode_status(light(), {"work_mode": "white", "bright_value_v2": 505, "temp_value_v2": 1000}, False)
        self.assertEqual(white["brightness"], 50)
        self.assertEqual(white["color_temp"], 6500)
        self.assertFalse(white["online"])

    def test_cloud_colour_brightness_does_not_write_white_dp(self):
        client = Mock()
        client.query_raw_status.return_value = {"work_mode": "colour", "colour_data_v2": {"h": 120, "s": 700, "v": 900}}
        with patch("lumisync.drivers.cloud.account_manager.client_for", return_value=client):
            CloudLightAdapter(light()).set_brightness(25)
        commands = client.send_commands.call_args.args[1]
        self.assertEqual(commands[0], {"code": "work_mode", "value": "colour"})
        self.assertEqual(json.loads(commands[1]["value"]), {"h": 120, "s": 700, "v": 250})

    def test_cloud_white_only_device_does_not_offer_colour_or_streaming(self):
        functions = {k: FUNCTIONS[k] for k in ("switch_led", "bright_value_v2", "temp_value_v2")}
        adapter = CloudLightAdapter(light(tuya_functions=functions))
        self.assertFalse(adapter.capabilities.supports_color)
        self.assertTrue(adapter.capabilities.supports_white)
        with self.assertRaises(AccountError):
            adapter.begin_stream()

    def test_official_qr_authorization_stores_only_the_issued_session(self):
        client = TuyaSharingClient()
        client._login.qr_code = Mock(return_value={"success": True, "result": {"qrcode": "qr-token"}})
        payload = client.request_qr("test-code")
        self.assertEqual(payload, "tuyaSmart--qrLogin?token=qr-token")
        client._login.login_result = Mock(return_value=(True, {"access_token": "test-token", "refresh_token": "test-refresh", "endpoint": "https://apigw-eu.iotbing.com", "terminal_id": "terminal", "uid": "owner", "expire_time": 3600, "t": 1}))
        self.assertTrue(client.finish_qr())
        self.assertNotIn("password", client.credentials)
        self.assertEqual(client.credentials["token_info"]["refresh_token"], "test-refresh")
        client.close()

    def test_qr_redirect_endpoint_cannot_receive_the_session_token(self):
        client = TuyaSharingClient()
        client._qr_token = "qr-token"
        client._login.login_result = Mock(return_value=(True, {"access_token": "test-token", "refresh_token": "test-refresh", "endpoint": "https://vendor.evil.example", "terminal_id": "terminal", "uid": "owner"}))
        client.credentials["user_code"] = "test-code"
        with self.assertRaises(AccountError):
            client.finish_qr()
        self.assertNotIn("token_info", client.credentials)
        client.close()


class AccountPrivacyTests(unittest.TestCase):
    def test_emails_and_legacy_account_labels_are_mostly_masked(self):
        self.assertEqual(mask_email("minlor@example.com"), "m•••@•••.com")
        self.assertEqual(mask_account_label("Tuya Smart · minlor+lights@example.com"), "Tuya Smart · m•••@•••.com")
        self.assertEqual(mask_account_label("Tuya Smart · m•••@•••.com"), "Tuya Smart · m•••@•••.com")
        self.assertEqual(mask_account_label("Govee account"), "Govee account")


if __name__ == "__main__":
    unittest.main()
