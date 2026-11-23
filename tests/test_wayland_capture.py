"""Experimental Wayland capture: portal share, frame hand-off, up-front checks.

Every test here uses fakes for the portal and the GStreamer reader; nothing
talks to D-Bus or starts GStreamer, so the suite runs the same on Windows,
X11 and Wayland machines.
"""

import os
import sys
import threading
import time
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from lumisync.config.options import GENERAL
from lumisync.config.sync_settings import SyncSettings
from lumisync.sync import monitor, wayland_capture as wc
from lumisync.sync import session as sync_session

STREAM = wc.PortalStream(node_id=43, width=1920, height=1080)


def frame(value: int) -> np.ndarray:
    return np.full((180, 320, 3), value, dtype=np.uint8)


class MemoryTokenStore:
    def __init__(self, token=None):
        self.token = token
        self.saved = []

    def load(self):
        return self.token

    def save(self, token):
        self.saved.append(token)
        self.token = token


class FakePortal:
    """Records calls; ``start`` can block, refuse or reject a token."""

    def __init__(self, *, grant_token="token-2", deny=False, reject_tokens=False, gate=None):
        self.grant_token = grant_token
        self.deny = deny
        self.reject_tokens = reject_tokens
        self.gate = gate
        self.started_with = []
        self.remotes = 0
        self.closed = 0

    def start(self, restore_token, cancelled):
        self.started_with.append(restore_token)
        if self.gate is not None:
            while not self.gate.wait(0.01):
                if cancelled():
                    raise wc.ScreenSharingDenied(wc.DENIED_MESSAGE)
        if self.deny:
            raise wc.ScreenSharingDenied(wc.DENIED_MESSAGE)
        if restore_token and self.reject_tokens:
            raise wc.PortalError("SelectSources: invalid restore token")
        return wc.PortalGrant(STREAM, self.grant_token)

    def open_remote(self):
        self.remotes += 1
        return 100 + self.remotes

    def close(self):
        self.closed += 1


class FakeReader:
    instances = []

    def __init__(self, fd, stream, fps):
        self.fd, self.stream, self.fps = fd, stream, fps
        self.slot = wc.LatestFrame()
        self.started = False
        self.stopped = False
        self.problem = None
        FakeReader.instances.append(self)

    def start(self):
        self.started = True

    def latest(self):
        return self.slot.take()

    def error(self):
        return self.problem

    def stop(self):
        self.stopped = True


class InlineThread:
    """Runs the portal worker synchronously so state is settled on return."""

    def __init__(self, target, args=(), **_kwargs):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


def make_service(portal=None, store=None, thread_factory=InlineThread):
    portal = portal or FakePortal()
    store = store if store is not None else MemoryTokenStore()
    service = wc.WaylandScreenCast(
        portal_factory=lambda: portal,
        reader_factory=FakeReader,
        token_store=store,
        fps=lambda: 45,
        thread_factory=thread_factory,
    )
    return service, portal, store


class LatestFrameTests(unittest.TestCase):
    def test_newest_frame_wins_and_is_taken_once(self):
        slot = wc.LatestFrame()
        self.assertIsNone(slot.take())
        slot.put(frame(1))
        slot.put(frame(2))
        slot.put(frame(3))
        self.assertEqual(int(slot.take()[0, 0, 0]), 3)
        self.assertIsNone(slot.take())


