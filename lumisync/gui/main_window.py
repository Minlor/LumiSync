"""
Main application window for the LumiSync GUI.
"""

import os
import sys
import time
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QSystemTrayIcon,
)
from PySide6.QtCore import QProcess, Qt, QThread, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QDesktopServices

from ..updates import is_installed_build, select_update_asset

from .app_settings import app_settings

from .resources.icons import IconKey, icon as app_icon
from .widgets import NavigationShell
from .widgets.toast import ToastManager
from .theme import apply_theme
from ..utils.logging import setup_logger

from .controllers.device_controller import DeviceController
from .controllers.firewall_monitor import FirewallMonitor
from .controllers.sync_controller import SyncController
from .controllers.update_controller import UpdateController

logger = setup_logger('lumisync_gui')

# Prevent a repeating timer's exception from stacking modal error dialogs.
_error_dialog_open = False
_recent_error_times = {}


def _quit_application() -> None:
    app = QApplication.instance()
    if app is not None:
        app.quit()


class LumiSyncMainWindow(QMainWindow):
    """Main application window for LumiSync."""

    def __init__(self):
        super().__init__()
        logger.info("Initializing LumiSync GUI")

        self.setWindowTitle("LumiSync")
        self.setMinimumSize(800, 520)
        self.resize(1120, 760)
        self.setMenuBar(None)  # pyright: ignore[reportArgumentType]

        self.settings = app_settings()

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
        # Shared Windows Firewall state for the Devices and Settings pages.
        self.firewall_monitor = FirewallMonitor(self)

        self._quitting = False
        self._shutdown_started = False
        self._shutdown_ready = False
        self._shutdown_deadline = None
        self._update_progress: QProgressDialog | None = None
        self._update_timer: QTimer | None = None
        self._update_offer_version: str | None = None
        self._update_download_interactive = True
        self._tray_message_is_update = False

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
        self.device_controller.notice.connect(self.show_notice)
        self.sync_controller.status_updated.connect(self.show_status)
        self.sync_controller.sync_error.connect(self.show_error)
        self.sync_controller.sync_started.connect(self._on_sync_started)
        self.sync_controller.sync_stopped.connect(
            self.device_controller.clear_sync_activity
        )
        self.update_controller.status_updated.connect(self.show_status)
        self.update_controller.update_available.connect(self._on_update_available)
        self.update_controller.download_progress.connect(self._on_update_progress)
        self.update_controller.download_finished.connect(self._on_update_downloaded)
        self.update_controller.download_failed.connect(self._on_update_download_failed)

        device = self.device_controller.get_selected_device()
        if device:
            self.sync_controller.set_device(device)

    def _on_sync_started(self, mode: str) -> None:
        self.device_controller.mark_sync_active(
            mode, self.sync_controller.get_selected_devices()
        )

    def setup_ui(self):
        from .views.devices_view import DevicesView
        from .views.draw_view import DrawView
        from .views.modes_view import ModesView
        from .views.settings_page import SettingsPage

        self.devices_view = DevicesView(self.device_controller, self.firewall_monitor)
        self.monitor_sync_view = ModesView(
            self.sync_controller, self.device_controller, mode="monitor"
        )
        self.music_sync_view = ModesView(
            self.sync_controller, self.device_controller, mode="music"
        )
        # Keep the old attribute as a compatibility alias for integrations that
        # used it before Monitor and Music became first-class destinations.
        self.modes_view = self.monitor_sync_view
        self.draw_view = DrawView(self.device_controller)

        # A compact Fluent-style rail: an icon with a short label per page.
        # The full page title stays in the tooltip and accessibility tree.
        self.nav_shell = NavigationShell(title="LumiSync", icon_only=True)
        self.setCentralWidget(self.nav_shell)

        self.nav_shell.add_page(
            key="devices", title="Devices",
            icon=app_icon(IconKey.NETWORK), widget=self.devices_view,
        )
        self.nav_shell.set_page_svg("devices", "network.svg")

        self.nav_shell.add_page(
            key="monitor", title="Monitor Sync", label="Monitor",
            icon=app_icon(IconKey.SCREEN), widget=self.monitor_sync_view,
        )
        self.nav_shell.set_page_svg("monitor", "screen.svg")

        self.nav_shell.add_page(
            key="music", title="Music Sync", label="Music",
            icon=app_icon(IconKey.MUSIC), widget=self.music_sync_view,
        )
        self.nav_shell.set_page_svg("music", "music.svg")

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

        # Draw only matters with a pixel panel; offer it once one exists.
        for signal in (
            self.device_controller.devices_discovered,
            self.device_controller.device_added,
            self.device_controller.device_removed,
            self.device_controller.device_updated,
        ):
            signal.connect(self._update_draw_page_visibility)
        self._update_draw_page_visibility()

    def _update_draw_page_visibility(self, *_args) -> None:
        from ..drivers.registry import capabilities_for_device

        def is_matrix(device) -> bool:
            if str(device.get("transport") or "").lower() == "ble":
                return True
            try:
                return capabilities_for_device(device).is_matrix
            except Exception:
                return False

        self.nav_shell.set_page_visible(
            "draw", any(is_matrix(device) for device in self.device_controller.devices)
        )

    def setup_tray(self):
        """System tray icon so the app can keep syncing with the window closed."""
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.info("System tray unavailable; running without a tray icon")
            return

        tray = QSystemTrayIcon(self)
        tray.setIcon(app_icon(IconKey.TRAY))
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
        tray.messageClicked.connect(self._on_tray_message_clicked)
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

    def _on_tray_message_clicked(self) -> None:
        self.show_from_tray()
        is_update = self._tray_message_is_update
        self._tray_message_is_update = False
        if not is_update:
            return
        if self.update_controller.ready_installer:
            QTimer.singleShot(0, self._offer_ready_update)
        elif self._update_offer_version:
            QTimer.singleShot(0, self.show_update_dialog)

    def _minimize_to_tray_enabled(self) -> bool:
        raw = self.settings.value("ui/minimize_to_tray", True)
        return str(raw).lower() in ("true", "1")

    def quit_app(self):
        """Quit deliberately (tray menu) — skips the tray-hide and the prompt."""
        self._quitting = True
        if self.tray is not None:
            # Hide the icon first: on a slow shutdown the app should visibly
            # be on its way out rather than appearing to ignore the action.
            self.tray.hide()
        try:
            self.sync_controller.shutdown()
        except Exception:
            pass
        self.close()

    def setup_status_bar(self):
        raw = self.settings.value("ui/status_bar", True)
        show_status = str(raw).lower() in ("true", "1")
        self.statusBar().setVisible(show_status)
        if show_status:
            self.statusBar().showMessage("Ready")

    def set_window_material(self, material: str, *, notify: bool = True) -> str:
        """Apply and persist the requested window material.

        Returns the material that is actually active. Unsupported translucent
        materials fall back to the solid blue theme.
        """

        from .utils.window_effects import (
            apply_windows_acrylic,
            apply_windows_mica,
            disable_windows_backdrop,
        )

        requested = str(material or "acrylic").strip().lower()
        if requested not in {"acrylic", "mica", "solid"}:
            requested = "acrylic"

        if requested == "acrylic":
            applied = apply_windows_acrylic(self)
        elif requested == "mica":
            applied = apply_windows_mica(self)
        else:
            applied = disable_windows_backdrop(self)

        active = requested if applied else "solid"
        if not applied:
            disable_windows_backdrop(self)

        self.settings.setValue("ui/window_material", active)
        settings_page = getattr(self, "settings_page", None)
        if settings_page is not None:
            settings_page.set_window_material_value(active)

        if notify:
            labels = {
                "acrylic": "Acrylic",
                "mica": "Mica",
                "solid": "Solid Dark",
            }
            if active == requested:
                self.show_status(f"Window material changed to {labels[active]}.")
            else:
                self.show_error(
                    f"{labels[requested]} is unavailable; using Solid Dark."
                )
        return active

    def show_status(self, message: str, timeout: int = 5000):
        """Status: the status bar (when shown) and the log. Never a toast.

        Progress and confirmations ("Imported 4 devices", "Searching…") are
        status. Anything the user must see now is a notice (show_notice) or
        an error (show_error); page-level problems are InfoBars on the page.
        """
        configured = str(
            self.settings.value("ui/status_bar", True)
        ).lower() in ("true", "1")
        if configured:
            self.statusBar().showMessage(message, timeout)
        logger.info(f"Status: {message}")

    def show_notice(self, message: str, kind: str = "info"):
        """Notice: a toast. Controllers also send it as a status for the bar."""
        if kind == "error":
            self.toasts.error(message)
            logger.warning("Notice: %s", message)
            return
        if kind == "success":
            self.toasts.success(message)
        elif kind == "warning":
            self.toasts.warning(message)
        else:
            self.toasts.show(message)
        logger.info("Notice: %s", message)

    def show_error(self, message: str):
        self.statusBar().showMessage(message, 8000)
        self.toasts.error(message)
        logger.error(f"Error: {message}")

    def show_about(self):
        from .. import __version__ as _version

        QMessageBox.about(
            self, "About LumiSync",
            "<h2>LumiSync</h2>"
            "<p>Sync smart lights and pixel panels with your screen and audio.</p>"
            f"<p><b>Version:</b> {_version}</p>"
            "<p><a href='https://github.com/Minlor/LumiSync'>GitHub Repository</a></p>"
        )

    # ------------------------------------------------------------------ updates

    # A tray app can run for weeks; look for a new release twice a day.
    UPDATE_RECHECK_MS = 12 * 60 * 60 * 1000

    def check_for_updates_on_startup(self) -> None:
        self.update_controller.check_now(silent=True)
        if self._update_timer is None:
            timer = QTimer(self)
            timer.setInterval(self.UPDATE_RECHECK_MS)
            timer.timeout.connect(
                lambda: self.update_controller.check_now(silent=True)
            )
            timer.start()
            self._update_timer = timer

    def check_firewall_on_startup(self) -> None:
        """Read Windows Firewall once after startup (installed builds only).

        The result lands in the log as "Windows Firewall: <status>" and drives
        the Devices InfoBar and the Settings card.
        """
        if not self.firewall_monitor.check():
            logger.debug("Windows Firewall: not checked (not an installed Windows build)")

    def _update_is_skipped(self, result) -> bool:
        skipped = str(self.settings.value("updates/skipped_version", "") or "").strip()
        latest = str(result.latest_version or "").strip()
        return bool(skipped and latest and skipped == latest)

    def _auto_download_updates(self) -> bool:
        raw = self.settings.value("updates/auto_download", False)
        return str(raw).lower() in ("true", "1")

    def can_install_update(self, result) -> bool:
        """Whether this copy can download and run the release installer.

        Only the packaged Windows app replaces itself. A source checkout or a
        pip install opens the release page instead of installing a second copy.
        """
        if result is None or sys.platform != "win32" or not is_installed_build():
            return False
        asset = select_update_asset(result.assets)
        return asset is not None and "setup" in asset.name.casefold()

    def _notify(self, message: str) -> None:
        """Tell the user something without stealing focus."""
        if self.isVisible() and not self.isMinimized():
            self.toasts.show(message, duration=8000)
        elif self.tray is not None and QSystemTrayIcon.supportsMessages():
            self._tray_message_is_update = True
            self.tray.showMessage("LumiSync", message, app_icon(IconKey.APP), 8000)
        logger.info("Notice: %s", message)

    def _set_update_badge(self, visible: bool) -> None:
        try:
            self.nav_shell.set_page_badge("settings", visible)
        except Exception:
            pass

    def _on_update_available(self, result) -> None:
        latest = result.latest_version or "a newer version"
        silent = bool(getattr(self.update_controller, "last_check_silent", False))
        if not silent:
            self._set_update_badge(True)
            self.show_update_dialog(result)
            return

        # Background checks never open a modal dialog: the window may be
        # hidden in the tray right after sign-in.
        if self._update_is_skipped(result):
            logger.info("LumiSync v%s is available but was skipped", latest)
            return
        if self._update_offer_version == latest:
            return  # Already offered; the periodic recheck stays quiet.
        self._update_offer_version = latest
        self._set_update_badge(True)
        if self.can_install_update(result) and self._auto_download_updates():
            self._start_update_download(result, interactive=False)
            return
        self._notify(
            f"LumiSync v{latest} is available. Open Settings to update."
        )

    def show_update_dialog(self, result=None) -> None:
        """Offer the newest release: install it, open it, skip it or wait."""
        result = result or self.update_controller.last_result
        if result is None or not result.is_update_available:
            return
        if self.update_controller.ready_installer:
            self._offer_ready_update()
            return
        release_url = result.release_url or "https://github.com/Minlor/LumiSync/releases"
        latest = result.latest_version or "a newer version"
        skipped = self._update_is_skipped(result)
        can_install = self.can_install_update(result)

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("LumiSync Update Available")
        box.setText(f"LumiSync v{latest} is available.")
        note = " You skipped this version earlier." if skipped else ""
        if can_install:
            box.setInformativeText(
                f"You are running v{result.current_version}.{note} Download and "
                "install it now? The download is checked against the release's "
                "SHA-256 checksum before it runs, and your settings are kept."
            )
            download_button = box.addButton(
                "Download && Install", QMessageBox.ButtonRole.AcceptRole
            )
        else:
            box.setInformativeText(
                f"You are running v{result.current_version}.{note} "
                "Open the release page to download it."
            )
            download_button = None
        open_button = box.addButton(
            "Release Notes" if can_install else "Open Release",
            QMessageBox.ButtonRole.ActionRole,
        )
        skip_button = None
        if result.latest_version and not skipped:
            skip_button = box.addButton(
                "Skip This Version", QMessageBox.ButtonRole.ActionRole
            )
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(download_button or open_button)
        body = (getattr(result, "body", None) or "").strip()
        if body:
            box.setDetailedText(body[:4000])

        box.exec()
        clicked = box.clickedButton()
        if download_button is not None and clicked is download_button:
            self._start_update_download(result, interactive=True)
        elif skip_button is not None and clicked is skip_button:
            self.settings.setValue("updates/skipped_version", str(latest))
            self._set_update_badge(False)
            self.show_status(f"Skipped LumiSync v{latest}.")
        elif clicked is open_button:
            QDesktopServices.openUrl(QUrl(release_url))

    def _start_update_download(self, result, *, interactive: bool = True) -> None:
        self.settings.remove("updates/skipped_version")
        self._update_download_interactive = interactive
        if interactive:
            progress = QProgressDialog(
                f"Downloading LumiSync v{result.latest_version or ''}...",
                "",
                0,
                100,
                self,
            )
            progress.setWindowTitle("LumiSync Update")
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setMinimumDuration(0)
            progress.setAutoClose(False)
            progress.setAutoReset(False)
            progress.setCancelButton(None)
            progress.setValue(0)
            progress.show()
            self._update_progress = progress
        self.update_controller.download_and_install(result)

    def _close_update_progress(self) -> None:
        progress = self._update_progress
        self._update_progress = None
        if progress is not None:
            progress.close()
            progress.deleteLater()

    def _on_update_progress(self, written: int, total: int) -> None:
        progress = self._update_progress
        if progress is None:
            return
        if total > 0:
            progress.setMaximum(100)
            progress.setValue(max(0, min(100, int(written * 100 / total))))
        else:
            progress.setMaximum(0)  # busy indicator when the size is unknown

    def _on_update_download_failed(self, message: str) -> None:
        self._close_update_progress()
        if not self._update_download_interactive:
            # A background download failing is not worth a dialog; the
            # Settings page still offers the update by hand.
            logger.warning("Background update download failed: %s", message)
            return
        QMessageBox.warning(
            self,
            "Update Download Failed",
            f"{message}\n\nYou can still download the release from GitHub.",
        )

    def _on_update_downloaded(self, _path: str) -> None:
        self._close_update_progress()
        self._set_update_badge(True)
        if self._update_download_interactive and self.isVisible():
            self._offer_ready_update()
            return
        result = self.update_controller.last_result
        latest = getattr(result, "latest_version", None) or "The update"
        self._notify(
            f"LumiSync v{latest} is downloaded. Open Settings to restart and update."
        )

    def _offer_ready_update(self) -> None:
        path = self.update_controller.ready_installer
        if not path:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle("LumiSync Update Ready")
        box.setText("The update was downloaded and verified.")
        box.setInformativeText(
            "LumiSync closes while the installer runs, then starts again. "
            "Your devices and settings are kept."
        )
        install_button = box.addButton(
            "Restart && Update", QMessageBox.ButtonRole.AcceptRole
        )
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(install_button)
        box.exec()
        if box.clickedButton() is install_button:
            self.install_ready_update()
        else:
            self.show_status("The update is ready. Install it from Settings when you like.")

    def install_ready_update(self) -> None:
        """Run the verified installer silently and quit so it can replace us."""
        path = self.update_controller.ready_installer
        if not path or not os.path.isfile(path):
            self.update_controller.ready_installer = None
            QMessageBox.warning(
                self,
                "Update Installer",
                "The downloaded update is no longer available. Check for updates again.",
            )
            return
        try:
            # /launch=1 restarts the new version; /update=1 keeps the user's
            # current auto-start choice. Restart Manager is not asked to
            # relaunch anything, so only one copy comes back.
            started = QProcess.startDetached(
                path,
                [
                    "/SILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                    "/CLOSEAPPLICATIONS",
                    "/launch=1",
                    "/update=1",
                ],
            )
            if isinstance(started, tuple):
                started = started[0]
        except Exception as exc:
            logger.warning("Could not start the update installer: %s", exc)
            started = False

        if not started:
            QMessageBox.warning(
                self,
                "Update Installer",
                f"Could not start the installer at:\n{path}\n\n"
                "Open its folder and run it manually.",
            )
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
            return

        self.quit_app()

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
        if not self.isMaximized() and not self.isFullScreen():
            available = self.screen().availableGeometry().adjusted(12, 12, -12, -36)
            self.resize(min(self.width(), available.width()), min(self.height(), available.height()))
            self.move(max(available.left(), min(self.x(), available.right() - self.width() + 1)),
                      max(available.top(), min(self.y(), available.bottom() - self.height() + 1)))

    def closeEvent(self, event):
        if self._shutdown_ready:
            if self.tray is not None:
                self.tray.hide()
            event.accept()
            _quit_application()
            return
        if self._shutdown_started:
            event.ignore()
            return
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
                self._tray_message_is_update = False
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
        self._shutdown_started = True
        self._quitting = True
        # A stuck background task (account refresh, scan, blocked socket) must
        # never leave the app half-dead in the tray: force the exit after this.
        self._shutdown_deadline = time.monotonic() + 4.0
        self.setEnabled(False)
        # Keep the event loop alive while bounded I/O drains. Destroying a
        # running QThread or closing its pooled connection can crash Qt.
        event.ignore()
        try:
            self.device_controller.shutdown()
            self.update_controller.shutdown()
            if hasattr(self.sync_controller, 'shutdown'):
                self.sync_controller.shutdown()
            else:
                if hasattr(self.sync_controller, 'stop_sync'):
                    self.sync_controller.stop_sync()
                if hasattr(self.sync_controller, 'flush_sync_settings'):
                    self.sync_controller.flush_sync_settings()
            draw_view = getattr(self, "draw_view", None)
            if draw_view is not None:
                draw_view._stop_send()
        except Exception:
            # Never leave a disabled window that ignores every later close.
            logger.exception("A shutdown step failed; continuing to exit")
        finally:
            self._finish_shutdown()

    def _force_exit(self) -> None:
        """Save everything we can, then leave without waiting on a stuck thread.

        Destroying a running QThread (or a native BLE connect that outlives the
        deadline) aborts the process from Qt's cleanup path. Exiting directly
        keeps the saved settings intact and avoids that crash.
        """
        logger.warning("Shutdown deadline exceeded; forcing process exit")
        try:
            self.settings.setValue("geometry", self.saveGeometry())
            self.settings.sync()
        except Exception:
            pass
        try:
            self.sync_controller.flush_sync_settings()
        except Exception:
            pass
        try:
            self.device_controller._adopt_pooled_discovery()
        except Exception:
            pass
        os._exit(0)

    def _on_about_to_quit(self) -> None:
        """Best-effort cleanup when the session ends without a closeEvent.

        Windows sign-out and shutdown end the event loop directly. Stop any
        running sync so the lights leave stream mode, and save what we can.
        """
        if self._shutdown_ready:
            return
        for step in (
            self.sync_controller.stop_sync,
            self.sync_controller.flush_sync_settings,
        ):
            try:
                step()
            except Exception:
                logger.debug("Session-end cleanup step failed", exc_info=True)
        try:
            self.settings.setValue("geometry", self.saveGeometry())
            self.settings.sync()
        except Exception:
            pass

    def _close_connections_bounded(self) -> None:
        """Close pooled devices and account sessions without hanging the exit.

        Bluetooth panels and MQTT sessions can each take seconds to close.
        The installer waits about 12 s for us to quit, so the closes run on a
        helper thread and get whatever remains of the shutdown deadline.
        """
        import threading

        from ..drivers import pool
        from ..accounts.manager import account_manager

        def close_all() -> None:
            for closer in (pool.close_all, account_manager.close_all):
                try:
                    closer()
                except Exception:
                    logger.debug("Closing connections failed", exc_info=True)

        worker = threading.Thread(target=close_all, name="lumisync-close", daemon=True)
        worker.start()
        deadline = self._shutdown_deadline or (time.monotonic() + 4.0)
        worker.join(max(0.5, deadline - time.monotonic() + 2.0))
        if worker.is_alive():
            logger.warning("Device connections are still closing; exiting anyway")

    def _finish_shutdown(self) -> None:
        try:
            draw_view = getattr(self, "draw_view", None)
            draw_thread = getattr(draw_view, "_thread", None)
            draw_running = draw_thread is not None and not draw_thread.wait(0)
            overdue = (
                self._shutdown_deadline is not None
                and time.monotonic() >= self._shutdown_deadline
            )
            busy = (
                not self.device_controller.shutdown()
                or not self.update_controller.shutdown()
                or self.sync_controller.is_syncing()
                or draw_running
                or QThreadPool.globalInstance().activeThreadCount()
            )
            if busy:
                if overdue:
                    self._force_exit()
                    return
                self.statusBar().showMessage("Finishing device connections before closing…")
                QTimer.singleShot(100, self._finish_shutdown)
                return
            # Sync may have taught a pooled adapter new Tuya data points just
            # before it stopped; persist them before the pool closes.
            try:
                self.device_controller._adopt_pooled_discovery()
            except Exception:
                pass
            self._close_connections_bounded()
        except Exception:
            logger.exception("Shutdown cleanup failed; forcing exit")
        self._shutdown_ready = True
        # Quit directly instead of calling close() again: this runs inside the
        # original closeEvent, and a re-entrant close() is ignored by Qt, which
        # left the process running with no window after a quick shutdown.
        if self.tray is not None:
            self.tray.hide()
        from ..drivers import tuya_aoi, tuya_presence
        tuya_presence.stop_presence_listener()
        try:
            tuya_aoi.aoi().stop()
        except Exception:
            pass
        _quit_application()


