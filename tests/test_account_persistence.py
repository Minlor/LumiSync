"""Account metadata, credential removal and background failures stay safe."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from lumisync.accounts.errors import AccountError
from lumisync.accounts.manager import AccountManager
from lumisync.accounts.secrets import CredentialVault
from lumisync.gui.controllers import background
from lumisync.gui.controllers.device_controller import DeviceController

UNAVAILABLE = AccountError("Secure credential storage is unavailable.", "credential_store")
GET_DATA = "lumisync.gui.controllers.device_controller.devices.get_data"
WRITE_JSON = "lumisync.gui.controllers.device_controller.devices.writeJSON"


class CredentialRemovalTests(unittest.TestCase):
    def test_delete_without_a_credential_store_means_nothing_was_stored(self):
        store = CredentialVault()
        with patch.object(store, "_backend", side_effect=UNAVAILABLE), \
                self.assertLogs("lumisync.accounts.secrets", level="INFO") as logs:
            store.delete("account/fixture")
        self.assertIn("no saved credential", logs.output[0])
        self.assertNotIn("account/fixture", "\n".join(logs.output))

    def test_session_account_from_before_a_restart_disconnects_without_a_keyring(self):
        store = CredentialVault()  # Fresh process: the session copy is gone.
        manager = AccountManager()
        with patch("lumisync.accounts.manager.vault", store), \
                patch.object(store, "_backend", side_effect=UNAVAILABLE):
            manager.disconnect("fixture")

    def test_a_real_vault_failure_is_still_reported(self):
        store = CredentialVault()
        backend = Mock()
        backend.get_password.side_effect = OSError("fixture-secret")
        with patch.object(store, "_backend", return_value=backend), \
                self.assertRaises(AccountError) as caught:
            store.delete("account/fixture")
        self.assertNotIn("fixture-secret", str(caught.exception))


class AccountMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    SETTINGS = {
        "devices": [
            {"mac": "fixture-session", "device_id": "fixture-session", "transport": "tuya_cloud", "account_id": "session"},
            {"mac": "fixture-saved", "device_id": "fixture-saved", "transport": "tuya_cloud", "account_id": "saved"},
        ],
        "selectedDevice": 0,
        "accounts": [
            {"id": "session", "provider": "tuya_qr", "remember": False},
            {"id": "saved", "provider": "tuya_qr", "remember": True},
            {"id": "legacy", "provider": "govee_api"},
        ],
        "groups": [{"name": "Desk", "devices": ["fixture-saved"]}],
    }

    def settings(self):
        return {key: [dict(item) for item in value] if isinstance(value, list) else value
                for key, value in self.SETTINGS.items()}

    def test_session_only_accounts_are_pruned_at_startup(self):
        with patch(GET_DATA, return_value=self.settings()), patch(WRITE_JSON) as write:
            controller = DeviceController()
        controller.shutdown()
        write.assert_called_once()
        written = write.call_args.args[0]
        self.assertEqual([account["id"] for account in written["accounts"]], ["saved", "legacy"])
        self.assertNotIn("account_id", written["devices"][0])
        self.assertEqual(written["devices"][1]["account_id"], "saved")
        self.assertEqual(written["groups"], self.SETTINGS["groups"])
        self.assertNotIn("account_id", controller.devices[0])

    def test_startup_without_session_accounts_does_not_write(self):
        settings = self.settings()
        settings["accounts"] = settings["accounts"][1:]
        with patch(GET_DATA, return_value=settings), patch(WRITE_JSON) as write:
            controller = DeviceController()
        controller.shutdown()
        write.assert_not_called()

    def controller(self):
        with patch.object(DeviceController, "_init_devices"):
            controller = DeviceController()
        controller.devices = self.settings()["devices"]
        self.addCleanup(controller.shutdown)
        return controller

    def test_remove_account_always_removes_its_metadata(self):
        controller = self.controller()
        with patch(GET_DATA, return_value=self.settings()), patch(WRITE_JSON) as write:
            self.assertTrue(controller.remove_account("saved"))
        written = write.call_args.args[0]
        self.assertEqual([account["id"] for account in written["accounts"]], ["session", "legacy"])
        self.assertEqual(written["devices"][0]["account_id"], "session")
        self.assertNotIn("account_id", written["devices"][1])
        self.assertNotIn("account_id", controller.devices[1])


class SettingsWriteFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch.object(DeviceController, "_init_devices"):
            self.controller = DeviceController()
        self.controller.devices = [
            {"mac": "fixture-a", "model": "H619C", "ip": "192.0.2.10"},
            {"mac": "fixture-b", "model": "H6199", "ip": "192.0.2.20",
             "local_transport": "lan", "cloud_transport": "govee_account", "account_id": "fixture"},
        ]
        self.statuses = []
        self.controller.status_updated.connect(self.statuses.append)
        self.groups = []
        self.controller.groups_changed.connect(self.groups.append)
        failure = PermissionError(13, "Access is denied", "C:\\fixture\\settings.json")
        patches = [
            patch(GET_DATA, return_value={"devices": [], "groups": [{"name": "Desk", "devices": ["fixture-a"]}]}),
            patch(WRITE_JSON, side_effect=failure),
        ]
        for active in patches:
            active.start()
            self.addCleanup(active.stop)

    def tearDown(self):
        self.controller.shutdown()

    def assert_friendly(self):
        self.assertTrue(self.statuses)
        for message in self.statuses:
            self.assertNotIn("Errno", message)
            self.assertNotIn("Access is denied", message)
            self.assertNotIn("fixture\\settings.json", message)

    def test_group_changes_report_a_failed_save(self):
        self.assertFalse(self.controller.save_group("Shelf", [0, 1]))
        self.assertFalse(self.controller.delete_group("Desk"))
        self.assertEqual(self.groups, [])
        self.assertFalse(any(message.startswith(("Saved group", "Deleted group")) for message in self.statuses))
        self.assert_friendly()

    def test_account_and_connection_changes_report_a_failed_save(self):
        before = [dict(device) for device in self.controller.devices]
        updated = []
        self.controller.device_updated.connect(lambda index, device: updated.append((index, device)))
        self.assertFalse(self.controller.remove_account("fixture"))
        self.assertFalse(self.controller.set_connection_at(1, "govee_account"))
        self.assertFalse(self.controller.import_account_devices(
            {"id": "fixture", "provider": "govee_account"}, [{"mac": "fixture-c", "transport": "govee_account"}]))
        self.assertEqual([dict(device) for device in self.controller.devices], before)
        self.assertEqual(updated[-1][0], 1)
        self.assertEqual(updated[-1][1].get("transport", "lan"), "lan")  # Choice reverted.
        self.assertEqual(len(self.statuses), 3)
        self.assert_friendly()


class BackgroundTaskLoggingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def run_task(self, action):
        results = []
        task = background.BackgroundTask(action)
        task.signals.completed.connect(lambda value, error: results.append((value, error)))
        task.run()
        self.assertIsNone(task.action)
        return results[0]

    def test_unexpected_failure_is_logged_without_its_message(self):
        secret = "password=" + "fixture-secret"

        def action():
            raise RuntimeError(secret)

        with self.assertLogs(background.logger, level="ERROR") as logs:
            value, error = self.run_task(action)
        output = "\n".join(logs.output)
        self.assertIn("RuntimeError", output)
        self.assertIn("action", output)  # The failing frame is named.
        self.assertNotIn("fixture-secret", output)
        self.assertIsNone(value)
        self.assertNotIn("fixture-secret", error)

    def test_account_errors_reach_the_user_and_log_only_their_code(self):
        def action():
            raise AccountError("Sign in again from Devices → Accounts.", "authentication")

        with self.assertLogs(background.logger, level="INFO") as logs:
            _, error = self.run_task(action)
        self.assertEqual(error, "Sign in again from Devices → Accounts.")
        self.assertIn("authentication", logs.output[0])
        self.assertNotIn("Sign in again", logs.output[0])

    def test_success_is_not_logged_as_a_failure(self):
        with patch.object(background.logger, "error") as logged:
            value, error = self.run_task(lambda: SimpleNamespace(ok=True))
        self.assertTrue(value.ok)
        self.assertIsNone(error)
        logged.assert_not_called()


if __name__ == "__main__":
    unittest.main()
