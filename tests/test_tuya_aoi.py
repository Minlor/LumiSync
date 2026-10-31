"""AoI UDP 40001 fast channel: frames, session derivation and the client."""
import threading
import unittest
import unittest.mock as mock

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import struct

from lumisync.drivers import tuya_aoi
from lumisync.drivers.tuya_aoi import (
    AOI_HEADER,
    AOI_TAIL,
    BUSINESS_REQUEST_TYPE,
    CONSULT_KEY,
    CONSULT_REQUEST_TYPE,
    ENVELOPE_BYTES,
    TuyaAoiClient,
    aoi_add,
    business_frame,
    child_mate_id,
    consult_frame,
    consult_payload,
    hmac_sha256,
    main_mate_id,
    main_uuid_for_mac,
    negotiate_key_from,
    parse_response_frame,
    respond_consult,
    rgbic_business_value,
    rgb_to_hsv_tuya,
    session_key_from,
)
from lumisync.drivers.tuya_lan import TuyaLightAdapter
from tests.test_drivers import FakeTuyaHandle

LOCAL_KEY = "0123456789abcdef"
MAIN_UUID = bytes.fromhex("a1b2c3d4e5f60718")


def main_mate():
    return main_mate_id(MAIN_UUID)


def _decrypt_block(block: bytes, key: bytes, nonce: bytes, add: bytes) -> bytes:
    length = struct.unpack(">I", block[4:8])[0]
    data = block[8:8 + length]
    tag = block[8 + length:8 + length + 16]
    return AESGCM(key).decrypt(nonce, data + tag, add)


def _aoi_struct():
    return struct


class IdentityTests(unittest.TestCase):
    def test_main_uuid_is_a_stable_mac_hash(self):
        self.assertEqual(main_uuid_for_mac("aabbccddeeff"), main_uuid_for_mac("aabbccddeeff"))
        self.assertEqual(len(main_uuid_for_mac()), 8)

    def test_mate_ids_pad_device_ids_to_26_bytes(self):
        mate = main_mate()
        device_id = "fixturedevice000000004"
        child = child_mate_id(device_id, mate)
        self.assertEqual(len(mate), 4)
        self.assertEqual(len(child), 4)
        self.assertNotEqual(child, mate)
        self.assertNotEqual(child_mate_id(device_id, main_mate_id(b"9" * 8)), child)