class PipelineTests(unittest.TestCase):
    def test_frame_size_keeps_aspect_and_alignment(self):
        self.assertEqual(wc.frame_size(STREAM), (320, 180))
        self.assertEqual(wc.frame_size(wc.PortalStream(1, 3440, 1440)), (320, 134))
        # Unknown size: assume 16:9. Widths stay a multiple of 4 (no RGB row padding).
        self.assertEqual(wc.frame_size(wc.PortalStream(1)), (320, 180))
        self.assertEqual(wc.frame_size(STREAM, width=322)[0], 320)

    def test_pipeline_drops_to_the_sync_rate_before_scaling_and_converting(self):
        description = wc.pipeline_description(7, STREAM, 45, "appsink name=sink")
        self.assertTrue(description.startswith("pipewiresrc fd=7 path=43 "))
        self.assertLess(description.index("videorate"), description.index("videoscale"))
        self.assertLess(description.index("videoscale"), description.index("videoconvert"))
        self.assertIn("max-rate=45", description)
        self.assertIn("format=RGB,width=320,height=180", description)
        self.assertTrue(description.endswith("! appsink name=sink"))

    def test_parse_streams_reads_jeepney_variants(self):
        raw = ("a(ua{sv})", [(43, {"size": ("(ii)", (2560, 1440)), "source_type": ("u", 1)}), (44, {})])
        self.assertEqual(
            wc.parse_streams(raw),
            [wc.PortalStream(43, 2560, 1440), wc.PortalStream(44, 0, 0)],
        )
        self.assertEqual(wc.parse_streams(None), [])

    def test_frozen_child_environment_drops_bundled_libraries(self):
        bundle = os.path.abspath(os.path.join(os.sep, "opt", "LumiSync"))
        env = {
            "LD_LIBRARY_PATH": os.pathsep.join([bundle, os.path.join(bundle, "_internal"), "/usr/lib/extra"]),
            "GST_PLUGIN_PATH": os.path.join(bundle, "gst"),
            "HOME": "/home/user",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(sys, "frozen", True, create=True),
            patch.object(sys, "executable", os.path.join(bundle, "LumiSync")),
            patch.object(sys, "_MEIPASS", os.path.join(bundle, "_internal"), create=True),
        ):
            child = wc._child_environment()
        self.assertEqual(child["LD_LIBRARY_PATH"], "/usr/lib/extra")
        self.assertNotIn("GST_PLUGIN_PATH", child)
        self.assertEqual(child["HOME"], "/home/user")

    def test_source_checkout_keeps_the_environment(self):
        with patch.dict(os.environ, {"LD_LIBRARY_PATH": "/x"}, clear=True):
            self.assertEqual(wc._child_environment()["LD_LIBRARY_PATH"], "/x")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        FakeReader.instances = []

    def test_no_frame_yet_returns_none_like_dxcam(self):
        service, _portal, _store = make_service()
        handle = service.acquire()
        self.assertEqual(service.state, wc.RUNNING)
        self.assertIsNone(handle.grab())

    def test_newest_frame_wins_between_sync_ticks(self):
        service, _portal, _store = make_service()
        handle = service.acquire()
        reader = FakeReader.instances[-1]
        for value in (10, 20, 30):
            reader.slot.put(frame(value))
        self.assertEqual(int(handle.grab()[0, 0, 0]), 30)
        # Nothing new since: the session keeps using its last frame.
        self.assertIsNone(handle.grab())

    def test_reader_gets_the_portal_stream_and_remote(self):
        service, _portal, _store = make_service()
        service.acquire()
        reader = FakeReader.instances[-1]
        self.assertTrue(reader.started)
        self.assertEqual((reader.fd, reader.stream, reader.fps), (101, STREAM, 45))

    def test_the_share_is_asked_once_and_reused_between_sessions(self):
        service, portal, _store = make_service()
        first = service.acquire()
        first.close()
        self.assertTrue(FakeReader.instances[0].stopped, "an idle share must not keep reading frames")
        self.assertEqual(portal.closed, 0, "the portal session stays open between sessions")

        second = service.acquire()
        reader = FakeReader.instances[-1]
        reader.slot.put(frame(5))
        self.assertEqual(int(second.grab()[0, 0, 0]), 5)
        self.assertEqual(portal.started_with, [None], "no second share dialog")
        self.assertEqual(portal.remotes, 2, "only the PipeWire remote is reopened")

    def test_denied_share_stops_with_a_plain_message_and_asks_again_next_time(self):
        portal = FakePortal(deny=True)
        service, _portal, store = make_service(portal=portal, store=MemoryTokenStore("old"))
        handle = service.acquire()
        self.assertEqual(service.state, wc.DENIED)
        with self.assertRaises(monitor.ScreenSharingDeniedError) as caught:
            handle.grab()
        self.assertEqual(str(caught.exception), wc.DENIED_MESSAGE)
        self.assertIsNone(store.token, "a refused share forgets the old grant")
        self.assertEqual(portal.closed, 1)

        portal.deny = False
        service.acquire()
        self.assertEqual(service.state, wc.RUNNING)
        self.assertEqual(len(portal.started_with), 2)

    def test_restore_token_is_stored_and_reused_on_the_next_run(self):
        store = MemoryTokenStore()
        service, portal, _store = make_service(portal=FakePortal(grant_token="grant-1"), store=store)
        service.acquire()
        self.assertEqual(store.token, "grant-1")
        service.shutdown()

        next_run, next_portal, _ = make_service(portal=FakePortal(grant_token="grant-2"), store=store)
        next_run.acquire()
        self.assertEqual(next_portal.started_with, ["grant-1"])
        self.assertEqual(store.token, "grant-2", "tokens are single use; the new one replaces it")

    def test_rejected_restore_token_is_dropped_and_the_user_asked_again(self):
        store = MemoryTokenStore("stale")
        portal = FakePortal(grant_token="fresh", reject_tokens=True)
        service, _portal, _store = make_service(portal=portal, store=store)
        service.acquire()
        self.assertEqual(service.state, wc.RUNNING)
        self.assertEqual(portal.started_with, ["stale", None])
        self.assertEqual(store.saved, [None, "fresh"])

    def test_desktop_without_persistence_clears_the_token(self):
        store = MemoryTokenStore("old")
        service, _portal, _store = make_service(portal=FakePortal(grant_token=None), store=store)
        service.acquire()
        self.assertIsNone(store.token)

    def test_ended_stream_stops_sync_instead_of_reopening_the_dialog(self):
        service, portal, _store = make_service()
        handle = service.acquire()
        FakeReader.instances[-1].problem = "the screen stream ended"
        with self.assertRaises(wc.ScreenSharingDenied) as caught:
            handle.grab()
        self.assertEqual(str(caught.exception), wc.STOPPED_MESSAGE)
        self.assertEqual(service.state, wc.FAILED)
        self.assertTrue(FakeReader.instances[-1].stopped)
        self.assertEqual(portal.closed, 1)

    def test_missing_gstreamer_is_reported_through_the_share(self):
        service = wc.WaylandScreenCast(
            portal_factory=FakePortal,
            reader_factory=None,
            token_store=MemoryTokenStore(),
            thread_factory=InlineThread,
        )
        with patch.object(wc, "default_reader_factory", return_value=None):
            handle = service.acquire()
        with self.assertRaises(wc.ScreenSharingDenied) as caught:
            handle.grab()
        self.assertEqual(str(caught.exception), wc.MISSING_GSTREAMER_MESSAGE)

    def test_shutdown_stops_the_reader_and_closes_the_portal(self):
        service, portal, _store = make_service()
        service.acquire()
        service.shutdown()
        self.assertTrue(FakeReader.instances[-1].stopped)
        self.assertEqual(portal.closed, 1)
        with self.assertRaises(wc.ScreenSharingDenied):
            service.acquire()


