import unittest
from unittest.mock import patch

from lumisync import connection, devices
from lumisync.config.options import CONNECTION


class SubnetHostsTests(unittest.TestCase):
    def test_generates_full_range_excluding_self(self):
        hosts = connection.subnet_hosts("192.168.1.50")
        self.assertEqual(len(hosts), 253)          # 1..254 minus self
        self.assertIn("192.168.1.1", hosts)
        self.assertIn("192.168.1.254", hosts)
        self.assertNotIn("192.168.1.50", hosts)    # never probe ourselves
        self.assertNotIn("192.168.1.0", hosts)
        self.assertNotIn("192.168.1.255", hosts)

    def test_invalid_input_yields_no_targets(self):
        self.assertEqual(connection.subnet_hosts(None), [])
        self.assertEqual(connection.subnet_hosts("not-an-ip"), [])


class DeepDiscoveryTests(unittest.TestCase):
    def setUp(self):
        CONNECTION.devices = []

    def tearDown(self):
        CONNECTION.devices = []

    def test_deep_scan_merges_sweep_results(self):
        class FakeServer:
            def close(self):
                pass

        saved = {"devices": [], "selectedDevice": 0, "time": 1}
        multicast_device = {"mac": "aa:bb", "model": "H619C", "ip": "192.168.0.10", "port": 4003}
        swept_device = {"mac": "cc:dd", "model": "H6199", "ip": "192.168.0.20", "port": 4003}

        with (
            patch.object(devices, "_load_saved_settings", return_value=saved),
            patch.object(devices, "connect", return_value=(FakeServer(), [multicast_device])),
            patch.object(connection, "sweep_scan", return_value=(FakeServer(), [swept_device])),
            patch.object(devices, "writeJSON"),
        ):
            result = devices.discover_lan_devices(preserve_existing=True, deep=True)

        macs = {d["mac"] for d in result["devices"]}
        self.assertEqual(macs, {"aa:bb", "cc:dd"})

    def test_normal_scan_does_not_sweep(self):
        class FakeServer:
            def close(self):
                pass

        saved = {"devices": [], "selectedDevice": 0, "time": 1}
        with (
            patch.object(devices, "_load_saved_settings", return_value=saved),
            patch.object(devices, "connect", return_value=(FakeServer(), [])),
            patch.object(connection, "sweep_scan", side_effect=AssertionError("swept")),
            patch.object(devices, "writeJSON"),
        ):
            result = devices.discover_lan_devices(preserve_existing=True)  # deep defaults False

        self.assertEqual(result["lastDiscoveryCount"], 0)


if __name__ == "__main__":
    unittest.main()