class FramingTests(unittest.TestCase):
    def test_add_is_the_documented_22_byte_layout(self):
        mate = main_mate()
        add = aoi_add(0x11223344, CONSULT_REQUEST_TYPE, mate, 86, 1)
        self.assertEqual(len(add), 22)
        self.assertEqual(add[0:2], b"\x00\x00")
        self.assertEqual(add[2:6], b"\x11\x22\x33\x44")
        self.assertEqual(add[6:10], CONSULT_REQUEST_TYPE)
        self.assertEqual(add[10:14], mate)
        self.assertEqual(struct.unpack(">I", add[14:18])[0], 86)
        self.assertEqual(struct.unpack(">I", add[18:22])[0], 1)

    def test_consult_frame_encodes_token_and_randA(self):
        def random_bytes(count):
            return bytes([count]) * count
        frame, nonce, add, consults = consult_frame(
            ["gw1"], main_mate(), 7,
            random_bytes=random_bytes,
        )
        self.assertTrue(frame.startswith(AOI_HEADER))
        self.assertTrue(frame.endswith(AOI_TAIL))
        self.assertEqual(frame[38:42], child_mate_id("gw1", main_mate()))
        self.assertEqual(
            struct.unpack(">I", frame[18:22])[0],
            ENVELOPE_BYTES + 60,
        )
        self.assertEqual(len(consults), 1)
        (token, rand_a), = consults["gw1"],
        self.assertEqual(len(token), 16)
        self.assertEqual(len(rand_a), 16)
        block = frame[38:38 + 60]
        self.assertEqual(len(block), 60)
        plaintext = _decrypt_block(block, CONSULT_KEY, nonce, add)
        self.assertEqual(plaintext, consult_payload(token, rand_a))
        self.assertEqual(nonce, bytes([12]) * 12)
        # The reply's mate echoes the requested device's mate id through the
        # AAD: parse_response_frame must expose that slot for matching.
        # The request AAD carries the sender's own main mate.
        self.assertEqual(add[10:14], main_mate())

    def test_parse_response_frame_round_trip(self):
        mate = main_mate()
        add = aoi_add(9, bytes.fromhex("00000006"), mate, 42, 1)
        nonce = b"answernonce1"
        encrypted = b"x" * 20
        tag = b"t" * 16
        frame = AOI_HEADER + add + nonce + child_mate_id("gw1", mate) + \
            struct.pack(">I", len(encrypted)) + encrypted + tag + AOI_TAIL
        parsed = parse_response_frame(frame)
        self.assertIsNotNone(parsed)
        reply_mate, reply_nonce, reply_cipher, reply_tag, reply_add = parsed
        self.assertEqual(reply_mate, child_mate_id("gw1", mate))
        self.assertEqual(reply_nonce, nonce)
        self.assertEqual(reply_cipher, encrypted)
        self.assertEqual(reply_tag, tag)
        self.assertEqual(reply_add, add)

    def test_business_frame_encrypts_under_the_session_key(self):
        mate = main_mate()
        session = b"s" * 16
        nonce = b"businesnonce"
        value = bytes.fromhex("00010014010f0f0f")
        frame = business_frame("gw2", mate, 42, session, value, nonce=nonce)
        block = frame[38:-4]
        plain = _decrypt_block(block, session, frame[26:38], frame[4:26])
        self.assertEqual(plain, value)
        self.assertEqual(
            struct.unpack(">I", frame[18:22])[0],
            ENVELOPE_BYTES + 8 + len(value) + 16,
        )
        self.assertEqual(frame[10:14], BUSINESS_REQUEST_TYPE)


class SessionTests(unittest.TestCase):
    def test_negotiation_key_and_session_key_share_the_app_derivation(self):
        token = bytes(range(16))
        rand_a = bytes(range(16, 32))
        rand_b = bytes(range(32, 48))
        negotiate = negotiate_key_from(bytes(LOCAL_KEY, "ascii"), token)
        self.assertEqual(len(negotiate), 16)
        session = session_key_from(negotiate, rand_a, rand_b)
        self.assertEqual(len(session), 16)
        self.assertNotEqual(hmac_sha256(rand_a, negotiate), hmac_sha256(session, negotiate))
        self.assertEqual(negotiate, negotiate_key_from(bytes(LOCAL_KEY, "ascii"), token))
        self.assertEqual(session, session_key_from(negotiate, rand_a, rand_b))


class RgbicValueTests(unittest.TestCase):
    def test_rgbic_business_value_matches_the_vendor_layout(self):
        bands = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
        zone_bands = [1, 2, 3, 4, 1, 2]
        value = rgbic_business_value(bands, zone_bands, 6).hex()
        def band(r, g, b):
            h, s, v = rgb_to_hsv_tuya((r, g, b))
            return f"{h:04x}{s:04x}{v:04x}"
        expected = (
            "0004"
            + band(255, 0, 0)
            + band(0, 255, 0)
            + band(0, 0, 255)
            + band(255, 255, 0)
            + "0006"
            + "010203040102"
        )
        self.assertEqual(value, expected)