class ThreadHandOffTests(unittest.TestCase):
    def setUp(self):
        FakeReader.instances = []

    def test_acquire_and_grab_never_wait_for_the_dialog(self):
        gate = threading.Event()
        portal = FakePortal(gate=gate)
        service, _portal, _store = make_service(portal=portal, thread_factory=threading.Thread)
        results = {}

        def sync_loop():
            started = time.perf_counter()
            handle = service.acquire()
            results["waiting"] = handle.waiting_for_user()
            results["first"] = handle.grab()
            results["elapsed"] = time.perf_counter() - started
            results["handle"] = handle

        worker = threading.Thread(target=sync_loop)
        worker.start()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(results["waiting"])
        self.assertIsNone(results["first"])
        self.assertLess(results["elapsed"], 0.5)

        gate.set()
        deadline = time.monotonic() + 2
        while service.state != wc.RUNNING and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(service.state, wc.RUNNING)
        FakeReader.instances[-1].slot.put(frame(9))
        self.assertEqual(int(results["handle"].grab()[0, 0, 0]), 9)
        service.shutdown()

    def test_frames_from_a_reader_thread_reach_the_sync_thread(self):
        service, _portal, _store = make_service()
        handle = service.acquire()
        reader = FakeReader.instances[-1]
        writer = threading.Thread(target=lambda: [reader.slot.put(frame(v)) for v in range(1, 101)])
        writer.start()
        writer.join()
        latest = handle.grab()
        self.assertEqual(int(latest[0, 0, 0]), 100)

    def test_shutdown_cancels_a_pending_dialog(self):
        gate = threading.Event()
        portal = FakePortal(gate=gate)
        service, _portal, store = make_service(portal=portal, thread_factory=threading.Thread)
        service.acquire()
        service.shutdown()
        deadline = time.monotonic() + 2
        while portal.closed == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(portal.closed, 1)
        self.assertEqual(service.state, wc.IDLE)
        self.assertEqual(FakeReader.instances, [])


