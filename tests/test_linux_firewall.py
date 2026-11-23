"""Linux firewall hint: detect ufw/firewalld without root, never change them.

``systemctl`` is faked; nothing here runs a real command or touches a firewall.
"""

import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from lumisync.config.options import CONNECTION
from lumisync.drivers.tuya_presence import PRESENCE_PORT
from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.utils import linux_firewall
from lumisync.gui.views import devices_view as devices_view_module
from lumisync.gui.views.devices_view import DevicesView


def runner(output):
    calls = []

    def run(arguments, timeout):
        calls.append((list(arguments), timeout))
        if isinstance(output, BaseException):
            raise output
        return output

    run.calls = calls  # type: ignore[attr-defined]
    return run


class DetectionTests(unittest.TestCase):
    def detect(self, output, *, ufw_enabled=True, platform="linux"):
        run = runner(output)
        return linux_firewall.detect_active_firewall(
            run, platform=platform, ufw_enabled=lambda: ufw_enabled
        ), run.calls  # type: ignore[attr-defined]

    def test_one_systemctl_call_with_a_short_timeout(self):
        name, calls = self.detect("active\ninactive\n")
        self.assertEqual(name, "ufw")
        self.assertEqual(calls, [(["is-active", "ufw", "firewalld"], linux_firewall.SYSTEMCTL_TIMEOUT_SECONDS)])
        self.assertLessEqual(linux_firewall.SYSTEMCTL_TIMEOUT_SECONDS, 3)

    def test_firewalld(self):
        self.assertEqual(self.detect("inactive\nactive\n")[0], "firewalld")

    def test_nothing_active(self):
        self.assertIsNone(self.detect("inactive\ninactive\n")[0])
        self.assertIsNone(self.detect("unknown\nfailed\n")[0])

    def test_ufw_service_up_but_ufw_disabled_counts_as_off(self):
        # Ubuntu keeps ufw.service "active" after `ufw disable`.
        self.assertIsNone(self.detect("active\ninactive\n", ufw_enabled=False)[0])
        self.assertEqual(self.detect("active\nactive\n", ufw_enabled=False)[0], "firewalld")
        # An unreadable ufw.conf trusts systemctl.
        self.assertEqual(self.detect("active\ninactive\n", ufw_enabled=None)[0], "ufw")

    def test_missing_or_failing_systemctl_is_unknown(self):
        self.assertIsNone(self.detect(None)[0])
        self.assertIsNone(self.detect("")[0])
        self.assertIsNone(self.detect(OSError("boom"))[0])

    def test_other_platforms_never_run_systemctl(self):
        for platform in ("win32", "darwin"):
            name, calls = self.detect("active\nactive\n", platform=platform)
            self.assertIsNone(name)
            self.assertEqual(calls, [])

    def test_run_systemctl_handles_absence_and_timeouts(self):
        with patch.object(linux_firewall.shutil, "which", return_value=None):
            self.assertIsNone(linux_firewall._run_systemctl(["is-active", "ufw"], 1.0))
        with patch.object(linux_firewall.shutil, "which", return_value="/usr/bin/systemctl"), \
                patch.object(linux_firewall.subprocess, "run",
                             side_effect=subprocess.TimeoutExpired("systemctl", 1.0)):
            self.assertIsNone(linux_firewall._run_systemctl(["is-active", "ufw"], 1.0))
        completed = subprocess.CompletedProcess([], 3, stdout="active\ninactive\n", stderr="")
        with patch.object(linux_firewall.shutil, "which", return_value="/usr/bin/systemctl"), \
                patch.object(linux_firewall.subprocess, "run", return_value=completed) as run:
            self.assertEqual(linux_firewall._run_systemctl(["is-active", "ufw"], 1.0), "active\ninactive\n")
        self.assertEqual(run.call_args.kwargs["timeout"], 1.0)
        self.assertNotIn("sudo", run.call_args.args[0])

    def test_ufw_conf_parsing(self):
        with TemporaryDirectory() as folder:
            conf = Path(folder) / "ufw.conf"
            conf.write_text("# comment\nENABLED=yes\nLOGLEVEL=low\n")
            self.assertTrue(linux_firewall._ufw_enabled_in_config(str(conf)))
            conf.write_text("ENABLED=no\n")
            self.assertFalse(linux_firewall._ufw_enabled_in_config(str(conf)))
            self.assertIsNone(linux_firewall._ufw_enabled_in_config(str(Path(folder) / "missing")))

    def test_detect_is_cached(self):
        linux_firewall.reset_cache()
        self.addCleanup(linux_firewall.reset_cache)
        run = runner("inactive\nactive\n")
        with patch.object(linux_firewall.sys, "platform", "linux"):
            self.assertFalse(linux_firewall.is_detected())
            self.assertEqual(linux_firewall.detect(run), "firewalld")
            self.assertEqual(linux_firewall.detect(run), "firewalld")
        self.assertEqual(len(run.calls), 1)  # type: ignore[attr-defined]
        self.assertEqual(linux_firewall.cached(), "firewalld")


class CommandTests(unittest.TestCase):
    def test_ports_come_from_the_code_constants(self):
        self.assertEqual(
            linux_firewall.listen_ports(),
            tuple(sorted({CONNECTION.default.listen_port, PRESENCE_PORT})),
        )
        self.assertEqual(linux_firewall.listen_ports(), (4002, 6667))

    def test_ufw_commands(self):
        self.assertEqual(
            linux_firewall.allow_commands("ufw"),
            ["sudo ufw allow 4002/udp comment 'LumiSync'", "sudo ufw allow 6667/udp comment 'LumiSync'"],
        )

    def test_firewalld_commands(self):
        self.assertEqual(
            linux_firewall.allow_commands("firewalld"),
            ["sudo firewall-cmd --permanent --add-port=4002/udp --add-port=6667/udp"
             " && sudo firewall-cmd --reload"],
        )
        self.assertEqual(linux_firewall.allow_commands("iptables"), [])
        self.assertEqual(linux_firewall.allow_commands_text("nftables"), "")


class DevicesViewLinuxFirewallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        linux_firewall.reset_cache()
        self.addCleanup(linux_firewall.reset_cache)
        self.folder = TemporaryDirectory(prefix="lumisync-linux-fw-")
        self.addCleanup(self.folder.cleanup)
        self.settings_path = str(Path(self.folder.name) / "ui.ini")
        patcher = patch.object(
            devices_view_module, "app_settings",
            side_effect=lambda: QSettings(self.settings_path, QSettings.Format.IniFormat),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tasks = []
        patcher = patch.object(
            devices_view_module, "start_task",
            side_effect=lambda action, callback: self.tasks.append((action, callback)) or Mock(),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        with patch("lumisync.gui.controllers.device_controller.devices.get_data",
                   return_value={"devices": [], "selectedDevice": 0}):
            self.controller = DeviceController()
        self.addCleanup(self.controller.shutdown)
        self.notices = []
        self.controller.notice.connect(lambda message, kind: self.notices.append((message, kind)))
        self.views = []

    def tearDown(self):
        for view in self.views:
            view.close()
            view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def view(self, platform="linux", detected="ufw") -> DevicesView:
        with patch.object(self.controller, "get_groups", return_value=[]), \
                patch.object(self.controller, "refresh_device_states"), \
                patch.object(devices_view_module.sys, "platform", platform):
            view = DevicesView(self.controller)
        self.views.append(view)
        view.resize(1100, 760)
        view.show()
        self.app.processEvents()
        # Finish the background detection with a faked systemctl answer.
        for action, callback in self.tasks:
            self.assertIs(action, linux_firewall.detect)
            states = {"ufw": "active\ninactive\n", "firewalld": "inactive\nactive\n", None: "inactive\ninactive\n"}
            with patch.object(linux_firewall.sys, "platform", "linux"), \
                    patch.object(linux_firewall, "_ufw_enabled_in_config", return_value=True):
                callback(linux_firewall.detect(runner(states[detected])), None)
        self.tasks.clear()
        return view

    def search(self, view, *, available=True, lan=0, tuya=0):
        view._on_search_finished({
            "lan": {"available": available, "found": lan, "error": None},
            "tuya": {"available": True, "found": tuya, "error": None},
            "bluetooth": {"available": True, "found": 0},
        })

    def test_shows_after_an_empty_search_with_an_active_firewall(self):
        view = self.view()
        bar = view.linux_firewall_info_bar
        self.assertTrue(bar.isHidden(), "nothing before a search")
        self.search(view)
        self.assertFalse(bar.isHidden())
        self.assertFalse(view.info_host.isHidden())
        self.assertEqual(bar.title(), devices_view_module.LINUX_FIREWALL_TITLE)
        self.assertIn("ufw is active", bar.message())
        self.assertEqual(bar.action_button.text(), "Copy commands")
        self.assertTrue(view.firewall_info_bar.isHidden(), "the Windows bar stays out of it")

    def test_hidden_when_devices_answer_or_the_network_is_down(self):
        view = self.view()
        bar = view.linux_firewall_info_bar
        self.search(view, lan=1)
        self.assertTrue(bar.isHidden())
        self.search(view, tuya=2)
        self.assertTrue(bar.isHidden())
        self.search(view, available=False)
        self.assertTrue(bar.isHidden())
        self.assertFalse(view.network_info_bar.isHidden())

    def test_hidden_without_an_active_firewall_or_off_linux(self):
        view = self.view(detected=None)
        self.search(view)
        self.assertTrue(view.linux_firewall_info_bar.isHidden())
        linux_firewall.reset_cache()
        windows = self.view(platform="win32")
        self.assertEqual(self.tasks, [])
        self.search(windows)
        self.assertTrue(windows.linux_firewall_info_bar.isHidden())

    def test_copy_puts_the_exact_commands_on_the_clipboard(self):
        view = self.view(detected="firewalld")
        self.search(view)
        view.linux_firewall_info_bar.action_button.click()
        self.assertEqual(
            QGuiApplication.clipboard().text(),
            linux_firewall.allow_commands_text("firewalld"),
        )
        self.assertEqual(len(self.notices), 1)
        self.assertEqual(self.notices[0][1], "info")

    def test_dismiss_is_remembered_across_pages(self):
        view = self.view()
        self.search(view)
        view.linux_firewall_info_bar._close()
        self.assertTrue(view.linux_firewall_info_bar.isHidden())
        self.search(view)
        self.assertTrue(view.linux_firewall_info_bar.isHidden())
        stored = QSettings(self.settings_path, QSettings.Format.IniFormat)
        self.assertEqual(str(stored.value(devices_view_module.LINUX_FIREWALL_DISMISSED_KEY)).lower(), "true")
        again = self.view()
        self.search(again)
        self.assertTrue(again.linux_firewall_info_bar.isHidden())

    def test_detection_runs_off_the_gui_thread_once(self):
        with patch.object(self.controller, "get_groups", return_value=[]), \
                patch.object(self.controller, "refresh_device_states"), \
                patch.object(devices_view_module.sys, "platform", "linux"):
            view = DevicesView(self.controller)
            self.views.append(view)
            pc = DevicesView(self.controller, scope="pc")
            self.views.append(pc)
        self.assertEqual([action for action, _callback in self.tasks], [linux_firewall.detect])


if __name__ == "__main__":
    unittest.main()
