"""
Main application window for the LumiSync GUI.
This module contains the main application window and entry point for the PyQt6 GUI.
"""

import sys
from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QMessageBox, QApplication
)
from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QAction
from qt_material import apply_stylesheet

from .resources import ResourceManager
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
        self.setup_menus()
        self.setup_status_bar()

        # Set window icon
        self.set_icon()

        # Restore window geometry
        self.restore_geometry()

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

        # Create tab widget as central widget
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # Import views here to avoid circular imports
        from .views.devices_view import DevicesView
        from .views.modes_view import ModesView

        # Create views with controllers
        self.devices_view = DevicesView(self.device_controller)
        self.modes_view = ModesView(self.sync_controller)

        # Add tabs
        self.tabs.addTab(self.devices_view, "Devices")
        self.tabs.addTab(self.modes_view, "Modes")

        logger.debug("User interface created")

    def setup_menus(self):
        """Set up the menu bar."""
        logger.debug("Setting up menu bar")
        menubar = self.menuBar()

        # File menu
        file_menu = menubar.addMenu("&File")

        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.setStatusTip("Exit application")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # View menu
        view_menu = menubar.addMenu("&View")

        theme_action = QAction("Toggle &Theme", self)
        theme_action.setShortcut("Ctrl+T")
        theme_action.setStatusTip("Switch between dark and light themes")
        theme_action.triggered.connect(self.toggle_theme)
        view_menu.addAction(theme_action)

        # Help menu
        help_menu = menubar.addMenu("&Help")

        about_action = QAction("&About", self)
        about_action.setStatusTip("About LumiSync")
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

        logger.debug("Menu bar created")

    def setup_status_bar(self):
        """Set up the status bar."""
        self.statusBar().showMessage("Ready")
        logger.debug("Status bar created")

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
            icon = ResourceManager.get_icon("lightbulb-on.png")
            if not icon.isNull():
                self.setWindowIcon(icon)
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