class AvailabilityTests(unittest.TestCase):
    def tearDown(self):
        wc.reset_availability()

    def test_all_present_is_supported_with_the_experimental_note(self):
        self.assertEqual(
            wc.availability([(lambda: True, "a"), (lambda: True, "b")]),
            (True, wc.EXPERIMENTAL_NOTE),
        )

    def test_first_missing_piece_names_what_to_install(self):
        checks = [(lambda: True, "dbus"), (lambda: False, wc.MISSING_GSTREAMER_MESSAGE), (lambda: False, "portal")]
        self.assertEqual(wc.availability(checks), (False, wc.MISSING_GSTREAMER_MESSAGE))
        self.assertIn("X11", wc.MISSING_GSTREAMER_MESSAGE)
        self.assertIn("X11", wc.MISSING_PORTAL_MESSAGE)

    def test_a_crashing_check_counts_as_missing(self):
        def boom():
            raise OSError("no session bus")

        self.assertEqual(wc.availability([(boom, wc.MISSING_PORTAL_MESSAGE)]), (False, wc.MISSING_PORTAL_MESSAGE))

    def test_failures_are_rechecked_after_a_while_successes_are_kept(self):
        now = [0.0]
        results = iter([False, True])
        checks = [(lambda: next(results), "missing")]
        with patch.object(wc, "_checks", return_value=checks):
            self.assertEqual(wc.availability(clock=lambda: now[0])[0], False)
            now[0] = 5.0
            self.assertEqual(wc.availability(clock=lambda: now[0])[0], False, "cached")
        with patch.object(wc, "_checks", return_value=[(lambda: True, "missing")]):
            now[0] = wc.AVAILABILITY_RETRY_AFTER + 1
            self.assertEqual(wc.availability(clock=lambda: now[0]), (True, wc.EXPERIMENTAL_NOTE))
        with patch.object(wc, "_checks", side_effect=AssertionError("success is cached")):
            now[0] = 10_000.0
            self.assertTrue(wc.availability(clock=lambda: now[0])[0])


