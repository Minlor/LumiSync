"""Main window chrome: no status bar, and Mica / Acrylic repaint from transparent."""

import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QRegion
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QStackedWidget, QStatusBar, QWidget

from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.main_window import LumiSyncMainWindow
from lumisync.gui.utils.animations import fade_swap_stack
from lumisync.gui.utils.window_effects import clear_backdrop_region, has_backdrop_material


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])


class NoStatusBarTests(_QtCase):
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

        def cleanup():
            window._quitting = True
            window.hide()
            window.device_controller.shutdown()
            window.sync_controller.shutdown()
            window.update_controller.shutdown()
            window.deleteLater()

        self.addCleanup(cleanup)
        return window

    def test_the_window_has_no_status_bar_and_settings_has_no_status_bar_row(self):
        window = self.build_window()
        # A stale preference from an older version must change nothing.
        window.settings.setValue("ui/status_bar", True)
        window.show()
        self.app.processEvents()
        window.show_status("Searching for devices…")
        window.show_error("Error: something failed")
        self.app.processEvents()
        self.assertIsNone(window.findChild(QStatusBar))
        self.assertFalse(hasattr(window, "setup_status_bar"))
        page = window.settings_page
        self.assertFalse(hasattr(page, "statusbar_check"))
        self.assertNotIn("Status bar", [label.text() for label in page.findChildren(QLabel)])


class BackdropRepaintTests(_QtCase):
    def window(self, backdrop: str) -> LumiSyncMainWindow:
        window = LumiSyncMainWindow.__new__(LumiSyncMainWindow)
        QMainWindow.__init__(window)
        self.addCleanup(window.deleteLater)
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, bool(backdrop))
        window.setProperty("backdrop", backdrop)
        window.resize(120, 80)
        return window

    @staticmethod
    def render_over_stale_pixels(widget: QWidget) -> QImage:
        image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(200, 30, 30))  # What the previous page left behind.
        # No DrawWindowBackground: only the window's own paintEvent may clear.
        widget.render(image, QPoint(0, 0), QRegion(), QWidget.RenderFlag.DrawChildren)
        return image

    def test_a_material_window_repaints_from_transparent(self):
        for material in ("mica", "acrylic"):
            with self.subTest(material=material):
                window = self.window(material)
                self.assertTrue(has_backdrop_material(window))
                image = self.render_over_stale_pixels(window)
                self.assertEqual(image.pixelColor(60, 40).alpha(), 0)

    def test_a_translucent_child_is_blended_over_transparent_not_over_the_old_page(self):
        window = self.window("mica")
        child = QWidget()
        child.setAutoFillBackground(True)
        palette = child.palette()
        palette.setColor(child.backgroundRole(), QColor(28, 28, 28, 158))
        child.setPalette(palette)
        window.setCentralWidget(child)
        image = self.render_over_stale_pixels(window)
        pixel = image.pixelColor(60, 40)
        self.assertAlmostEqual(pixel.alpha(), 158, delta=2)
        self.assertAlmostEqual(pixel.red(), 28, delta=3)

    def test_the_solid_theme_does_not_clear(self):
        window = self.window("")
        self.assertFalse(has_backdrop_material(window))
        image = self.render_over_stale_pixels(window)
        self.assertEqual(image.pixelColor(60, 40), QColor(200, 30, 30))

    def test_clear_backdrop_region_only_touches_the_region(self):
        class Probe(QWidget):
            def paintEvent(self, event):  # noqa: N802 - Qt API
                clear_backdrop_region(self, QRegion(0, 0, 10, 20))

        probe = Probe()
        self.addCleanup(probe.deleteLater)
        probe.resize(40, 20)
        image = self.render_over_stale_pixels(probe)
        self.assertEqual(image.pixelColor(5, 10).alpha(), 0)
        self.assertEqual(image.pixelColor(30, 10), QColor(200, 30, 30))


class MaterialPageFadeTests(_QtCase):
    def test_fade_with_a_material_ends_on_the_target_page_with_no_effects_left(self):
        window = QMainWindow()
        self.addCleanup(window.deleteLater)
        window.setProperty("backdrop", "mica")
        stack = QStackedWidget()
        pages = [QWidget(), QWidget()]
        for page in pages:
            stack.addWidget(page)
        window.setCentralWidget(stack)
        window.resize(200, 120)
        window.show()
        self.app.processEvents()
        fade_swap_stack(stack, 1, duration=40)
        QTest.qWait(400)
        self.assertEqual(stack.currentIndex(), 1)
        self.assertTrue(pages[1].isVisible())
        self.assertFalse(pages[0].isVisible())
        for page in pages:
            self.assertIsNone(page.graphicsEffect())


if __name__ == "__main__":
    unittest.main()
