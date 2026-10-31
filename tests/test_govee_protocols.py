"""Coverage for the H6080, 0x0B, segment-frame and appliance protocols."""

import base64
import json
import unittest
from unittest.mock import Mock, patch

from lumisync import connection
from lumisync.accounts.errors import AccountError
from lumisync.drivers.cloud import CloudLightAdapter


class FakeSocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, address):
        self.sent.append((data, address))

    def close(self):
        pass


def checksum(data: bytes) -> int:
    value = 0
    for byte in data:
        value ^= byte
    return value


class H6080ProtocolTests(unittest.TestCase):
    def test_family_detection(self):
        self.assertTrue(connection.is_h6080_sku("H6080"))
        self.assertTrue(connection.is_h6080_sku(" h608a "))
        self.assertFalse(connection.is_h6080_sku("H619C"))
        self.assertFalse(connection.is_h6080_sku(None))

    def test_color_payload_carries_light_type_and_timestamp(self):
        with patch.object(connection.time, "time", return_value=1234.5):
            payload = connection.build_h6080_color_json(
                {"ip": "192.0.2.10"}, 1, 2, 3, kelvin=4000
            )

        data = payload["msg"]["data"]
        self.assertEqual(payload["msg"]["cmd"], "colorwc")
        self.assertEqual(data["color"], {"r": 1, "g": 2, "b": 3})
        self.assertEqual(data["colorTemInKelvin"], 4000)
        self.assertEqual(data["lightType"], 1)
        self.assertEqual(data["timeStamp"], 1234500)

    def test_switch_payload(self):
        with patch.object(connection.time, "time", return_value=10.0):
            payload = connection.build_h6080_switch_json({"ip": "192.0.2.10"}, True)

        self.assertEqual(payload["msg"]["cmd"], "turn")
        self.assertEqual(payload["msg"]["data"]["value"], 1)
        self.assertEqual(payload["msg"]["data"]["timeStamp"], 10000)

    def test_send_helpers_target_the_device_port(self):
        server = FakeSocket()
        connection.send_h6080_color(server, {"ip": "192.0.2.10"}, 9, 8, 7)
        connection.send_h6080_switch(server, {"ip": "192.0.2.10"}, False)

        self.assertEqual(len(server.sent), 2)
        self.assertEqual(server.sent[0][1], ("192.0.2.10", 4003))
        self.assertEqual(json.loads(server.sent[1][0])["msg"]["data"]["value"], 0)


class ZeroX0BProtocolTests(unittest.TestCase):
    def setUp(self):
        self.sku = "TEST-0X0B"
        connection.register_0x0b_sku(self.sku)

    def tearDown(self):
        connection._0X0B_SKUS.discard(self.sku)

    def test_registered_sku_uses_the_0x0b_color_command(self):
        self.assertTrue(connection.is_0x0b_sku(self.sku))
        with patch.object(connection.time, "time", return_value=20.0):
            payload = connection.build_0x0b_color_json(
                {"ip": "192.0.2.10"}, 4, 5, 6
            )

        self.assertEqual(payload["msg"]["cmd"], "0x0B")
        self.assertEqual(payload["msg"]["data"]["color"], {"r": 4, "g": 5, "b": 6})
        self.assertEqual(payload["msg"]["data"]["timeStamp"], 20000)

    def test_send_0x0b_color_writes_one_datagram(self):
        server = FakeSocket()
        connection.send_0x0b_color(server, {"ip": "192.0.2.10"}, 1, 2, 3)

        self.assertEqual(len(server.sent), 1)
        self.assertEqual(json.loads(server.sent[0][0])["msg"]["cmd"], "0x0B")


