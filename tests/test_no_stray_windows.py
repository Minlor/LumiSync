"""No widget may flash up as its own window.

``setVisible(True)`` on a widget that has no parent yet turns it into a
top-level window. The layout adopts it a moment later, so on a real desktop
it shows up as a small blank window that closes again at once. Startup and
every device refresh used to do this dozens of times.

The rule checked here: building a page, card or dialog without showing it
shows nothing, and using a shown page only ever opens real windows (dialogs,
popups, tooltips), never stray child widgets.
"""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QMenu, QWidget

from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.controllers.firewall_monitor import FirewallMonitor
from lumisync.gui.controllers.sync_controller import SyncController
from lumisync.gui.dialogs.accounts_dialog import AccountsDialog
from lumisync.gui.theme import apply_theme
from lumisync.gui.utils.firewall import FirewallState
from lumisync.gui.views.devices_view import DevicesView
from lumisync.gui.views.modes_view import ModesView
from lumisync.gui.views.settings_page import SettingsPage
from lumisync.gui.widgets.device_card import DeviceCard
from lumisync.gui.widgets.device_inspector import DeviceInspector
from lumisync.gui.widgets.product_controls import ToggleSwitch
from lumisync.gui.widgets.settings_card import (
    SettingsCard,
    SettingsExpander,
    SettingsGroup,
    SettingsList,
)

ACCOUNT = {"id": "lsc", "provider": "lsc_account", "label": "LSC Smart Connect · m•••@•••.com", "remember": True}
LAMP = {"mac": "fixture-lamp", "name": "Desk lamp", "model": "H619C", "sku": "H619C", "transport": "lan",
        "ip": "192.0.2.5"}
PANEL = {"mac": "fixture-panel", "name": "Panel", "transport": "ble", "ble_address": "AA:BB:CC:DD:EE:FF",
         "matrix_size": "32x32", "segment_count": 10}
PLUG = {"transport": "tuya", "device_kind": "smart_plug", "device_id": "fixture-plug", "mac": "tuya:fixture-plug",
        "name": "Plug", "ip": "192.0.2.9", "tuya_functions": {"switch_1": {"type": "bool", "dp_id": 1}}}
STRIP = {"transport": "tuya", "device_id": "fixture-strip", "mac": "tuya:fixture-strip", "ip": "192.0.2.10",
         "name": "Strip", "local_key_ref": "fixture-key-reference", "cloud_transport": "tuya_cloud",
         "account_id": "lsc"}
DEVICES = (LAMP, PANEL, PLUG, STRIP)

# Window kinds that are meant to be separate windows.
_REAL_WINDOW_TYPES = {
    Qt.WindowType.Dialog,
    Qt.WindowType.Sheet,
    Qt.WindowType.Drawer,
    Qt.WindowType.Popup,
    Qt.WindowType.Tool,
    Qt.WindowType.ToolTip,
    Qt.WindowType.SplashScreen,
}


class _WindowSpy(QObject):
    """Records every widget that is shown as a window."""

    def __init__(self):
        super().__init__()
        self.allowed: list[QWidget] = []
        self.shown: list[str] = []
        self.strays: list[str] = []

    def allow(self, widget: QWidget) -> QWidget:
        self.allowed.append(widget)
        return widget

    def eventFilter(self, obj, event):  # noqa: N802 - Qt API
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
            description = f"{type(obj).__name__} {obj.objectName()!r} {obj.width()}x{obj.height()}"
            self.shown.append(description)
            expected = (
                any(obj is widget for widget in self.allowed)
                or isinstance(obj, (QMainWindow, QDialog, QMenu))
                or obj.windowType() in _REAL_WINDOW_TYPES
            )
            if not expected:
                self.strays.append(description)
        return False


class NoStrayWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.previous_style = self.app.styleSheet()
        apply_theme(self.app)
        self.temporary = TemporaryDirectory(prefix="lumisync-stray-test-")
        self.settings = QSettings(str(Path(self.temporary.name) / "ui.ini"), QSettings.Format.IniFormat)
        for target in ("lumisync.gui.views.modes_view.app_settings",
                       "lumisync.gui.controllers.sync_controller.app_settings"):
            patcher = patch(target, return_value=self.settings)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.spy = _WindowSpy()
        self.app.installEventFilter(self.spy)
        self.widgets: list[QWidget] = []

    def tearDown(self):
        self.app.removeEventFilter(self.spy)
        for widget in reversed(self.widgets):
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.app.setStyleSheet(self.previous_style)
        self.settings.sync()
        self.temporary.cleanup()

    # Helpers ---------------------------------------------------------------
    def keep(self, widget: QWidget) -> QWidget:
        self.widgets.append(widget)
        return widget

    def show(self, widget: QWidget, width: int = 1100, height: int = 760) -> QWidget:
        self.spy.allow(widget)
        widget.resize(width, height)
        widget.show()
        self.app.processEvents()
        return widget

    def assert_nothing_shown(self, what: str) -> None:
        self.app.processEvents()
        self.assertEqual(self.spy.shown, [], f"Building {what} showed windows before show()")

    def assert_no_strays(self) -> None:
        self.app.processEvents()
        self.assertEqual(self.spy.strays, [], "Child widgets were shown as their own windows")

    def device_controller(self) -> DeviceController:
        with patch("lumisync.gui.controllers.device_controller.devices.get_data",
                   return_value={"devices": [], "selectedDevice": 0}):
            controller = DeviceController()
        self.addCleanup(controller.shutdown)
        controller.devices = [dict(device) for device in DEVICES]
        return controller

    @staticmethod
    def device_list():
        items = [dict(PANEL), dict(LAMP)]
        controller = SimpleNamespace(devices=items, get_groups=lambda: [],
                                     get_selected_device=lambda: items[0],
                                     is_device_busy=Mock(return_value=False), save_group=Mock(),
                                     set_zone_count_at=Mock(return_value=True), mark_device_output=Mock())
        for name in ("devices_discovered", "device_added", "device_removed", "device_selected",
                     "device_updated", "groups_changed"):
            setattr(controller, name, Mock())
        return controller

    def sync_controller(self) -> SyncController:
        controller = SyncController()
        controller.start_monitor_sync = Mock()
        controller.start_music_sync = Mock()
        controller.stop_sync = Mock()
        return controller

    # Construction never shows anything --------------------------------------
    def test_settings_building_blocks_show_nothing_while_being_built(self):
        group = self.keep(SettingsGroup("General"))
        group.add_card(SettingsCard("settings", "Start with Windows", "Opens in the tray", ToggleSwitch()))
        rows = SettingsList()
        rows.add_row(SettingsCard("info", "Version", None, None, in_list=True))
        rows.add_row(SettingsCard(None, "No icon", "Description", None, in_list=True))
        group.add_card(rows)
        group.add_card(SettingsExpander("layers", "LED layout", "Zones", QWidget()))
        self.keep(SettingsGroup(""))
        self.assert_nothing_shown("settings cards")

    def test_device_cards_and_inspector_show_nothing_while_being_built(self):
        for index, device in enumerate(DEVICES):
            self.keep(DeviceCard(index, dict(device)))
            inspector = self.keep(DeviceInspector())
            inspector.set_device(index, dict(device), {}, primary=False)
        self.assert_nothing_shown("device cards")

    def test_pages_show_nothing_while_being_built(self):
        sync = self.sync_controller()
        for mode in ("monitor", "music"):
            self.keep(ModesView(sync, self.device_list(), mode=mode))
        self.keep(SettingsPage(self.settings))
        controller = self.device_controller()
        with patch.object(controller, "get_groups", return_value=[]), \
                patch.object(controller, "refresh_device_states"):
            self.keep(DevicesView(controller))
        self.assert_nothing_shown("the pages")

    # Using shown pages only opens real windows ------------------------------
    def test_device_refresh_inspector_and_account_issue_open_no_stray_windows(self):
        controller = self.device_controller()
        with patch.object(controller, "get_groups", return_value=[]), \
                patch.object(controller, "refresh_device_states"), \
                patch.object(controller, "get_accounts", return_value=[ACCOUNT]):
            view = self.show(self.keep(DevicesView(controller)))
            # A refresh that changes the list rebuilds every card.
            controller.devices = [dict(device, name=f"{device['name']} 2") for device in DEVICES]
            view._rebuild_cards()
            for index in range(len(DEVICES)):
                view._open_inspector(index)
                self.app.processEvents()
            view._close_inspector()
            controller.set_account_issue(ACCOUNT, "Sign in again.")
            self.app.processEvents()
        self.assert_no_strays()

    def test_firewall_card_and_info_bar_open_no_stray_windows(self):
        monitor = FirewallMonitor(program=r"C:\Fixture\LumiSync.exe")
        host = self.keep(QWidget())
        host.firewall_monitor = monitor
        SettingsPage(self.settings, host)
        controller = self.device_controller()
        with patch.object(controller, "get_groups", return_value=[]), \
                patch.object(controller, "refresh_device_states"):
            devices = self.keep(DevicesView(controller, monitor))
            self.assert_nothing_shown("the firewall card and InfoBar")
            self.show(host, 1020, 760)
            self.show(devices)
            for status, block_all in (("missing", False), ("public", False), ("blocked", True),
                                      ("allowed", False), ("blocked", False)):
                monitor._on_checked(FirewallState(status, "fixture", block_all), None)
                self.app.processEvents()
        self.assert_no_strays()

    def test_shown_pages_and_accounts_dialog_open_no_stray_windows(self):
        sync = self.sync_controller()
        for mode in ("monitor", "music"):
            page = self.show(self.keep(ModesView(sync, self.device_list(), mode=mode)), 1020, 760)
            expander = getattr(page, "led_layout_expander", None)
            if expander is not None:
                expander.set_expanded(True, animate=False)
                expander.set_expanded(False, animate=False)
        self.show(self.keep(SettingsPage(self.settings)), 1020, 760)
        controller = self.device_controller()
        with patch("lumisync.gui.dialogs.accounts_dialog.suggested_account_country",
                   return_value=("PL", "Windows region")), \
                patch.object(controller, "get_accounts", return_value=[ACCOUNT]):
            dialog = self.keep(AccountsDialog(controller))
            dialog.show()
            self.app.processEvents()
        self.assert_no_strays()


if __name__ == "__main__":
    unittest.main()
