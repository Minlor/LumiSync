"""Personal login, OEM identities, RSA/MFA and native DP wire fixtures."""

import datetime
import hashlib
import hmac
import io
import json
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from lumisync.accounts.errors import AccountError
from lumisync.accounts.manager import AccountManager, make_client
from lumisync.accounts.mobile_profile import _dex_config, _embedded_key, _manifest, extract_profile
from lumisync.accounts.tuya_mobile import TuyaMobileClient, _endpoint, mobile_signature


def profile(brand="lsc"):
    return {"package": "com.lscsmartconnection.smart" if brand == "lsc" else "com.tuya.smart",
            "app_key": "test-app-key", "signing_key": "test-signing-key", "ch_key": "12345678", "app_version": "2.0.7", "ttid": "test"}


def reply(result=None, *, code=None):
    data = {"success": code is None, "result": result, "errorCode": code, "errorMsg": "private-response"}
    return SimpleNamespace(status_code=200, json=lambda: data)


def post_data(session, index):
    return json.loads(session.request.call_args_list[index].kwargs["data"]["postData"])


SCHEMA = [{"id": dp, "code": code, "mode": "rw", "property": prop} for dp, code, prop in (
    (20, "switch_led", {"type": "bool"}), (21, "work_mode", {"type": "enum", "range": ["white", "colour"]}),
    (22, "bright_value_v2", {"type": "value", "min": 10, "max": 1000}),
    (23, "temp_value_v2", {"type": "value", "min": 0, "max": 1000}), (24, "colour_data_v2", {"type": "string"}),
)]


class MobileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def token(self, token="single-use"):
        public = self.key.public_key()
        numbers = public.public_numbers()
        import base64
        return {"publicKey": str(numbers.n), "exponent": str(numbers.e), "token": token,
                "pbKey": base64.b64encode(public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).decode()}

    def client(self, responses, brand="lsc", **credentials):
        session = Mock()
        session.request.side_effect = responses
        return TuyaMobileClient(brand, credentials, profile=profile(brand), session=session), session

    def test_signing_ignores_unsigned_params_and_hashes_exact_utf8_body(self):
        params = {"a": "thing.m.example", "v": "1.0", "time": "1700000000", "postData": '{"name":"Żółty"}', "sid": "", "nd": "1", "gid": "77", "sdkVersion": "7.7.0"}
        md5 = hashlib.md5(params["postData"].encode()).hexdigest()
        swap = md5[8:16] + md5[:8] + md5[24:32] + md5[16:24]
        canonical = f"a=thing.m.example||postData={swap}||time=1700000000||v=1.0"
        expected = hmac.new(b"fixture-key", canonical.encode(), hashlib.sha256).hexdigest()
        self.assertEqual(mobile_signature(params, "fixture-key"), expected)
        self.assertEqual(params["postData"], '{"name":"Żółty"}')

    def test_login_encrypts_md5_with_rsa_and_discards_password(self):
        for brand in ("lsc", "tuya"):
            client, session = self.client([reply({}), reply(self.token()), reply({"sid": "account-session", "domain": {"mobileApiUrl": "https://a1-weaz.tuyaeu.com"}})], brand, sid="previous-session")
            client.login("me@example.com", "password with spaces ", "+48")
            body = post_data(session, 2)
            decrypted = self.key.decrypt(bytes.fromhex(body["passwd"]), padding.PKCS1v15())
            self.assertEqual(decrypted, hashlib.md5(b"password with spaces ").hexdigest().encode())
            self.assertEqual(json.loads(body["options"]), {"group": 1, "mfaCode": ""})
            self.assertEqual(body["ifencrypt"], 1)
            self.assertEqual(post_data(session, 1), {"countryCode": "48", "username": "me@example.com", "isUid": False})
            self.assertTrue(all("sid" not in call.kwargs["data"] for call in session.request.call_args_list))
            self.assertEqual(client.credentials["sid"], "account-session")
            self.assertEqual(client.endpoint, "https://a1-weaz.tuyaeu.com/api.json")
            self.assertNotIn("password", client.credentials)
            self.assertNotIn("passwd", client.credentials)
            self.assertNotIn("token", client.credentials)

    def test_pem_challenge_is_supported_and_invalid_rsa_has_no_plaintext_fallback(self):
        token = self.token()
        del token["publicKey"], token["exponent"]
        client, session = self.client([reply({}), reply(token), reply({"sid": "session"})])
        client.login("me@example.com", "test-password", "48")
        self.assertEqual(len(bytes.fromhex(post_data(session, 2)["passwd"])), 256)
        client, session = self.client([reply({}), reply({"token": "challenge", "pbKey": "invalid"})])
        with self.assertRaises(AccountError) as caught:
            client.login("me@example.com", "test-password", "48")
        self.assertEqual(caught.exception.code, "response")
        self.assertEqual(session.request.call_count, 2)

    def test_mfa_send_uses_a_fresh_single_use_challenge(self):
        client, session = self.client([reply({}), reply(self.token("first")), reply(code="MFA_NEED_SEND_CODE"), reply(self.token("second")), reply(True)])
        with self.assertRaises(AccountError) as caught:
            client.login("me@example.com", "test-password", "48")
        self.assertEqual(caught.exception.code, "verification_required")
        self.assertEqual(post_data(session, 2)["token"], "first")
        self.assertEqual(post_data(session, 4)["token"], "second")
        self.assertEqual(session.request.call_args.kwargs["data"]["a"], "thing.m.user.username.mfa.code.get")
        self.assertNotIn("sid", client.credentials)
        self.assertNotIn("password", client.credentials)

    def test_mfa_code_is_sent_inside_options_and_wrong_password_sends_no_email(self):
        client, session = self.client([reply({}), reply(self.token()), reply({"sid": "session"})])
        client.login("me@example.com", "test-password", "48", mfa_code="123456")
        self.assertEqual(json.loads(post_data(session, 2)["options"])["mfaCode"], "123456")
        client, session = self.client([reply({}), reply(self.token()), reply(code="USER_PASSWD_ERROR")])
        with self.assertRaises(AccountError) as caught:
            client.login("me@example.com", "test-password", "48")
        self.assertNotIn("private-response", str(caught.exception))
        self.assertEqual(session.request.call_count, 3)

    def test_rejected_app_identity_stops_before_requesting_or_sending_password(self):
        client, session = self.client([reply(code="ILLEGAL_CLIENT_ID")])
        with self.assertRaises(AccountError) as caught:
            client.login("me@example.com", "test-password", "48")
        self.assertEqual(caught.exception.code, "app_profile")
        self.assertEqual(session.request.call_count, 1)
        self.assertNotIn("test-password", str(session.request.call_args))

    def test_oem_profile_cannot_be_reused_for_another_brand(self):
        client, session = self.client([])
        client.credentials["profile"] = profile("tuya")
        with self.assertRaises(AccountError):
            client.verify_profile()
        session.request.assert_not_called()

    def test_untrusted_session_domains_are_rejected_before_saving_or_using_session(self):
        bad = ("http://a1.tuyaeu.com", "https://a1.tuyaeu.com.evil.example", "https://a1.tuyaeu.com:444", "https://account:password@a1.tuyaeu.com", "https://a1.tuyaeu.com/collect", "https://a1.tuyaeu.com?token=secret")
        for endpoint in bad:
            with self.subTest(endpoint=endpoint), self.assertRaises(AccountError):
                _endpoint(endpoint)
        client, session = self.client([reply({}), reply(self.token()), reply({"sid": "session", "domain": {"mobileApiUrl": bad[1]}})])
        with self.assertRaises(AccountError):
            client.login("me@example.com", "test-password", "48")
        self.assertNotIn("sid", client.credentials)
        self.assertEqual(session.request.call_count, 3)

    def test_account_country_controls_initial_region(self):
        for brand, country, expected in (("lsc", "48", "tuyaeu"), ("tuya", "1", "tuyaus"), ("tuya", "86", "tuyacn"), ("tuya", "91", "tuyain"), ("lsc", "91", "tuyaeu")):
            client, session = self.client([reply({}), reply(self.token()), reply({"sid": "session"})], brand)
            client.login("me@example.com", "test-password", country)
            self.assertIn(expected, session.request.call_args.args[1])

    def test_session_expiry_clears_saved_session_without_replaying_commands(self):
        client, session = self.client([reply(code="USER_SESSION_INVALID")], sid="expired")
        client.credentials_updated = Mock()
        with self.assertRaises(AccountError):
            client._request("thing.m.device.get")
        self.assertNotIn("sid", client.credentials)
        client.credentials_updated.assert_called_once()
        self.assertEqual(session.request.call_count, 1)

    def test_device_discovery_uses_native_schema_and_separates_gateway_children(self):
        client, session = self.client([reply([{"gid": 77, "id": 0}]), reply([
            {"devId": "bulb", "name": "Desk", "productId": "lamp-product", "localKey": "abcdefghijklmnop"},
            {"devId": "child", "productId": "lamp-product", "deviceTopo": {"parentDevId": "hub", "nodeId": "zigbee"}, "localKey": "abcdefghijklmnop"},
            {"devId": "socket", "productId": "socket-product"},
        ]), reply([
            {"id": "lamp-product", "schemaInfo": {"schema": json.dumps(SCHEMA)}},
            {"id": "socket-product", "schemaInfo": {"schema": json.dumps([{"id": 1, "code": "switch", "property": {"type": "bool"}}])}},
        ])], sid="session")
        devices = client.list_devices()
        self.assertEqual([d["device_id"] for d in devices], ["bulb", "child"])
        self.assertEqual(devices[0]["dp_map"]["colour"], 24)
        self.assertTrue(devices[0]["local_supported"])
        self.assertFalse(devices[1]["local_supported"])
        self.assertEqual(devices[1]["tuya_gateway_id"], "hub")
        self.assertEqual(devices[0]["tuya_home_id"], 77)
        self.assertNotIn("devices", client.credentials)  # OS vault size stays bounded.
        self.assertEqual(post_data(session, 1), {"gid": 77})
        self.assertEqual(session.request.call_args.kwargs["data"]["a"], "m.life.device.ref.info.my.list")
        self.assertEqual(session.request.call_args.kwargs["data"]["v"], "7.2")

    def test_switch_category_from_product_discovery_enables_the_native_relay(self):
        # The linked account returned category on ProductBean, not DeviceRespBean.
        schema = [{"id": 1, "code": "switch_1", "mode": "rw", "property": {"type": "bool"}},
                  {"id": 16, "code": "backlight_switch", "mode": "rw", "property": {"type": "bool"}}]
        client, session = self.client([reply([{"gid": 77}]), reply([{"devId": "wall", "name": "Wall", "productId": "switch-product", "localKey": "abcdefghijklmnop"}]),
                                       reply([{"id": "switch-product", "category": "kg", "categoryCode": "wf_ble_kg", "schemaInfo": {"schema": json.dumps(schema)}}]),
                                       reply(True), reply({"cloudOnline": True, "dataPointInfo": {"dps": {"1": False, "16": True}}})], sid="session")
        found = client.list_devices()
        self.assertEqual(len(found), 1)
        device = found[0]
        self.assertEqual(device["tuya_category"], "kg")
        self.assertEqual(device["dp_map"]["power"], 1)
        self.assertEqual(device["tuya_power_code"], "switch_1")
        self.assertTrue(device["local_supported"])
        client.bind_device(device)
        client.send_commands("wall", [{"code": "switch_1", "value": False}])
        self.assertEqual(json.loads(post_data(session, 3)["dps"]), {"1": False})
        self.assertEqual(client.query_status(device), {"online": True, "power_on": False})

    def test_multi_gang_discovery_keeps_routing_for_all_channels(self):
        schema = [{"id": dp, "code": "switch_" + str(dp), "mode": "rw", "property": {"type": "bool"}} for dp in (1, 2)]
        client, session = self.client([reply([{"gid": 77}]), reply([{"devId": "wall", "name": "Wall", "productId": "switch-product"}]),
                                       reply([{"id": "switch-product", "category": "kg", "schemaInfo": {"schema": json.dumps(schema)}}]), reply(True), reply(True)], sid="session")
        found = client.list_devices()
        self.assertEqual(len(found), 2)
        for device in found:
            client.bind_device(device)
            client.send_commands(device["device_id"], [{"code": device["tuya_power_code"], "value": True}])
        self.assertEqual(json.loads(post_data(session, 3)["dps"]), {"1": True})
        self.assertEqual(json.loads(post_data(session, 4)["dps"]), {"2": True})

    def test_socket_discovery_and_restored_binding_preserve_read_only_metering(self):
        schema = [{"id": 1, "code": "switch_1", "mode": "rw", "property": {"type": "bool"}},
                  {"id": 19, "code": "cur_power", "mode": "ro", "property": {"type": "value", "scale": 1, "unit": "W"}},
                  {"id": 20, "code": "cur_voltage", "mode": "ro", "property": {"type": "value", "scale": 1, "unit": "V"}},
                  {"id": 17, "code": "add_ele", "mode": "ro", "property": {"type": "value", "scale": 3, "unit": "kWh"}}]
        client, session = self.client([reply([{"gid": 77}]), reply([{"devId": "plug", "productId": "socket-product"}]),
                                       reply([{"id": "socket-product", "category": "cz", "schemaInfo": {"schema": json.dumps(schema)}}]),
                                       reply({"cloudOnline": True, "dataPointInfo": {"dps": {"1": False, "19": 1234, "20": 2301, "17": 50}}})], sid="session")
        device = client.list_devices()[0]
        self.assertEqual(device["device_kind"], "smart_plug")
        self.assertNotIn("cur_power", device["tuya_functions"])
        self.assertIn("cur_power", device["tuya_status_functions"])
        client.bind_device(device)
        status = client.query_status(device)
        self.assertEqual(status, {"online": True, "power_on": False, "power_w": 123.4, "voltage_v": 230.1})
        calls = session.request.call_count
        with self.assertRaises(AccountError) as caught:
            client.send_commands("plug", [{"code": "cur_power", "value": 1000}])
        self.assertEqual(caught.exception.code, "unsupported")
        self.assertEqual(session.request.call_count, calls)

    def test_monthly_energy_query_uses_the_account_statistics_endpoint(self):
        from datetime import date
        client, session = self.client([reply({"result": {"20261001": "1.25", "20261002": "#"}})], sid="session")
        device = {"device_id": "plug", "tuya_home_id": 77, "tuya_status_functions": {
            "add_ele": {"dp_id": 17, "values": {"unit": "kWh", "scale": 3}}}}
        with patch("lumisync.accounts.tuya_energy.month_bounds", return_value=(date(2026, 10, 1), date(2026, 10, 8))):
            history = client.query_energy_history(device, "2026-10")
        self.assertEqual(history["total_kwh"], 1.25)
        self.assertEqual(session.request.call_args.kwargs["data"]["a"], "tuya.m.dp.rang.stat.day.list")
        self.assertEqual(session.request.call_args.kwargs["data"]["v"], "2.0")
        self.assertEqual(post_data(session, 0), {"devId": "plug", "dpId": "17", "type": "sum", "startDay": "20261001", "endDay": "20261008", "auto": 2})
        self.assertEqual(session.request.call_args.kwargs["data"]["gid"], "77")
        with self.assertRaises(AccountError):
            client.query_energy_history({"device_id": "plain-switch"}, "2026-10")
        self.assertEqual(session.request.call_count, 1)

    def test_five_digit_territory_country_can_sign_in(self):
        client, session = self.client([reply({}), reply(self.token()), reply({"sid": "session"})])
        client.login("me@example.com", "test-password", "35818")
        self.assertEqual(post_data(session, 1)["countryCode"], "35818")
        self.assertEqual(client.credentials["country_code"], "35818")

    def test_malformed_discovery_models_return_a_safe_response_error(self):
        for responses in ([reply([None])],
                          [reply([{"gid": 77}]), reply([None]), reply([])],
                          [reply([{"gid": 77}]), reply([]), reply([None])],
                          [reply([{"gid": 77}]), reply([{"devId": "bulb", "deviceTopo": "invalid"}]), reply([])]):
            client, _ = self.client(responses, sid="session")
            with self.assertRaises(AccountError) as caught:
                client.list_devices()
            self.assertEqual(caught.exception.code, "response")

    def test_cloud_commands_encode_native_hex_hsv_and_preserve_gateway_routing(self):
        client, session = self.client([reply(True)], sid="session")
        client.bind_device({"device_id": "bulb", "tuya_gateway_id": "hub", "tuya_home_id": 77,
                            "tuya_functions": {"work_mode": {"dp_id": 9}, "colour_data_v2": {"dp_id": 12}}})
        client.send_commands("bulb", [{"code": "work_mode", "value": "colour"}, {"code": "colour_data_v2", "value": '{"h":120,"s":1000,"v":500}'}])
        body = post_data(session, 0)
        self.assertEqual(body["gwId"], "hub")
        self.assertEqual(json.loads(body["dps"]), {"9": "colour", "12": "007803e801f4"})
        self.assertEqual(session.request.call_args.kwargs["data"]["gid"], "77")

    def test_negative_command_acknowledgement_is_not_success(self):
        client, _ = self.client([reply(False)], sid="session")
        client.bind_device({"device_id": "bulb", "tuya_functions": {"switch_led": {"dp_id": 20}}})
        with self.assertRaises(AccountError):
            client.send_commands("bulb", [{"code": "switch_led", "value": True}])

    def test_status_decodes_native_dp_values_for_cloud_controls(self):
        client, _ = self.client([reply({"isOnline": True, "dps": {"20": False, "21": "colour", "24": "007803e801f4"}})], sid="session")
        functions = {s["code"]: {"code": s["code"], "dp_id": s["id"], "values": s["property"]} for s in SCHEMA}
        device = {"device_id": "bulb", "tuya_functions": functions, "dp_schema": "v2"}
        client.bind_device(device)
        status = client.query_status(device)
        self.assertIs(status["power_on"], False)
        self.assertEqual(status["brightness"], 50)
        self.assertEqual(status["color"], (0, 128, 0))

    def test_current_mobile_model_reads_nested_dps_and_cloud_online(self):
        client, _ = self.client([reply({"cloudOnline": False, "dataPointInfo": {"dps": {"20": False, "21": "white", "22": 505}}})], sid="session")
        functions = {s["code"]: {"code": s["code"], "dp_id": s["id"], "values": s["property"]} for s in SCHEMA}
        device = {"device_id": "bulb", "tuya_functions": functions, "dp_schema": "v2"}
        client.bind_device(device)
        status = client.query_status(device)
        self.assertIs(status["online"], False)
        self.assertIs(status["power_on"], False)
        self.assertEqual(status["brightness"], 50)

    def test_provider_factory_and_restored_client_bind_device_schema(self):
        client = make_client("lsc_account", {"profile": profile(), "sid": "session", "password": "must-be-discarded"})
        self.addCleanup(client.close)
        self.assertIsInstance(client, TuyaMobileClient)
        self.assertNotIn("password", client.credentials)
        manager = AccountManager()
        manager._clients["account"] = client
        device = {"account_id": "account", "device_id": "bulb", "tuya_functions": {"switch_led": {"dp_id": 20}}}
        self.assertIs(manager.client_for(device), client)
        self.assertEqual(client._devices["bulb"]["dp_codes"], {"switch_led": "20"})
        self.assertIs(manager.client_for({"account_id": "account"}), client)


