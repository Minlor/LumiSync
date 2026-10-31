"""Typed device descriptors normalize legacy settings names once."""

import unittest
from unittest.mock import patch

from lumisync.device import Device, as_device
from lumisync.drivers.registry import capabilities_for_device


class DeviceModelTests(unittest.TestCase):
    def test_legacy_names_are_normalized_when_a_device_is_loaded(self):
        device = Device.from_dict(
            {
                "devId": "lamp-1",
                "segmentCountOverride": "24",
                "localKey": "abcdefghijklmnop",
                "Device_Port": 4020,
                "model": "H619C",
            }
        )

        self.assertEqual(device.device_id, "lamp-1")
        self.assertEqual(device.segment_count_override, 24)
        self.assertEqual(device.get("local_key"), "abcdefghijklmnop")
        self.assertEqual(device.get("device_port"), 4020)
        self.assertNotIn("devId", device.to_dict())
        self.assertNotIn("segmentCountOverride", device.to_dict())

    def test_mapping_compatibility_keeps_legacy_callers_working(self):
        device = Device.from_dict({"mac": "AA:BB", "model": "H619C"})

        self.assertEqual(device["mac"], "AA:BB")
        self.assertEqual(device.get("model"), "H619C")
        self.assertEqual(device["transport"], "lan")
        self.assertNotIn("cloud_transport", device)
        self.assertEqual(
            device,
            {"mac": "AA:BB", "model": "H619C", "transport": "lan"},
        )

    def test_as_device_accepts_plain_dicts_and_typed_devices(self):
        plain = as_device({"mac": "AA:BB"})
        typed = as_device(plain)
        self.assertIs(plain, typed)
        self.assertEqual(typed.identity, "AA:BB")

    def test_lan_capabilities_do_not_open_a_socket(self):
        with patch(
            "lumisync.drivers.govee_lan.connection.create_lan_socket",
            side_effect=AssertionError("capability lookup must not open a socket"),
        ):
            capabilities = capabilities_for_device(
                {"transport": "lan", "model": "H619C"}
            )

        self.assertGreater(capabilities.segment_count, 0)


if __name__ == "__main__":
    unittest.main()
