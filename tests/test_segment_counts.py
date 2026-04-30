import unittest
from unittest.mock import patch

from lumisync import connection
from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.controllers.sync_controller import SyncController, fit_led_mapping_to_count
from lumisync.gui.widgets.led_mapping_widget import fit_led_colors_to_device


class SegmentCountTests(unittest.TestCase):
    def test_model_without_metadata_uses_default_segment_count(self):
        self.assertEqual(connection.get_segment_count({"model": "H6167"}), 10)

    def test_user_override_handles_four_zone_devices(self):
        self.assertEqual(
            connection.get_segment_count(
                {"model": "H6167", "segment_count_override": 4},
            ),
            4,
        )

    def test_led_mapping_test_payload_is_sized_per_device(self):
        colors = [(index, index, index) for index in range(10)]

        fitted = fit_led_colors_to_device({"segment_count_override": 4}, colors)

        self.assertEqual(len(fitted), 4)
        self.assertEqual(fitted, [(0, 0, 0), (2, 2, 2), (5, 5, 5), (7, 7, 7)])

    def test_led_mapping_spreads_logical_regions_across_segment_count(self):
        mapping = [(0, index) for index in range(10)]

        self.assertEqual(
            fit_led_mapping_to_count(mapping, 4),
            [(0, 0), (0, 2), (0, 5), (0, 7)],
        )

    def test_sync_controller_closes_idle_server(self):
        class FakeServer:
            closed = False

            def close(self):
                self.closed = True

        controller = SyncController()
        server = FakeServer()
        controller.server = server

        controller.stop_sync()

        self.assertTrue(server.closed)
        self.assertIsNone(controller.server)

    def test_device_controller_saves_zone_count_override(self):
        saved = {
            "devices": [
                {
                    "mac": "aa:bb",
                    "model": "H619C",
                    "ip": "192.168.0.10",
                    "port": 4003,
                }
            ],
            "selectedDevice": 0,
            "time": 1,
        }

        with (
            patch("lumisync.gui.controllers.device_controller.devices.get_data", return_value=saved),
            patch("lumisync.gui.controllers.device_controller.devices.writeJSON") as write_json,
        ):
            controller = DeviceController()
            self.assertTrue(controller.set_zone_count_at(0, 20))

        self.assertEqual(controller.devices[0]["segment_count_override"], 20)
        written = write_json.call_args.args[0]
        self.assertEqual(written["devices"][0]["segment_count_override"], 20)

    def test_device_controller_clears_zone_count_override(self):
        saved = {
            "devices": [
                {
                    "mac": "aa:bb",
                    "model": "H619C",
                    "ip": "192.168.0.10",
                    "port": 4003,
                    "segment_count_override": 20,
                }
            ],
            "selectedDevice": 0,
            "time": 1,
        }

        with (
            patch("lumisync.gui.controllers.device_controller.devices.get_data", return_value=saved),
            patch("lumisync.gui.controllers.device_controller.devices.writeJSON") as write_json,
        ):
            controller = DeviceController()
            self.assertTrue(controller.set_zone_count_at(0, None))

        self.assertNotIn("segment_count_override", controller.devices[0])
        written = write_json.call_args.args[0]
        self.assertNotIn("segment_count_override", written["devices"][0])


if __name__ == "__main__":
    unittest.main()