class ClientHandshakeTests(unittest.TestCase):
    def setUp(self):
        self.client = TuyaAoiClient(
            main_uuid=MAIN_UUID,
            random_bytes=lambda count: bytes([count]) * count,
            now=lambda: 100.0,
        )

    def _reply_for(self, device_id: str) -> bytes:
        token, rand_a, _done = self.client._pending[device_id]
        nonce = b"answernonce1"
        add = aoi_add(77, bytes.fromhex("00000006"),
                      child_mate_id(device_id, main_mate()),
                      ENVELOPE_BYTES + 116, 1)
        return respond_consult(LOCAL_KEY, token, rand_a, nonce, add,
                               main_mate(), 77, device_id)

    def test_consult_reply_establishes_the_session(self):
        self.client.learn_local_key("gw1", LOCAL_KEY)
        frame, _nonce, _add, consults = consult_frame(
            ["gw1"], main_mate(), self.client.next_sequence(),
            random_bytes=self.client._random,
        )
        token, rand_a = consults["gw1"]
        self.client._pending["gw1"] = (token, rand_a, threading.Event())
        reply = self._reply_for("gw1")
        parsed = parse_response_frame(reply)
        self.assertIsNotNone(parsed)
        self.client.handle_datagram(reply)
        self.assertTrue(self.client.has_session("gw1"))
        negotiate = negotiate_key_from(bytes(LOCAL_KEY, "ascii"), token)
        rand_b = AESGCM(negotiate).decrypt(parsed[1], parsed[2] + parsed[3], parsed[4])[:16]
        self.assertEqual(self.client.session_key("gw1"), session_key_from(negotiate, rand_a, rand_b))
        self.assertTrue(self.client.send_business("gw1", b"\x00\x01\x02"))

    def test_business_send_before_negotiation_returns_false(self):
        self.assertFalse(self.client.send_business("gwX", b"\x00\x01"))

    def test_sequence_increments(self):
        self.assertEqual(self.client.next_sequence(), 1)
        self.assertEqual(self.client.next_sequence(), 2)


class AdapterAoiTests(unittest.TestCase):
    """The adapter prefers AoI when the session exists, else the TCP path."""

    def _adapter(self):
        device = {"ip": "1.2.3.4", "device_id": "gw1", "local_key": LOCAL_KEY,
                  "tuya_paint_dp": 61, "tuya_paint_zones": 4,
                  "tuya_functions": {"paint_colour_data": {"dp_id": 61, "type": "raw"}}}
        return TuyaLightAdapter(device)

    def test_adapter_streams_business_frames_when_the_session_is_up(self):
        adapter = self._adapter()
        adapter._dev = FakeTuyaHandle()
        stub = mock.Mock()
        stub.started = threading.Event()
        stub.started.set()
        stub.has_session = mock.Mock(return_value=True)
        stub.send_business = mock.Mock(return_value=True)
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
        with mock.patch.object(tuya_aoi, "AOI_FAST_STREAM", True), \
                mock.patch.object(tuya_aoi, "aoi", lambda: stub):
            self.assertTrue(adapter._stream_via_aoi(colors))
        sent_value = stub.send_business.call_args[0][1]
        self.assertTrue(bytes.fromhex(sent_value.hex()).startswith(b"\x00\x04"))
        # The TCP handle is untouched: no probe, no DP writes.
        self.assertFalse(adapter._dev.single)
        self.assertFalse(adapter._dev.multiple)

    def test_adapter_falls_back_when_the_fast_stream_is_off(self):
        adapter = self._adapter()
        adapter._dev = FakeTuyaHandle()
        with mock.patch.object(tuya_aoi, "AOI_FAST_STREAM", False):
            self.assertFalse(adapter._stream_via_aoi([(1, 2, 3)]))

    def test_a_sessionless_adapter_waits_like_the_vendor_app(self):
        adapter = self._adapter()
        adapter._dev = FakeTuyaHandle()
        stub = mock.Mock()
        stub.started = threading.Event()
        stub.started.set()
        stub.has_session = mock.Mock(return_value=False)
        stub.send_business = mock.Mock()
        colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
        with mock.patch.object(tuya_aoi, "AOI_FAST_STREAM", True), \
                mock.patch.object(tuya_aoi, "aoi", lambda: stub):
            self.assertTrue(adapter._stream_via_aoi(colors))
        stub.send_business.assert_not_called()
        # But stream becoming negotiated simply starts sending the next frames.


if __name__ == "__main__":
    unittest.main()