def main():
    """Entry point for the GUI."""
    logger.info("Starting LumiSync GUI")

    try:
        app = QApplication(sys.argv)
        app.setApplicationName("LumiSync")
        app.setOrganizationName("Minlor")
        # The window can hide to the tray; the tray Quit action ends the app.
        app.setQuitOnLastWindowClosed(False)

        from .crash_reporting import install_crash_reporting
        install_crash_reporting(logger)

        from .utils.single_instance import SingleInstance
        guard = SingleInstance(app)
        if not guard.try_acquire():
            logger.info("LumiSync is already running; asked it to show itself")
            sys.exit(0)

        _install_excepthook()

        if not _resolve_legacy_instance():
            logger.info("Exiting so the older LumiSync keeps control of the lights")
            sys.exit(0)

        from .utils import autostart
        try:
            if autostart.repair_if_stale():
                logger.info("Updated the autostart entry to this install location")
        except Exception:
            logger.warning("Could not repair the autostart entry", exc_info=True)

        try:
            from threading import Thread

            from .controllers.update_controller import cleanup_old_update_downloads

            Thread(
                target=cleanup_old_update_downloads,
                name="lumisync-update-cleanup",
                daemon=True,
            ).start()
        except Exception:
            logger.debug("Could not schedule update cleanup", exc_info=True)

        from ..drivers import tuya_presence
        tuya_presence.start_presence_listener()

        apply_theme(app)

        from .utils.window_effects import install_dark_titlebar_filter
        install_dark_titlebar_filter(app)

        window = LumiSyncMainWindow()
        requested_material = window.settings.value(
            "ui/window_material",
            "acrylic",
        )
        active_material = window.set_window_material(
            str(requested_material),
            notify=False,
        )
        logger.info("Window material enabled: %s", active_material)
        # Sign-in launches start quietly in the tray; any other launch, or a
        # desktop without a tray, shows the window.
        if "--minimized" in sys.argv[1:] and window.tray is not None:
            logger.info("Started minimized to the tray")
        else:
            window.show()
        from .. import devices as device_settings
        settings_warning = device_settings.consume_settings_warning()
        if settings_warning:
            window.show()  # A recovered settings file is worth seeing at once.
            QTimer.singleShot(
                0, lambda message=settings_warning: window.show_error(message)
            )
        guard.activate_requested.connect(window.show_from_tray)
        guard.quit_requested.connect(window.quit_app)
        app.aboutToQuit.connect(window._on_about_to_quit)
        QTimer.singleShot(1000, window.check_for_updates_on_startup)
        # After the window settles: a COM read of the firewall policy.
        QTimer.singleShot(3000, window.check_firewall_on_startup)

        exit_code = app.exec()
        logger.info(f"LumiSync GUI closed (exit {exit_code})")
        sys.exit(exit_code)

    except Exception as e:
        logger.critical(f"Uncaught exception in GUI: {str(e)}", exc_info=True)
        raise


