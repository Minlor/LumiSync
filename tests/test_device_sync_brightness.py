"""Per-light sync brightness: settings, the sync engine, the controller and the pages."""

import os
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lumisync.config.options import BRIGHTNESS, SYNC  # noqa: E402
from lumisync.config.sync_settings import SYNC_SETTINGS_KEYS, SyncSettings, coerce_device_brightness  # noqa: E402
from lumisync.sync import processing  # noqa: E402
from lumisync.sync import session as sync_session  # noqa: E402

STRIP = {"mac": "fixture-strip", "name": "TV backlight", "model": "H6199", "sku": "H6199", "transport": "lan",
         "ip": "192.0.2.6", "device_kind": "led_strip"}
LAMP = {"mac": "fixture-lamp", "name": "Desk strip", "model": "H619C", "sku": "H619C", "transport": "lan",
        "ip": "192.0.2.5", "device_kind": "led_strip"}
MOUSE = {"device_id": "hidpp-046d-c08b", "name": "G502 HERO", "model": "Logitech G502 HERO", "transport": "usb",
         "type": "logitech_hidpp", "device_kind": "mouse", "usb_vendor_id": 0x046D, "usb_product_id": 0xC08B}


class SettingsTests(unittest.TestCase):
    def test_defaults_are_100_percent_and_nothing_is_stored(self):
        settings = SyncSettings()
        self.assertEqual(settings.device_brightness, {})
        self.assertEqual(settings.device_level("anything"), 1.0)
        self.assertEqual(settings.device_level(""), 1.0)
        self.assertEqual(settings.device_brightness_json(), "{}")
        self.assertEqual(settings.frame_brightness("monitor", "x"), settings.monitor_brightness)
        self.assertEqual(settings.frame_brightness("music", "x"), settings.music_brightness)

    def test_levels_are_clamped_and_100_percent_removes_the_entry(self):
        settings = SyncSettings()
        self.assertEqual(settings.set_device_level("a", 0.4), 0.4)
        self.assertEqual(settings.set_device_level("b", 0.01), 0.1)
        self.assertEqual(settings.set_device_level("c", 7), 1.0)
        self.assertEqual(settings.device_brightness, {"a": 0.4, "b": 0.1})
        before = settings.device_brightness
        settings.set_device_level("a", 1.0)
        self.assertEqual(settings.device_brightness, {"b": 0.1})
        self.assertEqual(before, {"a": 0.4, "b": 0.1})  # Replaced, never mutated in place.
        self.assertAlmostEqual(settings.frame_brightness("monitor", "b"), settings.monitor_brightness * 0.1)

    def test_qsettings_round_trip_keeps_absent_lights(self):
        with TemporaryDirectory() as folder:
            store = QSettings(str(Path(folder) / "sync.ini"), QSettings.Format.IniFormat)
            settings = SyncSettings()
            settings.set_device_level("fixture-lamp", 0.4)
            settings.set_device_level("AA:BB:CC:DD:EE:FF", 0.65)  # A light not in any inventory now.
            settings.to_qsettings(store)
            store.sync()
            self.assertEqual(
                store.value(SYNC_SETTINGS_KEYS["device_brightness"]),
                '{"AA:BB:CC:DD:EE:FF": 0.65, "fixture-lamp": 0.4}',
            )
            loaded = SyncSettings.from_qsettings(QSettings(str(Path(folder) / "sync.ini"), QSettings.Format.IniFormat))
        self.assertEqual(loaded.device_brightness, {"fixture-lamp": 0.4, "AA:BB:CC:DD:EE:FF": 0.65})

    def test_a_missing_key_reads_as_all_100_percent(self):
        with TemporaryDirectory() as folder:
            store = QSettings(str(Path(folder) / "empty.ini"), QSettings.Format.IniFormat)
            self.assertEqual(SyncSettings.from_qsettings(store).device_brightness, {})

    def test_garbage_is_treated_as_100_percent(self):
        for raw in ("not json", "[1, 2]", "42", "", None, 3.5, ["a"], b"\xff\xfe"):
            with self.subTest(raw=raw):
                self.assertEqual(coerce_device_brightness(raw), {})
        mixed = '{"ok": 0.5, "high": 9, "low": -1, "text": "x", "nan": NaN, "bool": true, "": 0.3, "none": null}'
        self.assertEqual(coerce_device_brightness(mixed), {"ok": 0.5, "low": 0.1})
        self.assertEqual(SyncSettings.from_mapping({"device_brightness": "{broken"}).device_brightness, {})

    def test_json_settings_and_copies(self):
        loaded = SyncSettings.from_mapping({"device_brightness": {"a": 0.3, "b": "0.5"}})
        self.assertEqual(loaded.device_brightness, {"a": 0.3, "b": 0.5})
        self.assertEqual(loaded.to_dict()["device_brightness"], {"a": 0.3, "b": 0.5})
        self.assertEqual(SyncSettings.from_mapping(loaded.to_dict()), loaded)
        live = SyncSettings()
        live.update_from(loaded)
        self.assertEqual(live.device_brightness, {"a": 0.3, "b": 0.5})
        self.assertIsNot(live.device_brightness, loaded.device_brightness)
        self.assertEqual(loaded.copy().device_brightness, {"a": 0.3, "b": 0.5})
        # Older settings without the field keep the fallback's levels.
        self.assertEqual(SyncSettings.from_mapping({}, fallback=loaded).device_brightness, {"a": 0.3, "b": 0.5})


