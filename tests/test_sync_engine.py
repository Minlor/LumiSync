"""Monitor sync engine: capture recovery, stream keepalive, caching and pacing."""

import sys
import threading
import time
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from lumisync.config.sync_settings import SyncSettings
from lumisync.drivers.base import DeviceCapabilities, TransportAdapter
from lumisync.sync import monitor
from lumisync.sync import session as sync_session


class InlineSender:
    def __init__(self, send, max_hz=40.0, **_kwargs):
        self._send = send
        self.max_hz = max_hz

    def submit(self, frame):
        self._send(frame)

    def last_error(self):
        return None

    def close(self):
        pass


class RecordingAdapter:
    def __init__(self, capabilities=None, *, stop_after=None, stop_event=None):
        self.capabilities = capabilities or SimpleNamespace(
            segment_count=3, max_update_hz=40.0
        )
        self.device = {}
        self.frames = []
        self.keepalives = 0
        self.stop_after = stop_after
        self.stop_event = stop_event

    def begin_stream(self):
        pass

    def keep_stream_alive(self):
        self.keepalives += 1

    def set_segments(self, colors):
        self.frames.append(list(colors))
        if self.stop_event is not None and self.stop_after and len(self.frames) >= self.stop_after:
            self.stop_event.set()

    def end_stream(self):
        pass

    def close(self):
        pass


class StillGrab:
    def __init__(self, frame=None):
        self.frame = frame if frame is not None else np.full((4, 8, 3), (90, 40, 10), dtype=np.uint8)
        self.closed = 0
        self.calls = 0

    def capture_array(self):
        self.calls += 1
        return self.frame

    def close(self):
        self.closed += 1


def run_monitor(adapters, grab_factory, *, settings=None, stop=None, mapping_provider=None,
                statuses=None, errors=None, extra_patches=()):
    stop = stop or threading.Event()
    statuses = statuses if statuses is not None else []
    errors = errors if errors is not None else []
    queue = list(adapters)
    with (
        patch.object(sync_session.pool, "acquire", side_effect=lambda *_a, **_k: queue.pop(0)),
        patch.object(sync_session.monitor, "ScreenGrab", side_effect=grab_factory),
        patch.object(sync_session.SyncSession, "_wait", lambda self, seconds: None),
    ):
        for extra in extra_patches:
            extra.start()
        try:
            session = sync_session.SyncSession(
                "monitor",
                [{"mac": f"AA:{index}", "model": "H6672"} for index in range(len(adapters))],
                settings or SyncSettings(delta_threshold=0),
                stop_event=stop,
                mapping_provider=mapping_provider,
                on_status=statuses.append,
                on_error=errors.append,
                sender_factory=InlineSender,
            )
            session.run()
        finally:
            for extra in extra_patches:
                extra.stop()
    return session, statuses, errors


class FakeRecoveryHandler:
    """Mirrors dxcam's DisplayRecoveryHandler.handle() retry loop."""

    def __init__(self, wait):
        self._wait = wait
        self.attempts = 0

    def handle(self):
        while True:
            self.attempts += 1
            try:
                raise OSError("DXGI_ERROR_DEVICE_REMOVED")
            except OSError:
                delay = self._wait.next_delay_seconds()
                time.sleep(delay)


class ProgressiveWait:
    def reset(self):
        pass

    def next_delay_seconds(self):
        return 0.001