def fake_dex():
    strings = ["Lcom/thingclips/sample/BuildConfig;", "THING_SMART_APPKEY", "THING_SMART_SECRET", "THING_SMART_TTID", "A" * 20, "B" * 32, "test-ttid"]
    data = bytearray(112 + len(strings) * 4 + 4 + 24 + 32)
    data[:8] = b"dex\n035\0"
    so, to, fo, co = 112, 112 + len(strings) * 4, 112 + len(strings) * 4 + 4, 112 + len(strings) * 4 + 28
    struct.pack_into("<12I", data, 56, len(strings), so, 1, to, 0, 0, 3, fo, 0, 0, 1, co)
    for i, s in enumerate(strings):
        struct.pack_into("<I", data, so + 4 * i, len(data))
        data.extend(bytes([len(s)]) + s.encode() + b"\0")
    for i in range(3):
        struct.pack_into("<HHI", data, fo + i * 8, 0, 0, i + 1)
    class_data = len(data)
    data.extend(b"\x03\0\0\0\0\x19\x01\x19\x01\x19")
    static_data = len(data)
    data.extend(b"\x03\x17\x04\x17\x05\x17\x06")
    struct.pack_into("<8I", data, co, 0, 0, 0, 0, 0, 0, class_data, static_data)
    return bytes(data)