class InlineSender:
    def __init__(self, send, max_hz=40.0, **_kwargs):
        self._send = send

    def submit(self, frame):
        self._send(frame)

    def last_error(self):
        return None

    def close(self):
        pass


class RecordingAdapter:
    def __init__(self, on_frame=None):
        self.capabilities = SimpleNamespace(segment_count=3, max_update_hz=40.0)
        self.device = {}
        self.frames = []
        self.on_frame = on_frame

    def begin_stream(self):
        pass

    def keep_stream_alive(self):
        pass

    def set_segments(self, colors):
        self.frames.append(list(colors))
        if self.on_frame is not None:
            self.on_frame(len(self.frames))

    def end_stream(self):
        pass

    def close(self):
        pass


class StillGrab:
    def __init__(self):
        height, width = 4, 9
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:, :3] = (250, 120, 30)
        frame[:, 3:6] = (40, 200, 90)
        frame[:, 6:] = (180, 60, 240)
        self.frame = frame

    def capture_array(self):
        return self.frame

    def close(self):
        pass


DEVICES = [{"mac": "AA:00", "model": "H6672"}, {"mac": "AA:01", "model": "H6672"}]


def run_monitor(settings, adapters, stop):
    queue = list(adapters)
    with (
        patch.object(sync_session.pool, "acquire", side_effect=lambda *_a, **_k: queue.pop(0)),
        patch.object(sync_session.monitor, "ScreenGrab", side_effect=lambda **_k: StillGrab()),
        patch.object(sync_session.SyncSession, "_wait", lambda self, seconds: None),
    ):
        sync_session.SyncSession(
            "monitor", DEVICES[:len(adapters)], settings, stop_event=stop, sender_factory=InlineSender,
        ).run()


def stop_after(stop, count):
    return lambda frames: stop.set() if frames >= count else None