class CaptureRecoveryTests(unittest.TestCase):
    def test_bounded_wait_ends_dxcams_recovery_loop(self):
        camera = SimpleNamespace(_display_recovery=FakeRecoveryHandler(ProgressiveWait()))
        self.assertTrue(monitor.install_bounded_recovery(camera))
        wait = camera._display_recovery._wait
        wait._timeout = 0.2

        started = time.monotonic()
        with self.assertRaises(monitor.CaptureRecoveryTimeout):
            camera._display_recovery.handle()

        self.assertLess(time.monotonic() - started, 2.0)
        self.assertGreater(camera._display_recovery.attempts, 1)

    def test_bounded_wait_stops_at_once_when_the_session_stops(self):
        stop = threading.Event()
        stop.set()
        camera = SimpleNamespace(_display_recovery=FakeRecoveryHandler(ProgressiveWait()))
        monitor.install_bounded_recovery(camera, stop)

        with self.assertRaises(monitor.CaptureRecoveryTimeout):
            camera._display_recovery.handle()
        self.assertEqual(camera._display_recovery.attempts, 1)

    def test_bounded_wait_restarts_after_a_successful_recovery(self):
        now = [0.0]
        wait = monitor.BoundedRecoveryWait(timeout=3.0, clock=lambda: now[0])
        self.assertLessEqual(wait.next_delay_seconds(), 0.25)
        now[0] = 2.9
        wait.next_delay_seconds()
        wait.reset()
        now[0] = 10.0
        self.assertGreater(wait.next_delay_seconds(), 0)
        now[0] = 13.5
        with self.assertRaises(monitor.CaptureRecoveryTimeout):
            wait.next_delay_seconds()

    def test_other_dxcam_layouts_are_left_alone(self):
        self.assertFalse(monitor.install_bounded_recovery(object()))
        self.assertFalse(monitor.install_bounded_recovery(SimpleNamespace(_display_recovery=object())))
        handler = SimpleNamespace(_wait=object())
        self.assertFalse(monitor.install_bounded_recovery(SimpleNamespace(_display_recovery=handler)))

    def fake_dxcam(self, outputs):
        module = types.ModuleType("dxcam")

        class Singleton(type):
            _instances = {}

            def __call__(cls, *args, **kwargs):
                if cls not in cls._instances:
                    cls._instances[cls] = super().__call__(*args, **kwargs)
                return cls._instances[cls]

        class DXFactory(metaclass=Singleton):
            builds = 0

            def __init__(self):
                DXFactory.builds += 1
                self.outputs = outputs

        module.Singleton = Singleton
        module.DXFactory = DXFactory
        setattr(module, "__factory", DXFactory())
        return module

    def test_rebuild_replaces_dxcams_factory_so_displays_re_enumerate(self):
        module = self.fake_dxcam([])
        old = vars(module)["__factory"]
        with (
            patch.dict(sys.modules, {"dxcam": module}),
            patch.object(monitor.GENERAL, "platform", "Windows"),
        ):
            self.assertTrue(monitor.rebuild_capture_backend())

        self.assertIsNot(vars(module)["__factory"], old)
        self.assertEqual(module.DXFactory.builds, 2)

    def test_rebuild_degrades_without_dxcams_private_factory(self):
        module = types.ModuleType("dxcam")
        with (
            patch.dict(sys.modules, {"dxcam": module}),
            patch.object(monitor.GENERAL, "platform", "Windows"),
        ):
            self.assertFalse(monitor.rebuild_capture_backend())
        with patch.object(monitor.GENERAL, "platform", "Linux"):
            self.assertFalse(monitor.rebuild_capture_backend())

    def test_display_name_is_found_on_any_graphics_adapter(self):
        out = lambda name: SimpleNamespace(devicename=name)  # noqa: E731
        module = self.fake_dxcam([[out("\\\\.\\DISPLAY1")], [out("\\\\.\\DISPLAY3"), out("\\\\.\\DISPLAY2")]])

        self.assertEqual(monitor.find_dxcam_output(module, "\\\\.\\display2"), (1, 1))
        self.assertIsNone(monitor.find_dxcam_output(module, "\\\\.\\DISPLAY9"))
        self.assertIsNone(monitor.find_dxcam_output(module, ""))
        self.assertIsNone(monitor.find_dxcam_output(types.ModuleType("dxcam"), "\\\\.\\DISPLAY1"))

    def test_recovery_timeout_releases_the_capture_and_retries(self):
        stop = threading.Event()
        adapter = RecordingAdapter(stop_event=stop, stop_after=2)

        class TimedOutGrab:
            closed = 0

            def capture_array(self):
                raise monitor.CaptureRecoveryTimeout("The display did not come back")

            def close(self):
                TimedOutGrab.closed += 1

        grabs = [TimedOutGrab(), TimedOutGrab(), StillGrab()]
        rebuilds = []
        _session, _statuses, errors = run_monitor(
            [adapter],
            lambda **_kwargs: grabs.pop(0),
            stop=stop,
            extra_patches=[
                patch.object(sync_session.monitor, "rebuild_capture_backend", side_effect=lambda: rebuilds.append(1)),
            ],
        )

        self.assertEqual(TimedOutGrab.closed, 2)
        self.assertEqual(len(rebuilds), 1)  # after the second failure in a row
        self.assertEqual(sum("did not come back" in error for error in errors), 1)
        self.assertGreaterEqual(len(adapter.frames), 2)

    def test_screen_capture_is_released_when_sync_stops(self):
        stop = threading.Event()
        grab = StillGrab()
        run_monitor([RecordingAdapter(stop_event=stop, stop_after=2)], lambda **_kwargs: grab, stop=stop)

        self.assertEqual(grab.closed, 1)

    def test_screen_capture_is_released_after_a_fatal_error(self):
        grab = StillGrab()

        def broken_mapping(_count, _aspect):
            raise monitor.ScreenCaptureDependencyError("capture backend missing")

        _session, _statuses, errors = run_monitor(
            [RecordingAdapter()], lambda **_kwargs: grab, mapping_provider=broken_mapping
        )

        self.assertEqual(grab.closed, 1)
        self.assertIn("capture backend missing", errors)

    def test_display_name_reaches_the_capture_and_fallbacks_are_reported(self):
        stop = threading.Event()
        seen = []

        def factory(**kwargs):
            seen.append(kwargs)
            grab = StillGrab()
            grab.fallback_note = "Display 2 was not found by the screen capture"
            return grab

        settings = SyncSettings(monitor_display=1)
        settings.monitor_display_name = "\\\\.\\DISPLAY2"
        _session, statuses, _errors = run_monitor(
            [RecordingAdapter(stop_event=stop, stop_after=2)], factory, settings=settings, stop=stop
        )

        self.assertEqual(seen[0]["display_index"], 1)
        self.assertEqual(seen[0]["display_name"], "\\\\.\\DISPLAY2")
        self.assertIs(seen[0]["stop_event"], stop)
        self.assertEqual(statuses.count("Display 2 was not found by the screen capture"), 1)


