"""
Main application window for the LumiSync GUI.
This module contains the main application window and entry point for the PyQt6 GUI.
"""

import sys
from PyQt6.QtWidgets import (
    QMainWindow, QMessageBox, QApplication
)
from PyQt6.QtCore import Qt, QSettings
from qt_material import apply_stylesheet

from .resources.icons import IconKey, icon as app_icon
from .widgets import NavigationShell
from .dialogs.settings_dialog import SettingsDialog
from .utils import (
    apply_qt_translucent_background,
    enable_windows_dwm_blur,
    enable_windows_backdrop,
    WindowsBackdropType,
)
from ..utils.logging import setup_logger

# Import controllers (will be enhanced with signals)
from .controllers.device_controller import DeviceController
from .controllers.sync_controller import SyncController

# Set up logger
logger = setup_logger('lumisync_gui')


class LumiSyncMainWindow(QMainWindow):
    """Main application window for LumiSync."""

    def __init__(self):
        super().__init__()
        logger.info("Initializing LumiSync PyQt6 GUI application")

        # Window configuration
        self.setWindowTitle("LumiSync")
        self.setMinimumSize(800, 500)
        self.resize(900, 600)

        # Remove classic menu bar (we expose actions via sidebar/settings)
        self.setMenuBar(None)

        # Settings for saving window state
        self.settings = QSettings("Minlor", "LumiSync")

        # Theme tracking
        self._current_theme = 'dark_blue.xml'

        # Create controllers
        logger.info("Creating controllers")
        self.device_controller = DeviceController()
        self.sync_controller = SyncController()

        # Link controllers
        self.setup_controller_connections()

        # Set up UI
        self.setup_ui()
        self.setup_status_bar()

        # Set window icon
        self.set_icon()

        # Restore window geometry
        self.restore_geometry()

        # Optional modern effects (safe no-op on non-Windows)
        self._apply_window_effects()

        logger.info("LumiSync PyQt6 GUI application initialized successfully")

    def setup_controller_connections(self):
        """Connect controller signals to each other and the main window."""
        logger.debug("Setting up controller connections")

        # Link device selection to sync controller
        # When a device is selected in device controller, update sync controller
        self.device_controller.device_selected.connect(
            self.sync_controller.set_device
        )

        # Connect status updates to status bar
        self.device_controller.status_updated.connect(
            self.show_status
        )
        self.sync_controller.status_updated.connect(
            self.show_status
        )

        logger.debug("Controller connections established")

    def setup_ui(self):
        """Set up the user interface."""
        logger.debug("Setting up user interface")

        # Import views here to avoid circular imports
        from .views.devices_view import DevicesView
        from .views.modes_view import ModesView

        # Create views with controllers
        self.devices_view = DevicesView(self.device_controller)
        self.modes_view = ModesView(self.sync_controller)

        # Icon-only navigation rail
        self.nav_shell = NavigationShell(title="LumiSync", icon_only=True)
        self.setCentralWidget(self.nav_shell)

        self.nav_shell.add_page(
            key="devices",
            title="Devices",
            icon=app_icon(IconKey.NETWORK),
            widget=self.devices_view,
        )
        self.nav_shell.set_page_svg("devices", "network.svg")

        self.nav_shell.add_page(
            key="modes",
            title="Sync Modes",
            icon=app_icon(IconKey.SCREEN),
            widget=self.modes_view,
        )
        self.nav_shell.set_page_svg("modes", "screen.svg")

        # Bottom settings cog
        self.nav_shell.set_settings(
            icon_name="settings.svg",
            callback=self.show_settings,
        )

        logger.debug("User interface created")

    def show_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec():
            # Apply theme
            saved_theme = self.settings.value("theme", "dark_blue.xml")
            if saved_theme != self._current_theme:
                self._current_theme = str(saved_theme)
                apply_stylesheet(QApplication.instance(), theme=self._current_theme)

            # Re-apply effects right away after changing toggles
            self._apply_window_effects()

    def _apply_window_effects(self) -> None:
        """Apply optional translucency / backdrop effects."""

        enable_translucency = bool(self.settings.value("ui/translucent", True, type=bool))

        # Override qt-material's opaque background if effects are on
        if enable_translucency:
            self.setStyleSheet("QMainWindow { background: transparent; }")
        else:
            self.setStyleSheet("") # Restore theme default

        if enable_translucency:
            try:
                apply_qt_translucent_background(self)
            except Exception as e:
                logger.debug(f"Failed to enable translucent background: {e}")

        # Modern Windows backdrop (Win11+) - preferred
        enable_backdrop = bool(self.settings.value("ui/windows_backdrop", False, type=bool))
        if enable_backdrop:
            backdrop_type = str(self.settings.value("ui/windows_backdrop_type", "mica"))
            desired = WindowsBackdropType.MICA if backdrop_type == "mica" else WindowsBackdropType.TABBED
            try:
                hwnd = int(self.winId())
                ok = enable_windows_backdrop(hwnd, desired)
                if not ok:
                    # fallback to legacy blur
                    ok = enable_windows_dwm_blur(hwnd)
                logger.debug(f"Windows backdrop enabled: {ok} (type={backdrop_type})")
            except Exception as e:
                logger.debug(f"Failed to enable Windows backdrop: {e}")

    def setup_status_bar(self):
        """Set up the status bar."""
        # Optional: hide status bar for a cleaner UI; keep message logging.
        show_status = bool(self.settings.value("ui/status_bar", False, type=bool))
        self.statusBar().setVisible(show_status)
        if show_status:
            self.statusBar().showMessage("Ready")
        logger.debug("Status bar configured")

    def show_status(self, message: str, timeout: int = 5000):
        """Display status message in the status bar.

        Args:
            message: Status message to display
            timeout: Timeout in milliseconds (0 for permanent)
        """
        self.statusBar().showMessage(message, timeout)
        logger.info(f"Status: {message}")

    def toggle_theme(self):
        """Toggle between dark and light Material Design themes."""
        logger.info("Toggling theme")

        # Switch between dark and light themes
        if 'dark' in self._current_theme:
            new_theme = 'light_blue.xml'
        else:
            new_theme = 'dark_blue.xml'

        # Apply the new theme
        apply_stylesheet(QApplication.instance(), theme=new_theme)
        self._current_theme = new_theme

        # Save theme preference
        self.settings.setValue("theme", new_theme)

        self.show_status(f"Theme changed to {'light' if 'light' in new_theme else 'dark'} mode")
        logger.info(f"Theme changed to: {new_theme}")

    def show_about(self):
        """Show about dialog."""
        logger.debug("Showing about dialog")
        QMessageBox.about(
            self,
            "About LumiSync",
            "<h2>LumiSync</h2>"
            "<p>A program that allows you to easily sync your Govee lights.</p>"
            "<p><b>Version:</b> 0.3.0</p>"
            "<p><b>Built with:</b> PyQt6 & Qt Material Design</p>"
            "<p>&copy; 2023-2026 Minlor</p>"
            "<p><a href='https://github.com/Minlor/LumiSync'>GitHub Repository</a></p>"
        )

    def set_icon(self):
        """Set the application icon if available."""
        try:
            ico = app_icon(IconKey.APP)
            if not ico.isNull():
                self.setWindowIcon(ico)
                logger.debug("Application icon set")
            else:
                logger.debug("Icon file not found")
        except Exception as e:
            logger.warning(f"Failed to set application icon: {str(e)}")

    def restore_geometry(self):
        """Restore window geometry from settings."""
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
            logger.debug("Window geometry restored")

        # Restore theme preference
        saved_theme = self.settings.value("theme", "dark_blue.xml")
        if saved_theme != self._current_theme:
            self._current_theme = saved_theme
            apply_stylesheet(QApplication.instance(), theme=saved_theme)
            logger.debug(f"Theme restored: {saved_theme}")

    def closeEvent(self, event):
        """Handle window close event.

        Args:
            event: QCloseEvent
        """
        logger.info("Window close requested")

        # Ask for confirmation
        reply = QMessageBox.question(
            self,
            "Quit",
            "Do you want to quit LumiSync?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            logger.info("User confirmed quit")

            # Save window geometry
            self.settings.setValue("geometry", self.saveGeometry())
            logger.debug("Window geometry saved")

            # Stop any active sync
            if hasattr(self.sync_controller, 'stop_sync'):
                self.sync_controller.stop_sync()
                logger.info("Stopped active sync operations")

            # Accept the event (close the window)
            event.accept()
            logger.info("Application closing")
        else:
            # Ignore the event (keep the window open)
            event.ignore()
            logger.info("User canceled quit")


def main():
    """Main entry point for the PyQt6 GUI application."""
    logger.info("Starting LumiSync PyQt6 GUI application")

    try:
        # Create QApplication
        app = QApplication(sys.argv)
        app.setApplicationName("LumiSync")
        app.setOrganizationName("Minlor")

        # Apply Material Design theme
        logger.info("Applying Material Design theme")
        apply_stylesheet(app, theme='dark_blue.xml')

        # Create and show main window
        window = LumiSyncMainWindow()
        window.show()

        logger.info("Main window displayed")

        # Start event loop
        exit_code = app.exec()
        logger.info(f"LumiSync PyQt6 GUI application closed with exit code: {exit_code}")
        sys.exit(exit_code)

    except Exception as e:
        logger.critical(f"Uncaught exception in PyQt6 GUI application: {str(e)}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