class MonitorSessionTests(unittest.TestCase):
    def settings(self, **levels):
        # No smoothing and no delta threshold: each frame is the exact target.
        settings = SyncSettings(smoothing=1.0, delta_threshold=0, monitor_brightness=0.75)
        for key, level in levels.items():
            settings.set_device_level(key, level)
        return settings

    def frames(self, settings, count=2):
        stop = threading.Event()
        first, second = RecordingAdapter(), RecordingAdapter(stop_after(stop, count))
        run_monitor(settings, [first, second], stop)
        return first.frames, second.frames

    def test_each_light_gets_mode_brightness_times_its_level(self):
        full = SyncSettings(smoothing=1.0, delta_threshold=0, monitor_brightness=1.0)
        reference, _ = self.frames(full)
        base_first, base_second = self.frames(self.settings())
        first, second = self.frames(self.settings(**{"AA:01": 0.4}))

        # 100% everywhere: exactly the frames without per-light levels.
        self.assertEqual(base_first[0], processing.apply_brightness(reference[0], 0.75))
        self.assertEqual(base_second[0], base_first[0])
        # The other light is untouched; the dimmed one is scaled by 0.75 x 0.4.
        self.assertEqual(first, base_first)
        self.assertEqual(second[0], processing.apply_brightness(reference[0], 0.75 * 0.4))
        self.assertEqual(second[0], [(int(r * 0.3), int(g * 0.3), int(b * 0.3)) for r, g, b in reference[0]])
        self.assertNotEqual(second[0], base_second[0])

    def test_a_level_change_applies_on_the_next_frame_without_a_restart(self):
        settings = self.settings()
        stop = threading.Event()
        opened = []

        def on_frame(count):
            if count == 2:
                settings.set_device_level("AA:00", 0.5)  # What the GUI slider does.
            if count >= 4:
                stop.set()

        adapter = RecordingAdapter(on_frame)
        queue = [adapter]

        def acquire(*_a, **_k):
            opened.append(1)
            return queue.pop(0)

        with (
            patch.object(sync_session.pool, "acquire", side_effect=acquire),
            patch.object(sync_session.monitor, "ScreenGrab", side_effect=lambda **_k: StillGrab()),
            patch.object(sync_session.SyncSession, "_wait", lambda self, seconds: None),
        ):
            sync_session.SyncSession("monitor", DEVICES[:1], settings, stop_event=stop,
                                     sender_factory=InlineSender).run()
        self.assertEqual(len(opened), 1)  # The same stream: nothing was reopened.
        self.assertEqual(adapter.frames[0], adapter.frames[1])
        self.assertNotEqual(adapter.frames[2], adapter.frames[1])
        for new_color, old_color in zip(adapter.frames[2], adapter.frames[0]):
            for new, old in zip(new_color, old_color):
                self.assertLessEqual(new, old)
        self.assertEqual(adapter.frames[2], adapter.frames[3])

    def test_live_change_gives_the_exact_dimmed_frame(self):
        full = SyncSettings(smoothing=1.0, delta_threshold=0, monitor_brightness=1.0)
        stop = threading.Event()
        reference = RecordingAdapter(stop_after(stop, 1))
        run_monitor(full, [reference], stop)

        settings = self.settings()
        stop = threading.Event()

        def on_frame(count):
            if count == 1:
                settings.set_device_level("AA:00", 0.5)
            if count >= 2:
                stop.set()

        adapter = RecordingAdapter(on_frame)
        run_monitor(settings, [adapter], stop)
        self.assertEqual(adapter.frames[0], processing.apply_brightness(reference.frames[0], 0.75))
        self.assertEqual(adapter.frames[1], processing.apply_brightness(reference.frames[0], 0.75 * 0.5))


class MusicCapture:
    device_name = "Speakers"

    def __init__(self, stop, reads=6):
        self.stop = stop
        self.reads = reads

    def start(self):
        pass

    def wait_until_ready(self, timeout):
        return True

    def read(self, numframes, timeout):
        if self.reads <= 0:
            self.stop.set()
            return None
        self.reads -= 1
        t = np.arange(1104) / 48000.0
        tone = (0.5 * np.sin(2 * np.pi * 110 * t)).astype(np.float32)
        return np.stack([tone, tone], axis=1)

    def seconds_since_data(self):
        return 0.0

    def close(self):
        pass


class VolumeProbe:
    def linear_gain(self):
        return None


FRAME = [(200, 100, 50), (255, 0, 128), (10, 20, 30)]


