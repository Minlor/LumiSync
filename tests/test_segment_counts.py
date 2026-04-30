import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from lumisync import connection, led_mapping
from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.controllers.sync_controller import (
    SyncController,
    fit_led_mapping_to_count,
    get_led_mapping_from_settings,
    sample_region_color,
)
from lumisync.gui.widgets.led_mapping_widget import (
    LedMappingWidget,
    fit_led_colors_to_device,
    rect_test_color,
)


class SegmentCountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

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

    def test_led_mapping_spreads_normalized_regions_across_segment_count(self):
        mapping = [
            {"x": index / 10, "y": 0.0, "w": 0.1, "h": 0.2}
            for index in range(10)
        ]

        self.assertEqual(
            fit_led_mapping_to_count(mapping, 4),
            [mapping[0], mapping[2], mapping[5], mapping[7]],
        )

    def test_screen_mapping_generates_exact_zone_counts(self):
        for count in (4, 10, 12, 20):
            with self.subTest(count=count):
                mapping = led_mapping.generate_screen_mapping(count, 16 / 9)
                self.assertEqual(len(mapping), count)
                for rect in mapping:
                    touches_edge = (
                        rect["x"] <= 0.001
                        or rect["y"] <= 0.001
                        or rect["x"] + rect["w"] >= 0.999
                        or rect["y"] + rect["h"] >= 0.999
                    )
                    self.assertTrue(touches_edge)

    def test_four_zone_mapping_prioritizes_edges_without_middle_zone(self):
        mapping = led_mapping.generate_screen_mapping(4, 16 / 9, 0.25)

        self.assertEqual(
            mapping,
            [
                {"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.25},
                {"x": 0.0, "y": 0.25, "w": 0.25, "h": 0.5},
                {"x": 0.0, "y": 0.75, "w": 1.0, "h": 0.25},
                {"x": 0.75, "y": 0.25, "w": 0.25, "h": 0.5},
            ],
        )

    def test_ten_zone_mapping_goes_around_screen_clockwise(self):
        mapping = led_mapping.generate_screen_mapping(10, 16 / 9, 0.25)

        positions = [
            (round(rect["x"], 3), round(rect["y"], 3))
            for rect in mapping
        ]
        self.assertEqual(
            positions,
            [
                (0.667, 0.0),
                (0.333, 0.0),
                (0.0, 0.0),
                (0.0, 0.25),
                (0.0, 0.5),
                (0.0, 0.75),
                (0.333, 0.75),
                (0.667, 0.75),
                (0.75, 0.5),
                (0.75, 0.25),
            ],
        )

    def test_capture_depth_expands_edge_zones_inward(self):
        shallow = led_mapping.generate_screen_mapping(4, 16 / 9, 0.10)
        deep = led_mapping.generate_screen_mapping(4, 16 / 9, 0.45)

        self.assertLess(shallow[0]["h"], deep[0]["h"])
        self.assertLess(shallow[1]["w"], deep[1]["w"])

    def test_led_mapping_widget_preview_uses_configured_zone_count(self):
        class FakeSettings:
            def value(self, _key, default=None):
                return default

            def setValue(self, _key, _value):
                pass

        widget = LedMappingWidget(FakeSettings())

        widget.set_segment_count(4)
        self.assertEqual(len(widget.get_mapping()), 4)

        widget.set_segment_count(12)
        self.assertEqual(len(widget.get_mapping()), 12)

    def test_reverse_order_persists_and_changes_mapping_order(self):
        saved = {}

        class FakeSettings:
            def value(self, _key, default=None):
                return default

            def setValue(self, key, value):
                saved[key] = value

        widget = LedMappingWidget(FakeSettings())
        original = widget.get_mapping()

        widget._reverse_order()

        self.assertEqual(widget.get_mapping(), list(reversed(original)))
        self.assertIn(led_mapping.NORMALIZED_MAPPING_KEY, saved)

    def test_test_colors_follow_mapped_screen_rectangles(self):
        class FakeSettings:
            def value(self, _key, default=None):
                return default

            def setValue(self, _key, _value):
                pass

        widget = LedMappingWidget(FakeSettings())
        widget.set_segment_count(4)
        sent_colors = []

        def capture_send(colors, skip_razer_enable=False):
            sent_colors.append(list(colors))

        widget._send_colors_to_strip = capture_send
        widget._test_mode_active = True

        widget._update_test_display()
        before = sent_colors[-1]
        widget._reverse_order()
        after = sent_colors[-1]

        self.assertNotEqual(before, after)
        self.assertEqual(after, list(reversed(before)))

    def test_rect_test_color_is_tied_to_screen_position(self):
        left = rect_test_color({"x": 0.0, "y": 0.25, "w": 0.25, "h": 0.5})
        right = rect_test_color({"x": 0.75, "y": 0.25, "w": 0.25, "h": 0.5})

        self.assertNotEqual(left, right)

    def test_capture_depth_control_persists_and_regenerates_mapping(self):
        saved = {}

        class FakeSettings:
            def value(self, _key, default=None):
                return default

            def setValue(self, key, value):
                saved[key] = value

        widget = LedMappingWidget(FakeSettings())

        widget._on_capture_depth_changed(45)

        self.assertEqual(saved[led_mapping.CAPTURE_DEPTH_KEY], 0.45)
        self.assertEqual(widget.get_mapping()[0]["h"], 0.45)

    def test_saved_monitor_mapping_can_use_custom_zone_count(self):
        mapping = [
            {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5},
            {"x": 0.5, "y": 0.0, "w": 0.5, "h": 0.5},
            {"x": 0.0, "y": 0.5, "w": 0.5, "h": 0.5},
            {"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5},
        ]

        class FakeSettings:
            def __init__(self, *_args, **_kwargs):
                pass

            def value(self, key, default=None):
                if key == led_mapping.NORMALIZED_MAPPING_KEY:
                    return json.dumps(mapping)
                return default

        with patch("lumisync.gui.controllers.sync_controller.QSettings", FakeSettings):
            self.assertEqual(get_led_mapping_from_settings(4), mapping)

    def test_legacy_mapping_loads_as_full_screen_rectangles(self):
        class FakeSettings:
            def value(self, key, default=None):
                if key == led_mapping.LEGACY_MAPPING_KEY:
                    return json.dumps([[0, 0], [0, 1], [1, 0], [2, 3]])
                return default

        loaded = led_mapping.load_mapping_from_settings(FakeSettings(), 4)

        self.assertEqual(len(loaded), 4)
        self.assertEqual(set(loaded[0]), {"x", "y", "w", "h"})

    def test_monitor_sampling_uses_normalized_rectangles(self):
        class FakeScreen:
            def getpixel(self, point):
                return point[0], point[1], 0

        color = sample_region_color(
            FakeScreen(),
            100,
            50,
            {"x": 0.2, "y": 0.4, "w": 0.2, "h": 0.2},
        )

        self.assertEqual(color, (30, 25, 0))

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
