"""
Main application window for the LumiSync GUI.
"""

import sys
from PyQt6.QtWidgets import QMainWindow, QMessageBox, QApplication
from PyQt6.QtCore import Qt, QSettings

from .resources.icons import IconKey, icon as app_icon
from .widgets import NavigationShell
from .theme import apply_theme
from ..utils.logging import setup_logger

from .controllers.device_controller import DeviceController
from .controllers.sync_controller import SyncController

logger = setup_logger('lumisync_gui')


class LumiSyncMainWindow(QMainWindow):
    """Main application window for LumiSync."""

    def __init__(self):
        super().__init__()
        logger.info("Initializing LumiSync GUI")

        self.setWindowTitle("LumiSync")
        self.setMinimumSize(900, 560)
        self.resize(1040, 680)
        self.setMenuBar(None)

        self.settings = QSettings("Minlor", "LumiSync")

        logger.info("Creating controllers")
        self.device_controller = DeviceController()
        self.sync_controller = SyncController()

        self.setup_controller_connections()
        self.setup_ui()
        self.setup_status_bar()
        self.set_icon()
        self.restore_geometry()

        logger.info("LumiSync GUI initialized")

    def setup_controller_connections(self):
        self.device_controller.device_selected.connect(self.sync_controller.set_device)
        self.device_controller.status_updated.connect(self.show_status)
        self.sync_controller.status_updated.connect(self.show_status)

        device = self.device_controller.get_selected_device()
        if device:
            self.sync_controller.set_device(device)

    def setup_ui(self):
        from .views.devices_view import DevicesView
        from .views.modes_view import ModesView
        from .views.settings_page import SettingsPage

        self.devices_view = DevicesView(self.device_controller)
        self.modes_view = ModesView(self.sync_controller, self.device_controller)

        self.nav_shell = NavigationShell(title="LumiSync", icon_only=True)
        self.setCentralWidget(self.nav_shell)

        try:
            display_index = int(self.settings.value("sync/monitor_display", 0))
        except Exception:
            display_index = 0
        self.sync_controller.set_monitor_display(display_index)

        self.nav_shell.add_page(
            key="devices", title="Devices",
            icon=app_icon(IconKey.NETWORK), widget=self.devices_view,
        )
        self.nav_shell.set_page_svg("devices", "network.svg")

        self.nav_shell.add_page(
            key="modes", title="Sync Modes",
            icon=app_icon(IconKey.SCREEN), widget=self.modes_view,
        )
        self.nav_shell.set_page_svg("modes", "screen.svg")

        self.settings_page = SettingsPage(self.settings, self)
        self.nav_shell.add_page(
            key="settings", title="Settings",
            icon=app_icon(IconKey.SETTINGS), widget=self.settings_page, bottom=True,
        )
        self.nav_shell.set_page_svg("settings", "settings.svg")

    def setup_status_bar(self):
        show_status = bool(self.settings.value("ui/status_bar", False, type=bool))
        self.statusBar().setVisible(show_status)
        if show_status:
            self.statusBar().showMessage("Ready")

    def show_status(self, message: str, timeout: int = 5000):
        self.statusBar().showMessage(message, timeout)
        logger.info(f"Status: {message}")

    def show_about(self):
        QMessageBox.about(
            self, "About LumiSync",
            "<h2>LumiSync</h2>"
            "<p>Sync your Govee lights with your screen and audio.</p>"
            "<p><b>Version:</b> 0.4.1</p>"
            "<p><a href='https://github.com/Minlor/LumiSync'>GitHub Repository</a></p>"
        )

    def set_icon(self):
        try:
            ico = app_icon(IconKey.APP)
            if not ico.isNull():
                self.setWindowIcon(ico)
        except Exception as e:
            logger.warning(f"Failed to set application icon: {str(e)}")

    def restore_geometry(self):
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def closeEvent(self, event):
        reply = QMessageBox.question(
            self, "Quit", "Do you want to quit LumiSync?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.settings.setValue("geometry", self.saveGeometry())
            if hasattr(self.sync_controller, 'stop_sync'):
                self.sync_controller.stop_sync()
            event.accept()
        else:
            event.ignore()


def main():
    """Entry point for the GUI."""
    logger.info("Starting LumiSync GUI")

    try:
        app = QApplication(sys.argv)
        app.setApplicationName("LumiSync")
        app.setOrganizationName("Minlor")

        apply_theme(app)

        window = LumiSyncMainWindow()
        window.show()

        exit_code = app.exec()
        logger.info(f"LumiSync GUI closed (exit {exit_code})")
        sys.exit(exit_code)

    except Exception as e:
        logger.critical(f"Uncaught exception in GUI: {str(e)}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
