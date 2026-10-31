"""Tuya LAN presence frames (UDP 6667) decode into online knowledge."""

import json
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from lumisync.drivers import tuya_presence
from lumisync.drivers.tuya_presence import (
    PresenceRegistry,
    TuyaPresenceListener,
    _FRAME_TYPE,
    _HEADER,
    _KEY,
    _TAIL,
)


def build_presence_frame(device_id: str, nonce: bytes = b"N" * 12,
                         key: bytes = _KEY, broken: bool = False) -> bytes:
    payload = json.dumps({"gwId": device_id}).encode("utf-8")
    # App layout: header, 14-byte AAD (version+seq+type+id), nonce, ciphertext, tag, tail.
    aad = bytes.fromhex("0000") + bytes.fromhex("01020304") + _FRAME_TYPE + bytes.fromhex("0a0b0c0d")
    cipher = AESGCM(key)
    encrypted = cipher.encrypt(nonce, payload, aad)
    ciphertext, tag = encrypted[:-16], encrypted[-16:]
    blob = b"".join([_HEADER, aad, nonce, ciphertext, tag, _TAIL])
    if broken:
        blob = blob[:-4] + b"\x00\x00\x66\x99"
    return blob


class PresenceRegistryTests(unittest.TestCase):
    def test_registry_tracks_freshness(self):
        registry = PresenceRegistry()
        registry.note_seen("dev1", at=100.0)
        self.assertEqual(registry.last_seen("dev1"), 100.0)
        self.assertIsNone(registry.last_seen("dev2"))
        self.assertTrue(registry.is_online("dev1", now=100.0))
        self.assertTrue(registry.is_online("dev1", now=110.0))
        self.assertTrue(registry.is_online("dev1", now=100.0 + tuya_presence.PRESENCE_FRESH_AFTER))
        # One second past the app's own grace window is already offline.
        self.assertFalse(registry.is_online("dev1", now=100.0 + tuya_presence.PRESENCE_FRESH_AFTER + 1))
        self.assertEqual(registry.age("dev1", now=110.0), 10.0)

    def test_last_seen_never_seen_is_none(self):
        registry = PresenceRegistry()
        self.assertIsNone(registry.last_seen("missing"))
        self.assertFalse(registry.is_online("missing"))


class PresenceFrameTests(unittest.TestCase):
    def setUp(self):
        self.registry = PresenceRegistry()
        self.listener = TuyaPresenceListener(self.registry)

    def test_valid_frame_marks_device_seen(self):
        self.listener.handle_datagram(build_presence_frame("fixturedevice000000005"))
        self.assertTrue(self.registry.is_online("fixturedevice000000005"))

    def test_callbacks_receive_the_device_and_address(self):
        seen = []
        listener = TuyaPresenceListener(self.registry, on_seen=lambda dev, addr: seen.append((dev, addr)))
        listener.handle_datagram(build_presence_frame("gw1"), ("192.168.0.44", 6667))
        self.assertEqual(seen, [("gw1", "192.168.0.44")])

    def test_wrong_header_type_or_tail_is_ignored(self):
        self.listener.handle_datagram(
            build_presence_frame("gw2", broken=True)[:-4] + b"0000")
        self.listener.handle_datagram(build_presence_frame("gw2")[:4] + b"0000")
        self.listener.handle_datagram(build_presence_frame("gw2").replace(
            _FRAME_TYPE, bytes.fromhex("00000014"), 1))
        # Short packets cannot carry an announcement block.
        self.listener.handle_datagram(_HEADER + _FRAME_TYPE + _TAIL)
        self.assertIsNone(self.registry.last_seen("gw2"))

    def test_ciphertext_from_another_key_is_ignored(self):
        other = bytes(16)
        self.listener.handle_datagram(
            build_presence_frame("gw3", key=other))
        self.assertIsNone(self.registry.last_seen("gw3"))

    def test_non_json_payload_is_ignored(self):
        nonce = b"n" * 12
        aad = bytes.fromhex("0000") + bytes.fromhex("01020304") + _FRAME_TYPE + bytes.fromhex("0a0b0c0d")
        cipher = AESGCM(_KEY)
        ciphertext = cipher.encrypt(nonce, b"not json at all", aad)
        blob = b"".join([_HEADER, aad, nonce, ciphertext, _TAIL])
        self.listener.handle_datagram(blob)
        self.assertIsNone(self.registry.last_seen("gw4"))


if __name__ == "__main__":
    unittest.main()
