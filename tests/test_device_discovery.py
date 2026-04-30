import unittest
from unittest.mock import Mock, patch

from lumisync import devices
from lumisync.config.options import CONNECTION


class FakeServer:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class DeviceDiscoveryTests(unittest.TestCase):
    def setUp(self):
        CONNECTION.devices = []

    def tearDown(self):
        CONNECTION.devices = []

    def _discover_with(
        self,
        saved_settings,
        discovered_devices,
        preserve_existing=True,
    ):
        server = FakeServer()
        with (
            patch.object(devices, "_load_saved_settings", return_value=saved_settings),
            patch.object(devices, "connect", return_value=(server, discovered_devices)),
            patch.object(devices, "writeJSON") as write_json,
        ):
            result = devices.discover_lan_devices(preserve_existing=preserve_existing)
        return result, server, write_json

    def test_zero_response_scan_preserves_saved_devices(self):
        saved = {
            "devices": [
                {
                    "mac": "aa:bb",
                    "model": "H619C",
                    "ip": "192.168.0.10",
                    "port": 4001,
                }
            ],
            "selectedDevice": 0,
            "time": 1,
        }

        result, server, write_json = self._discover_with(saved, [])

        self.assertTrue(server.closed)
        self.assertEqual(result["lastDiscoveryCount"], 0)
        self.assertEqual(result["devices"], saved["devices"])
        self.assertEqual(result["selectedDevice"], 0)
        write_json.assert_called_once()
        self.assertEqual(write_json.call_args.args[0]["devices"], saved["devices"])

    def test_same_mac_scan_updates_existing_device_without_duplication(self):
        saved = {
            "devices": [
                {
                    "mac": "aa:bb",
                    "model": "H619C",
                    "ip": "192.168.0.10",
                    "port": 4001,
                    "manual": True,
                }
            ],
            "selectedDevice": 0,
            "time": 1,
        }
        discovered = [
            {
                "mac": "aa:bb",
                "model": "H619C",
                "ip": "192.168.0.42",
                "port": 4001,
            }
        ]

        result, _server, _write_json = self._discover_with(saved, discovered)

        self.assertEqual(result["lastDiscoveryCount"], 1)
        self.assertEqual(len(result["devices"]), 1)
        self.assertEqual(result["devices"][0]["ip"], "192.168.0.42")
        self.assertTrue(result["devices"][0]["manual"])

    def test_manual_device_survives_missed_scan(self):
        saved = {
            "devices": [
                {
                    "mac": "manual-1",
                    "model": "Manual Device",
                    "ip": "192.168.0.20",
                    "port": 4003,
                    "manual": True,
                }
            ],
            "selectedDevice": 0,
            "time": 1,
        }

        result, _server, _write_json = self._discover_with(saved, [])

        self.assertEqual(len(result["devices"]), 1)
        self.assertTrue(result["devices"][0]["manual"])
        self.assertEqual(result["devices"][0]["ip"], "192.168.0.20")

    def test_selected_index_is_preserved_or_clamped(self):
        saved = {
            "devices": [{"mac": "aa"}, {"mac": "bb"}],
            "selectedDevice": 1,
            "time": 1,
        }
        result, _server, _write_json = self._discover_with(saved, [])
        self.assertEqual(result["selectedDevice"], 1)

        saved["selectedDevice"] = 99
        result, _server, _write_json = self._discover_with(saved, [])
        self.assertEqual(result["selectedDevice"], 1)

    def test_discover_lan_devices_does_not_call_second_listen(self):
        saved = {"devices": [], "selectedDevice": 0, "time": 1}
        server = FakeServer()
        with (
            patch.object(devices, "_load_saved_settings", return_value=saved),
            patch.object(devices, "connect", return_value=(server, [])),
            patch.object(devices, "listen", Mock(side_effect=AssertionError("second listen"))),
            patch.object(devices, "writeJSON"),
        ):
            result = devices.discover_lan_devices()

        self.assertEqual(result["lastDiscoveryCount"], 0)

    def test_parse_ignores_unsupported_zone_count_from_scan_response(self):
        messages = [
            b'{"msg":{"cmd":"scan","data":{"device":"aa:bb","sku":"H619C","ip":"192.168.0.10","unknownZoneCount":20}}}'
        ]

        parsed = devices.parse(messages)

        self.assertNotIn("unknownZoneCount", parsed[0])
        self.assertEqual(parsed[0]["model"], "H619C")
        self.assertEqual(parsed[0]["port"], 4003)


if __name__ == "__main__":
    unittest.main()
