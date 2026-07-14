import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from lumisync import devices
from lumisync.gui.widgets.device_chip import group_member_ids
from lumisync.gui.widgets.pixel_canvas import line_cells


class PixelStrokeTests(unittest.TestCase):
    def test_fast_horizontal_stroke_contains_every_cell(self):
        self.assertEqual(
            line_cells((1, 3), (6, 3)),
            [(1, 3), (2, 3), (3, 3), (4, 3), (5, 3), (6, 3)],
        )

    def test_diagonal_stroke_is_contiguous_in_both_directions(self):
        forward = line_cells((2, 1), (9, 6))
        backward = line_cells((9, 6), (2, 1))

        self.assertEqual(forward[0], (2, 1))
        self.assertEqual(forward[-1], (9, 6))
        self.assertEqual(backward, list(reversed(forward)))
        for first, second in zip(forward, forward[1:]):
            self.assertLessEqual(abs(first[0] - second[0]), 1)
            self.assertLessEqual(abs(first[1] - second[1]), 1)


class PackagedSettingsPathTests(unittest.TestCase):
    def test_source_checkout_keeps_relative_settings_path(self):
        with patch.object(sys, "frozen", False, create=True):
            self.assertEqual(devices.settings_path(), Path("settings.json"))

    def test_frozen_build_uses_per_user_app_data(self):
        with tempfile.TemporaryDirectory() as temp:
            local_app_data = Path(temp) / "LocalAppData"
            executable = Path(temp) / "app" / "LumiSync.exe"
            with (
                patch.dict(os.environ, {"LOCALAPPDATA": str(local_app_data)}),
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "executable", str(executable)),
            ):
                self.assertEqual(
                    devices.settings_path(),
                    local_app_data / "LumiSync" / "settings.json",
                )

    def test_wheel_install_uses_per_user_app_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            local_app_data = root / "LocalAppData"
            installed_module = root / "site-packages" / "lumisync" / "devices.py"
            installed_module.parent.mkdir(parents=True)
            installed_module.touch()

            with (
                patch.dict(os.environ, {"LOCALAPPDATA": str(local_app_data)}),
                patch.object(devices, "__file__", str(installed_module)),
                patch.object(sys, "frozen", False, create=True),
                patch.object(sys, "_MEIPASS", None, create=True),
                patch.object(sys, "executable", str(root / "python.exe")),
            ):
                self.assertEqual(
                    devices.settings_path(),
                    local_app_data / "LumiSync" / "settings.json",
                )

    def test_frozen_build_migrates_repository_preview_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repository = root / "repo"
            repository.mkdir()
            (repository / "pyproject.toml").write_text("[project]\nname='x'\n")
            legacy_data = {
                "devices": [{"model": "Panel", "transport": "ble"}],
                "selectedDevice": 0,
                "groups": [{"name": "Desk", "devices": ["Panel"]}],
            }
            (repository / "settings.json").write_text(json.dumps(legacy_data))

            executable = repository / "build" / "preview" / "LumiSync.exe"
            local_app_data = root / "LocalAppData"
            empty_cwd = root / "empty"
            empty_cwd.mkdir()
            old_cwd = Path.cwd()
            try:
                os.chdir(empty_cwd)
                with (
                    patch.dict(os.environ, {"LOCALAPPDATA": str(local_app_data)}),
                    patch.object(sys, "frozen", True, create=True),
                    patch.object(sys, "executable", str(executable)),
                ):
                    destination = devices.settings_path()
            finally:
                os.chdir(old_cwd)

            self.assertTrue(destination.is_file())
            self.assertEqual(json.loads(destination.read_text()), legacy_data)

    def test_onedir_layout_is_detected_if_pyinstaller_flag_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            executable_dir = root / "LumiSync"
            (executable_dir / "_internal").mkdir(parents=True)
            executable = executable_dir / "LumiSync.exe"
            local_app_data = root / "LocalAppData"
            with (
                patch.dict(os.environ, {"LOCALAPPDATA": str(local_app_data)}),
                patch.object(sys, "frozen", False, create=True),
                patch.object(sys, "_MEIPASS", None, create=True),
                patch.object(sys, "executable", str(executable)),
            ):
                self.assertEqual(
                    devices.settings_path(),
                    local_app_data / "LumiSync" / "settings.json",
                )


class SyncTargetTests(unittest.TestCase):
    def test_group_members_are_filtered_and_keep_saved_order(self):
        group = {"name": "Desk", "devices": ["second", "gone", "first"]}
        self.assertEqual(
            group_member_ids(group, {"first", "second"}),
            ["second", "first"],
        )


if __name__ == "__main__":
    unittest.main()
