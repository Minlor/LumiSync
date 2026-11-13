"""Main-window update decisions: background checks never open a modal dialog."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from lumisync.gui.main_window import LumiSyncMainWindow  # noqa: E402
from lumisync.updates import ReleaseAsset, UpdateCheckResult  # noqa: E402


def _result(version="1.0.1", assets=()):
    return UpdateCheckResult(
        current_version="1.0.0",
        latest_version=version,
        is_update_available=True,
        release_url="https://example/release",
        assets=tuple(assets),
    )


def _window(*, silent, skipped=False, installable=False, auto=False, offered=None):
    return SimpleNamespace(
        update_controller=SimpleNamespace(last_check_silent=silent),
        _update_is_skipped=lambda _result: skipped,
        _update_offer_version=offered,
        _set_update_badge=MagicMock(),
        can_install_update=lambda _result: installable,
        _auto_download_updates=lambda: auto,
        _start_update_download=MagicMock(),
        _notify=MagicMock(),
        show_update_dialog=MagicMock(),
    )


class BackgroundUpdateCheckTests(unittest.TestCase):
    def test_background_check_notifies_without_a_dialog(self):
        window = _window(silent=True)
        LumiSyncMainWindow._on_update_available(window, _result())  # type: ignore[arg-type]
        window._notify.assert_called_once()
        window.show_update_dialog.assert_not_called()
        window._set_update_badge.assert_called_once_with(True)
        self.assertEqual(window._update_offer_version, "1.0.1")

    def test_periodic_recheck_does_not_repeat_the_same_offer(self):
        window = _window(silent=True, offered="1.0.1")
        LumiSyncMainWindow._on_update_available(window, _result())  # type: ignore[arg-type]
        window._notify.assert_not_called()
        window._start_update_download.assert_not_called()

    def test_skipped_version_stays_quiet(self):
        window = _window(silent=True, skipped=True)
        LumiSyncMainWindow._on_update_available(window, _result())  # type: ignore[arg-type]
        window._notify.assert_not_called()
        window._set_update_badge.assert_not_called()

    def test_auto_download_runs_in_the_background(self):
        window = _window(silent=True, installable=True, auto=True)
        result = _result()
        LumiSyncMainWindow._on_update_available(window, result)  # type: ignore[arg-type]
        window._start_update_download.assert_called_once_with(result, interactive=False)
        window.show_update_dialog.assert_not_called()

    def test_manual_check_opens_the_dialog(self):
        window = _window(silent=False)
        result = _result()
        LumiSyncMainWindow._on_update_available(window, result)  # type: ignore[arg-type]
        window.show_update_dialog.assert_called_once_with(result)


class InstallEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.setup = ReleaseAsset("LumiSync-Setup.exe", "https://example/setup.exe")

    def test_source_and_pip_installs_never_run_the_installer(self):
        with patch("lumisync.gui.main_window.sys.platform", "win32"), patch(
            "lumisync.gui.main_window.is_installed_build", return_value=False
        ):
            self.assertFalse(
                LumiSyncMainWindow.can_install_update(None, _result(assets=[self.setup]))  # type: ignore[arg-type]
            )

    def test_packaged_windows_build_installs_the_setup_asset(self):
        with patch("lumisync.gui.main_window.sys.platform", "win32"), patch(
            "lumisync.gui.main_window.is_installed_build", return_value=True
        ):
            self.assertTrue(
                LumiSyncMainWindow.can_install_update(None, _result(assets=[self.setup]))  # type: ignore[arg-type]
            )

    def test_linux_opens_the_release_page_instead(self):
        appimage = ReleaseAsset("LumiSync-x86_64.AppImage", "https://example/app")
        with patch("lumisync.gui.main_window.sys.platform", "linux"), patch(
            "lumisync.gui.main_window.is_installed_build", return_value=True
        ):
            self.assertFalse(
                LumiSyncMainWindow.can_install_update(None, _result(assets=[appimage]))  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