class StreamKeepaliveTests(unittest.TestCase):
    def session_with(self, adapter):
        session = sync_session.SyncSession("monitor", [{"mac": "AA"}], SyncSettings())
        session.adapters = {"strip": adapter}
        session.senders = {"strip": InlineSender(adapter.set_segments)}
        session._keys = [(session.devices[0], "strip")]
        session._last_keepalive_at["strip"] = 0.0
        return session

    def test_base_adapter_keepalive_is_a_no_op(self):
        class Minimal(TransportAdapter):
            capabilities = DeviceCapabilities()

            def set_power(self, on):
                pass

            def set_brightness(self, percent):
                pass

            def set_color(self, r, g, b):
                pass

            def set_segments(self, colors):
                pass

        self.assertIsNone(Minimal({}).keep_stream_alive())

    def test_stream_is_rearmed_every_ten_seconds(self):
        adapter = RecordingAdapter()
        session = self.session_with(adapter)

        for now in (1.0, 5.0, 9.9):
            session._keep_streams_alive(now)
        self.assertEqual(adapter.keepalives, 0)
        session._keep_streams_alive(10.0)
        self.assertEqual(adapter.keepalives, 1)
        session._keep_streams_alive(15.0)
        self.assertEqual(adapter.keepalives, 1)
        session._keep_streams_alive(20.5)
        self.assertEqual(adapter.keepalives, 2)

    def test_stream_is_rearmed_before_the_first_frame_after_a_pause(self):
        adapter = RecordingAdapter()
        session = self.session_with(adapter)

        session._submit("strip", [(1, 2, 3)], 1.0)
        session._submit("strip", [(1, 2, 3)], 2.5)
        self.assertEqual(adapter.keepalives, 0)
        session._submit("strip", [(1, 2, 3)], 6.0)  # 3.5 s without a frame
        self.assertEqual(adapter.keepalives, 1)
        self.assertEqual(len(adapter.frames), 3)

    def test_keepalive_failures_do_not_stop_the_stream(self):
        adapter = RecordingAdapter()
        adapter.keep_stream_alive = lambda: (_ for _ in ()).throw(OSError("unreachable"))
        session = self.session_with(adapter)

        session._keep_streams_alive(30.0)
        session._submit("strip", [(1, 2, 3)], 31.0)
        self.assertEqual(len(adapter.frames), 1)

    def test_monitor_sync_rearms_streams_on_schedule(self):
        stop = threading.Event()
        adapter = RecordingAdapter(stop_event=stop, stop_after=8)
        tick = {"now": 0.0}

        def monotonic():
            tick["now"] += 1.0
            return tick["now"]

        run_monitor(
            [adapter],
            lambda **_kwargs: StillGrab(),
            stop=stop,
            extra_patches=[patch.object(sync_session.time, "monotonic", side_effect=monotonic)],
        )

        self.assertGreaterEqual(adapter.keepalives, 1)