class SegmentFrameTests(unittest.TestCase):
    def test_standard_segment_frame_layout_and_checksum(self):
        frame = connection.build_segment_frame([(1, 2, 3), (4, 5, 6)])

        self.assertEqual(frame[:-1], bytes([0xBB, 0, 1, 0xB1, 2, 1, 2, 3, 4, 5, 6]))
        self.assertEqual(frame[-1], checksum(frame[:-1]))

    def test_multi_pack_coalesces_identical_runs(self):
        frame = connection.build_segment_frame_multi_pack(
            [(1, 2, 3), (1, 2, 3), (4, 5, 6)]
        )

        expected = bytes([0xBB, 0, 1, 0xB2, 2, 0, 2, 1, 2, 3, 0, 1, 4, 5, 6])
        self.assertEqual(frame[:-1], expected)
        self.assertEqual(frame[-1], checksum(frame[:-1]))

    def test_multi_pack_and_gradient_handle_empty_frames(self):
        multi = connection.build_segment_frame_multi_pack([])
        gradient = connection.build_segment_frame_gradient([])

        self.assertEqual(multi[:-1], bytes([0xBB, 0, 1, 0xB2, 0]))
        self.assertEqual(multi[-1], checksum(multi[:-1]))
        self.assertEqual(gradient[:-1], bytes([0xBB, 0, 1, 0xB3, 0, 0, 0, 0, 0, 0, 0]))
        self.assertEqual(gradient[-1], checksum(gradient[:-1]))

    def test_gradient_frame_uses_first_and_last_color(self):
        frame = connection.build_segment_frame_gradient(
            [(1, 2, 3), (7, 8, 9), (4, 5, 6)]
        )

        expected = bytes([0xBB, 0, 1, 0xB3, 3, 1, 2, 3, 4, 5, 6])
        self.assertEqual(frame[:-1], expected)
        self.assertEqual(frame[-1], checksum(frame[:-1]))

    def test_send_helpers_wrap_frames_in_a_razer_payload(self):
        server = FakeSocket()
        connection.send_segment_frame_multi_pack(
            server, {"ip": "192.0.2.10"}, [(1, 2, 3)]
        )
        connection.send_segment_frame_gradient(
            server, {"ip": "192.0.2.10"}, [(1, 2, 3)]
        )

        self.assertEqual(len(server.sent), 2)
        for data, _address in server.sent:
            payload = json.loads(data)
            blob = base64.b64decode(payload["msg"]["data"]["pt"])
            self.assertEqual(blob[0:3], bytes([0xBB, 0, 1]))
            self.assertIn(blob[3], (0xB2, 0xB3))


class ApplianceControlTests(unittest.TestCase):
    def adapter(self):
        return CloudLightAdapter(
            {
                "transport": "govee_cloud",
                "device_id": "appliance",
                "sku": "H7121",
                "cloud_capabilities": [
                    {
                        "instance": "fanLevel",
                        "parameters": {"range": {"min": 1, "max": 3}},
                    },
                    {
                        "instance": "humiditySet",
                        "parameters": {"range": {"min": 30, "max": 80}},
                    },
                    {"instance": "purifierMode"},
                ],
            }
        )

    def test_fan_speed_is_clamped_to_the_device_range(self):
        adapter = self.adapter()
        client = Mock()
        with patch(
            "lumisync.drivers.cloud.account_manager.client_for", return_value=client
        ):
            adapter.set_fan_speed(100)
        client.control.assert_called_once_with(adapter.device, "fanLevel", 3)

    def test_humidity_target_is_bounded(self):
        adapter = self.adapter()
        client = Mock()
        with patch(
            "lumisync.drivers.cloud.account_manager.client_for", return_value=client
        ):
            adapter.set_humidity(10)
        client.control.assert_called_once_with(adapter.device, "humiditySet", 30)

    def test_generic_capability_escape_hatch(self):
        adapter = self.adapter()
        client = Mock()
        with patch(
            "lumisync.drivers.cloud.account_manager.client_for", return_value=client
        ):
            adapter.set_appliance_capability("purifierMode", "auto")
        client.control.assert_called_once_with(adapter.device, "purifierMode", "auto")

    def test_unknown_capability_is_rejected_before_sending(self):
        adapter = self.adapter()
        with self.assertRaises(AccountError):
            adapter.set_appliance_capability("unknownMode", 1)


if __name__ == "__main__":
    unittest.main()
