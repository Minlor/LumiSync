"""A failed settings read must never turn into a save that erases data."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from lumisync import devices
from lumisync.accounts.errors import AccountError
from lumisync.accounts.secrets import CredentialVault
from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.controllers.device_store import DeviceStore, SettingsReloaded
from lumisync.utils import file_operations

SAVED = {
    "devices": [
        {"mac": "AA:BB", "model": "H619C", "ip": "192.0.2.10"},
        {"mac": "CC:DD", "model": "H6199", "ip": "192.0.2.20"},
    ],
    "selectedDevice": 1,
    "time": 1,
    "accounts": [{"id": "fixture-account", "provider": "govee_account", "remember": True}],
    "groups": [{"name": "Desk", "devices": ["aa:bb"]}],
    "sync": {"brightness": 0.5},
    "customKey": "kept",
}

LOCKED = PermissionError(13, "Permission denied")


class SettingsFile:
    """A temporary settings.json that can be made unreadable on demand."""

    def __init__(self, data):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "settings.json"
        self.path.write_text(json.dumps(data), encoding="utf-8")
        self.locked = False
        original = devices._read_settings_file

        def read(path):
            # Like an antivirus/backup lock: the file exists but cannot be opened.
            if self.locked and Path(path).exists():
                raise LOCKED
            return original(path)

        self._patches = [
            patch.object(devices, "settings_path", return_value=self.path),
            patch.object(devices, "_read_settings_file", side_effect=read),
            # Device descriptors here carry no keys; keep the OS vault out of it.
            patch("lumisync.accounts.secrets.protect_device", side_effect=lambda device, **_: dict(device)),
        ]

    def __enter__(self):
        for active in self._patches:
            active.start()
        devices._settings_warning = None
        return self

    def __exit__(self, *exc):
        for active in reversed(self._patches):
            active.stop()
        devices._settings_warning = None
        self.directory.cleanup()

    def raw(self) -> bytes:
        return self.path.read_bytes()

    def data(self) -> dict:
        return json.loads(self.raw())


class SettingsReadFailureTests(unittest.TestCase):
    def test_unreadable_file_raises_instead_of_returning_an_empty_inventory(self):
        with SettingsFile(SAVED) as settings:
            settings.locked = True
            with self.assertRaises(devices.SettingsUnavailable):
                devices.load_settings()
            warning = devices.consume_settings_warning() or ""
        self.assertIn("will not save changes", warning)
        self.assertNotIn("Errno", warning)

    def test_locked_backup_of_a_missing_file_is_not_replaced_by_an_empty_one(self):
        with SettingsFile(SAVED) as settings:
            backup = settings.path.with_name("settings.json.bak")
            settings.path.replace(backup)
            settings.locked = True
            with self.assertRaises(devices.SettingsUnavailable):
                devices.load_settings()
            self.assertTrue(backup.exists())

    def test_update_settings_does_not_write_after_a_failed_read(self):
        with SettingsFile(SAVED) as settings:
            before = settings.raw()
            settings.locked = True
            with self.assertRaises(devices.SettingsUnavailable):
                devices.update_settings({"sync": {"brightness": 1.0}})
            self.assertEqual(settings.raw(), before)

    def test_discovery_without_saving_still_runs_while_the_file_is_locked(self):
        with SettingsFile(SAVED) as settings, \
                patch.object(devices.CONNECTION, "devices", []), \
                patch.object(devices, "connect", return_value=(Mock(), [{"mac": "EE:FF", "ip": "192.0.2.30"}])), \
                patch.object(devices, "writeJSON") as write:
            settings.locked = True
            result = devices.discover_lan_devices(persist=False)
            with self.assertRaises(devices.SettingsUnavailable):
                devices.discover_lan_devices(persist=True)
        self.assertEqual(result["lastDiscoveryCount"], 1)
        write.assert_not_called()


class DeviceStoreTests(unittest.TestCase):
    def test_failed_startup_read_blocks_saves_until_a_reread_reloads_the_file(self):
        reloaded = []
        with SettingsFile(SAVED) as settings:
            before = settings.raw()
            store = DeviceStore()
            store.on_reloaded = lambda: reloaded.append(True)
            settings.locked = True
            with self.assertRaises(devices.SettingsUnavailable):
                store.load()
            self.assertEqual(store.devices, [])

            # Still locked: nothing is written.
            with self.assertRaises(devices.SettingsUnavailable):
                store.save()
            self.assertEqual(settings.raw(), before)

            # Readable again: the inventory is reloaded and this one change,
            # computed against the empty inventory, is dropped.
            settings.locked = False
            with self.assertRaises(SettingsReloaded):
                store.save()
            self.assertEqual(settings.raw(), before)
            self.assertEqual([device["mac"] for device in store.devices], ["AA:BB", "CC:DD"])
            self.assertEqual(store.selected_index, 1)
            self.assertEqual(reloaded, [True])

            # Saving resumes and keeps every key the inventory does not own.
            store.save(selected=0)
            saved = settings.data()
        self.assertEqual(len(saved["devices"]), 2)
        self.assertEqual(saved["selectedDevice"], 0)
        for key in ("accounts", "groups", "sync", "customKey"):
            self.assertEqual(saved[key], SAVED[key])

    def test_read_failure_at_save_time_writes_nothing_and_later_saves_resume(self):
        with SettingsFile(SAVED) as settings:
            store = DeviceStore()
            store.load()
            before = settings.raw()
            settings.locked = True
            with self.assertRaises(devices.SettingsUnavailable):
                store.write({"groups": []})
            with self.assertRaises(devices.SettingsUnavailable):
                store.save()
            self.assertTrue(store.unavailable)
            self.assertEqual(settings.raw(), before)

            settings.locked = False
            store.write({"groups": []})
            saved = settings.data()
        self.assertFalse(store.unavailable)
        self.assertEqual(saved["groups"], [])
        self.assertEqual(len(saved["devices"]), 2)
        self.assertEqual(saved["accounts"], SAVED["accounts"])
        self.assertEqual(saved["sync"], SAVED["sync"])

    def test_a_failed_reload_does_not_discard_an_inventory_loaded_before(self):
        with SettingsFile(SAVED) as settings:
            store = DeviceStore()
            store.load()
            settings.locked = True
            with self.assertRaises(devices.SettingsUnavailable):
                store.load()
            settings.locked = False
            store.devices[0]["name"] = "Renamed"
            store.save()
            saved = settings.data()
        self.assertEqual(saved["devices"][0]["name"], "Renamed")

    def test_writers_replace_only_their_own_keys_from_the_file_as_it_is_now(self):
        with SettingsFile(SAVED) as settings:
            store = DeviceStore()
            store.load()
            # Another writer (the sync settings mirror) changes the file meanwhile.
            changed = settings.data()
            changed["sync"] = {"brightness": 0.9}
            settings.path.write_text(json.dumps(changed), encoding="utf-8")
            store.write(lambda current: {"accounts": current["accounts"] + [{"id": "second"}]})
            saved = settings.data()
        self.assertEqual(saved["sync"], {"brightness": 0.9})
        self.assertEqual([account["id"] for account in saved["accounts"]], ["fixture-account", "second"])
        self.assertEqual(saved["groups"], SAVED["groups"])
        self.assertEqual(saved["customKey"], "kept")

    def test_display_reads_of_a_locked_file_are_never_saved(self):
        with SettingsFile(SAVED) as settings:
            store = DeviceStore()
            store.load()
            before = settings.raw()
            settings.locked = True
            self.assertEqual(store.load_safe().get("accounts", []), [])
            self.assertTrue(store.unavailable)
            with self.assertRaises(devices.SettingsUnavailable):
                store.save()
            self.assertEqual(settings.raw(), before)


class ControllerSettingsFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_locked_settings_at_startup_never_erase_the_file(self):
        with SettingsFile(SAVED) as settings:
            before = settings.raw()
            settings.locked = True
            controller = DeviceController()
            statuses = []
            controller.status_updated.connect(statuses.append)
            try:
                self.assertEqual(controller.devices, [])
                controller._init_devices()  # Startup again, now observed.
                self.assertTrue(any("can't read its settings" in message for message in statuses))
                controller.devices = [{"mac": "EE:FF", "model": "H619C", "ip": "192.0.2.30"}]
                self.assertFalse(controller.save_group("Desk", [0]))
                self.assertFalse(controller.set_zone_count_at(0, 20))
                self.assertFalse(controller.add_device_manually("192.0.2.40"))
                self.assertEqual(settings.raw(), before)
                self.assertFalse(any("Errno" in message for message in statuses))

                # Readable again: the next save attempt reloads the saved
                # inventory instead of writing the in-memory one over it.
                settings.locked = False
                self.assertFalse(controller.add_device_manually("192.0.2.40"))
                self.assertEqual(settings.raw(), before)
                self.assertEqual([d["mac"] for d in controller.devices], ["AA:BB", "CC:DD"])
                self.assertTrue(controller.add_device_manually("192.0.2.40"))
                saved = settings.data()
            finally:
                controller.shutdown()
        self.assertEqual(len(saved["devices"]), 3)
        self.assertEqual(saved["accounts"], SAVED["accounts"])
        self.assertEqual(saved["groups"], SAVED["groups"])

    def test_group_save_rereads_groups_and_keeps_other_keys(self):
        with SettingsFile(SAVED) as settings:
            with patch.object(DeviceController, "_init_devices"):
                controller = DeviceController()
            controller._store.load()
            try:
                self.assertTrue(controller.save_group("Shelf", [1]))
                saved = settings.data()
            finally:
                controller.shutdown()
        self.assertEqual([group["name"] for group in saved["groups"]], ["Desk", "Shelf"])
        for key in ("accounts", "sync", "customKey"):
            self.assertEqual(saved[key], SAVED[key])


class LocalKeyProtectionTests(unittest.TestCase):
    LEGACY = {
        "devices": [
            {"device_id": "bulb", "mac": "tuya:bulb", "transport": "tuya",
             "ip": "192.0.2.50", "local_key": "abcdefghijklmnop"},
            {"mac": "AA:BB", "model": "H619C", "ip": "192.0.2.10"},
        ],
        "selectedDevice": 0,
        "accounts": [{"id": "kept"}],
    }

    def setUp(self):
        devices._save_notice = None
        devices._key_store_notice_given = False
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "settings.json"
        self.path.write_text(json.dumps(self.LEGACY), encoding="utf-8")
        self.vault = CredentialVault()
        self._patches = [
            patch.object(devices, "settings_path", return_value=self.path),
            patch("lumisync.accounts.secrets.vault", self.vault),
        ]
        for active in self._patches:
            active.start()

    def tearDown(self):
        for active in reversed(self._patches):
            active.stop()
        devices._save_notice = None
        devices._key_store_notice_given = False
        self.directory.cleanup()

    def saved(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_unavailable_credential_store_keeps_the_saved_key_and_still_saves(self):
        unavailable = AccountError("Secure credential storage is unavailable.", "credential_store")
        notices = []
        with patch.object(self.vault, "_backend", side_effect=unavailable):
            store = DeviceStore()
            store.on_notice = notices.append
            store.load()
            store.devices[0]["ip"] = "192.0.2.51"
            store.save()
            store.devices[1]["name"] = "Desk lamp"
            store.save()
        saved = self.saved()
        self.assertEqual(saved["devices"][0]["local_key"], "abcdefghijklmnop")
        self.assertNotIn("local_key_ref", saved["devices"][0])
        self.assertEqual(saved["devices"][0]["ip"], "192.0.2.51")
        self.assertEqual(saved["devices"][1]["name"], "Desk lamp")
        self.assertEqual(saved["accounts"], [{"id": "kept"}])
        self.assertEqual(len(notices), 1)
        self.assertIn("credential", notices[0])
        self.assertNotIn("abcdefghijklmnop", notices[0])

    def test_unavailable_credential_store_never_writes_a_new_plaintext_key(self):
        unavailable = AccountError("Secure credential storage is unavailable.", "credential_store")
        with patch.object(self.vault, "_backend", side_effect=unavailable):
            devices.writeJSON({"devices": [
                {"device_id": "fresh", "mac": "tuya:fresh", "transport": "tuya", "local_key": "ponmlkjihgfedcba"},
            ]})
        self.assertNotIn("ponmlkjihgfedcba", self.path.read_text(encoding="utf-8"))

    def test_working_vault_stores_a_legacy_key_once_across_saves(self):
        backend = Mock()
        backend.get_password.return_value = None
        with patch.object(self.vault, "_backend", return_value=backend):
            store = DeviceStore()
            store.load()
            for _ in range(3):
                store.save()
        backend.set_password.assert_called_once()
        self.assertNotIn("local_key", store.devices[0])
        self.assertEqual(store.devices[0]["local_key_ref"], "tuya-local/bulb")
        saved = self.saved()
        self.assertNotIn("local_key", saved["devices"][0])
        self.assertEqual(saved["devices"][0]["local_key_ref"], "tuya-local/bulb")
        self.assertNotIn("abcdefghijklmnop", self.path.read_text(encoding="utf-8"))


class SettingsFileOverrideTests(unittest.TestCase):
    def test_env_override_replaces_the_default_settings_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "profile" / "nested" / "settings.json"
            with patch.dict(os.environ, {devices.SETTINGS_FILE_ENV: str(target)}), \
                    patch.object(devices, "_legacy_settings_candidates",
                                 side_effect=AssertionError("no migration with an override")):
                self.assertEqual(devices.settings_path(), target)
                self.assertTrue(target.parent.is_dir())
                # An explicit, non-default file name is never redirected.
                self.assertEqual(devices.settings_path("other.json"), Path("other.json"))
                with patch("lumisync.accounts.secrets.protect_device", side_effect=lambda device, **_: dict(device)):
                    devices.writeJSON({"devices": [{"mac": "AA:BB"}], "selectedDevice": 0})
                self.assertEqual(devices.load_settings()["devices"][0]["mac"], "AA:BB")
            self.assertTrue(target.is_file())

    def test_an_empty_override_is_ignored(self):
        with patch.dict(os.environ, {devices.SETTINGS_FILE_ENV: "  "}), \
                patch.object(devices, "_is_packaged_app", return_value=False), \
                patch.object(devices, "_is_source_checkout", return_value=True):
            self.assertEqual(devices.settings_path(), Path("settings.json"))

    def test_the_suite_runs_against_an_isolated_settings_file(self):
        configured = os.environ.get(devices.SETTINGS_FILE_ENV, "")
        self.assertTrue(configured)
        self.assertNotEqual(devices.settings_path().resolve(), (Path.cwd() / "settings.json").resolve())


class AtomicReplaceRetryTests(unittest.TestCase):
    def test_sharing_violations_are_retried(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(file_operations.time, "sleep") as sleep, \
                patch.object(file_operations.os, "replace",
                             side_effect=[PermissionError(13, "busy"), PermissionError(13, "busy"), None]) as replace:
            file_operations.write_json({"ok": True}, str(Path(directory) / "settings.json"))
        self.assertEqual(replace.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertAlmostEqual(sleep.call_args.args[0], 0.075)

    def test_a_persistent_lock_still_fails_and_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(file_operations.time, "sleep"), \
                patch.object(file_operations.os, "replace", side_effect=PermissionError(13, "busy")) as replace, \
                self.assertRaises(PermissionError):
            try:
                file_operations.write_json({"ok": True}, str(Path(directory) / "settings.json"))
            finally:
                leftovers = list(Path(directory).iterdir())
        self.assertEqual(replace.call_count, file_operations.REPLACE_RETRIES + 1)
        self.assertEqual(leftovers, [])

    def test_other_errors_are_not_retried(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(file_operations.time, "sleep") as sleep, \
                patch.object(file_operations.os, "replace", side_effect=OSError("disk full")) as replace, \
                self.assertRaises(OSError):
            file_operations.write_json({"ok": True}, str(Path(directory) / "settings.json"))
        self.assertEqual(replace.call_count, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
