import base64
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from lumisync import connection, utils


class LanHelperTests(unittest.TestCase):
    def test_get_segment_count_reads_user_override(self):
        self.assertEqual(connection.get_segment_count({"segment_count_override": 6}), 6)
        self.assertEqual(connection.get_segment_count({"segmentCountOverride": "16"}), 16)

    def test_get_segment_count_uses_default_for_missing_or_invalid_values(self):
        self.assertEqual(connection.get_segment_count({}, default=10), 10)
        self.assertEqual(connection.get_segment_count({"segment_count_override": "bad"}, default=8), 8)
        self.assertEqual(connection.get_segment_count({"segment_count_override": 0}, default=7), 7)
        self.assertEqual(connection.get_segment_count({"unknownZoneCount": 12}, default=9), 9)

    def test_get_device_port_uses_control_port_for_discovered_devices(self):
        self.assertEqual(connection.get_device_port({}), 4003)
        self.assertEqual(connection.get_device_port({"port": 4001}), 4003)
        self.assertEqual(connection.get_device_port({"port": 4010}), 4010)
        self.assertEqual(connection.get_device_port({"port": 4001, "manual": True}), 4001)
        self.assertEqual(connection.get_device_port({"Device_Port": 4020, "port": 4001}), 4020)

    def test_convert_colors_clamps_channels_and_checksum(self):
        payload = utils.convert_colors([(300, -5, 1)])
        packet = list(base64.b64decode(payload))

        self.assertEqual(packet[:-1], [0xBB, 0x00, 0x05, 0xB0, 0x01, 1, 255, 0, 1])

        checksum = 0
        for byte in packet[:-1]:
            checksum ^= byte
        self.assertEqual(packet[-1], checksum)

    def test_convert_colors_rejects_payloads_over_one_byte_length(self):
        with self.assertRaisesRegex(ValueError, "at most 255"):
            utils.convert_colors([(0, 0, 0)] * 256)

    def test_razer_mode_switch_uses_b1_frames_for_on_and_off(self):
        expected_frames = {
            True: bytes([0xBB, 0x00, 0x01, 0xB1, 0x01, 0x0A]),
            False: bytes([0xBB, 0x00, 0x01, 0xB1, 0x00, 0x0B]),
        }

        for enabled, expected in expected_frames.items():
            with self.subTest(enabled=enabled), patch.object(connection, "send") as send:
                connection.switch_razer(object(), {"ip": "192.0.2.10"}, enabled)

                message = send.call_args.args[2]
                encoded = message["msg"]["data"]["pt"]
                self.assertEqual(base64.b64decode(encoded), expected)

                checksum = 0
                for byte in expected[:-1]:
                    checksum ^= byte
                self.assertEqual(expected[-1], checksum)

    def test_resample_colors_spreads_samples_over_one_loop(self):
        colors = [(index, index, index) for index in range(10)]

        self.assertEqual(
            utils.resample_colors_to_count(colors, 4),
            [(0, 0, 0), (2, 2, 2), (5, 5, 5), (7, 7, 7)],
        )
        self.assertEqual(
            utils.resample_colors_to_count(colors[:2], 4),
            [(0, 0, 0), (0, 0, 0), (1, 1, 1), (0, 0, 0)],
        )

    def test_describe_port_conflict_is_user_facing(self):
        message = connection.describe_port_conflict(4002, owner="PID 1234")

        self.assertIn("UDP port 4002", message)
        self.assertIn("Govee Desktop", message)
        self.assertIn("LAN Control", message)
        self.assertIn("PID 1234", message)

    def test_cloud_runtime_module_is_not_importable(self):
        self.assertIsNone(importlib.util.find_spec("lumisync.cloud"))

    def test_runtime_code_has_no_cloud_references(self):
        root = Path(__file__).resolve().parents[1] / "lumisync"
        offenders = [
            path
            for path in root.rglob("*.py")
            if "cloud" in path.read_text(encoding="utf-8").lower()
        ]

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
