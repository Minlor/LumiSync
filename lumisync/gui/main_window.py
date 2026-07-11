"""
Main application window for the LumiSync GUI.
"""

import sys
from PySide6.QtWidgets import QMainWindow, QMenu, QMessageBox, QApplication, QSystemTrayIcon
from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices

from .resources.icons import IconKey, icon as app_icon
from .widgets import NavigationShell
from .widgets.toast import ToastManager
from .theme import apply_theme
from ..utils.logging import setup_logger

from .controllers.device_controller import DeviceController
from .controllers.sync_controller import SyncController, load_sync_settings
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
        load_sync_settings(self.settings)

        # If Govee Desktop is installed, learn real per-device segment counts and
        # capabilities from its local cache. No-op on Linux / when absent.
        from ..sku_catalog import import_govee_desktop_cache
        try:
            import_govee_desktop_cache()
        except Exception:
            pass

        self.toasts = ToastManager(self)

        logger.info("Creating controllers")
        self.device_controller = DeviceController()
        self.sync_controller = SyncController()
        self.update_controller = UpdateController()

        self._quitting = False

        self.setup_controller_connections()
        self.setup_ui()
        self.setup_status_bar()
        self.set_icon()
        self.setup_tray()
        self.restore_geometry()

        logger.info("LumiSync GUI initialized")

    def setup_controller_connections(self):
        self.device_controller.device_selected.connect(self.sync_controller.set_device)
        self.device_controller.status_updated.connect(self.show_status)
        self.sync_controller.status_updated.connect(self.show_status)
        self.sync_controller.sync_error.connect(self.show_error)
        self.update_controller.status_updated.connect(self.show_status)
        self.update_controller.update_available.connect(self._on_update_available)

        device = self.device_controller.get_selected_device()
        if device:
            self.sync_controller.set_device(device)

    def setup_ui(self):
        from .views.devices_view import DevicesView
        from .views.draw_view import DrawView
        from .views.modes_view import ModesView
        from .views.settings_page import SettingsPage

        self.devices_view = DevicesView(self.device_controller)
        self.modes_view = ModesView(self.sync_controller, self.device_controller)
        self.draw_view = DrawView(self.device_controller)

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

        self.nav_shell.add_page(
            key="draw", title="Draw",
            icon=app_icon(IconKey.DRAW), widget=self.draw_view,
        )
        self.nav_shell.set_page_svg("draw", "pencil.svg")

        self.settings_page = SettingsPage(self.settings, self)
        self.nav_shell.add_page(
            key="settings", title="Settings",
            icon=app_icon(IconKey.SETTINGS), widget=self.settings_page, bottom=True,
        )
        self.nav_shell.set_page_svg("settings", "settings.svg")

    def setup_tray(self):
        """System tray icon so the app can keep syncing with the window closed."""
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.info("System tray unavailable; running without a tray icon")
            return

        tray = QSystemTrayIcon(self)
        tray.setIcon(app_icon(IconKey.APP))
        tray.setToolTip("LumiSync")

        menu = QMenu(self)
        open_action = menu.addAction("Open LumiSync")
        open_action.triggered.connect(self.show_from_tray)
        menu.addSeparator()
        stop_action = menu.addAction("Stop All Syncs")
        stop_action.triggered.connect(self.sync_controller.stop_sync)
        menu.addSeparator()
        quit_action = menu.addAction("Quit LumiSync")
        quit_action.triggered.connect(self.quit_app)

        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self.tray = tray

    def show_from_tray(self):
        self.show()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.show_from_tray()

    def _minimize_to_tray_enabled(self) -> bool:
        raw = self.settings.value("ui/minimize_to_tray", True)
        return str(raw).lower() in ("true", "1")

    def quit_app(self):
        """Quit deliberately (tray menu) — skips the tray-hide and the prompt."""
        self._quitting = True
        self.close()

    def setup_status_bar(self):
        raw = self.settings.value("ui/status_bar", False)
        show_status = str(raw).lower() in ("true", "1")
        self.statusBar().setVisible(show_status)
        if show_status:
            self.statusBar().showMessage("Ready")

    def show_status(self, message: str, timeout: int = 5000):
        if self.statusBar().isVisible():
            self.statusBar().showMessage(message, timeout)
        self.toasts.show(message)
        logger.info(f"Status: {message}")

    def show_error(self, message: str):
        if self.statusBar().isVisible():
            self.statusBar().showMessage(message, 8000)
        self.toasts.error(message)
        logger.error(f"Error: {message}")

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
        # Closing the window hides to the tray (syncs keep running) unless the
        # user quit deliberately or disabled the tray behavior in Settings.
        if (
            not self._quitting
            and self.tray is not None
            and self._minimize_to_tray_enabled()
        ):
            event.ignore()
            self.hide()
            notice_shown = str(
                self.settings.value("ui/tray_notice_shown", False)
            ).lower() in ("true", "1")
            if not notice_shown:
                self.tray.showMessage(
                    "LumiSync is still running",
                    "Syncing continues in the background. Right-click the "
                    "tray icon to quit, or disable this in Settings.",
                )
                self.settings.setValue("ui/tray_notice_shown", True)
            return

        if not self._quitting:
            reply = QMessageBox.question(
                self, "Quit", "Do you want to quit LumiSync?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return

        self.settings.setValue("geometry", self.saveGeometry())
        if hasattr(self.sync_controller, 'stop_sync'):
            self.sync_controller.stop_sync()
        draw_view = getattr(self, "draw_view", None)
        if draw_view is not None:
            try:
                draw_view._stop_send()
            except Exception:
                pass
        # Close any persistent BLE connections.
        try:
            from ..drivers import pool
            pool.close_all()
        except Exception:
            pass
        if self.tray is not None:
            self.tray.hide()
        event.accept()
        QApplication.instance().quit()


def main():
    """Entry point for the GUI."""
    logger.info("Starting LumiSync GUI")

    try:
        app = QApplication(sys.argv)
        app.setApplicationName("LumiSync")
        app.setOrganizationName("Minlor")
        # The window can hide to the tray; the tray Quit action ends the app.
        app.setQuitOnLastWindowClosed(False)

        from .utils.single_instance import SingleInstance
        guard = SingleInstance(app)
        if not guard.try_acquire():
            logger.info("LumiSync is already running; asked it to show itself")
            sys.exit(0)

        _install_excepthook()

        apply_theme(app)

        from .utils.window_effects import install_dark_titlebar_filter
        install_dark_titlebar_filter(app)

        window = LumiSyncMainWindow()
        window.show()
        guard.activate_requested.connect(window.show_from_tray)
        QTimer.singleShot(1000, window.check_for_updates_on_startup)

        exit_code = app.exec()
        logger.info(f"LumiSync GUI closed (exit {exit_code})")
        sys.exit(exit_code)

    except Exception as e:
        logger.critical(f"Uncaught exception in GUI: {str(e)}", exc_info=True)
        raise


def _install_excepthook():
    """Log uncaught exceptions and tell the user instead of dying silently.

    Qt swallows Python exceptions raised inside slots/event handlers and, on
    PySide6, terminates the process without any UI feedback. This hook writes
    the traceback to the log and shows a dialog pointing at the log file.
    """
    from ..utils.logging import get_logs_directory

    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
        )
        try:
            QMessageBox.critical(
                None,
                "LumiSync — Unexpected Error",
                f"Something went wrong:\n\n{exc_type.__name__}: {exc_value}\n\n"
                f"Details were written to the logs in:\n{get_logs_directory()}",
            )
        except Exception:
            pass

    sys.excepthook = hook


if __name__ == "__main__":
    main()
