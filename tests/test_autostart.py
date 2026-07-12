import os
import tempfile
import unittest
from pathlib import Path

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