def run_music(settings, adapters, stop, on_frame=None):
    queue = list(adapters)
    with (
        patch.object(sync_session.pool, "acquire", side_effect=lambda *_a, **_k: queue.pop(0)),
        patch.object(sync_session, "_audio_capture", side_effect=lambda _n: MusicCapture(stop)),
        patch.object(sync_session, "_audio_output_id", return_value=None),
        patch.object(sync_session.system_volume, "MasterVolumeProbe", VolumeProbe),
        patch.object(sync_session.audio.MusicPatternRenderer, "render", lambda self, *a, **k: list(FRAME)),
        patch.object(sync_session.SyncSession, "_wait", lambda self, seconds: None),
    ):
        sync_session.SyncSession(
            "music", DEVICES[:len(adapters)], settings, stop_event=stop, sender_factory=InlineSender,
        ).run()


class MusicSessionTests(unittest.TestCase):
    def test_each_light_gets_mode_brightness_times_its_level(self):
        settings = SyncSettings(music_smoothing=1.0, music_brightness=0.85)
        stop = threading.Event()
        first, second = RecordingAdapter(), RecordingAdapter()
        run_music(settings, [first, second], stop)
        # 100%: exactly the mode brightness, as before per-light levels.
        expected = processing.apply_brightness(FRAME, 0.85)
        self.assertEqual(first.frames[0], expected)
        self.assertEqual(first.frames[0], [(170, 85, 42), (216, 0, 108), (8, 17, 25)])
        self.assertEqual(second.frames[0], expected)

        settings.set_device_level("AA:01", 0.5)
        stop = threading.Event()
        first, second = RecordingAdapter(), RecordingAdapter()
        run_music(settings, [first, second], stop)
        self.assertEqual(first.frames[0], expected)
        self.assertEqual(second.frames[0], [(85, 42, 21), (108, 0, 54), (4, 8, 12)])

    def test_a_level_change_applies_without_a_restart(self):
        settings = SyncSettings(music_smoothing=1.0, music_brightness=0.85)
        stop = threading.Event()

        def on_frame(count):
            if count == 2:
                settings.set_device_level("AA:00", 0.2)

        adapter = RecordingAdapter(on_frame)
        run_music(settings, [adapter], stop)
        self.assertGreaterEqual(len(adapter.frames), 4)
        self.assertEqual(adapter.frames[1], processing.apply_brightness(FRAME, 0.85))
        self.assertEqual(adapter.frames[2], processing.apply_brightness(FRAME, 0.85 * 0.2))
        self.assertEqual(adapter.frames[-1], processing.apply_brightness(FRAME, 0.85 * 0.2))


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="lumisync-levels-test-")
        self.settings = QSettings(str(Path(self.temporary.name) / "ui.ini"), QSettings.Format.IniFormat)
        self.config = (dict(vars(SYNC)), dict(vars(BRIGHTNESS)))
        self.patches = [
            patch("lumisync.gui.views.modes_view.app_settings", return_value=self.settings),
            patch("lumisync.gui.controllers.sync_controller.app_settings", return_value=self.settings),
            # Keep the shared settings.json out of it in both directions.
            patch("lumisync.gui.controllers.sync_controller.devices.get_data", return_value={}),
            patch("lumisync.gui.controllers.sync_controller.devices.update_settings"),
        ]
        for item in self.patches:
            item.start()
        self.widgets = []

    def tearDown(self):
        for widget in reversed(self.widgets):
            widget.close()
            widget.deleteLater()
        self.app.processEvents()
        for item in reversed(self.patches):
            item.stop()
        vars(SYNC).update(self.config[0])
        vars(BRIGHTNESS).update(self.config[1])
        self.settings.sync()
        self.temporary.cleanup()

    def sync(self):
        from lumisync.gui.controllers.sync_controller import SyncController

        controller = SyncController()
        controller.start_monitor_sync = Mock()
        controller.start_music_sync = Mock()
        controller.stop_sync = Mock()
        return controller