class MonitorPipelineTests(unittest.TestCase):
    def test_matrix_panels_stream_one_whole_screen_colour(self):
        stop = threading.Event()
        panel = RecordingAdapter(
            DeviceCapabilities(transport="ble", segment_count=1024, matrix_size=(32, 32), max_update_hz=10.0),
            stop_event=stop,
            stop_after=1,
        )
        mapped = []

        def mapping_provider(count, aspect):
            mapped.append(count)
            return sync_session._default_mapping(count, aspect)

        run_monitor([panel], lambda **_kwargs: StillGrab(), stop=stop, mapping_provider=mapping_provider)

        self.assertEqual(mapped, [])
        self.assertEqual(len(panel.frames[0]), 1)

    def test_devices_with_the_same_mapping_share_one_average_per_frame(self):
        stop = threading.Event()
        first = RecordingAdapter()
        second = RecordingAdapter(stop_event=stop, stop_after=3)
        grab = StillGrab()
        calls = []
        real = sync_session.processing.average_zone_colors

        def counting(frame, mapping, **kwargs):
            calls.append(id(frame))
            return real(frame, mapping, **kwargs)

        run_monitor(
            [first, second],
            lambda **_kwargs: grab,
            stop=stop,
            extra_patches=[patch.object(sync_session.processing, "average_zone_colors", side_effect=counting)],
        )

        # One still frame object: averaged once for both devices, then reused.
        self.assertGreaterEqual(grab.calls, 3)
        self.assertEqual(len(calls), 1)

    def test_loop_rate_is_capped_at_the_fastest_device(self):
        session = sync_session.SyncSession("monitor", [], SyncSettings())
        session.adapters = {
            "a": RecordingAdapter(SimpleNamespace(segment_count=1, max_update_hz=8.0)),
            "b": RecordingAdapter(SimpleNamespace(segment_count=1, max_update_hz=20.0)),
        }
        session._keys = [({}, "a"), ({}, "b")]

        cap = session._device_rate_cap()
        self.assertEqual(cap, 20.0)
        self.assertAlmostEqual(session._frame_interval(45, cap), 1 / 20)
        self.assertAlmostEqual(session._frame_interval(10, cap), 1 / 10)
        self.assertAlmostEqual(session._frame_interval(45, None), 1 / 45)

    def test_slow_transport_smoothing_uses_time_since_the_last_update(self):
        stop = threading.Event()
        adapter = RecordingAdapter(SimpleNamespace(segment_count=1, max_update_hz=8.0))
        dts = []

        class RecordingSmoother:
            def __init__(self, alpha=0.5, count=0):
                self.alpha = alpha

            def update(self, target, dt=None):
                dts.append(dt)
                if len(dts) >= 4:
                    stop.set()
                return [tuple(color) for color in target]

        tick = {"now": 0.0}

        def monotonic():
            tick["now"] += 0.1
            return tick["now"]

        # A still screen and a high delta threshold suppress resends, so the
        # time since the last *send* keeps growing while updates are 0.1 s apart.
        run_monitor(
            [adapter],
            lambda **_kwargs: StillGrab(),
            settings=SyncSettings(delta_threshold=255),
            stop=stop,
            extra_patches=[
                patch.object(sync_session.processing, "ColorSmoother", RecordingSmoother),
                patch.object(sync_session.time, "monotonic", side_effect=monotonic),
                patch.object(sync_session.processing, "frame_needs_send", side_effect=lambda previous, *_a, **_k: previous is None),
            ],
        )

        self.assertIsNone(dts[0])
        for dt in dts[1:]:
            self.assertLess(dt, 1.0)

    def test_black_capture_on_linux_gives_a_one_time_hint(self):
        stop = threading.Event()
        adapter = RecordingAdapter(stop_event=stop, stop_after=40)
        tick = {"now": 0.0}

        def monotonic():
            tick["now"] += 0.5
            return tick["now"]

        black = np.zeros((8, 8, 3), dtype=np.uint8)
        _session, statuses, _errors = run_monitor(
            [adapter],
            lambda **_kwargs: StillGrab(black),
            settings=SyncSettings(delta_threshold=0),
            stop=stop,
            extra_patches=[
                patch.object(sync_session.GENERAL, "platform", "Linux"),
                patch.object(sync_session.time, "monotonic", side_effect=monotonic),
            ],
        )

        self.assertEqual(sum("only returns black frames" in status for status in statuses), 1)

    def test_started_is_reported_only_after_devices_open(self):
        started = []

        def acquire(device, _server=None):
            raise RuntimeError("unreachable")

        with patch.object(sync_session.pool, "acquire", side_effect=acquire):
            sync_session.SyncSession(
                "monitor", [{"mac": "AA"}], SyncSettings(), on_started=started.append
            ).run()
        self.assertEqual(started, [])

        stop = threading.Event()
        with (
            patch.object(sync_session.pool, "acquire", return_value=RecordingAdapter(stop_event=stop, stop_after=1)),
            patch.object(sync_session.monitor, "ScreenGrab", side_effect=lambda **_kwargs: StillGrab()),
            patch.object(sync_session.SyncSession, "_wait", lambda self, seconds: None),
        ):
            sync_session.SyncSession(
                "monitor", [{"mac": "AA"}], SyncSettings(), stop_event=stop,
                on_started=started.append, sender_factory=InlineSender,
            ).run()
        self.assertEqual(started, [1])


class SyncSettingsRuntimeFieldTests(unittest.TestCase):
    def test_display_name_is_runtime_only(self):
        settings = SyncSettings(monitor_display=2)
        settings.monitor_display_name = "\\\\.\\DISPLAY3"

        self.assertNotIn("monitor_display_name", settings.to_dict())
        self.assertEqual(settings.copy().monitor_display_name, "\\\\.\\DISPLAY3")

        loaded = SyncSettings.from_mapping({"monitor_display": 1})
        settings.update_from(loaded)
        self.assertEqual(settings.monitor_display, 1)
        self.assertEqual(settings.monitor_display_name, "\\\\.\\DISPLAY3")


if __name__ == "__main__":
    unittest.main()
