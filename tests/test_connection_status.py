import json
import unittest

from lumisync.connection import parse_status_payload


class ConnectionStatusTests(unittest.TestCase):
    def test_parse_status_payload_accepts_desktop_status_shape(self):
        payload = {
            "msg": {
                "cmd": "status",
                "data": {"onOff": 0, "brightness": 100, "pt": "uwABsgAI"},
            }
        }

        status = parse_status_payload(json.dumps(payload).encode("utf-8"))

        self.assertIsNotNone(status)
        self.assertFalse(status["power_on"])
        self.assertEqual(status["brightness"], 100)
        self.assertIsNone(status["color"])
        self.assertEqual(status["raw"]["pt"], "uwABsgAI")

    def test_parse_status_payload_accepts_devstatus_shape(self):
        payload = {
            "msg": {
                "cmd": "devStatus",
                "data": {
                    "onOff": 1,
                    "brightness": 42,
                    "color": {"r": 12, "g": 34, "b": 56},
                    "colorTemInKelvin": 0,
                },
            }
        }

        status = parse_status_payload(payload)

        self.assertIsNotNone(status)
        self.assertTrue(status["power_on"])
        self.assertEqual(status["brightness"], 42)
        self.assertEqual(status["color"], (12, 34, 56))
        self.assertEqual(status["color_temp"], 0)

    def test_parse_status_payload_ignores_unrelated_commands(self):
        self.assertIsNone(parse_status_payload({"msg": {"cmd": "scan", "data": {}}}))

    def test_parse_status_payload_handles_invalid_json(self):
        self.assertIsNone(parse_status_payload(b"{not json"))


if __name__ == "__main__":
    unittest.main()