class ControllerTests(_QtCase):
    def test_get_set_reset_and_signal(self):
        controller = self.sync()
        changes = []
        controller.device_sync_brightness_changed.connect(lambda key, level: changes.append((key, level)))
        self.assertEqual(controller.get_device_sync_brightness(LAMP), 1.0)
        controller.set_device_sync_brightness(LAMP, 0.4)
        controller.set_device_sync_brightness(LAMP, 0.4)  # Unchanged: no second signal.
        controller.set_device_sync_brightness("hidpp-046d-c08b", 0.7)
        self.assertEqual(changes, [("fixture-lamp", 0.4), ("hidpp-046d-c08b", 0.7)])
        self.assertEqual(controller.get_device_sync_brightness(LAMP), 0.4)
        self.assertEqual(controller.get_device_sync_brightness(MOUSE), 0.7)
        # The live object a running session reads.
        self.assertEqual(controller.sync_settings.device_level("fixture-lamp"), 0.4)
        self.assertEqual(SyncSettings.from_qsettings(self.settings).device_brightness,
                         {"fixture-lamp": 0.4, "hidpp-046d-c08b": 0.7})

        changes.clear()
        controller.reset_device_sync_brightness([LAMP, STRIP])
        self.assertEqual(changes, [("fixture-lamp", 1.0)])
        self.assertEqual(controller.get_device_sync_brightness(MOUSE), 0.7)
        controller.reset_device_sync_brightness()
        self.assertEqual(changes[-1], ("hidpp-046d-c08b", 1.0))
        self.assertEqual(SyncSettings.from_qsettings(self.settings).device_brightness, {})

    def test_a_new_controller_loads_the_saved_levels(self):
        self.sync().set_device_sync_brightness(STRIP, 0.25)
        self.assertEqual(self.sync().get_device_sync_brightness(STRIP), 0.25)


