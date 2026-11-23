"""Fluent theme: the rail, painted controls, app-wide combo flyouts, the
Lights list, the Settings page and toasts."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, QSettings, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QStyle,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from lumisync.config.options import BRIGHTNESS, SYNC
from lumisync.gui.controllers.sync_controller import SyncController
from lumisync.gui.theme import apply_theme, qcolor
from lumisync.gui.theme.fluent_style import COMBO_POPUP_NAME, FluentComboDelegate
from lumisync.gui.views.modes_view import ModesView
from lumisync.gui.views.settings_page import SettingsPage
from lumisync.gui.widgets.device_chip import CUSTOM_SELECTION, DeviceChipStrip, device_id
from lumisync.gui.widgets.navigation_shell import (
    ICON_ONLY_RAIL_WIDTH,
    NavigationShell,
    NavRailItemDelegate,
    RAIL_ITEM_HEIGHT,
)
from lumisync.gui.widgets.product_controls import ProductComboBox, ProductSlider, ToggleSwitch
from lumisync.gui.widgets.toast import KIND_ERROR, KIND_SUCCESS, KIND_WARNING, MAX_TOASTS, ToastManager

LIGHTS = [
    {"name": "Desk strip", "model": "H619C", "transport": "lan", "ip": "192.0.2.10",
     "mac": "AA:00:00:00:00:01", "segment_count_override": 10},
    {"name": "Shelf strip", "model": "H619C", "transport": "lan", "ip": "192.0.2.11",
     "mac": "AA:00:00:00:00:02", "segment_count_override": 8},
    {"model": "Fixture panel", "transport": "ble", "ble_address": "AA:BB:CC:DD:EE:FF",
     "mac": "AA:BB:CC:DD:EE:FF", "matrix_size": "32x32"},
]


def close_enough(first: QColor, second: QColor, tolerance: int = 24) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(first.getRgb()[:3], second.getRgb()[:3]))


class FluentThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.previous_style = self.app.styleSheet()
        apply_theme(self.app)
        self.widgets = []
        self.temporary = TemporaryDirectory(prefix="lumisync-fluent-test-")
        self.settings = QSettings(str(Path(self.temporary.name) / "ui.ini"), QSettings.Format.IniFormat)
        self.config = (dict(vars(SYNC)), dict(vars(BRIGHTNESS)))

    def tearDown(self):
        for widget in reversed(self.widgets):
            widget.close()
            widget.deleteLater()
        self.app.processEvents()
        vars(SYNC).update(self.config[0])
        vars(BRIGHTNESS).update(self.config[1])
        self.app.setStyleSheet(self.previous_style)
        self.settings.sync()
        self.temporary.cleanup()

    def keep(self, widget, width=None, height=None):
        self.widgets.append(widget)
        if width:
            widget.resize(width, height or widget.height())
        return widget

    # ---------------------------------------------------------------- rail

    def test_rail_is_icon_only_and_keeps_names_for_tooltips_and_accessibility(self):
        shell = self.keep(NavigationShell(icon_only=True), 900, 600)
        for key, title, label in (("devices", "Devices", None), ("monitor", "Monitor Sync", "Monitor"),
                                  ("settings", "Settings", None)):
            shell.add_page(key=key, title=title, icon=QIcon(), widget=QWidget(), label=label,
                           bottom=key == "settings")
            shell.set_page_svg(key, "screen.svg")
        shell.show()
        self.app.processEvents()
        self.assertEqual(shell.sidebar.width(), ICON_ONLY_RAIL_WIDTH)
        self.assertLessEqual(ICON_ONLY_RAIL_WIDTH, 80)
        for lst in (shell.nav_list, shell.bottom_nav_list):
            for row in range(lst.count()):
                item = lst.item(row)
                self.assertEqual(item.text(), "")
                self.assertTrue(item.toolTip())
                self.assertEqual(item.data(Qt.ItemDataRole.AccessibleTextRole), item.toolTip())
        self.assertEqual(shell.nav_list.item(1).data(NavRailItemDelegate.LABEL_ROLE), "Monitor")

        # The delegate paints a highlight tab and an icon, never a label.
        delegate = shell.nav_list.itemDelegate()
        for state in (QStyle.StateFlag.State_None, QStyle.StateFlag.State_Selected,
                      QStyle.StateFlag.State_MouseOver):
            option = QStyleOptionViewItem()
            option.rect = QRect(0, 0, ICON_ONLY_RAIL_WIDTH, RAIL_ITEM_HEIGHT)
            option.state = state | QStyle.StateFlag.State_Enabled
            painter = MagicMock()
            delegate.paint(painter, option, shell.nav_list.model().index(1, 0))
            painter.drawText.assert_not_called()
            painter.drawPixmap.assert_called()

        shell.set_page_badge("settings", True)
        shell.set_current_by_key("settings")
        self.app.processEvents()
        self.assertFalse(shell.grab().isNull())

    # ------------------------------------------------------------- controls

    def test_slider_paints_every_state_and_jumps_to_the_click(self):
        slider = self.keep(ProductSlider(Qt.Orientation.Horizontal), 240, 32)
        slider.setRange(0, 100)
        slider.setValue(100)
        slider.show()
        self.app.processEvents()
        image = slider.grab().toImage()
        self.assertTrue(close_enough(image.pixelColor(60, 16), qcolor("fl_accent")))
        for enabled, inner in ((True, 7.0), (True, 5.0), (False, 6.0)):
            slider.setEnabled(enabled)
            slider.set_inner(inner)
            self.assertFalse(slider.grab().isNull())
        slider.setEnabled(True)
        QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(2, 16))
        self.assertEqual(slider.value(), 0)
        QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=QPoint(120, 16))
        self.assertTrue(slider.isSliderDown())
        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=QPoint(238, 16))
        self.assertFalse(slider.isSliderDown())
        self.assertEqual(slider.value(), 100)
        slider.setFocus()
        QTest.keyClick(slider, Qt.Key.Key_Home)
        self.assertEqual(slider.value(), 0)

    def test_toggle_switch_paints_every_state_and_keeps_its_api(self):
        bare = self.keep(ToggleSwitch())
        labelled = self.keep(ToggleSwitch("Wrap a long preference label beside the switch"), 300, 40)
        for switch in (bare, labelled):
            switch.show()
            for enabled in (True, False):
                for checked in (False, True):
                    switch.setEnabled(enabled)
                    switch.setChecked(checked)
                    switch.set_position(1.0 if checked else 0.0)
                    switch.set_hover(1.0)
                    self.assertFalse(switch.grab().isNull())
        self.assertEqual(bare.sizeHint().height(), 32)
        self.assertLess(bare.sizeHint().width(), 60)
        self.assertGreater(labelled.heightForWidth(140), labelled.heightForWidth(600))
        bare.setEnabled(True)
        bare.setChecked(True)
        bare.set_position(1.0)
        image = bare.grab().toImage()
        self.assertTrue(close_enough(image.pixelColor(8, bare.height() // 2), qcolor("fl_accent")))
        toggled = Mock()
        bare.toggled.connect(toggled)
        QTest.mouseClick(bare, Qt.MouseButton.LeftButton)
        toggled.assert_called_once_with(False)
        self.assertEqual(bare.accessibleDescription(), "Off")

    # ---------------------------------------------------------------- combo

    def test_every_combo_gets_the_fluent_flyout_wherever_it_was_built(self):
        plain = self.keep(QComboBox())  # as if built in a file the theme does not own
        plain.addItems(["Display 1 · 2560×1440 (primary)", "Display 2 · 1920×1080"])
        product = self.keep(ProductComboBox())
        product.addItems(["Acrylic", "Mica", "Solid"])
        for combo in (plain, product):
            combo.ensurePolished()
            self.assertIsInstance(combo.itemDelegate(), FluentComboDelegate)
            container = combo.view().parentWidget()
            self.assertEqual(container.objectName(), COMBO_POPUP_NAME)
            self.assertTrue(container.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
            self.assertTrue(container.windowFlags() & Qt.WindowType.FramelessWindowHint)
            option = QStyleOptionViewItem()
            option.fontMetrics = combo.fontMetrics()
            self.assertEqual(combo.itemDelegate().sizeHint(option, combo.model().index(0, 0)).height(), 36)
        self.assertEqual(product.height() if product.isVisible() else product.minimumHeight(), 32)

        # A deliberately installed delegate is never replaced.
        custom = self.keep(QComboBox())
        from PySide6.QtWidgets import QStyledItemDelegate
        mine = QStyledItemDelegate(custom)
        custom.view()
        custom.setItemDelegate(mine)
        custom.ensurePolished()
        self.assertIs(custom.itemDelegate(), mine)

        # Editable combos keep working.
        editable = self.keep(QComboBox())
        editable.setEditable(True)
        editable.addItems(["one", "two"])
        editable.show()
        self.app.processEvents()
        self.assertIsInstance(editable.itemDelegate(), FluentComboDelegate)
        editable.lineEdit().setFocus()
        editable.lineEdit().selectAll()
        QTest.keyClicks(editable.lineEdit(), "three")
        self.assertEqual(editable.currentText(), "three")

        # The popup paints its rounded flyout and rows without errors.
        plain.show()
        plain.showPopup()
        self.app.processEvents()
        container = plain.view().parentWidget()
        self.assertTrue(container.isVisible())
        self.assertFalse(container.grab().isNull())
        plain.hidePopup()

    # --------------------------------------------------------------- lights

    def test_lights_rows_toggle_selection_and_the_group_row_applies_members(self):
        strip = self.keep(DeviceChipStrip("Lights"), 700, 400)
        ids = [device_id(device) for device in LIGHTS]
        strip.set_devices(LIGHTS, default_ids=[ids[0]])
        strip.set_groups([{"name": "Shelves", "devices": [ids[1], ids[2]]},
                          {"name": "Ghost", "devices": ["not-a-device"]}])
        strip.show()
        self.app.processEvents()
        self.assertEqual(strip.row_names(), ["Desk strip", "Shelf strip", "Fixture panel"])
        self.assertIn("LED strip", strip.rows()[0].description_label.text())
        self.assertIn("10 zones", strip.rows()[0].description_label.text())
        self.assertIn("Bluetooth", strip.rows()[2].description_label.text())
        # Only groups with members that still exist are offered.
        combo = strip.group_combo
        self.assertTrue(combo.isVisible())
        self.assertEqual([combo.itemText(i) for i in range(combo.count())], [CUSTOM_SELECTION, "Shelves"])
        self.assertEqual(combo.currentText(), CUSTOM_SELECTION)

        changes = []
        strip.selection_changed.connect(changes.append)
        QTest.mouseClick(strip.toggles()[1], Qt.MouseButton.LeftButton)
        self.assertEqual(changes[-1], [ids[0], ids[1]])
        self.assertEqual(strip.selected_ids(), [ids[0], ids[1]])

        # Applying the group sets exactly its members on the switches.
        combo.activated.emit(combo.findText("Shelves"))
        self.assertEqual(changes[-1], [ids[1], ids[2]])
        self.assertEqual([switch.isChecked() for switch in strip.toggles()], [False, True, True])
        self.assertEqual(combo.currentText(), "Shelves")

        # A manual change returns the picker to "Custom selection".
        QTest.mouseClick(strip.toggles()[0], Qt.MouseButton.LeftButton)
        self.assertEqual(combo.currentText(), CUSTOM_SELECTION)
        self.assertEqual(strip.selected_ids(), ids)

        # Clicking anywhere on a row flips its switch, like a WinUI card.
        row = strip.rows()[2]
        QTest.mouseClick(row, Qt.MouseButton.LeftButton, pos=QPoint(60, row.height() // 2))
        self.assertFalse(strip.toggles()[2].isChecked())

        # Without groups there is no group row.
        strip.set_groups([])
        self.assertFalse(strip.group_combo.isVisible())

    def test_sync_page_refusals_reach_the_window_as_notices(self):
        host = self.keep(QWidget(), 1020, 760)
        host.show_notice = Mock()
        layout = QVBoxLayout(host)
        cloud = {"name": "Fixture desk lamp", "model": "H6008", "transport": "govee_account",
                 "device_id": "fixture-lamp", "account_id": "fixture"}
        controller = SimpleNamespace(devices=[dict(LIGHTS[0])], get_groups=lambda: [],
                                     get_selected_device=lambda: None, is_device_busy=Mock(return_value=False))
        for name in ("devices_discovered", "device_added", "device_removed", "device_selected",
                     "device_updated", "groups_changed"):
            setattr(controller, name, Mock())
        sync = SyncController()
        with patch("lumisync.gui.views.modes_view.app_settings", return_value=self.settings), \
                patch("lumisync.gui.controllers.sync_controller.app_settings", return_value=self.settings):
            page = ModesView(sync, controller, mode="music")
            layout.addWidget(page)
            host.show()
            self.app.processEvents()
            status = Mock()
            sync.status_updated.connect(status)
            self.assertFalse(page._can_start_sync([cloud]))
        host.show_notice.assert_called_once()
        message, kind = host.show_notice.call_args.args
        self.assertIn("can only be reached through the cloud", message)
        self.assertEqual(kind, "error")
        status.assert_called_once_with(message)

    # -------------------------------------------------------------- settings

    def test_settings_page_exposes_update_controls_without_a_section_list(self):
        with patch("lumisync.gui.utils.autostart.is_enabled", return_value=False):
            page = self.keep(SettingsPage(self.settings), 900, 640)
        page.show()
        self.app.processEvents()
        self.assertFalse(hasattr(page, "section_nav"))
        self.assertFalse(hasattr(page, "section_stack"))
        for name in ("install_update_button", "check_updates_button", "open_release_button",
                     "auto_update_toggle", "update_status_label", "open_logs_button",
                     "repository_button", "account_setup_button", "tray_check",
                     "autostart_check", "window_material_combo"):
            self.assertTrue(hasattr(page, name), name)
        # The status bar option is gone: the window has no status bar.
        self.assertFalse(hasattr(page, "statusbar_check"))
        titles = [label.text() for label in page.findChildren(QLabel)]
        self.assertNotIn("Status bar", titles)
        self.assertIsInstance(page.auto_update_toggle, ToggleSwitch)
        self.assertIsInstance(page.tray_check, ToggleSwitch)
        self.assertTrue(page.install_update_button.isHidden())
        page._on_update_downloaded("fixture.exe")
        self.assertFalse(page.install_update_button.isHidden())
        self.assertIn("Restart", page.install_update_button.text())
        self.assertIn("downloaded", page.update_status_label.text())
        page.set_window_material_value("mica")
        self.assertEqual(page.window_material_combo.currentData(), "mica")
        self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)

    # ---------------------------------------------------------------- toasts

    def test_toasts_use_severities_stack_three_and_coalesce(self):
        window = self.keep(QWidget(), 900, 640)
        window.show()
        manager = ToastManager(window)
        manager.show("Searching for lights")
        manager.success("Imported 4 devices")
        manager.warning("Battletron: command sent via the cloud")
        manager.error("Plug did not answer")
        visible = [toast for toast in manager._toasts if not toast._closing]
        self.assertEqual(len(visible), MAX_TOASTS)
        self.assertEqual([toast.severity for toast in visible], ["success", "caution", "critical"])
        self.assertEqual([toast.property("kind") for toast in visible], [KIND_SUCCESS, KIND_WARNING, KIND_ERROR])
        manager.error("Plug did not answer")
        self.assertEqual(len([t for t in manager._toasts if not t._closing]), MAX_TOASTS)
        self.app.processEvents()
        for toast in visible:
            self.assertFalse(toast.grab().isNull())
            card = toast.card_rect()
            self.assertLessEqual(card.width(), 360)
            self.assertLessEqual(toast.geometry().right() - (toast.width() - card.right()), window.width() - 16 + 1)


if __name__ == "__main__":
    unittest.main()
