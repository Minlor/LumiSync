"""Render README/website images with isolated example data and no device I/O."""

from __future__ import annotations

import argparse
import os
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from lumisync.gui.controllers.device_controller import DeviceController  # noqa: E402
from lumisync.gui.dialogs.accounts_dialog import AccountsDialog  # noqa: E402
from lumisync.gui.main_window import LumiSyncMainWindow  # noqa: E402
from lumisync.gui.theme import apply_theme  # noqa: E402
from lumisync.groups import device_key  # noqa: E402
from readme_fixtures import example_accounts, example_devices, example_history  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "assets" / "screenshots")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="lumisync-documentation-") as temporary, ExitStack() as scope:
        previous = Path.cwd()
        scope.callback(os.chdir, previous)
        os.chdir(temporary)
        Path("settings.json").write_text('{"devices":[],"groups":[]}', encoding="utf-8")
        scope.enter_context(patch.dict(os.environ, {"LOCALAPPDATA": temporary, "XDG_CONFIG_HOME": temporary}))
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        for settings_scope in (QSettings.Scope.UserScope, QSettings.Scope.SystemScope):
            QSettings.setPath(QSettings.Format.IniFormat, settings_scope, temporary)
        scope.enter_context(patch.object(DeviceController, "refresh_device_states"))
        scope.enter_context(patch.object(DeviceController, "refresh_device_state_at"))
        scope.enter_context(patch.object(DeviceController, "get_accounts", side_effect=example_accounts))
        scope.enter_context(patch("lumisync.sku_catalog.import_govee_desktop_cache"))
        app = QApplication([])
        if os.name == "nt":
            for font in ("segoeui.ttf", "segoeuib.ttf", "segoeuil.ttf", "segoeuii.ttf"):
                QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + font)
            app.setFont(QFont("Segoe UI", 10))
        apply_theme(app)
        if os.name == "nt":
            app.setStyleSheet(app.styleSheet().replace('"Segoe UI Variable", "Inter", "Segoe UI", sans-serif', '"Segoe UI"'))
        window = LumiSyncMainWindow()
        window.resize(1297, 792)
        window.set_window_material("solid", notify=False)
        window.setWindowTitle("LumiSync · Example devices and readings")
        controller = window.device_controller
        controller.devices = example_devices()
        for index, device in enumerate(controller.devices):
            controller.device_states[device_key(device)] = {
                "online": True, "status_source": "confirmed", "power_on": index != 5,
                "brightness": 70, "color": (50, 110, 230), "power_w": 215.1 if index == 4 else 0.0,
                "voltage_v": 233.8, "current_a": 1.023 if index == 4 else 0.0, "last_seen": time.time(),
            }
        controller.devices_discovered.emit(controller.devices)
        for key in ("monitor", "music"):
            strip = getattr(getattr(window, key + "_sync_view"), key + "_chips")
            if strip._target_buttons and not strip.selected_devices():
                strip._target_buttons[0].click()
        window.show()

        def save(widget, name):
            QTest.qWait(350)
            if not widget.grab().save(str(output / f"lumisync-{name}.png"), "PNG"):
                raise RuntimeError(f"Could not capture {name}")
            print("Captured", output / f"lumisync-{name}.png")

        try:
            for key, name in (("devices", "devices"), ("monitor", "monitor-sync"), ("music", "music-sync")):
                window.nav_shell.set_current_by_key(key)
                save(window, name)
            window.nav_shell.set_current_by_key("devices")
            window.resize(1297, 860)
            window.devices_view._open_inspector(4)
            energy = window.devices_view.inspector.energy
            energy._show_history(example_history(energy.month.currentData()))
            save(window, "plug-energy")
            assert window.devices_view.inspector_scroll.verticalScrollBar().maximum() == 0
            window.resize(1297, 792)
            dialog = AccountsDialog(controller, window)
            dialog.setWindowTitle("Accounts · Example data")
            dialog.resize(860, 700)
            dialog.show()
            dialog.provider_buttons["tuya"].click()
            dialog._show_status("Example accounts only. Enter your own details to sign in.")
            save(dialog, "accounts")
            dialog.reject()
            dialog.deleteLater()
            window.nav_shell.set_current_by_key("settings")
            window.settings_page.section_nav.setCurrentRow(4)
            save(window, "about")
        finally:
            window.quit_app()
            app.processEvents()


if __name__ == "__main__":
    main()
