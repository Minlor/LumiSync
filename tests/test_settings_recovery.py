"""Damaged settings files must be preserved, not silently replaced."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lumisync import devices


class SettingsRecoveryTests(unittest.TestCase):
    def setUp(self):
        devices._settings_warning = None
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "settings.json"

    def tearDown(self):
        self.directory.cleanup()
        devices._settings_warning = None

    def load(self):
        with patch.object(devices, "settings_path", return_value=self.path):
            return devices._load_saved_settings()

    def test_invalid_json_is_set_aside_and_reported(self):
        self.path.write_text('{"devices": [', encoding="utf-8")

        loaded = self.load()

        self.assertEqual(loaded["devices"], [])
        self.assertFalse(self.path.exists())
        self.assertTrue(list(self.directory.name and Path(self.directory.name).glob("settings.json.corrupt-*")))
        warning = devices.consume_settings_warning()
        self.assertIsNotNone(warning)
        self.assertIn("damaged", warning)
        self.assertIn("no usable backup", warning)
        self.assertIsNone(devices.consume_settings_warning())

    def test_invalid_utf8_is_caught_and_quarantined(self):
        self.path.write_bytes(b'{"devices": [\xff\xfe]}')

        loaded = self.load()

        self.assertEqual(loaded["devices"], [])
        self.assertTrue(Path(self.directory.name).glob("settings.json.corrupt-*"))
        self.assertIn("damaged", devices.consume_settings_warning() or "")

    def test_last_good_backup_is_restored(self):
        backup = self.path.with_name("settings.json.bak")
        backup.write_text(
            json.dumps({"devices": [{"mac": "AA:BB", "model": "H619C"}], "time": 1}),
            encoding="utf-8",
        )
        self.path.write_text("{not json", encoding="utf-8")

        loaded = self.load()

        self.assertEqual(len(loaded["devices"]), 1)
        self.assertEqual(loaded["devices"][0]["mac"], "AA:BB")
        warning = devices.consume_settings_warning()
        self.assertIn("restored", warning or "")
        # The backup itself is left untouched for the next recovery.
        self.assertTrue(backup.exists())

    def test_missing_file_still_triggers_first_discovery(self):
        with patch.object(devices, "settings_path", return_value=self.path), patch.object(
            devices,
            "discover_lan_devices",
            return_value={"devices": [], "selectedDevice": 0},
        ) as discover:
            devices.get_data(refresh=True)

        discover.assert_called_once_with(preserve_existing=True)

    def test_missing_file_is_restored_from_backup(self):
        backup = self.path.with_name("settings.json.bak")
        backup.write_text(
            json.dumps({"devices": [{"mac": "AA:BB"}], "time": 1}),
            encoding="utf-8",
        )

        loaded = self.load()

        self.assertEqual(loaded["devices"][0]["mac"], "AA:BB")
        self.assertIn("restored", devices.consume_settings_warning() or "")

    def test_write_keeps_the_previous_good_file_as_backup(self):
        original = {
            "devices": [{"mac": "AA:BB", "model": "H619C"}],
            "selectedDevice": 0,
            "time": 1,
        }
        self.path.write_text(json.dumps(original), encoding="utf-8")

        with patch.object(devices, "settings_path", return_value=self.path), patch(
            "lumisync.accounts.secrets.protect_device", side_effect=lambda device: device
        ):
            devices.writeJSON(
                {
                    "devices": [{"mac": "CC:DD", "model": "H6199"}],
                    "selectedDevice": 0,
                    "time": 2,
                }
            )

        saved = json.loads(self.path.read_text(encoding="utf-8"))
        backup = json.loads(
            self.path.with_name("settings.json.bak").read_text(encoding="utf-8")
        )
        self.assertEqual(saved["devices"][0]["mac"], "CC:DD")
        self.assertEqual(backup["devices"][0]["mac"], "AA:BB")

    def test_corrupt_file_is_not_copied_over_a_good_backup(self):
        backup = self.path.with_name("settings.json.bak")
        backup.write_text(
            json.dumps({"devices": [{"mac": "AA:BB"}], "time": 1}),
            encoding="utf-8",
        )
        self.path.write_text("{broken", encoding="utf-8")

        with patch.object(devices, "settings_path", return_value=self.path), patch(
            "lumisync.accounts.secrets.protect_device", side_effect=lambda device: device
        ):
            devices.writeJSON({"devices": [{"mac": "CC:DD"}], "time": 2})

        backup_data = json.loads(backup.read_text(encoding="utf-8"))
        self.assertEqual(backup_data["devices"][0]["mac"], "AA:BB")


if __name__ == "__main__":
    unittest.main()