class BackendSelectionTests(unittest.TestCase):
    """``ScreenGrab`` and ``capture_supported`` per platform and compositor."""

    def setUp(self):
        FakeReader.instances = []
        self.service, self.portal, _store = make_service()
        wc.set_service(self.service)
        self.addCleanup(wc.set_service, None)
        self.addCleanup(monitor.ScreenGrab.reset_cache)

    def session_type(self, platform, compositor):
        return patch.multiple(GENERAL, platform=platform, compositor=compositor)

    def test_wayland_uses_the_portal_share_and_hands_back_rgb(self):
        with (
            self.session_type("Linux", "wayland"),
            patch.object(wc, "availability", return_value=(True, wc.EXPERIMENTAL_NOTE)),
        ):
            grab = monitor.ScreenGrab(display_index=1, display_name="DP-2")
            self.assertEqual(grab.capture_frame(), (None, "RGB"))
            FakeReader.instances[-1].slot.put(frame(77))
            array, order = grab.capture_frame()
            self.assertEqual((array.shape, order), ((180, 320, 3), "RGB"))
            self.assertEqual(int(array[0, 0, 0]), 77)
            grab.close()
        self.assertEqual(self.portal.closed, 0, "closing a grab keeps the share for the next session")
        self.assertTrue(FakeReader.instances[-1].stopped)

    def test_wayland_grab_announces_the_share_dialog(self):
        gate = threading.Event()
        service, _portal, _ = make_service(portal=FakePortal(gate=gate), thread_factory=threading.Thread)
        wc.set_service(service)
        with (
            self.session_type("Linux", "wayland"),
            patch.object(wc, "availability", return_value=(True, wc.EXPERIMENTAL_NOTE)),
        ):
            grab = monitor.ScreenGrab()
        self.assertEqual(grab.fallback_note, wc.WAITING_NOTE)
        gate.set()
        service.shutdown()

    def test_wayland_without_the_backend_raises_the_install_message(self):
        with (
            self.session_type("Linux", "wayland"),
            patch.object(wc, "availability", return_value=(False, wc.MISSING_PORTAL_MESSAGE)),
        ):
            with self.assertRaises(monitor.WaylandUnsupportedError) as caught:
                monitor.ScreenGrab()
            self.assertEqual(monitor.capture_supported(), (False, wc.MISSING_PORTAL_MESSAGE))
        self.assertEqual(str(caught.exception), wc.MISSING_PORTAL_MESSAGE)
        self.assertEqual(self.portal.started_with, [])

    def test_wayland_capture_supported_carries_the_experimental_note(self):
        with (
            self.session_type("Linux", "wayland"),
            patch.object(wc, "availability", return_value=(True, wc.EXPERIMENTAL_NOTE)),
        ):
            self.assertEqual(monitor.capture_supported(), (True, wc.EXPERIMENTAL_NOTE))

    def test_x11_still_uses_mss_and_never_the_portal(self):
        class FakeMss:
            monitors = [{"all": True}, {"first": True}, {"second": True}]

            def grab(self, region):
                return np.zeros((2, 2, 4), dtype=np.uint8) + (1 if "second" in region else 0)

            def close(self):
                pass

        fake_module = types.ModuleType("mss")
        fake_module.mss = FakeMss  # type: ignore[attr-defined]
        availability = Mock()
        with (
            self.session_type("Linux", "x11"),
            patch.dict(sys.modules, {"mss": fake_module}),
            patch.object(wc, "availability", availability),
            patch("importlib.util.find_spec", return_value=object()),
        ):
            grab = monitor.ScreenGrab(display_index=1)
            array, order = grab.capture_frame()
            self.assertEqual(monitor.capture_supported(), (True, ""))
        self.assertIsInstance(grab.camera, FakeMss)
        self.assertEqual((array.shape, order, int(array[0, 0, 0])), ((2, 2, 4), "BGRA", 1))
        availability.assert_not_called()
        self.assertEqual(self.portal.started_with, [])

    def test_windows_still_uses_dxcam_and_never_the_portal(self):
        camera = SimpleNamespace(grab=lambda: np.zeros((2, 2, 4), dtype=np.uint8), release=lambda: None)
        fake_dxcam = types.ModuleType("dxcam")
        fake_dxcam.create = Mock(return_value=camera)  # type: ignore[attr-defined]
        availability = Mock()
        with (
            self.session_type("Windows", None),
            patch.dict(sys.modules, {"dxcam": fake_dxcam}),
            patch.object(wc, "availability", availability),
        ):
            grab = monitor.ScreenGrab()
            _array, order = grab.capture_frame()
        self.assertIs(grab.camera, camera)
        self.assertEqual(order, "BGRA")
        self.assertEqual(fake_dxcam.create.call_args.kwargs["output_color"], "BGRA")
        availability.assert_not_called()
        self.assertEqual(self.portal.started_with, [])


