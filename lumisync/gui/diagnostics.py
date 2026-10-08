"""Exercise the real GUI in a disposable settings profile for release checks."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from .main_window import LumiSyncMainWindow
from .dialogs.accounts_dialog import AccountsDialog
from .theme import apply_theme


def check_gui_startup() -> None:
    """Render and close the main window without discovery, login, or updates.

    Run in a fresh process, as this temporarily redirects application settings.
    The source and frozen app use different device-settings locations; both
    receive an empty profile before the real controllers are constructed.
    """
    previous_directory = Path.cwd()
    previous_appdata = os.environ.get("LOCALAPPDATA")
    with TemporaryDirectory(prefix="lumisync-gui-check-") as temporary:
        folder = Path(temporary)
        (folder / "LumiSync").mkdir()
        for file in (folder / "settings.json", folder / "LumiSync" / "settings.json"):
            file.write_text('{"devices":[],"selectedDevice":0}', encoding="utf-8")
        os.environ["LOCALAPPDATA"] = temporary
        os.chdir(folder)
        try:
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            for scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
                QSettings.setPath(QSettings.Format.IniFormat, scope, temporary)
            app = QApplication([])
            app.setApplicationName("LumiSync")
            app.setOrganizationName("Minlor")
            app.setQuitOnLastWindowClosed(False)
            window = None
            accounts = None
            try:
                apply_theme(app)
                window = LumiSyncMainWindow()
                window.show()
                app.processEvents()
                if not window.isVisible() or window.grab().isNull():
                    raise RuntimeError("The main window did not render")
                accounts = AccountsDialog(window.device_controller, window)
                for provider in ("govee_account", "tuya_account", "lsc_account"):
                    accounts.provider.setCurrentIndex(accounts.provider.findData(provider))
                    accounts.show()
                    app.processEvents()
                    if accounts.grab().isNull():
                        raise RuntimeError("The account form did not render")
            finally:
                if accounts is not None:
                    accounts.reject()
                if window is not None:
                    window.quit_app()
                    app.processEvents()
                    if not window._shutdown_ready:
                        raise RuntimeError("The main window did not finish shutting down")
        finally:
            os.chdir(previous_directory)
            if previous_appdata is None:
                os.environ.pop("LOCALAPPDATA", None)
            else:
                os.environ["LOCALAPPDATA"] = previous_appdata
