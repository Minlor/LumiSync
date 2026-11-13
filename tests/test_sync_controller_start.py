"""SyncController start-up: capture support, display names, "started" timing."""

import os
import re
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from lumisync.gui.controllers import sync_controller
from lumisync.gui.controllers.sync_controller import SyncController, screen_name_for_index

BLE_PANEL = {"transport": "ble", "type": "idotmatrix", "ble_address": "AA:BB:CC:DD:EE:FF", "name": "Panel"}


class FakeSettings:
    def value(self, _key, default=None):
        return default

    def setValue(self, _key, _value):
        pass


class FakeWorker(QObject):
    status_updated = Signal(str)
    error_occurred = Signal(str)
    streaming_started = Signal(int)
    finished = Signal()

    def __init__(self, server, devices, stop_event, settings, controller=None):
        super().__init__()
        self.devices = list(devices)

    def run(self):
        self.streaming_started.emit(len(self.devices))
        self.finished.emit()


class SyncControllerStartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_controller(self):
        controller = SyncController()
        controller._settings = FakeSettings()
        controller.flush_sync_settings = lambda: None
        messages = []
        controller.status_updated.connect(messages.append)
        return controller, messages

    def pump(self, condition, timeout=5.0):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        return condition()

    def test_unsupported_capture_refuses_before_any_device_starts(self):
        controller, messages = self.make_controller()
        started = []
        controller.sync_started.connect(started.append)

        with (
            patch.object(sync_controller.monitor, "capture_supported", return_value=(False, "Wayland has no screen grab")),
            patch.object(sync_controller, "MonitorSyncWorker") as worker_cls,
        ):
            controller.start_sync("monitor", [BLE_PANEL])
            self.assertEqual(controller.monitor_capture_supported(), (False, "Wayland has no screen grab"))

        self.assertEqual(messages, ["Wayland has no screen grab"])
        self.assertEqual(started, [])
        self.assertIsNone(controller.sync_thread)
        worker_cls.assert_not_called()

    def test_started_is_reported_once_the_devices_opened(self):
        controller, messages = self.make_controller()

        with (
            patch.object(sync_controller.monitor, "capture_supported", return_value=(True, "")),
            patch.object(sync_controller, "MonitorSyncWorker", FakeWorker),
        ):
            controller.start_sync("monitor", [BLE_PANEL])
            self.assertFalse(any("sync started" in message for message in messages))
            self.assertTrue(self.pump(lambda: any("sync started" in message for message in messages)))
            self.assertTrue(self.pump(lambda: controller.sync_thread is None))

        started = [message for message in messages if "sync started" in message]
        self.assertEqual(len(started), 1)
        self.assertRegex(started[0], r"^Monitor sync started with 1 device at \d+% brightness$")

    def test_partial_start_names_how_many_devices_opened(self):
        controller, messages = self.make_controller()
        controller.current_sync_mode = "music"
        controller.active_devices = [dict(BLE_PANEL), dict(BLE_PANEL, ble_address="11:22:33:44:55:66")]

        controller._on_streaming_started(1)

        self.assertTrue(re.match(r"^Music sync started with 1 of 2 devices at \d+% brightness$", messages[0]))

    def test_display_index_is_resolved_to_the_windows_device_name(self):
        controller, _messages = self.make_controller()
        screens = QGuiApplication.screens()
        self.assertTrue(screens)
        controller.sync_settings.monitor_display = 0

        # Qt 6 names Windows screens after the monitor model; capture needs the
        # GDI device name, which dxcam reports for each output.
        with patch.object(sync_controller, "windows_display_device", return_value="\\\\.\\DISPLAY7"):
            controller._resolve_monitor_display_name()
        self.assertEqual(controller.sync_settings.monitor_display_name, "\\\\.\\DISPLAY7")

        with patch.object(sync_controller, "windows_display_device", return_value=""):
            controller._resolve_monitor_display_name()
        self.assertEqual(controller.sync_settings.monitor_display_name, screens[0].name())

        self.assertEqual(screen_name_for_index(len(screens) + 3), "")
        self.assertNotIn("monitor_display_name", controller.sync_settings.to_dict())


if __name__ == "__main__":
    unittest.main()
