"""Settings tabs: General · Sync · Updates · About, show_section and the
places in the app that open Settings on a specific tab."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QSettings, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.main_window import LumiSyncMainWindow
from lumisync.gui.theme import apply_theme
from lumisync.gui.views.settings_page import LAST_TAB_KEY, SettingsPage
from lumisync.gui.widgets.selector_bar import SELECTOR_HEIGHT

TABS = {
    "general": ["General"],
    "sync": ["Monitor tuning", "Music tuning", "Groups"],
    "updates": ["Updates"],
    "about": ["About"],
}
SECTIONS = {
    "general": ("general", "General"),
    "sync": ("sync", "Monitor tuning"),
    "updates": ("updates", "Updates"),
    "about": ("about", "About"),
    "monitor": ("sync", "Monitor tuning"),
    "music": ("sync", "Music tuning"),
    "groups": ("sync", "Groups"),
}
# Every control the page exposed before it had tabs, and its section.
CONTROLS = {
    "tray_check": "general",
    "autostart_check": "general",
    "window_material_combo": "general",
    "smoothing_slider": "monitor",
    "saturation_slider": "monitor",
    "fps_slider": "monitor",
    "gamma_check": "monitor",
    "gain_slider": "music",
    "music_smoothing_slider": "music",
    "check_updates_button": "updates",
    "install_update_button": "updates",
    "open_release_button": "updates",
    "auto_update_toggle": "updates",
    "update_status_label": "updates",
    "about_version_label": "about",
    "repository_button": "about",
    "author_button": "about",
    "support_button": "about",
    "account_setup_button": "about",
    "open_logs_button": "about",
}


def available(version="99.0"):
    return SimpleNamespace(error=None, is_update_available=True, latest_version=version,
                           release_url="https://github.com/Minlor/LumiSync/releases", current_version="1.0.0")


class SettingsTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if os.name == "nt":
            for name in ("segoeui.ttf", "segoeuib.ttf"):
                QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + name)

    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="lumisync-tabs-test-")
        self.settings = QSettings(str(Path(self.temporary.name) / "ui.ini"), QSettings.Format.IniFormat)
        self.previous_style = self.app.styleSheet()
        self.previous_font = self.app.font()
        self.app.setFont(QFont("Segoe UI", 10))
        apply_theme(self.app)
        self.app.setStyleSheet(self.app.styleSheet().replace(
            '"Segoe UI Variable", "Inter", "Segoe UI", sans-serif', '"Segoe UI"'))
        self.widgets = []

    def tearDown(self):
        for widget in reversed(self.widgets):
            widget.close()
            widget.deleteLater()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.setStyleSheet(self.previous_style)
        self.app.setFont(self.previous_font)
        self.settings.sync()
        self.temporary.cleanup()

    def page(self, width=900, height=640, host=None):
        with patch("lumisync.gui.utils.autostart.is_enabled", return_value=False):
            page = SettingsPage(self.settings, host)
        self.widgets.append(page)
        if host is None:
            page.resize(width, height)
            page.show()
        else:
            layout = QVBoxLayout(host)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(page)
            host.resize(width, height)
            host.show()
        self.app.processEvents()
        return page

    def host(self, groups=3):
        host = QWidget()
        self.widgets.append(host)
        controller = SimpleNamespace(
            get_groups=Mock(return_value=[{"name": f"Fixture group {i}", "devices": ["a", "b"]}
                                          for i in range(groups)]),
            groups_changed=Mock(), delete_group=Mock(),
        )
        host.device_controller = controller
        host.update_controller = SimpleNamespace(check_started=Mock(), check_finished=Mock(),
                                                 last_result=None, check_now=Mock(), ready_installer=None)
        return host

    def visible_headers(self, page):
        return [group.header_label.text() for group in page.groups() if group.isVisible()]

    def assert_in_viewport(self, widget, scroll: QScrollArea):
        self.assertTrue(widget.isVisible(), widget.objectName() or type(widget).__name__)
        origin = widget.mapTo(scroll.viewport(), QPoint(0, 0))
        self.assertGreaterEqual(origin.y(), 0)
        self.assertGreaterEqual(origin.x(), 0)
        self.assertLessEqual(origin.x() + widget.width(), scroll.viewport().width())
        self.assertLessEqual(origin.y() + widget.height(), scroll.viewport().height())

    # ----------------------------------------------------------------- tabs

    def test_each_tab_shows_only_its_groups_and_the_old_accessor_is_unchanged(self):
        page = self.page()
        self.assertEqual([group.header_label.text() for group in page.groups()],
                         ["General", "Monitor tuning", "Music tuning", "Groups", "Updates", "About"])
        self.assertEqual([(key, label, [g.header_label.text() for g in groups])
                          for key, label, groups in page.tab_sections()],
                         [(key, key.title(), headers) for key, headers in TABS.items()])
        self.assertEqual(page.current_tab(), "general")
        self.assertEqual(page.tab_bar.keys(), list(TABS))
        for key, headers in TABS.items():
            with self.subTest(tab=key):
                page.select_tab(key)
                self.app.processEvents()
                self.assertEqual(page.current_tab(), key)
                self.assertEqual(self.visible_headers(page), headers)
                self.assertTrue(page.tab_bar.item(key).isChecked())
        with self.assertRaises(ValueError):
            page.select_tab("display")

    def test_single_group_tabs_hide_the_heading_that_repeats_the_tab_label(self):
        page = self.page()
        for key, headers in TABS.items():
            with self.subTest(tab=key):
                page.select_tab(key)
                self.app.processEvents()
                shown = [group for group in page.groups() if group.isVisible()]
                self.assertEqual([group.header_label.text() for group in shown], headers)
                for group in shown:
                    first_card = group.findChild(QWidget, "SettingsCard")
                    self.assertIsNotNone(first_card)
                    if len(headers) == 1:
                        # The label lives on, hidden, and takes no space.
                        self.assertFalse(group.header_visible())
                        self.assertFalse(group.header_label.isVisible())
                        self.assertEqual(group.header_label.text(), headers[0])
                        self.assertEqual(first_card.mapTo(group, QPoint(0, 0)).y(), 0)
                    else:
                        self.assertTrue(group.header_label.isVisible())
                        self.assertGreaterEqual(first_card.mapTo(group, QPoint(0, 0)).y(),
                                                group.header_label.height())
                if len(headers) == 1:
                    # Cards start just under the tab row, as headings did before.
                    top = shown[0].mapTo(page.scroll.viewport(), QPoint(0, 0)).y()
                    self.assertEqual(top, page._body_layout.contentsMargins().top())

    def test_the_selector_stays_above_the_scroll_area_and_is_a_slim_text_row(self):
        page = self.page()
        self.assertFalse(page.scroll.widget().isAncestorOf(page.tab_bar))
        self.assertLess(page.tab_bar.mapTo(page, QPoint(0, 0)).y(), page.scroll.mapTo(page, QPoint(0, 0)).y())
        self.assertGreaterEqual(page.tab_bar.height(), 32)
        self.assertEqual(page.tab_bar.height(), SELECTOR_HEIGHT)
        self.assertEqual([page.tab_bar.item(key).text() for key in TABS], ["General", "Sync", "Updates", "About"])
        # The first tab's text lines up with the cards below.
        first = page.tab_bar.item("general")
        from lumisync.gui.widgets.selector_bar import TAB_PADDING_X
        text_left = first.mapTo(page, QPoint(TAB_PADDING_X, 0)).x()
        card_left = page.general_group.mapTo(page, QPoint(0, 0)).x()
        self.assertEqual(text_left, card_left)
        items = [page.tab_bar.item(key) for key in TABS]
        for left, right in zip(items, items[1:]):
            gap = right.x() - (left.x() + left.width())
            self.assertGreaterEqual(gap, 4)
            self.assertLessEqual(gap, 8)
        self.assertFalse(page.tab_bar.grab().isNull())

    def test_changing_tab_scrolls_back_to_the_top(self):
        page = self.page(712, 488, host=self.host(groups=6))
        page.select_tab("sync")
        self.app.processEvents()
        bar = page.scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(bar.maximum())
        page.select_tab("general")
        self.assertEqual(bar.value(), 0)
        page.select_tab("sync")
        self.app.processEvents()
        self.assertEqual(bar.value(), 0)
        bar.setValue(bar.maximum())
        QTest.mouseClick(page.tab_bar.item("about"), Qt.MouseButton.LeftButton)
        self.assertEqual(page.current_tab(), "about")
        self.assertEqual(bar.value(), 0)

    def test_the_last_tab_is_remembered_and_restored(self):
        page = self.page()
        self.assertIsNone(self.settings.value(LAST_TAB_KEY))  # Opening the page writes nothing.
        QTest.mouseClick(page.tab_bar.item("updates"), Qt.MouseButton.LeftButton)
        self.assertEqual(self.settings.value(LAST_TAB_KEY), "updates")
        page.show_section("music")
        self.assertEqual(self.settings.value(LAST_TAB_KEY), "sync")
        restored = self.page()
        self.assertEqual(restored.current_tab(), "sync")
        self.assertEqual(self.visible_headers(restored), TABS["sync"])
        for stored, expected in (("about", "about"), ("display", "general"), ("", "general"), ("ABOUT", "about")):
            with self.subTest(stored=stored):
                self.settings.setValue(LAST_TAB_KEY, stored)
                self.assertEqual(self.page().current_tab(), expected)

    def test_show_section_opens_the_right_tab_for_every_key(self):
        page = self.page(712, 488, host=self.host(groups=6))
        groups = {group.header_label.text(): group for group in page.groups()}
        for key, (tab, header) in SECTIONS.items():
            with self.subTest(section=key):
                page.select_tab("about" if tab != "about" else "general")
                page.show_section(key)
                self.app.processEvents()
                self.assertEqual(page.current_tab(), tab)
                self.assertEqual(self.visible_headers(page), TABS[tab])
                group = groups[header]
                # Single-group tabs hide the heading; the group's top shows.
                target = group.header_label if group.header_visible() else group.findChild(
                    QWidget, "SettingsCard") or group
                self.assert_in_viewport(target, page.scroll)
        page.show_section("groups")
        self.app.processEvents()
        self.assertGreater(page.scroll.verticalScrollBar().value(), 0)
        with self.assertRaises(ValueError):
            page.show_section("display")

    def test_show_section_on_a_hidden_page_scrolls_once_it_is_shown(self):
        page = self.page(712, 488, host=self.host(groups=6))
        page.hide()
        page.show_section("groups")
        self.app.processEvents()
        page.show()
        self.app.processEvents()
        self.app.processEvents()
        self.assertEqual(page.current_tab(), "sync")
        self.assert_in_viewport(page.groups_group.header_label, page.scroll)

    def test_keyboard_moves_between_tabs_and_the_bar_is_one_tab_stop(self):
        page = self.page()
        bar = page.tab_bar
        self.assertEqual(bar.accessibleName(), "Settings sections")
        self.assertEqual(bar.focusPolicy(), Qt.FocusPolicy.StrongFocus)
        for key in TABS:
            item = bar.item(key)
            self.assertEqual(item.focusPolicy(), Qt.FocusPolicy.NoFocus)
            self.assertEqual(item.accessibleName(), key.title())
        bar.setFocus()
        for key, expected in ((Qt.Key.Key_Right, "sync"), (Qt.Key.Key_Right, "updates"), (Qt.Key.Key_Right, "about"),
                              (Qt.Key.Key_Right, "about"), (Qt.Key.Key_Left, "updates"), (Qt.Key.Key_Home, "general"),
                              (Qt.Key.Key_Left, "general"), (Qt.Key.Key_End, "about")):
            QTest.keyClick(bar, key)
            self.assertEqual(page.current_tab(), expected)
            self.assertEqual(self.visible_headers(page), TABS[expected])
        self.assertTrue(bar.hasFocus())

    def test_ctrl_tab_cycles_tabs_while_the_page_shows(self):
        page = self.page()
        page.activateWindow()
        page.tray_check.setFocus()
        self.app.processEvents()
        sequences = [shortcut.key().toString() for shortcut in page._tab_shortcuts]
        self.assertIn("Ctrl+Tab", sequences)
        page.cycle_tab(1)
        self.assertEqual(page.current_tab(), "sync")
        page.cycle_tab(-1)
        page.cycle_tab(-1)
        self.assertEqual(page.current_tab(), "about")  # wraps around
        page.cycle_tab(1)
        self.assertEqual(page.current_tab(), "general")
        if QApplication.activeWindow() is page:
            QTest.keyClick(page.tray_check, Qt.Key.Key_Tab, Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(page.current_tab(), "sync")
            QTest.keyClick(page.tray_check, Qt.Key.Key_Backtab,
                           Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
            self.assertEqual(page.current_tab(), "general")
        page.hide()
        page.cycle_tab(1)
        self.assertEqual(page.current_tab(), "general")  # not while hidden

    def test_the_updates_tab_gets_a_dot_for_a_new_update_until_it_is_opened(self):
        page = self.page(host=self.host())
        bar = page.tab_bar
        self.assertFalse(bar.has_badge("updates"))
        page._on_update_check_finished(available("99.0"))
        self.assertTrue(bar.has_badge("updates"))
        self.assertGreater(bar.item("updates").sizeHint().width(),
                           bar.item("updates").fontMetrics().horizontalAdvance("Updates") + 24)
        self.assertFalse(bar.item("updates").grab().isNull())
        page.select_tab("updates")
        self.assertFalse(bar.has_badge("updates"))
        page.select_tab("general")
        page._on_update_check_finished(available("99.0"))  # the periodic recheck
        self.assertFalse(bar.has_badge("updates"))
        page._on_update_check_finished(available("99.1"))
        self.assertTrue(bar.has_badge("updates"))
        page._on_update_check_finished(SimpleNamespace(error=None, is_update_available=False))
        self.assertFalse(bar.has_badge("updates"))
        page._on_update_downloaded("fixture.exe")
        self.assertTrue(bar.has_badge("updates"))
        page.show_section("updates")
        self.assertFalse(bar.has_badge("updates"))
        page._on_update_check_finished(available("99.2"))  # seen at once on the open tab
        self.assertFalse(bar.has_badge("updates"))

    def test_every_tab_fits_the_minimum_size_without_horizontal_scrolling(self):
        page = self.page(712, 488, host=self.host())
        page._on_update_check_finished(available())
        for key in TABS:
            with self.subTest(tab=key):
                page.select_tab(key)
                self.app.processEvents()
                self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
                last = page.tab_bar.item("about")
                right = last.mapTo(page, QPoint(last.width(), 0)).x()
                self.assertLessEqual(right, page.width())
                self.assertFalse(page.grab().isNull())

    def test_every_control_from_before_is_reachable_from_its_tab(self):
        page = self.page(712, 488, host=self.host())
        page._on_update_check_finished(available())  # shows Update now and Release notes
        for name, section in CONTROLS.items():
            with self.subTest(control=name):
                control = getattr(page, name)
                page.show_section(section)
                page.scroll.ensureWidgetVisible(control, 0, 0)
                self.app.processEvents()
                self.assert_in_viewport(control, page.scroll)
        # Saved groups' Delete buttons live on the Sync tab.
        page.show_section("groups")
        for row in page._groups_list.findChildren(QWidget, "SettingsCard") or [page._groups_list]:
            page.scroll.ensureWidgetVisible(row, 0, 0)
            self.app.processEvents()
            self.assert_in_viewport(row, page.scroll)


class SettingsCallSiteTests(unittest.TestCase):
    """The places that send the user into Settings land on the right tab."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def build_window(self) -> LumiSyncMainWindow:
        patches = [
            patch.object(DeviceController, name)
            for name in ("refresh_device_states", "refresh_pc_hardware_states",
                         "refresh_local_device_states", "refresh_account_device_states")
        ]
        patches += [
            patch.object(DeviceController, "get_groups", return_value=[]),
            patch("lumisync.gui.controllers.device_controller.devices.get_data",
                  return_value={"devices": [], "selectedDevice": 0}),
            patch("lumisync.gui.main_window.QSystemTrayIcon.isSystemTrayAvailable", return_value=False),
            patch("lumisync.gui.utils.autostart.is_enabled", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        window = LumiSyncMainWindow()
        window.device_controller._store.write = Mock()
        window.settings.remove(LAST_TAB_KEY)

        def cleanup():
            window._quitting = True
            window.hide()
            window.device_controller.shutdown()
            window.sync_controller.shutdown()
            window.update_controller.shutdown()
            window.settings.remove(LAST_TAB_KEY)
            window.deleteLater()
            # Destroy it now: a live window would be restyled by every later
            # test's apply_theme and slow the rest of the run down.
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

        self.addCleanup(cleanup)
        return window

    def test_open_settings_section_selects_the_page_and_the_tab(self):
        window = self.build_window()
        window.settings_page.select_tab("general")
        for section, tab in (("groups", "sync"), ("updates", "updates"), ("about", "about"),
                             ("monitor", "sync"), ("general", "general")):
            with self.subTest(section=section):
                window.open_settings_section(section)
                self.assertEqual(window.nav_shell.current_key(), "settings")
                self.assertEqual(window.settings_page.current_tab(), tab)

    def test_the_settings_rail_dot_opens_the_updates_tab(self):
        window = self.build_window()
        page = window.settings_page
        page.select_tab("general")
        window.nav_shell.set_current_by_key("devices")
        window.nav_shell.set_current_by_key("settings")
        self.assertEqual(page.current_tab(), "general")  # no dot, no jump
        window.nav_shell.set_current_by_key("devices")
        window._set_update_badge(True)
        window.nav_shell.set_current_by_key("settings")
        self.assertEqual(page.current_tab(), "updates")
        window._set_update_badge(False)
        page.select_tab("about")
        window.nav_shell.set_current_by_key("devices")
        window.nav_shell.set_current_by_key("settings")
        self.assertEqual(page.current_tab(), "about")

    def test_clicking_the_update_notification_opens_the_updates_tab(self):
        window = self.build_window()
        page = window.settings_page
        page.select_tab("about")
        window.nav_shell.set_current_by_key("devices")
        window._tray_message_is_update = True
        window._update_offer_version = "99.0"
        with patch.object(window, "show_from_tray"), \
                patch("lumisync.gui.main_window.QTimer.singleShot") as later:
            window._on_tray_message_clicked()
        self.assertEqual(window.nav_shell.current_key(), "settings")
        self.assertEqual(page.current_tab(), "updates")
        later.assert_any_call(0, window.show_update_dialog)

    def test_other_tray_messages_do_not_open_settings(self):
        window = self.build_window()
        window.nav_shell.set_current_by_key("devices")
        window._tray_message_is_update = False
        with patch.object(window, "show_from_tray"):
            window._on_tray_message_clicked()
        self.assertEqual(window.nav_shell.current_key(), "devices")


if __name__ == "__main__":
    unittest.main()
