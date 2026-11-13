import json
import socket
import time
import unittest
from unittest import mock

from lumisync import connection
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

    def test_parse_status_payload_rejects_non_object_levels(self):
        # Anything can arrive on the shared LAN port; none of these may raise.
        for payload in (b"[1,2]", b'"x"', b"7", b'{"msg":"x"}', b'{"msg":[1]}',
                        b'{"msg":{"data":[1]}}', b'{"msg":{"cmd":"status","data":[1]}}',
                        b'{"msg":{"cmd":"status","data":"on"}}', [1, 2], None):
            with self.subTest(payload=payload):
                self.assertIsNone(parse_status_payload(payload))

    def test_parse_status_payload_ignores_a_non_object_colour(self):
        status = parse_status_payload(
            b'{"msg":{"cmd":"status","data":{"onOff":1,"color":"rgb"}}}'
        )
        self.assertIsNotNone(status)
        self.assertIsNone(status["color"])


class DevStatusOnlyDevice:
    """Fake socket: the 'device' answers devStatus only, never status."""

    def __init__(self):
        self.timeout = None
        self.sent = []
        self.pending = []

    def gettimeout(self):
        return self.timeout

    def settimeout(self, value):
        self.timeout = value

    def sendto(self, data, address):
        command = json.loads(data)["msg"]["cmd"]
        self.sent.append(command)
        if command == "devStatus":
            reply = {"msg": {"cmd": "devStatus", "data": {"onOff": 1, "brightness": 80}}}
            self.pending.append((json.dumps(reply).encode(), (address[0], 4003)))

    def recvfrom(self, size):
        if self.pending:
            return self.pending.pop(0)
        time.sleep(self.timeout)
        raise socket.timeout("timed out")


class QueryStatusTests(unittest.TestCase):
    def test_devstatus_only_firmware_answers_within_the_timeout(self):
        fake = DevStatusOnlyDevice()
        started = time.monotonic()
        status = connection.query_status(fake, {"ip": "192.0.2.5"}, timeout=0.5)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(fake.sent, ["status", "devStatus"])
        self.assertTrue(status["power_on"])
        self.assertEqual(status["brightness"], 80)

    def test_silent_device_times_out_and_restores_the_socket_timeout(self):
        fake = DevStatusOnlyDevice()
        fake.sendto = lambda data, address: fake.sent.append(json.loads(data)["msg"]["cmd"])
        fake.timeout = 7.0
        started = time.monotonic()
        self.assertIsNone(connection.query_status(fake, {"ip": "192.0.2.5"}, timeout=0.2))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(fake.timeout, 7.0)


class ListenDrainTests(unittest.TestCase):
    def test_draining_a_chatty_port_stops_at_the_deadline(self):
        class Chatty:
            timeout = None

            def gettimeout(self):
                return self.timeout

            def settimeout(self, value):
                self.timeout = value

            def recvfrom(self, size):
                time.sleep(0.01)
                return b"{}", ("192.0.2.9", 4003)

        started = time.monotonic()
        with mock.patch.object(connection, "LISTEN_DRAIN_SECONDS", 0.2), \
                mock.patch("builtins.print"):
            messages = connection.listen(Chatty())
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertGreater(len(messages), 1)


if __name__ == "__main__":
    unittest.main()