def _resolve_legacy_instance() -> bool:
    """Make sure a LumiSync 0.8.x copy is not still running beside us.

    0.8.x used a different single-instance name, so it does not notice this
    version. Two copies would fight over the same LAN ports and lights.
    Returns False when the user prefers to keep the old copy running.
    """
    from .utils.single_instance import legacy_instance_running

    while legacy_instance_running():
        box = QMessageBox()
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("LumiSync")
        box.setText("An older LumiSync is still running.")
        box.setInformativeText(
            "Quit it from its tray icon (right-click the LumiSync icon, then "
            "Quit LumiSync), then choose Try Again. Running both would make "
            "them fight over your lights."
        )
        retry = box.addButton("Try Again", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Start Anyway", QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(retry)
        box.exec()
        clicked = box.clickedButton()
        if clicked is retry:
            continue
        return clicked is not cancel
    return True


def _install_excepthook():
    """Log uncaught exceptions and tell the user instead of dying silently.

    Qt swallows Python exceptions raised inside slots/event handlers and, on
    PySide6, terminates the process without any UI feedback. This hook writes
    the traceback to the log and shows a dialog pointing at the log file.

    PySide6 runs the hook on whichever thread raised, so the dialog is always
    posted to the GUI thread; widgets touched from a worker thread crash Qt.
    """
    from ..utils.logging import get_logs_directory
    from .crash_reporting import _safe_message

    state = {"dialogs": 0}
    max_dialogs = 3

    def show_dialog(name: str, text: str) -> None:
        global _error_dialog_open
        if _error_dialog_open:
            return
        if state["dialogs"] >= max_dialogs:
            # A broken timer or paint handler must not bury the user in
            # dialogs; later errors are logged only.
            return
        state["dialogs"] += 1
        _error_dialog_open = True
        try:
            QMessageBox.critical(
                None,
                "LumiSync · Unexpected Error",
                f"Something went wrong:\n\n{name}: {text}\n\n"
                f"Details were written to the logs in:\n{get_logs_directory()}",
            )
        except Exception:
            pass
        finally:
            _error_dialog_open = False

    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        text = _safe_message(str(exc_value))
        key = (exc_type.__name__, text)
        now = time.monotonic()
        repeated = now - _recent_error_times.get(key, 0.0) < 30.0
        if repeated:
            # Same failure again within the window: one line, not a traceback.
            logger.error("Uncaught exception repeated: %s: %s", exc_type.__name__, text)
            return
        logger.critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
        )
        _recent_error_times[key] = now
        # Bound the dedupe table for long sessions with a rotating error source.
        if len(_recent_error_times) > 50:
            oldest = min(
                _recent_error_times,
                key=lambda item: _recent_error_times.get(item, 0.0),
            )
            _recent_error_times.pop(oldest, None)

        app = QApplication.instance()
        if app is None:
            return
        name = exc_type.__name__
        if QThread.currentThread() is app.thread():
            show_dialog(name, text)
        else:
            QTimer.singleShot(0, app, lambda: show_dialog(name, text))

    sys.excepthook = hook


if __name__ == "__main__":
    main()
