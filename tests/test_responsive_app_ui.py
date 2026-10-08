"""Exercise desktop UI actions with disposable settings and mocked hardware I/O."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton, QScrollArea, QWidget

from lumisync.config.options import BRIGHTNESS, SYNC
from lumisync.gui.controllers.sync_controller import SyncController, SYNC_SETTINGS_KEYS
from lumisync.gui.dialogs.add_device_dialog import AddDeviceDialog
from lumisync.gui.dialogs.panel_tools_dialog import PanelToolsDialog
from lumisync.gui.theme import apply_theme
from lumisync.gui.views.draw_view import DrawView
from lumisync.gui.views.devices_view import DevicesView
from lumisync.gui.views.modes_view import ModesView
from lumisync.gui.views.settings_page import SettingsPage
from lumisync.gui.widgets.led_mapping_widget import LedMappingWidget, zone_test_color, zone_text_color
from lumisync.gui.widgets.navigation_shell import NavigationShell
from lumisync.gui.widgets.pixel_canvas import PixelCanvas
from lumisync.gui.widgets.product_controls import ToggleSwitch
from lumisync.gui.widgets.toast import ToastManager
from lumisync.sync import audio


PANEL = {"transport": "ble", "ble_address": "AA:BB:CC:DD:EE:FF", "mac": "AA:BB:CC:DD:EE:FF",
         "model": "Fixture panel", "matrix_size": "32x32", "segment_count": 10}


class ResponsiveAppUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if os.name == "nt":
            for name in ("segoeui.ttf", "segoeuib.ttf"):
                QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + name)

    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="lumisync-ui-test-")
        self.settings = QSettings(str(Path(self.temporary.name) / "ui.ini"), QSettings.Format.IniFormat)
        self.previous_style = self.app.styleSheet()
        self.previous_font = self.app.font()
        self.app.setFont(QFont("Segoe UI", 10))
        apply_theme(self.app)
        # The offscreen plugin cannot resolve a Windows font fallback list.
        self.app.setStyleSheet(self.app.styleSheet().replace(
            '"Segoe UI Variable", "Inter", "Segoe UI", sans-serif', '"Segoe UI"'))
        self.config = (dict(vars(SYNC)), dict(vars(BRIGHTNESS)))
        self.patches = [
            patch("lumisync.gui.views.modes_view.QSettings", return_value=self.settings),
            patch("lumisync.gui.controllers.sync_controller.QSettings", return_value=self.settings),
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
        self.app.setStyleSheet(self.previous_style)
        self.app.setFont(self.previous_font)
        self.settings.sync()
        self.temporary.cleanup()

    def show(self, widget, width=712, height=520):
        self.widgets.append(widget)
        widget.resize(width, height)
        widget.show()
        self.app.processEvents()
        return widget

    @staticmethod
    def devices(items=None):
        items = [dict(PANEL)] if items is None else items
        controller = SimpleNamespace(devices=items, get_groups=lambda: [],
                                     get_selected_device=lambda: items[0] if items else None,
                                     is_device_busy=Mock(return_value=False), save_group=Mock(),
                                     set_zone_count_at=Mock(return_value=True), mark_device_output=Mock())
        for name in ("devices_discovered", "device_added", "device_removed", "device_selected",
                     "device_updated", "groups_changed"):
            setattr(controller, name, Mock())
        return controller

    def sync(self):
        controller = SyncController()
        controller.start_monitor_sync = Mock()
        controller.start_music_sync = Mock()
        controller.stop_sync = Mock()
        return controller

    def click(self, widget):
        ancestor = widget.parentWidget()
        while ancestor is not None:
            if isinstance(ancestor, QScrollArea):
                ancestor.ensureWidgetVisible(widget)
                self.app.processEvents()
                self.assert_reachable(widget, ancestor.viewport())
                break
            ancestor = ancestor.parentWidget()
        self.assertTrue(widget.isVisible(), widget.accessibleName() or widget.objectName())
        self.assertTrue(widget.isEnabled())
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def assert_reachable(self, widget, container):
        self.assertTrue(widget.isVisible())
        origin = widget.mapTo(container, QPoint(0, 0))
        self.assertGreaterEqual(origin.x(), 0)
        self.assertGreaterEqual(origin.y(), 0)
        self.assertLessEqual(origin.x() + widget.width(), container.width())
        self.assertLessEqual(origin.y() + widget.height(), container.height())

    def test_short_sync_pages_keep_targets_brightness_and_start_stop_reachable(self):
        for mode in ("monitor", "music"):
            with self.subTest(mode=mode):
                controller = self.sync()
                page = self.show(ModesView(controller, self.devices(), mode=mode))
                button = getattr(page, mode + "_start_button")
                slider = getattr(page, mode + "_brightness_slider")
                self.assert_reachable(button, page)
                self.assert_reachable(slider, page.scroll.viewport())
                self.click(button)
                getattr(controller, "start_" + mode + "_sync").assert_called_once_with([PANEL])
                controller.is_syncing = Mock(return_value=True)
                controller.get_current_sync_mode = Mock(return_value=mode)
                controller.sync_started.emit(mode)
                self.assertIn("Stop", button.text())
                self.click(button)
                controller.stop_sync.assert_called_once()
                controller.is_syncing.return_value = False
                page.close()

    def test_music_reaction_buttons_and_compact_dropdown_share_persisted_state(self):
        page = self.show(ModesView(self.sync(), self.devices(), mode="music"), 1020, 760)
        for key, button in page.music_reaction_buttons.items():
            self.click(button)
            self.assertEqual(self.settings.value(SYNC_SETTINGS_KEYS["music_reaction"]), key)
            self.assertEqual(page.music_reaction_combo.currentData(), key)
            self.assertEqual(page.music_reaction_description.text(), audio.REACTION_DESCRIPTIONS[key])
        page.resize(712, 520)
        self.app.processEvents()
        self.assertTrue(page.music_reaction_combo.isVisible())
        self.assertTrue(page._reaction_picker.isHidden())
        self.assert_reachable(page.music_palette_combo, page.scroll.viewport())
        self.assert_reachable(page.music_auto_gain_check, page.scroll.viewport())
        page.music_reaction_combo.setFocus()
        QTest.keyClick(page.music_reaction_combo, Qt.Key.Key_Home)
        self.assertEqual(page.controller.get_music_reaction(), audio.REACTIONS[0])
        self.assertTrue(page.music_reaction_buttons[audio.REACTIONS[0]].isChecked())
        page.music_palette_combo.setFocus()
        QTest.keyClick(page.music_palette_combo, Qt.Key.Key_End)
        self.assertEqual(page.controller.get_music_palette(), page.music_palette_combo.currentData())
        page.music_brightness_slider.setFocus()
        QTest.keyClick(page.music_brightness_slider, Qt.Key.Key_Home)
        self.assertAlmostEqual(page.controller.get_music_brightness(), 0.1)
        before = page.music_auto_gain_check.isChecked()
        self.click(page.music_auto_gain_check)
        self.assertEqual(page.controller.get_music_auto_gain(), not before)
        with patch("lumisync.gui.views.modes_view.QInputDialog.getText", return_value=("Music desk", True)):
            self.click(page.music_group_button)
        page.device_controller.save_group.assert_called_once_with("Music desk", [0])
        with patch.object(page, "_adjust_zones_for_strip") as zones:
            self.click(page.music_zones_button)
            zones.assert_called_once_with(page.music_chips)

    def test_target_group_and_mapping_actions_preserve_selection_and_keyboard_swaps(self):
        devices = self.devices()
        page = self.show(ModesView(self.sync(), devices, mode="monitor"), 1020, 760)
        with patch("lumisync.gui.views.modes_view.QInputDialog.getText", return_value=("Desk", True)):
            self.click(page.monitor_group_button)
        devices.save_group.assert_called_once_with("Desk", [0])
        with patch.object(page, "_adjust_zones_for_strip") as zones:
            self.click(page.monitor_zones_button)
            zones.assert_called_once_with(page.monitor_chips)
        with patch.object(page.led_mapping_widget, "start_test_mode_if_not_active"), \
                patch.object(page.led_mapping_widget, "stop_test_mode_if_active"):
            self.click(page.mapping_toggle_button)
            QTest.qWait(250)
            self.assertTrue(page.led_mapping_container.isVisible())
            preview = page.led_mapping_widget.screen_preview
            original = page.led_mapping_widget.get_mapping()
            preview.setFocus()
            for key in (Qt.Key.Key_Return, Qt.Key.Key_Right, Qt.Key.Key_Return):
                QTest.keyClick(preview, key)
            swapped = page.led_mapping_widget.get_mapping()
            self.assertEqual(swapped[:2], original[:2][::-1])
            self.assertEqual(swapped[2:], original[2:])
            self.click(page.mapping_toggle_button)
            QTest.qWait(320)
            self.assertTrue(page.led_mapping_container.isHidden())
        self.click(page.monitor_chips._target_buttons[0])
        self.assertFalse(page.monitor_start_button.isEnabled())
        self.assertFalse(page.monitor_group_button.isEnabled())

    def test_mapping_reverse_reset_depth_and_test_controls_have_real_feedback(self):
        mapping = self.show(LedMappingWidget(self.settings))
        original = mapping.get_mapping()
        self.click(mapping.reverse_button)
        self.assertEqual(mapping.get_mapping(), original[::-1])
        mapping.capture_depth_slider.setFocus()
        QTest.keyClick(mapping.capture_depth_slider, Qt.Key.Key_End)
        self.assertIn("%", mapping.capture_depth_label.text())
        with patch("lumisync.gui.widgets.led_mapping_widget.QMessageBox.question",
                   return_value=QMessageBox.StandardButton.Yes):
            self.click(mapping.reset_button)
        self.assertIn("reset", mapping.selection_label.text())
        with patch.object(mapping, "_enable_razer_mode"), patch.object(mapping, "_send_colors_to_strip"), \
                patch.object(mapping, "_disable_razer_mode"):
            self.click(mapping.test_button)
            self.assertTrue(mapping._test_mode_active)
            self.assertEqual(mapping.test_button.text(), "Stop test")
            self.click(mapping.test_button)
            self.assertFalse(mapping._test_mode_active)

    def test_keyboard_pixel_painting_erasing_and_resizing_are_bounded(self):
        canvas = self.show(PixelCanvas(3, 2), 240, 180)
        canvas.set_color((10, 20, 30))
        canvas.setFocus()
        for key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_Space):
            QTest.keyClick(canvas, key)
        self.assertEqual(canvas.get_grid()[1][1], (10, 20, 30))
        self.assertIn("column 2, row 2", canvas.accessibleDescription())
        QTest.keyClick(canvas, Qt.Key.Key_Delete)
        self.assertTrue(canvas.is_empty())
        canvas.set_matrix_size(1, 1)
        for key in (Qt.Key.Key_Down, Qt.Key.Key_Right, Qt.Key.Key_Return):
            QTest.keyClick(canvas, key)
        self.assertEqual(canvas.get_grid(), [[(10, 20, 30)]])

    def test_draw_brush_frames_send_and_clear_survive_reflow(self):
        page = self.show(DrawView(self.devices()))
        for control in (page.color_button, page.fill_button, page.send_button, page.animation_toggle):
            self.assert_reachable(control, page)
        with patch("lumisync.gui.views.draw_view.QColorDialog.getColor", return_value=QColor(10, 20, 30)):
            self.click(page.color_button)
        self.click(page.fill_button)
        self.assertEqual(page.canvas.get_grid()[0][0], (10, 20, 30))
        self.click(page.animation_toggle)
        self.click(page.add_frame_button)
        page.canvas.fill((40, 50, 60))
        self.click(page.add_frame_button)
        self.assertEqual(len(page._frames), 2)
        page.resize(1020, 760)
        self.app.processEvents()
        self.assertEqual(page._frames[0][0][0], (10, 20, 30))
        with patch.object(page, "_send") as send:
            self.click(page.play_button)
            self.assertEqual(len(send.call_args.args[0]), 2)
            self.click(page.send_button)
            self.assertEqual(send.call_args.args[0][0][0][0], (40, 50, 60))
        with patch("lumisync.gui.views.draw_view.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.click(page.clear_frames_button)
            self.assertEqual(len(page._frames), 2)
        with patch("lumisync.gui.views.draw_view.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.click(page.clear_frames_button)
            self.click(page.clear_button)
        self.assertFalse(page._frames)
        self.assertTrue(page.canvas.is_empty())
        self.assertFalse(page.play_button.isEnabled())
        with patch.object(page, "_stop_send") as stop:
            page.stop_button.setEnabled(True)
            self.click(page.stop_button)
            stop.assert_called_once()

    def test_draw_size_changes_confirm_before_clearing_art_and_frames(self):
        page = self.show(DrawView(self.devices()))
        page.canvas.fill((10, 20, 30))
        page._add_frame()
        previous = page.size_combo.currentData()
        next_index = 1 if page.size_combo.currentIndex() == 0 else 0
        with patch("lumisync.gui.views.draw_view.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            page.size_combo.setCurrentIndex(next_index)
        self.assertEqual(page.size_combo.currentData(), previous)
        self.assertFalse(page.canvas.is_empty())
        self.assertEqual(len(page._frames), 1)
        with patch("lumisync.gui.views.draw_view.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            page.size_combo.setFocus()
            QTest.keyClick(page.size_combo, Qt.Key.Key_Home)
            if page.size_combo.currentData() == previous:
                QTest.keyClick(page.size_combo, Qt.Key.Key_End)
        self.assertNotEqual(page.size_combo.currentData(), previous)
        self.assertTrue(page.canvas.is_empty())
        self.assertFalse(page._frames)
        QTest.mouseClick(page.canvas, Qt.MouseButton.LeftButton, pos=page.canvas.rect().center())
        self.assertFalse(page.canvas.is_empty())

    def test_settings_sections_preferences_and_wrapped_switches_are_usable(self):
        with patch("lumisync.gui.utils.autostart.is_enabled", return_value=False):
            page = self.show(SettingsPage(self.settings))
        self.assertTrue(page.section_picker.isVisible())
        self.assertTrue(page.section_nav.isHidden())
        before = page.tray_check.isChecked()
        page.tray_check.setFocus()
        QTest.keyClick(page.tray_check, Qt.Key.Key_Space)
        self.assertEqual(self.settings.value("ui/minimize_to_tray"), not before)
        self.click(page.statusbar_check)
        self.assertTrue(self.settings.value("ui/status_bar"))
        page.window_material_combo.setFocus()
        QTest.keyClick(page.window_material_combo, Qt.Key.Key_End)
        self.assertEqual(self.settings.value("ui/window_material"), "solid")
        for index in range(page.section_stack.count()):
            page.section_picker.setCurrentIndex(index)
            self.assertEqual(page.section_stack.currentIndex(), index)
            scroll = page.section_stack.currentWidget()
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        page.resize(1020, 760)
        self.app.processEvents()
        self.assertTrue(page.section_nav.isVisible())
        page.section_nav.setCurrentRow(1)
        self.assertEqual(page.section_picker.currentIndex(), 1)
        page.smoothing_slider.setFocus()
        QTest.keyClick(page.smoothing_slider, Qt.Key.Key_Home)
        self.assertAlmostEqual(float(self.settings.value(SYNC_SETTINGS_KEYS["smoothing"])), 0.05)
        wrapped = ToggleSwitch("Wrap a deliberately long preference label without hiding its action")
        self.widgets.append(wrapped)
        self.assertGreater(wrapped.heightForWidth(200), wrapped.heightForWidth(800))

    def test_settings_tuning_startup_group_delete_and_update_feedback(self):
        host = self.show(QWidget(), 1020, 760)
        host.device_controller = self.devices()
        host.device_controller.get_groups = Mock(return_value=[{"name": "Fixture desk group", "devices": ["fixture"]}])
        host.device_controller.delete_group = Mock()
        host.sync_controller = self.sync()
        host.update_controller = SimpleNamespace(check_started=Mock(), check_finished=Mock(),
                                                 last_result=None, check_now=Mock())
        with patch("lumisync.gui.utils.autostart.is_supported", return_value=True), \
                patch("lumisync.gui.utils.autostart.is_enabled", return_value=False), \
                patch("lumisync.gui.utils.autostart.set_enabled") as startup:
            page = self.show(SettingsPage(self.settings, host), 1020, 760)
            self.click(page.autostart_check)
            startup.assert_called_once_with(True)
        for section, controls in (
            (1, ((page.smoothing_slider, "smoothing"), (page.saturation_slider, "saturation"),
                 (page.fps_slider, "monitor_fps"))),
            (2, ((page.gain_slider, "music_gain"), (page.music_smoothing_slider, "music_smoothing"))),
        ):
            page.section_nav.setCurrentRow(section)
            self.app.processEvents()
            for control, key in controls:
                control.setFocus()
                QTest.keyClick(control, Qt.Key.Key_End)
                self.assertIsNotNone(self.settings.value(SYNC_SETTINGS_KEYS[key]))
        page.section_nav.setCurrentRow(1)
        self.app.processEvents()
        before = page.gamma_check.isChecked()
        self.click(page.gamma_check)
        self.assertEqual(self.settings.value(SYNC_SETTINGS_KEYS["gamma_correct"]), not before)
        page.display_combo.addItem("Fixture second display", 1)
        page.display_combo.setFocus()
        QTest.keyClick(page.display_combo, Qt.Key.Key_End)
        self.assertEqual(self.settings.value("sync/monitor_display"), 1)
        page.section_nav.setCurrentRow(3)
        self.app.processEvents()
        delete = next(button for button in page.findChildren(QPushButton) if button.text() == "Delete")
        with patch("lumisync.gui.views.settings_page.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.click(delete)
        host.device_controller.delete_group.assert_called_once_with("Fixture desk group")
        page.section_nav.setCurrentRow(4)
        self.app.processEvents()
        self.click(page.check_updates_button)
        host.update_controller.check_now.assert_called_once()
        page._on_update_check_started()
        self.assertFalse(page.check_updates_button.isEnabled())
        page._on_update_check_finished(SimpleNamespace(error="Fixture offline error", is_update_available=False))
        self.assertIn("failed", page.update_status_label.text())
        page._on_update_check_finished(SimpleNamespace(error=None, is_update_available=True, latest_version="99.0",
                                                       release_url="https://github.com/Minlor/LumiSync/releases"))
        with patch("lumisync.gui.views.settings_page.QDesktopServices.openUrl", return_value=True) as open_url:
            self.click(page.open_release_button)
            self.click(page.repository_button)
            self.click(page.account_setup_button)
            self.assertEqual([call.args[0].toString() for call in open_url.call_args_list], [
                "https://github.com/Minlor/LumiSync/releases",
                "https://github.com/Minlor/LumiSync",
                "https://github.com/Minlor/LumiSync#account-setup",
            ])
        logs = str(Path(self.temporary.name) / "logs")
        with patch("lumisync.utils.logging.get_logs_directory", return_value=logs), \
                patch("lumisync.gui.views.settings_page.QDesktopServices.openUrl", return_value=True) as open_url:
            self.click(page.open_logs_button)
            self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()), Path(logs))
        from lumisync import __version__
        self.assertIn(__version__, page.about_version_label.text())
        for width, height in ((712, 488), (1020, 760)):
            page.resize(width, height)
            self.app.processEvents()
            scroll = page.section_stack.currentWidget()
            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
            for control in (page.check_updates_button, page.open_logs_button,
                            page.account_setup_button, page.repository_button):
                self.assert_reachable(control, scroll.viewport())

    def test_device_toolbar_inspector_and_group_controls_dispatch_to_the_correct_device(self):
        device = {"model": "H619C", "transport": "lan", "ip": "192.0.2.1", "mac": "fixture-light",
                  "local_transport": "lan", "cloud_transport": "govee_cloud", "account_id": "fixture",
                  "segment_count_override": 10}
        controller = Mock()
        controller.devices = [device]
        controller.selected_device_index = -1
        controller.get_groups.return_value = []
        controller.get_accounts.return_value = []
        controller.get_device_state_at.return_value = {"online": True, "status_source": "confirmed", "power_on": True,
                                                       "brightness": 65, "color_temp": 3300}
        page = self.show(DevicesView(controller), 1020, 760)
        self.click(page.find_devices_button)
        controller.find_devices.assert_called_once()
        for button in (page.add_button, page.accounts_button):
            QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
            self.click(button)
            self.assertIsNone(QApplication.activeModalWidget())
        card = page._cards[0]
        self.click(card.power_button)
        controller.toggle_power_at.assert_called_with(0)
        card.brightness_slider.setFocus()
        QTest.keyClick(card.brightness_slider, Qt.Key.Key_Home)
        QTest.qWait(300)
        controller.set_brightness_at.assert_called_with(0, card.brightness_slider.value())
        self.click(card)
        self.assertEqual(page._inspected_index, 0)
        inspector = page.inspector
        self.click(inspector.power_button)
        self.click(inspector.details_toggle)
        self.assertTrue(inspector.details_panel.isVisible())
        self.click(inspector.default_button)
        controller.select_device.assert_called_once_with(0)
        inspector.brightness_slider.setFocus()
        QTest.keyClick(inspector.brightness_slider, Qt.Key.Key_End)
        inspector.temperature_slider.setFocus()
        QTest.keyClick(inspector.temperature_slider, Qt.Key.Key_End)
        QTest.qWait(300)
        controller.set_color_temperature_at.assert_called_once_with(0, inspector.temperature_slider.value())
        with patch("lumisync.gui.widgets.device_inspector.QColorDialog.getColor", return_value=QColor(20, 30, 40)):
            self.click(inspector.color_button)
        controller.set_color_at.assert_called_once_with(0, 20, 30, 40)
        inspector.connection_combo.setFocus()
        QTest.keyClick(inspector.connection_combo, Qt.Key.Key_End)
        controller.set_connection_at.assert_called_once_with(0, "govee_cloud")
        QTimer.singleShot(100, lambda: (QApplication.activeModalWidget().setIntValue(12), QApplication.activeModalWidget().accept()))
        self.click(inspector.zones_button)
        controller.set_zone_count_at.assert_called_with(0, 12)
        self.click(inspector.reset_zones_button)
        controller.set_zone_count_at.assert_called_with(0, None)
        with patch("lumisync.gui.views.devices_view.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            self.click(inspector.remove_button)
        controller.remove_device.assert_not_called()
        with patch("lumisync.gui.views.devices_view.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            self.click(inspector.remove_button)
        controller.remove_device.assert_called_once_with(0)
        self.click(inspector.close_button)
        self.click(page.group_mode_button)
        QTest.qWait(200)
        self.click(page._cards[0])
        self.assertEqual(page._selected, {0})
        QTimer.singleShot(100, lambda: (QApplication.activeModalWidget().setTextValue("Desk"), QApplication.activeModalWidget().accept()))
        self.click(page.save_group_button)
        controller.save_group.assert_called_once_with("Desk", [0])
        self.click(page.group_mode_button)
        self.click(page.cancel_group_button)
        self.assertFalse(page._group_selection_mode)
        controller.devices = [dict(PANEL)]
        page._rebuild_cards()
        self.app.processEvents()
        self.click(page._cards[0])
        self.click(inspector.details_toggle)
        QTimer.singleShot(100, lambda: QApplication.activeModalWidget().reject())
        self.click(inspector.panel_tools_button)
        self.assertIsNone(QApplication.activeModalWidget())
        self.assertTrue(inspector.isVisible())

    def test_panel_tabs_validate_busy_state_and_send_the_selected_values(self):
        controller = self.devices()
        dialog = self.show(PanelToolsDialog(controller, 0), 520, 440)
        tasks = []
        adapter = Mock()
        with patch("lumisync.gui.dialogs.panel_tools_dialog.start_task",
                   side_effect=lambda operation, callback: tasks.append((operation, callback))), \
                patch("lumisync.gui.dialogs.panel_tools_dialog.pool.acquire", return_value=adapter):
            dialog.style.setValue(3)
            dialog.twenty_four.setChecked(False)
            dialog.rotate.setChecked(True)
            dialog.minutes.setValue(7)
            dialog.seconds.setValue(12)
            dialog.left.setValue(123)
            dialog.right.setValue(456)
            methods = (("show_clock", (3, False)), ("set_rotation", (True,)),
                       ("show_countdown", (7, 12)), ("show_scoreboard", (123, 456)))
            for index, (method, args) in enumerate(methods):
                tab = dialog.tabs.tabBar()
                QTest.mouseClick(tab, Qt.MouseButton.LeftButton, pos=tab.tabRect(index).center())
                self.assertEqual(dialog.tabs.currentIndex(), index)
                self.assert_reachable(dialog.buttons[index], dialog)
                self.click(dialog.buttons[index])
                self.assertTrue(dialog._busy)
                self.assertTrue(all(not button.isEnabled() for button in dialog.buttons))
                dialog.reject()
                self.assertTrue(dialog.isVisible())
                operation, callback = tasks.pop()
                operation()
                getattr(adapter, method).assert_called_once_with(*args)
                callback(None, None)
                self.assertEqual(dialog.status.text(), "Panel command sent.")
            controller.is_device_busy.return_value = True
            self.click(dialog.buttons[-1])
            self.assertIn("Stop the current output", dialog.status.text())
            self.assertFalse(tasks)
            controller.is_device_busy.return_value = False
            self.click(dialog.buttons[-1])
            tasks.pop()[1](None, "Fixture connection unavailable. Try again.")
            self.assertIn("unavailable", dialog.status.text())
            self.assertTrue(dialog.buttons[-1].isEnabled())
        dialog.reject()
        self.assertFalse(dialog.isVisible())

    def test_add_device_types_keep_validation_and_submit_actions_reachable(self):
        for kind in ("lan", "ble", "tuya"):
            with self.subTest(kind=kind):
                dialog = self.show(AddDeviceDialog(), 480, 520)
                dialog.type_combo.setCurrentIndex(dialog.type_combo.findData(kind))
                self.click(dialog.add_button)
                self.assertTrue(dialog.status_label.isVisible())
                self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
                self.assert_reachable(dialog.add_button, dialog)
                dialog.ip_entry.setText("192.0.2.1")
                if kind == "ble":
                    dialog.ble_address_entry.setText(PANEL["ble_address"])
                elif kind == "tuya":
                    dialog.tuya_id_entry.setText("fixture-device")
                    dialog.tuya_key_entry.setText("0123456789abcdef")
                    dialog.remember_key.setChecked(False)
                self.click(dialog.add_button)
                self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_navigation_keyboard_and_notification_dismissal_have_real_destinations(self):
        shell = NavigationShell()
        for key in ("devices", "monitor", "music", "draw", "settings"):
            shell.add_page(key=key, title=key.title(), icon=None, widget=QWidget(), bottom=key == "settings")
        self.show(shell, 800, 520)
        shell.nav_list.setFocus()
        for index in range(1, 4):
            QTest.keyClick(shell.nav_list, Qt.Key.Key_Down)
            QTest.qWait(300)
            self.assertEqual(shell.content_stack.currentIndex(), index)
        bottom = shell.bottom_nav_list
        QTest.mouseClick(bottom.viewport(), Qt.MouseButton.LeftButton, pos=bottom.visualItemRect(bottom.item(0)).center())
        QTest.qWait(300)
        self.assertEqual(shell.content_stack.currentIndex(), 4)
        manager = ToastManager(shell)
        manager.show("Brightness: 50%")
        manager.show("Brightness: 60%")
        self.assertEqual(len(manager._toasts), 1)
        toast = manager._toasts[0]
        self.assertEqual(toast.label.text(), "Brightness: 60%")
        self.assertEqual(toast.label.textFormat(), Qt.TextFormat.PlainText)
        self.assert_reachable(toast.close_button, shell)
        self.click(toast.close_button)
        self.assertTrue(toast._closing)
        QTest.qWait(250)
        self.assertFalse(manager._toasts)

    def test_colored_mapping_labels_keep_aa_contrast_over_the_composited_fill(self):
        def luminance(channels):
            linear = [(value / 255 / 12.92 if value / 255 <= 0.04045
                       else ((value / 255 + 0.055) / 1.055) ** 2.4) for value in channels]
            return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

        from lumisync.gui.theme import qcolor
        background = qcolor("surface_alt")
        for index in range(32):
            for alpha in (110, 190, 255):
                fill = QColor(*zone_test_color(index), alpha)
                foreground = zone_text_color(fill)
                composite = [fill_channel * fill.alphaF() + base * (1 - fill.alphaF())
                             for fill_channel, base in zip(fill.getRgb()[:3], background.getRgb()[:3])]
                light, dark = sorted((luminance(composite), luminance(foreground.getRgb()[:3])), reverse=True)
                self.assertGreaterEqual((light + 0.05) / (dark + 0.05), 4.5)


if __name__ == "__main__":
    unittest.main()