class SessionTests(unittest.TestCase):
    """A refused share ends Monitor sync once, with its own plain message."""

    def test_denied_share_stops_monitor_sync_with_a_plain_message(self):
        attempts = []

        class DeniedGrab:
            fallback_note = None

            def capture_frame(self):
                attempts.append(True)
                raise monitor.ScreenSharingDeniedError(wc.DENIED_MESSAGE)

            def close(self):
                pass

        adapter = SimpleNamespace(
            capabilities=SimpleNamespace(segment_count=1, max_update_hz=40.0, supports_streaming=True),
            device={},
            begin_stream=lambda: None,
            set_segments=lambda colors: None,
            end_stream=lambda: None,
            close=lambda: None,
        )
        errors, statuses = [], []
        stop = threading.Event()
        with (
            patch.object(sync_session.pool, "acquire", return_value=adapter),
            patch.object(sync_session.monitor, "ScreenGrab", side_effect=lambda **_kw: DeniedGrab()),
            patch.object(sync_session.SyncSession, "_wait", lambda self, seconds: None),
        ):
            sync_session.SyncSession(
                "monitor",
                [{"mac": "AA:BB", "model": "H6672"}],
                SyncSettings(),
                stop_event=stop,
                on_status=statuses.append,
                on_error=errors.append,
            ).run()
        self.assertEqual(errors, [wc.DENIED_MESSAGE])
        self.assertEqual(len(attempts), 1, "no retry loop that reopens the dialog")
        self.assertFalse(any("Traceback" in message for message in errors + statuses))