def fake_manifest():
    strings = ["manifest", "package", "versionName", "com.lscsmartconnection.smart", "2.0.7"]
    encoded = b""
    offsets = []
    for s in strings:
        offsets.append(len(encoded))
        encoded += bytes([len(s), len(s)]) + s.encode() + b"\0"
    size = 28 + len(strings) * 4 + len(encoded)
    pool = struct.pack("<HHI5I", 1, 28, size, len(strings), 0, 0x100, 28 + len(strings) * 4, 0) + struct.pack("<5I", *offsets) + encoded
    attrs = b"".join(struct.pack("<IIIHBBI", 0xffffffff, name, value, 8, 0, 3, value) for name, value in ((1, 3), (2, 4)))
    node = struct.pack("<HHI4I6H", 0x102, 16, 36 + len(attrs), 1, 0xffffffff, 0xffffffff, 0, 20, 20, 2, 0, 0, 0) + attrs
    return struct.pack("<HHI", 3, 8, 8 + len(pool) + len(node)) + pool + node


def fake_bmp():
    pixels = bytearray(8192)
    hash_code = 0
    for c in "A" * 20:
        hash_code = (hash_code * 31 + ord(c)) & 0xffffffff
    if hash_code >= 0x80000000:
        hash_code -= 0x100000000
    start = (abs(hash_code) % len(pixels)) // 2
    points = [6000, 7000]
    pixels[start + 1:start + 7] = bytes([1, 2]) + (start ^ points[0]).to_bytes(4, "big")
    secret = int.from_bytes(b"C" * 32, "big")
    for i, pos in enumerate(points):
        value = secret + 2 * (i + 1)
        encoded = bytes([1, i + 1, 32]) + value.to_bytes(32, "big") + (pos ^ points[(i + 1) % 2]).to_bytes(4, "big")
        pixels[pos:pos + len(encoded)] = encoded
    header = bytearray(54)
    header[:2] = b"BM"
    struct.pack_into("<I", header, 10, 54)
    return bytes(header + pixels)


