"""Sync must recover from capture and per-device failures."""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from lumisync.config.sync_settings import SyncSettings
from lumisync.sync import session as sync_session


class FakeAdapter:
    def __init__(self, stop_event=None, frames_before_stop=2):
        self.capabilities = SimpleNamespace(
            segment_count=1, max_update_hz=40.0, supports_streaming=True
        )
        self.device = {}
        self.frames = []
        self.ended = 0
        self.closed = 0
        self.stop_event = stop_event
        self.frames_before_stop = frames_before_stop

    def begin_stream(self):
        pass

    def set_segments(self, colors):
        self.frames.append(colors)
        if self.stop_event is not None and len(self.frames) >= self.frames_before_stop:
            self.stop_event.set()

    def end_stream(self):
        self.ended += 1

    def close(self):
        self.closed += 1


class InlineSender:
    def __init__(self, send, max_hz=40.0, **_kwargs):
        self._send = send

    def submit(self, frame):
        self._send(frame)

    def last_error(self):
        return None

    def close(self):
        pass


class ErrorReportingSender(InlineSender):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._errors = ["boom"]

    def last_error(self):
        return self._errors.pop(0) if self._errors else None


class MonitorRecoveryTests(unittest.TestCase):
    def run_session(self, grab_factory, adapter, *, sender_factory=InlineSender, devices=None):
        stop = threading.Event()
        adapter.stop_event = stop
        errors, statuses = [], []
        with (
            patch.object(sync_session.pool, "acquire", return_value=adapter),
            patch.object(sync_session.monitor, "ScreenGrab", side_effect=grab_factory),
            patch.object(sync_session.SyncSession, "_wait", lambda self, seconds: None),
        ):
            sync_session.SyncSession(
                "monitor",
                devices or [{"mac": "AA:BB", "model": "H6672"}],
                SyncSettings(),
                stop_event=stop,
                on_status=statuses.append,
                on_error=errors.append,
                sender_factory=sender_factory,
            ).run()
        return errors, statuses

    def test_broken_capture_is_released_and_recreated(self):
        broken_closed = []

        class BrokenGrab:
            def capture_array(self):
                raise RuntimeError("'NoneType' object has no attribute 'AcquireNextFrame'")

            def close(self):
                broken_closed.append(True)

        good = FakeAdapter()
        good_grab = type(
            "GoodGrab",
            (),
            {"capture_array": lambda self: np.full((2, 2, 3), (1, 2, 3), dtype=np.uint8)},
        )()
        grabs = [BrokenGrab(), good_grab]

        errors, _statuses = self.run_session(lambda **_: grabs.pop(0), good)

        self.assertTrue(broken_closed)
        self.assertEqual(len(errors), 1)
        self.assertIn("AcquireNextFrame", errors[0])
        self.assertGreaterEqual(len(good.frames), 2)
        self.assertGreaterEqual(good.ended, 1)

    def test_static_screen_reuses_the_last_frame_without_a_warning(self):
        class StillGrab:
            def __init__(self):
                self.calls = 0

            def capture_array(self):
                self.calls += 1
                if self.calls <= 3:
                    return None  # dxcam reports no new frame on a still screen
                return np.full((2, 2, 3), (10, 20, 30), dtype=np.uint8)

        adapter = FakeAdapter()
        _errors, statuses = self.run_session(lambda **_: StillGrab(), adapter)

        self.assertGreaterEqual(len(adapter.frames), 2)
        self.assertFalse(any("waiting for screen frames" in text for text in statuses))

    def test_one_unreachable_device_does_not_stop_the_rest(self):
        stop = threading.Event()
        good = FakeAdapter(stop)
        attempted = []

        def acquire(device, server=None):
            attempted.append(device)
            if device.get("mac") == "broken":
                raise RuntimeError("Tuya did not answer")
            return good

        errors, _statuses = [], []
        with (
            patch.object(sync_session.pool, "acquire", side_effect=acquire),
            patch.object(
                sync_session.monitor,
                "ScreenGrab",
                return_value=type(
                    "Grab",
                    (),
                    {"capture_array": lambda self: np.full((2, 2, 3), (1, 2, 3), dtype=np.uint8)},
                )(),
            ),
            patch.object(sync_session.SyncSession, "_wait", lambda self, seconds: None),
        ):
            sync_session.SyncSession(
                "monitor",
                [
                    {"mac": "broken", "model": "H619C"},
                    {"mac": "good", "model": "H619C"},
                ],
                SyncSettings(),
                stop_event=stop,
                on_error=errors.append,
                sender_factory=InlineSender,
            ).run()

        self.assertEqual([device.get("mac") for device in attempted], ["broken", "good"])
        self.assertGreaterEqual(len(good.frames), 2)
        self.assertTrue(any("Could not start 1 device" in message for message in errors))

    def test_send_failure_is_reported_once_and_does_not_stop_the_stream(self):
        adapter = FakeAdapter()
        errors, _statuses = self.run_session(
            lambda **_: type(
                "Grab",
                (),
                {"capture_array": lambda self: np.full((2, 2, 3), (1, 2, 3), dtype=np.uint8)},
            )(),
            adapter,
            sender_factory=ErrorReportingSender,
        )

        self.assertGreaterEqual(len(adapter.frames), 2)
        self.assertEqual(sum("boom" in message for message in errors), 1)


class ScriptedSender:
    """Plays back one ``(error, sent_count)`` pair per ``last_error()`` call."""

    alive = True

    def __init__(self, script):
        self._script = list(script)
        self.sent_count = 0

    def last_error(self):
        if not self._script:
            return None
        error, self.sent_count = self._script.pop(0)
        return error


class SendErrorReportingTests(unittest.TestCase):
    def drain(self, script, step=0.1):
        errors = []
        session = sync_session.SyncSession(
            "monitor",
            [{"mac": "AA:BB", "model": "H6672", "name": "Strip"}],
            SyncSettings(),
            on_error=errors.append,
        )
        session._keys = [(session.devices[0], "strip")]
        session.senders = {"strip": ScriptedSender(script)}
        for tick in range(len(script)):
            session._drain_sender_errors(now=tick * step)
        return errors

    def test_a_device_that_keeps_failing_is_reported_once(self):
        # last_error() clears on read, so a dead device alternates between an
        # error and an empty read. That must not count as recovering.
        errors = self.drain([("Tuya did not answer", 0), (None, 0)] * 50)

        self.assertEqual(errors, ["Strip: Tuya did not answer"])

    def test_a_flaky_device_is_reported_at_most_every_repeat_interval(self):
        # Every failure follows a successful send, 0.1 s apart, for 10 s.
        errors = self.drain([("timed out", sent) for sent in range(100)])

        self.assertEqual(errors, ["Strip: timed out"])

    def test_a_device_that_recovers_and_fails_later_is_reported_again(self):
        script = [("timed out", 0)]
        script += [(None, sent) for sent in range(1, 400)]
        script += [("timed out", 400)]  # 40 s later, after frames got through

        errors = self.drain(script)

        self.assertEqual(errors, ["Strip: timed out", "Strip: timed out"])

    def test_a_different_error_is_reported_straight_away(self):
        errors = self.drain([("timed out", 0), (None, 0), ("connection refused", 0)])

        self.assertEqual(errors, ["Strip: timed out", "Strip: connection refused"])


if __name__ == "__main__":
    unittest.main()