class PageTests(_QtCase):
    def devices(self, items=None, groups=None):
        items = [dict(STRIP), dict(LAMP), dict(MOUSE)] if items is None else items
        controller = SimpleNamespace(devices=items, get_groups=lambda: list(groups or []),
                                     get_selected_device=lambda: items[0] if items else None,
                                     is_device_busy=Mock(return_value=False), save_group=Mock(),
                                     set_zone_count_at=Mock(return_value=True), mark_device_output=Mock())
        for name in ("devices_discovered", "device_added", "device_removed", "device_selected",
                     "device_updated", "groups_changed"):
            setattr(controller, name, Mock())
        return controller

    def page(self, sync, devices, mode, ids=None):
        from lumisync.gui.views.modes_view import ModesView

        if ids is not None:
            self.settings.setValue(f"sync/{mode}/devices", ",".join(ids))
        page = ModesView(sync, devices, mode=mode)
        self.widgets.append(page)
        page.resize(1020, 760)
        page.show()
        self.app.processEvents()
        return page

    def test_rows_follow_the_selection_in_order(self):
        page = self.page(self.sync(), self.devices(), "monitor", ["fixture-strip", "hidpp-046d-c08b"])
        levels = page.monitor_light_levels
        self.assertEqual(levels.title_label.text(), "Brightness per light")
        self.assertFalse(levels.is_expanded())  # Collapsed by default.
        self.assertEqual(levels.row_names(), ["TV backlight", "G502 HERO"])
        self.assertEqual(levels.summary(), "All 100%")
        self.assertEqual([row.icon.filename() for row in levels.rows()],
                         ["led-strip.svg", "mouse.svg"])
        page.monitor_chips.rows()[1].switch.setChecked(True)  # Desk strip joins.
        self.assertEqual(levels.row_names(), ["TV backlight", "Desk strip", "G502 HERO"])
        for row in page.monitor_chips.rows()[:2]:
            row.switch.setChecked(False)
        self.assertEqual(levels.row_names(), ["G502 HERO"])  # One light still gets its row.
        self.assertTrue(levels.header.isEnabled())

    def test_slider_sets_the_level_and_reset_restores_it(self):
        sync = self.sync()
        page = self.page(sync, self.devices(), "monitor", ["fixture-strip", "fixture-lamp"])
        levels = page.monitor_light_levels
        levels.header.click()
        self.assertTrue(levels.is_expanded())
        self.assertFalse(levels.reset_button.isEnabled())
        lamp = levels.rows()[1]
        lamp.slider.setValue(40)
        self.assertEqual(lamp.value_label.text(), "40%")
        self.assertEqual(sync.get_device_sync_brightness(LAMP), 0.4)
        self.assertEqual(sync.sync_settings.frame_brightness("monitor", "fixture-lamp"),
                         sync.sync_settings.monitor_brightness * 0.4)
        self.assertEqual(levels.summary(), "1 adjusted")
        levels.rows()[0].slider.setValue(70)
        self.assertEqual(levels.summary(), "2 adjusted")
        self.assertTrue(levels.reset_button.isEnabled())
        levels.reset_button.click()
        self.assertEqual(levels.summary(), "All 100%")
        self.assertEqual([row.slider.value() for row in levels.rows()], [100, 100])
        self.assertEqual(sync.get_device_sync_brightness(LAMP), 1.0)
        self.assertEqual(sync.get_device_sync_brightness(STRIP), 1.0)

    def test_both_pages_show_the_same_levels(self):
        sync = self.sync()
        devices = self.devices()
        monitor = self.page(sync, devices, "monitor", ["fixture-lamp", "hidpp-046d-c08b"])
        music = self.page(sync, devices, "music", ["fixture-lamp"])
        monitor.monitor_light_levels.rows()[0].slider.setValue(35)
        self.assertEqual(music.music_light_levels.rows()[0].slider.value(), 35)
        self.assertEqual(music.music_light_levels.rows()[0].value_label.text(), "35%")
        self.assertEqual(music.music_light_levels.summary(), "1 adjusted")
        sync.reset_device_sync_brightness()
        self.assertEqual(monitor.monitor_light_levels.rows()[0].slider.value(), 100)
        self.assertEqual(music.music_light_levels.summary(), "All 100%")
        # A page built later shows the saved level.
        sync.set_device_sync_brightness(MOUSE, 0.6)
        later = self.page(sync, devices, "music", ["hidpp-046d-c08b"])
        self.assertEqual(later.music_light_levels.rows()[0].slider.value(), 60)
        self.assertEqual(later.music_light_levels.summary(), "1 adjusted")

    def test_a_group_lists_its_member_lights(self):
        group = {"name": "Desk", "devices": ["fixture-lamp", "hidpp-046d-c08b"]}
        page = self.page(self.sync(), self.devices(groups=[group]), "music", ["fixture-strip"])
        self.assertTrue(page.music_chips.apply_group("Desk"))
        self.assertEqual(page.music_light_levels.row_names(), ["Desk strip", "G502 HERO"])

    def test_no_selection_disables_the_row(self):
        page = self.page(self.sync(), self.devices(), "monitor", ["fixture-lamp"])
        page.monitor_chips.rows()[1].switch.setChecked(False)
        levels = page.monitor_light_levels
        self.assertEqual(levels.rows(), [])
        self.assertFalse(levels.header.isEnabled())

    def test_pages_work_with_a_controller_without_levels(self):
        from lumisync.gui.views.modes_view import LightBrightnessExpander, light_levels_summary

        self.assertEqual(light_levels_summary([1.0, 1.0]), "All 100%")
        self.assertEqual(light_levels_summary([0.5, 1.0, 0.999]), "1 adjusted")
        expander = LightBrightnessExpander()
        self.widgets.append(expander)
        expander.set_lights([dict(LAMP)], lambda _key: 0.3)
        self.assertEqual(expander.rows()[0].slider.value(), 30)
        self.assertEqual(expander.summary(), "1 adjusted")


if __name__ == "__main__":
    unittest.main()