class UpFrontCheckTests(unittest.TestCase):
    """The controller and the Monitor page allow Wayland when capture works."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def controller(self):
        from lumisync.gui.controllers.sync_controller import SyncController

        controller = SyncController()
        controller._settings = SimpleNamespace(value=lambda _key, default=None: default, setValue=lambda *_: None)
        controller.flush_sync_settings = lambda: None
        return controller

    def test_controller_starts_monitor_sync_on_a_supported_wayland_session(self):
        from lumisync.gui.controllers import sync_controller

        controller = self.controller()
        messages = []
        controller.status_updated.connect(messages.append)
        worker = Mock()
        with (
            patch.object(sync_controller.monitor, "capture_supported", return_value=(True, wc.EXPERIMENTAL_NOTE)),
            patch.object(sync_controller, "MonitorSyncWorker", worker),
            patch.object(sync_controller.QThread, "start"),
        ):
            controller.start_sync("monitor", [{"transport": "ble", "type": "idotmatrix", "ble_address": "AA:BB:CC:DD:EE:FF"}])
        worker.assert_called_once()
        self.assertNotIn(wc.EXPERIMENTAL_NOTE, messages)
        controller.sync_thread = None

    def test_controller_still_refuses_when_the_backend_is_missing(self):
        from lumisync.gui.controllers import sync_controller

        controller = self.controller()
        messages = []
        controller.status_updated.connect(messages.append)
        worker = Mock()
        with (
            patch.object(sync_controller.monitor, "capture_supported", return_value=(False, wc.MISSING_PORTAL_MESSAGE)),
            patch.object(sync_controller, "MonitorSyncWorker", worker),
        ):
            controller.start_sync("monitor", [{"transport": "ble", "type": "idotmatrix", "ble_address": "AA:BB:CC:DD:EE:FF"}])
        worker.assert_not_called()
        self.assertEqual(messages, [wc.MISSING_PORTAL_MESSAGE])

    def monitor_page(self, supported):
        from lumisync.gui.views import modes_view

        controller = self.controller()
        controller.monitor_capture_supported = lambda: supported
        page = modes_view.ModesView(controller, None, mode="monitor")
        self.addCleanup(page.close)
        page._update_action_states()
        return page, modes_view

    def test_wayland_page_labels_capture_experimental_and_defers_the_screen_choice(self):
        with patch.multiple(GENERAL, platform="Linux", compositor="wayland"):
            page, modes_view = self.monitor_page((True, wc.EXPERIMENTAL_NOTE))
        self.assertEqual(page._action_hints["monitor"].text(), wc.EXPERIMENTAL_NOTE)
        self.assertFalse(page.display_combo.isEnabled())
        self.assertEqual(page.display_combo.count(), 1)
        self.assertEqual(page.display_combo.currentText(), modes_view.WAYLAND_DISPLAY_ITEM)
        self.assertEqual(page.display_combo.toolTip(), modes_view.WAYLAND_DISPLAY_NOTE)

    def test_wayland_page_without_the_backend_says_what_to_install(self):
        with patch.multiple(GENERAL, platform="Linux", compositor="wayland"):
            page, _ = self.monitor_page((False, wc.MISSING_GSTREAMER_MESSAGE))
        self.assertEqual(page._action_hints["monitor"].text(), wc.MISSING_GSTREAMER_MESSAGE)
        self.assertFalse(page.monitor_start_button.isEnabled())

    def test_x11_page_keeps_its_display_picker(self):
        with patch.multiple(GENERAL, platform="Linux", compositor="x11"):
            page, modes_view = self.monitor_page((True, ""))
        self.assertTrue(page.display_combo.isEnabled())
        self.assertNotEqual(page.display_combo.itemText(0), modes_view.WAYLAND_DISPLAY_ITEM)
        self.assertEqual(page._action_hints["monitor"].text(), "")


@unittest.skipUnless(sys.platform.startswith("linux"), "pass_fds and pipes as on Linux")
class GstLaunchReaderTests(unittest.TestCase):
    """The child-process reader, driven by a stand-in for gst-launch-1.0."""

    def write_fake_launcher(self, body):
        import stat
        import tempfile

        directory = tempfile.mkdtemp(prefix="lumisync-gst-")
        self.addCleanup(__import__("shutil").rmtree, directory, True)
        path = os.path.join(directory, "gst-launch-1.0")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"#!{sys.executable}\n{body}\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
        return path

    def test_reads_whole_frames_and_keeps_only_the_newest(self):
        launcher = self.write_fake_launcher(
            "import sys, time\n"
            "for value in (1, 2, 3):\n"
            "    sys.stdout.buffer.write(bytes([value]) * (320 * 180 * 3))\n"
            "sys.stdout.flush()\n"
            "time.sleep(5)\n"
        )
        read_end, write_end = os.pipe()
        os.close(write_end)
        reader = wc.GstLaunchReader(read_end, STREAM, 30, executable=launcher)
        reader.start()
        self.addCleanup(reader.stop)
        deadline = time.monotonic() + 5
        latest = None
        while time.monotonic() < deadline:
            latest = reader.latest()
            if latest is not None and int(latest[0, 0, 0]) == 3:
                break
            time.sleep(0.02)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.shape, (180, 320, 3))
        self.assertEqual(int(latest[0, 0, 0]), 3)
        self.assertIsNone(reader.error())

    def test_a_dying_child_reports_its_last_error_line(self):
        launcher = self.write_fake_launcher(
            "import sys\nsys.stderr.write('ERROR: pipewiresrc: target not found\\n')\nsys.exit(1)\n"
        )
        read_end, write_end = os.pipe()
        os.close(write_end)
        reader = wc.GstLaunchReader(read_end, STREAM, 30, executable=launcher)
        reader.start()
        self.addCleanup(reader.stop)
        deadline = time.monotonic() + 5
        while reader.error() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(0.1)
        self.assertIn("target not found", reader.error() or "")


if __name__ == "__main__":
    unittest.main()
