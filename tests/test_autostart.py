import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lumisync.gui.utils import autostart


class LinuxAutostartTests(unittest.TestCase):
    """Exercise the Linux .desktop path regardless of the host OS."""

    def setUp(self):
        self._orig_system = autostart._SYSTEM
        self._orig_xdg = os.environ.get("XDG_CONFIG_HOME")
        self._tmp = tempfile.mkdtemp()
        autostart._SYSTEM = "Linux"
        os.environ["XDG_CONFIG_HOME"] = self._tmp

    def tearDown(self):
        autostart._SYSTEM = self._orig_system
        if self._orig_xdg is None:
            os.environ.pop("XDG_CONFIG_HOME", None)
        else:
            os.environ["XDG_CONFIG_HOME"] = self._orig_xdg

    def test_supported_on_linux(self):
        self.assertTrue(autostart.is_supported())

    def test_enable_writes_desktop_entry_then_disable_removes_it(self):
        self.assertFalse(autostart.is_enabled())

        self.assertTrue(autostart.set_enabled(True))
        self.assertTrue(autostart.is_enabled())

        path = Path(self._tmp) / "autostart" / "lumisync.desktop"
        self.assertTrue(path.exists())
        contents = path.read_text(encoding="utf-8")
        self.assertIn("[Desktop Entry]", contents)
        self.assertIn("Type=Application", contents)
        self.assertIn("Name=LumiSync", contents)
        self.assertIn("Exec=", contents)

        self.assertTrue(autostart.set_enabled(False))
        self.assertFalse(autostart.is_enabled())
        self.assertFalse(path.exists())

    def test_disable_when_absent_is_a_noop_success(self):
        self.assertTrue(autostart.set_enabled(False))
        self.assertFalse(autostart.is_enabled())

    def _write_entry(self, exec_line: str) -> Path:
        path = Path(self._tmp) / "autostart" / "lumisync.desktop"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"[Desktop Entry]\nExec={exec_line}\n", encoding="utf-8")
        return path

    def test_packaged_build_repairs_an_entry_for_a_deleted_copy_once(self):
        path = self._write_entry("/old/portable/LumiSync")
        with patch.object(autostart.sys, "frozen", True, create=True):
            self.assertTrue(autostart.repair_if_stale())
            contents = path.read_text(encoding="utf-8")
            self.assertIn(f"Exec={autostart._desktop_exec()}", contents)
            self.assertIn("--minimized", contents)
            self.assertFalse(autostart.repair_if_stale())

    def test_source_runs_never_take_over_the_sign_in_entry(self):
        path = self._write_entry("/old/portable/LumiSync")
        self.assertFalse(autostart.repair_if_stale())
        self.assertIn("Exec=/old/portable/LumiSync", path.read_text(encoding="utf-8"))

    def test_another_existing_copy_keeps_the_sign_in_entry(self):
        other = Path(self._tmp) / "Other" / "LumiSync"
        other.parent.mkdir(parents=True)
        other.write_text("", encoding="utf-8")
        path = self._write_entry(f'"{other}"')
        with patch.object(autostart.sys, "frozen", True, create=True):
            self.assertFalse(autostart.repair_if_stale())
        self.assertIn(str(other), path.read_text(encoding="utf-8"))

    def test_appimage_entry_points_at_the_image_not_the_temporary_mount(self):
        image = "/home/me/Apps/LumiSync 1.0.AppImage"
        path = self._write_entry('"/tmp/.mount_old/usr/bin/LumiSync" --minimized')
        with patch.dict(os.environ, {"APPIMAGE": image}), patch.object(
            autostart.sys, "frozen", True, create=True
        ), patch.object(autostart.sys, "executable", "/tmp/.mount_new/usr/bin/LumiSync"):
            self.assertTrue(autostart.repair_if_stale())
            contents = path.read_text(encoding="utf-8")
            self.assertIn('Exec="' + image + '" --minimized', contents)
            self.assertNotIn(".mount_", contents)
            # The next launch mounts somewhere else; the entry stays put.
            with patch.object(autostart.sys, "executable", "/tmp/.mount_other/usr/bin/LumiSync"):
                self.assertFalse(autostart.repair_if_stale())

    def test_exec_quoting_escapes_reserved_characters(self):
        quoted = autostart._quote_exec_argument('/home/me/50% $off/"x"')
        self.assertEqual(quoted, '"/home/me/50%% \\\\$off/\\\\"x\\\\""')

    def test_launch_command_starts_minimized(self):
        self.assertTrue(autostart._launch_command().endswith("--minimized"))

    def test_repair_without_an_entry_is_a_noop(self):
        self.assertFalse(autostart.repair_if_stale())


class UnsupportedPlatformTests(unittest.TestCase):
    def test_macos_is_unsupported(self):
        orig = autostart._SYSTEM
        autostart._SYSTEM = "Darwin"
        try:
            self.assertFalse(autostart.is_supported())
            self.assertFalse(autostart.is_enabled())
            self.assertFalse(autostart.set_enabled(True))
        finally:
            autostart._SYSTEM = orig


if __name__ == "__main__":
    unittest.main()
