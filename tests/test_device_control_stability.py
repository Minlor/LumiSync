"""Worker lifetime, command bursts and crash diagnostics regression coverage."""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.controllers.sync_controller import SyncController
from lumisync.gui.controllers.update_controller import UpdateController
from lumisync.gui.views.draw_view import DrawView
from tests.test_device_inventory_ui import inventory_fixtures


class DeviceControlStabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def controller(self):
        with patch.object(DeviceController, "_init_devices"):
            controller = DeviceController()
        controller.devices = inventory_fixtures()[:1]
        return controller

    def test_finished_status_worker_is_retained_until_the_native_thread_joins(self):
        controller = self.controller()
        thread, worker = Mock(), Mock()
        thread.isRunning.return_value = False
        thread.wait.side_effect = [False, True]
        controller.status_thread, controller.status_worker = thread, worker
        with patch("lumisync.gui.controllers.device_controller.QTimer.singleShot") as retry:
            controller._clear_status_refs()
            self.assertIs(controller.status_worker, worker)
            self.assertIs(controller.status_thread, thread)
            thread.deleteLater.assert_not_called()
            retry.call_args.args[1]()
        self.assertIsNone(controller.status_worker)
        self.assertIsNone(controller.status_thread)
        thread.deleteLater.assert_called_once_with()
        self.assertEqual(thread.wait.call_args.args, (0,))

    def test_discovery_and_bluetooth_workers_also_wait_for_native_cleanup(self):
        controller = self.controller()
        for thread_field, worker_field, cleanup in (
                ("discovery_thread", "discovery_worker", controller._clear_discovery_refs),
                ("_ble_scan_thread", "_ble_scan_worker", controller._clear_ble_scan_refs)):
            thread, worker = Mock(), Mock()
            thread.wait.side_effect = [False, True]
            setattr(controller, thread_field, thread)
            setattr(controller, worker_field, worker)
            with patch("lumisync.gui.controllers.device_controller.QTimer.singleShot") as retry:
                cleanup()
                self.assertIs(getattr(controller, worker_field), worker)
                retry.call_args.args[1]()
            self.assertIsNone(getattr(controller, thread_field))
            thread.deleteLater.assert_called_once_with()

    def test_rapid_input_keeps_only_the_latest_pending_value_for_each_control(self):
        controller = self.controller()
        key = controller._device_key(controller.devices[0])
        controller._command_tasks[key] = object()
        for value in range(100):
            controller.set_brightness_at(0, value)
            controller.turn_on_off_at(0, bool(value % 2))
        pending = controller._command_queue[key]
        self.assertEqual(len(pending), 2)
        self.assertEqual([(entry[1], entry[2]) for entry in pending],
                         [("set_brightness", (99,)), ("set_power", (True,))])
        controller.shutdown()

    def test_sync_draw_and_update_cleanup_retain_workers_until_native_join(self):
        sync = SyncController()
        update = UpdateController()
        device = self.controller()
        device.get_groups = Mock(return_value=[])
        draw = DrawView(device)
        for owner, thread_field, worker_field, cleanup, timer_target in (
                (sync, "sync_thread", "sync_worker", sync._on_sync_thread_finished,
                 "lumisync.gui.controllers.sync_controller.QTimer.singleShot"),
                (update, "check_thread", "check_worker", update._clear_refs,
                 "lumisync.gui.controllers.update_controller.QTimer.singleShot"),
                (draw, "_thread", "_worker", draw._clear_thread_refs,
                 "lumisync.gui.views.draw_view.QTimer.singleShot")):
            thread, worker = Mock(), Mock()
            thread.wait.side_effect = [False, True]
            setattr(owner, thread_field, thread)
            setattr(owner, worker_field, worker)
            with patch(timer_target) as retry:
                cleanup()
                self.assertIs(getattr(owner, worker_field), worker)
                thread.deleteLater.assert_not_called()
                retry.call_args.args[1]()
            self.assertIsNone(getattr(owner, thread_field))
            self.assertIsNone(getattr(owner, worker_field))
            thread.deleteLater.assert_called_once_with()
        draw.close()
        draw.deleteLater()

    def test_confirmation_timers_coalesce_and_resolve_the_current_device_identity(self):
        controller = self.controller()
        first = controller.devices[0]
        controller.devices.append({**first, "mac": "second-device"})
        for _ in range(100):
            controller._schedule_status_refresh(0)
        self.assertEqual(len(controller._confirmation_timers), 1)
        key, timer = next(iter(controller._confirmation_timers.items()))
        controller.devices.reverse()
        with patch.object(controller, "refresh_device_state_at") as refresh:
            controller._confirm_device_state(key)
            refresh.assert_called_once_with(1)
            refresh.reset_mock()
            controller.devices.pop(1)
            controller._confirm_device_state(key)
            refresh.assert_not_called()
        self.assertFalse(controller._confirmation_timers)
        controller.shutdown()

    def test_old_status_reply_cannot_replace_a_more_recent_command(self):
        controller = self.controller()
        before = time.time() - 1
        controller._record_command_state(0, {"brightness": 73, "power_on": False})
        controller._merge_device_state(0, {"query_started_at": before, "brightness": 1, "power_on": True})
        self.assertEqual(controller.get_device_state_at(0)["brightness"], 73)
        self.assertFalse(controller.get_device_state_at(0)["power_on"])
        controller._merge_device_state(0, {"query_started_at": time.time() + 1, "brightness": 74})
        self.assertEqual(controller.get_device_state_at(0)["brightness"], 74)

    def test_shutdown_cancels_pending_confirmation_requests(self):
        controller = self.controller()
        controller._schedule_status_refresh(0)
        timer = next(iter(controller._confirmation_timers.values()))
        with patch.object(controller, "refresh_device_state_at") as refresh:
            self.assertTrue(controller.shutdown())
            self.assertFalse(timer.isActive())
            loop = QEventLoop()
            QTimer.singleShot(30, loop.quit)
            loop.exec()
            refresh.assert_not_called()
        self.assertFalse(controller._confirmation_timers)

    def test_real_qt_controls_survive_1200_rapid_interactions_and_confirm_final_state(self):
        helper = Path(__file__).parent / "helpers" / "device_controls_stress.py"
        try:
            result = subprocess.run([sys.executable, "-X", "faulthandler", str(helper)],
                                    capture_output=True, text=True, timeout=35)
        except subprocess.TimeoutExpired as error:
            def output(value):
                return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
            self.fail("Rapid-control subprocess stalled.\n" + output(error.stdout) + output(error.stderr))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"pass": true', result.stdout)
        self.assertIn('"brightness": 73', result.stdout)

    def test_native_fault_output_is_written_and_qt_messages_mask_credentials(self):
        source = """
import faulthandler, io, logging, sys
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import qWarning
from lumisync.gui import crash_reporting
output = io.StringIO()
logger = logging.getLogger('fixture-crash-check')
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler(output))
with patch.object(crash_reporting, 'get_logs_directory', return_value=sys.argv[1]):
    crash_reporting.install_crash_reporting(logger)
    crash_reporting.install_crash_reporting(logger)
qWarning('fixture@example.test token=fixture-private-value')
faulthandler.dump_traceback(file=crash_reporting._fault_file)
crash_reporting._fault_file.flush()
assert 'fixture@example.test' not in output.getvalue()
assert 'fixture-private-value' not in output.getvalue()
files = list(Path(sys.argv[1]).glob('lumisync_fault_*.log'))
assert len(files) == 1
assert 'Current thread' in files[0].read_text()
print('PASS: persistent native stacks and masked Qt diagnostics')
"""
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, "-c", source, temporary],
                                    capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
