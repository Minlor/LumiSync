import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from lumisync.sync import monitor, processing


class ZoneAveragingTests(unittest.TestCase):
    def test_zones_isolate_screen_regions(self):
        frame = np.zeros((100, 200, 3), dtype=np.uint8)
        frame[:, :100] = (255, 0, 0)
        frame[:, 100:] = (0, 0, 255)

        left = processing.average_zone_colors(
            frame, [{"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0}]
        )[0]
        right = processing.average_zone_colors(
            frame, [{"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0}]
        )[0]

        self.assertEqual(left, (255, 0, 0))
        self.assertEqual(right, (0, 0, 255))

    def test_gamma_correct_average_is_brighter_than_naive(self):
        frame = np.zeros((10, 10, 3), dtype=np.uint8)
        frame[:, :5] = (255, 0, 0)  # half red, half black
        rect = [{"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}]

        gamma = processing.average_zone_colors(frame, rect, gamma_correct=True)[0]
        naive = processing.average_zone_colors(frame, rect, gamma_correct=False)[0]

        # Averaging in linear light yields ~188 for 50% coverage, not 128.
        self.assertGreater(gamma[0], naive[0])
        self.assertGreater(gamma[0], 180)

    def test_empty_frame_returns_black_per_zone(self):
        colors = processing.average_zone_colors(
            np.empty((0, 0, 3), dtype=np.uint8),
            [{"x": 0, "y": 0, "w": 1, "h": 1}, {"x": 0, "y": 0, "w": 1, "h": 1}],
        )
        self.assertEqual(colors, [(0, 0, 0), (0, 0, 0)])

    def test_thin_zone_still_samples_at_least_one_pixel(self):
        frame = np.full((100, 100, 3), 120, dtype=np.uint8)
        # A zone with near-zero height must not collapse to an empty slice.
        color = processing.average_zone_colors(
            frame, [{"x": 0.0, "y": 0.999, "w": 1.0, "h": 0.0}]
        )[0]
        self.assertEqual(color, (120, 120, 120))


class ColorAdjustmentTests(unittest.TestCase):
    def test_saturation_identity_at_one(self):
        colors = [(200, 150, 100), (10, 20, 30)]
        self.assertEqual(processing.apply_saturation(colors, 1.0), colors)

    def test_saturation_pushes_away_from_grey(self):
        (r, g, b) = processing.apply_saturation([(200, 150, 150)], 1.5)[0]
        # Red was above luma and should rise; the muted channels should drop.
        self.assertGreater(r, 200)
        self.assertLess(g, 150)

    def test_brightness_scales_all_channels(self):
        self.assertEqual(
            processing.apply_brightness([(200, 100, 50)], 0.5), [(100, 50, 25)]
        )

    def test_colors_changed_respects_threshold(self):
        self.assertFalse(
            processing.colors_changed([(10, 10, 10)], [(12, 12, 12)], 3)
        )
        self.assertTrue(
            processing.colors_changed([(10, 10, 10)], [(20, 10, 10)], 3)
        )
        # Different lengths always count as changed.
        self.assertTrue(processing.colors_changed([(0, 0, 0)], [(0, 0, 0), (0, 0, 0)], 3))

    def test_static_frame_is_resent_before_device_stream_timeout(self):
        colors = [(120, 30, 10)]

        self.assertFalse(
            processing.frame_needs_send(
                colors,
                colors,
                3,
                last_sent_at=10.0,
                now=10.2,
                keepalive_interval=0.5,
            )
        )
        self.assertTrue(
            processing.frame_needs_send(
                colors,
                colors,
                3,
                last_sent_at=10.0,
                now=10.5,
                keepalive_interval=0.5,
            )
        )

    def test_new_or_changed_frame_does_not_wait_for_keepalive(self):
        colors = [(120, 30, 10)]

        self.assertTrue(
            processing.frame_needs_send(
                None,
                colors,
                3,
                last_sent_at=None,
                now=10.0,
            )
        )
        self.assertTrue(
            processing.frame_needs_send(
                colors,
                colors,
                3,
                last_sent_at=None,
                now=10.0,
            )
        )
        self.assertTrue(
            processing.frame_needs_send(
                colors,
                [(200, 30, 10)],
                3,
                last_sent_at=9.9,
                now=10.0,
            )
        )


class MonitorLoopTests(unittest.TestCase):
    def test_cli_monitor_resends_static_frame_as_keepalive(self):
        class StopLoop(Exception):
            pass

        class FakeAdapter:
            capabilities = SimpleNamespace(segment_count=1)

            def __init__(self):
                self.frames = []

            def begin_stream(self):
                pass

            def set_segments(self, colors):
                self.frames.append(colors)
                if len(self.frames) == 2:
                    raise StopLoop

        class FakeScreenGrab:
            def capture_array(self):
                return np.full((2, 2, 3), (120, 30, 10), dtype=np.uint8)

        adapter = FakeAdapter()
        clock = iter((0.0, 0.1, 0.2, 0.6, 0.7))
        with (
            patch.object(monitor, "create_adapter", return_value=adapter),
            patch.object(monitor, "ScreenGrab", return_value=FakeScreenGrab()),
            patch.object(monitor.time, "monotonic", side_effect=lambda: next(clock)),
        ):
            with self.assertRaises(StopLoop):
                monitor.start(object(), {"model": "H6672"})

        self.assertEqual(len(adapter.frames), 2)


class ColorSmootherTests(unittest.TestCase):
    def test_first_frame_snaps_to_target(self):
        smoother = processing.ColorSmoother(alpha=0.5)
        self.assertEqual(smoother.update([(100, 100, 100)]), [(100, 100, 100)])

    def test_subsequent_frames_ease_toward_target(self):
        smoother = processing.ColorSmoother(alpha=0.5)
        smoother.update([(100, 100, 100)])
        self.assertEqual(smoother.update([(0, 0, 0)]), [(50, 50, 50)])

    def test_count_change_snaps_without_error(self):
        smoother = processing.ColorSmoother(alpha=0.5, count=3)
        smoother.update([(10, 10, 10)] * 3)
        result = smoother.update([(40, 40, 40)] * 5)
        self.assertEqual(len(result), 5)
        self.assertEqual(result, [(40, 40, 40)] * 5)


if __name__ == "__main__":
    unittest.main()
