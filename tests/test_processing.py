import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from lumisync import led_mapping
from lumisync.config.sync_settings import SyncSettings
from lumisync.sync import processing
from lumisync.sync import session as sync_session


def reference_average_zone_colors(frame, mapping, *, gamma_correct=True, channel_order="RGB"):
    """The pre-1.0 per-zone implementation, kept to pin the batched one."""
    if frame is None or frame.size == 0:
        return [(0, 0, 0) for _ in mapping]
    order = str(channel_order or "RGB").upper()
    height, width = frame.shape[:2]
    colors = []
    for rect in mapping:
        x1, y1, x2, y2 = processing._zone_bounds(rect, width, height)
        sub = frame[y1:y2, x1:x2, :3]
        step_y = max(1, sub.shape[0] // processing._MAX_SAMPLES_PER_AXIS)
        step_x = max(1, sub.shape[1] // processing._MAX_SAMPLES_PER_AXIS)
        sub = sub[::step_y, ::step_x]
        if order == "BGRA":
            sub = sub[..., [2, 1, 0]]
        sub = sub.reshape(-1, 3)
        if gamma_correct:
            linear = processing._SRGB_TO_LINEAR[sub]
            srgb = processing._linear_to_srgb(linear.mean(axis=0)) * 255.0
        else:
            srgb = sub.mean(axis=0)
        rgb = np.clip(np.rint(srgb), 0, 255).astype(int)
        colors.append((int(rgb[0]), int(rgb[1]), int(rgb[2])))
    return colors


class ZoneAveragingTests(unittest.TestCase):
    def test_batched_averaging_matches_the_per_zone_reference(self):
        rng = np.random.default_rng(1234)
        mappings = [
            led_mapping.generate_screen_mapping(count, 16 / 9)
            for count in (1, 4, 10, 37, 255)
        ]
        for shape, order in (((216, 384, 4), "BGRA"), ((90, 160, 3), "RGB"), ((50, 70, 4), "RGB")):
            frame = rng.integers(0, 256, size=shape, dtype=np.uint8)
            for mapping in mappings:
                for gamma in (True, False):
                    with self.subTest(shape=shape, order=order, zones=len(mapping), gamma=gamma):
                        self.assertEqual(
                            processing.average_zone_colors(
                                frame, mapping, gamma_correct=gamma, channel_order=order
                            ),
                            reference_average_zone_colors(
                                frame, mapping, gamma_correct=gamma, channel_order=order
                            ),
                        )

    def test_empty_mapping_returns_no_colors(self):
        frame = np.zeros((4, 4, 3), dtype=np.uint8)
        self.assertEqual(processing.average_zone_colors(frame, []), [])

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

    def test_native_bgra_frames_are_sampled_without_a_full_rgb_copy(self):
        # Windows dxcam / X11 mss deliver BGRA. The zone sampler swaps only the
        # sampled pixels, so the caller can keep the large native buffer.
        frame = np.array([[[20, 10, 5, 255]]], dtype=np.uint8)

        color = processing.average_zone_colors(
            frame,
            [{"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}],
            channel_order="BGRA",
        )[0]

        self.assertEqual(color, (5, 10, 20))


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

    def test_brightness_boost_is_identity_at_one(self):
        colors = [(200, 150, 100), (0, 0, 0), (255, 255, 255)]
        self.assertEqual(processing.apply_brightness_boost(colors, 1.0), colors)

    def test_brightness_boost_lifts_midtones_and_preserves_hue(self):
        boosted = processing.apply_brightness_boost([(100, 50, 25)], 1.5)[0]

        # The brightest channel sets the gain through the soft knee
        # (t*k / (1 + (k-1)*t)); every channel scales together, so the
        # red:green:blue ratio stays (2:1:0.5).
        self.assertEqual(boosted, (125, 63, 31))
        self.assertAlmostEqual(boosted[1] / boosted[0], 0.5, places=1)
        self.assertAlmostEqual(boosted[2] / boosted[0], 0.25, places=1)

    def test_brightness_boost_does_not_exceed_channel_ceiling(self):
        boosted = processing.apply_brightness_boost([(200, 180, 160)], 1.5)[0]

        self.assertGreater(boosted[0], 200)
        self.assertTrue(all(0 <= channel <= 255 for channel in boosted))
        self.assertEqual(processing.apply_brightness_boost([(255, 10, 0)], 1.5), [(255, 10, 0)])

    def test_brightness_boost_keeps_gradation_near_white(self):
        peaks = [150, 170, 190, 210, 230, 250, 255]
        boosted = [
            processing.apply_brightness_boost([(peak, peak, peak)], 1.5)[0][0]
            for peak in peaks
        ]

        # The old hard knee sent every peak >= 170 to 255. The soft knee lifts
        # them but keeps them distinct and in order.
        self.assertEqual(boosted, sorted(boosted))
        self.assertEqual(len(set(boosted)), len(peaks))
        self.assertEqual(boosted[-1], 255)

    def test_boost_curve_is_monotonic_and_maps_full_scale_to_full_scale(self):
        for factor in (1.0, 1.2, 1.5):
            values = [processing.boost_curve(peak, factor) for peak in range(256)]
            self.assertEqual(values, sorted(values))
            self.assertAlmostEqual(values[255], 255.0)
            self.assertAlmostEqual(values[0], 0.0)

    def test_boost_runs_before_brightness_so_the_cap_holds(self):
        settings = SyncSettings(
            monitor_brightness=0.75,
            monitor_boost=1.5,
            saturation=1.0,
            smoothing=1.0,
            gamma_correct=False,
        )
        frames = run_monitor_session(
            settings, np.full((4, 4, 3), 255, dtype=np.uint8), frames=1
        )

        # White at 75% brightness stays at the 75% ceiling despite the boost.
        self.assertEqual(frames[0], [(191, 191, 191)])

    def test_monitor_boost_is_clamped_to_supported_range(self):
        self.assertEqual(
            SyncSettings.from_mapping({"monitor_boost": 9.0}).monitor_boost,
            1.5,
        )
        self.assertEqual(
            SyncSettings.from_mapping({"monitor_boost": 0.2}).monitor_boost,
            1.0,
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


def run_monitor_session(settings, image, *, frames=1, capabilities=None):
    """Run a monitor session on a still image and return the frames sent."""
    stop = threading.Event()
    sent = []

    class Adapter:
        def __init__(self):
            self.capabilities = capabilities or SimpleNamespace(segment_count=1)

        def begin_stream(self):
            pass

        def set_segments(self, colors):
            sent.append(list(colors))
            if len(sent) >= frames:
                stop.set()

    class Grab:
        def capture_array(self):
            return image

    class Inline:
        def __init__(self, send, max_hz=40.0, **_kwargs):
            self._send = send

        def submit(self, frame):
            self._send(frame)

        def close(self):
            pass

    with (
        patch.object(sync_session.pool, "acquire", return_value=Adapter()),
        patch.object(sync_session.monitor, "ScreenGrab", return_value=Grab()),
        patch.object(sync_session.SyncSession, "_wait", lambda self, seconds: None),
    ):
        sync_session.SyncSession(
            "monitor",
            [{"model": "H6672"}],
            settings,
            stop_event=stop,
            sender_factory=Inline,
        ).run()
    return sent


class MonitorLoopTests(unittest.TestCase):
    def test_cli_monitor_resends_static_frame_as_keepalive(self):
        stop = threading.Event()

        class FakeAdapter:
            capabilities = SimpleNamespace(segment_count=1)

            def __init__(self):
                self.frames = []
                self.ended = False
                self.closed = False

            def begin_stream(self):
                pass

            def set_segments(self, colors):
                self.frames.append(colors)
                if len(self.frames) == 2:
                    stop.set()

            def end_stream(self):
                self.ended = True

            def close(self):
                self.closed = True

        class FakeScreenGrab:
            def capture_array(self):
                return np.full((2, 2, 3), (120, 30, 10), dtype=np.uint8)

        class InlineSender:
            def __init__(self, send, max_hz=40.0, **_kwargs):
                self._send = send

            def submit(self, frame):
                self._send(frame)

            def close(self):
                pass

        class FakeClock:
            def __init__(self):
                self.value = 0.0

            def __call__(self):
                self.value += 0.6
                return self.value

        adapter = FakeAdapter()
        with (
            patch.object(sync_session.pool, "acquire", return_value=adapter),
            patch.object(sync_session.monitor, "ScreenGrab", return_value=FakeScreenGrab()),
            patch.object(sync_session.time, "monotonic", FakeClock()),
        ):
            sync_session.SyncSession(
                "monitor",
                [{"model": "H6672"}],
                SyncSettings(),
                stop_event=stop,
                sender_factory=InlineSender,
            ).run()

        self.assertEqual(len(adapter.frames), 2)
        self.assertTrue(adapter.ended)
        self.assertTrue(adapter.closed)


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

    def test_smoothing_is_time_based_for_slower_transports(self):
        baseline = processing.ColorSmoother(alpha=0.55, count=1)
        baseline.update([(0, 0, 0)])
        normal = baseline.update(
            [(255, 255, 255)], dt=processing.ColorSmoother.REFERENCE_INTERVAL
        )
        slow = processing.ColorSmoother(alpha=0.55, count=1)
        slow.update([(0, 0, 0)])
        fast = slow.update(
            [(255, 255, 255)], dt=processing.ColorSmoother.REFERENCE_INTERVAL * 10
        )
        # A transport that updates 10x less often steps much further per frame,
        # so the fade completes in the same wall-clock time.
        self.assertGreater(fast[0][0], normal[0][0])

    def test_smoothing_without_dt_keeps_original_behaviour(self):
        smoother = processing.ColorSmoother(alpha=0.5)
        smoother.update([(100, 100, 100)])
        self.assertEqual(smoother.update([(0, 0, 0)]), [(50, 50, 50)])


if __name__ == "__main__":
    unittest.main()