class AppProfileTests(unittest.TestCase):
    def test_manifest_dex_and_bitmap_are_read_without_android_execution(self):
        self.assertEqual(_manifest(fake_manifest())["package"], "com.lscsmartconnection.smart")
        self.assertEqual(_dex_config(fake_dex())["THING_SMART_APPKEY"], "A" * 20)
        self.assertEqual(_embedded_key(fake_bmp(), "A" * 20), "C" * 32)

    def test_complete_apk_profile_uses_certificate_digest_and_native_identity_proof(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LumiSync test fixture")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key()).serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1)).sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.DER)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as z:
            z.writestr("AndroidManifest.xml", fake_manifest())
            z.writestr("classes.dex", fake_dex())
            z.writestr("assets/t_s.bmp", fake_bmp())
        raw = bytearray(stream.getvalue())
        eocd = raw.rfind(b"PK\x05\x06")
        central = struct.unpack_from("<I", raw, eocd + 16)[0]
        def lp(value):
            return struct.pack("<I", len(value)) + value
        value = lp(lp(lp(lp(b"") + lp(lp(cert))) + lp(b"") + lp(b"")))
        pair = struct.pack("<QI", len(value) + 4, 0x7109871a) + value
        block_size = len(pair) + 24
        block = struct.pack("<Q", block_size) + pair + struct.pack("<Q", block_size) + b"APK Sig Block 42"
        raw = raw[:central] + block + raw[central:]
        struct.pack_into("<I", raw, eocd + len(block) + 16, central + len(block))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "base.apk"
            path.write_bytes(raw)
            found = extract_profile(path, "lsc")
            digest = ":".join(f"{b:02X}" for b in hashlib.sha256(cert).digest())
            identity = "com.lscsmartconnection.smart_" + digest
            self.assertEqual(found["signing_key"], identity + "_" + "C" * 32 + "_" + "B" * 32)
            self.assertEqual(found["ch_key"], hmac.new(b"A" * 20, identity.encode(), hashlib.sha256).hexdigest()[8:16])
            with self.assertRaises(AccountError):
                extract_profile(path, "tuya")

    def test_corrupt_or_incomplete_package_returns_a_safe_actionable_error(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.apk"
            path.write_bytes(b"private-malformed-content")
            with self.assertRaises(AccountError) as caught:
                extract_profile(path, "lsc")
            self.assertEqual(caught.exception.code, "app_profile")
            self.assertNotIn("private-malformed-content", str(caught.exception))
        with self.assertRaises(AccountError):
            extract_profile("missing.apk", "unknown")


if __name__ == "__main__":
    unittest.main()
