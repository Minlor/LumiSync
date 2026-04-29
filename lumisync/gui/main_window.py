"""
Main application window for the LumiSync GUI.
"""

import sys
from PyQt6.QtWidgets import QMainWindow, QMessageBox, QApplication
from PyQt6.QtCore import QSettings, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices

from .resources.icons import IconKey, icon as app_icon
from .widgets import NavigationShell
from .theme import apply_theme
from ..utils.logging import setup_logger

from .controllers.device_controller import DeviceController
from .controllers.sync_controller import SyncController
from .controllers.update_controller import UpdateController

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
        self.update_controller = UpdateController()

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
        self.update_controller.status_updated.connect(self.show_status)
        self.update_controller.update_available.connect(self._on_update_available)

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
        from .. import __version__ as _version

        QMessageBox.about(
            self, "About LumiSync",
            "<h2>LumiSync</h2>"
            "<p>Sync your Govee lights with your screen and audio.</p>"
            f"<p><b>Version:</b> {_version}</p>"
            "<p><a href='https://github.com/Minlor/LumiSync'>GitHub Repository</a></p>"
        )

    def check_for_updates_on_startup(self) -> None:
        self.update_controller.check_now()

    def _on_update_available(self, result) -> None:
        release_url = result.release_url or "https://github.com/Minlor/LumiSync/releases"
        latest = result.latest_version or "a newer version"

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("LumiSync Update Available")
        box.setText(f"LumiSync v{latest} is available.")
        box.setInformativeText(
            f"You are running v{result.current_version}. "
            "Open the GitHub release page to download the update."
        )
        open_button = box.addButton("Open Release", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(open_button)

        box.exec()
        if box.clickedButton() is open_button:
            QDesktopServices.openUrl(QUrl(release_url))

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
        QTimer.singleShot(1000, window.check_for_updates_on_startup)

        exit_code = app.exec()
        logger.info(f"LumiSync GUI closed (exit {exit_code})")
        sys.exit(exit_code)

    except Exception as e:
        logger.critical(f"Uncaught exception in GUI: {str(e)}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
