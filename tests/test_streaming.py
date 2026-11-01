"""Latest-frame sender: slow transports must not throttle fast ones."""

import time
import unittest

from lumisync.sync.streaming import LatestFrameSender


class LatestFrameSenderTests(unittest.TestCase):
    def test_coalesces_to_the_newest_frame_and_stops(self):
        sent = []

        def slow_send(frame):
            sent.append(frame)
            time.sleep(0.04)

        sender = LatestFrameSender(slow_send, max_hz=100.0)
        try:
            sender.submit([(0, 0, 0)])
            time.sleep(0.01)  # let the first frame start sending
            for value in range(1, 10):
                sender.submit([(value, value, value)])
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and sent[-1:] != [[(9, 9, 9)]]:
                time.sleep(0.01)
        finally:
            sender.close()
        self.assertGreaterEqual(len(sent), 2)
        self.assertLess(len(sent), 10)  # intermediate frames were dropped
        self.assertEqual(sent[0], [(0, 0, 0)])
        self.assertEqual(sent[-1], [(9, 9, 9)])
        self.assertFalse(sender.alive)

    def test_close_while_idle_exits_and_ignores_late_submissions(self):
        sent = []
        sender = LatestFrameSender(sent.append, max_hz=30.0)
        sender.close()
        self.assertFalse(sender.alive)
        sender.submit([(1, 2, 3)])
        self.assertEqual(sent, [])

    def test_send_failures_are_reported_without_stopping_the_stream(self):
        frames = []

        def flaky(frame):
            frames.append(frame)
            if len(frames) == 1:
                raise RuntimeError("boom")

        sender = LatestFrameSender(flaky, max_hz=200.0)
        try:
            sender.submit([(1, 1, 1)])
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and len(frames) < 1:
                time.sleep(0.01)
            sender.submit([(2, 2, 2)])
            while time.monotonic() < deadline and len(frames) < 2:
                time.sleep(0.01)
        finally:
            error = sender.last_error()
            sender.close()
        self.assertEqual(error, "boom")
        self.assertEqual(frames[-1], [(2, 2, 2)])
        # Only the second frame went through; the session uses this count to
        # tell a recovered device from one that is still failing.
        self.assertEqual(sender.sent_count, 1)


    def test_fast_sender_is_not_slowed_by_a_slow_co_device(self):
        fast_times = []

        def fast_send(frame):
            fast_times.append(time.monotonic())

        def slow_send(frame):
            time.sleep(0.09)

        fast_sender = LatestFrameSender(fast_send, max_hz=40.0)
        slow_sender = LatestFrameSender(slow_send, max_hz=8.0)
        try:
            start = time.monotonic()
            for _ in range(40):  # one second of submitted frames
                frame = [(1, 2, 3)]
                fast_sender.submit(frame)
                slow_sender.submit(frame)
                time.sleep(1.0 / 40.0)
            elapsed = time.monotonic() - start
        finally:
            fast_sender.close()
            slow_sender.close()

        # The Govee-style sender keeps roughly its 40 fps cadence and is never
        # blocked by the Tuya-style 90 ms writes of the other device.
        self.assertGreaterEqual(len(fast_times), 30)
        gaps = [later - earlier for earlier, later in zip(fast_times, fast_times[1:])]
        self.assertTrue(all(gap < 0.06 for gap in gaps), gaps)
        self.assertLessEqual(len(fast_times) / elapsed, 45.0)

    def test_forty_hertz_pacing_matches_the_govee_transport_rate(self):
        sent = []
        sender = LatestFrameSender(sent.append, max_hz=40.0)
        try:
            start = time.monotonic()
            while time.monotonic() - start < 0.55:
                sender.submit([(1, 1, 1)])
                time.sleep(0.005)
        finally:
            sender.close()
        self.assertGreaterEqual(len(sent), 15)  # ~22 expected at 40 Hz
        self.assertLessEqual(len(sent), 28)


if __name__ == "__main__":
    unittest.main()
