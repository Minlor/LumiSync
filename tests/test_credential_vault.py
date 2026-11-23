"""Credential vault storage format, failure reporting and sign-in fallbacks.

Every test uses an in-memory backend; nothing reaches the OS credential store.
"""

import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from lumisync.accounts.errors import AccountError
from lumisync.accounts.manager import AccountManager
from lumisync.accounts.secrets import CHUNK_SIZE, SERVICE, CredentialVault

SECRET = "fixture-token-value"
ACCOUNT = "account/0123456789abcdef0123456789abcdef"


class StoreError(Exception):
    """Shaped like pywintypes.error: a numeric code and the failing call."""

    def __init__(self, winerror, funcname, text):
        super().__init__(winerror, funcname, text)
        self.winerror = winerror
        self.funcname = funcname


class MemoryBackend:
    """A keyring backend in memory, optionally refusing long values."""

    def __init__(self, max_length=None):
        self.entries = {}
        self.max_length = max_length
        self.fail_on = None  # a predicate on (username) that makes set_password raise

    def get_password(self, service, username):
        return self.entries.get((service, username))

    def set_password(self, service, username, password):
        if self.fail_on is not None and self.fail_on(username):
            raise StoreError(1783, "CredWrite", "The stub received bad data " + SECRET)
        if self.max_length is not None and len(password) > self.max_length:
            raise StoreError(1783, "CredWrite", "The stub received bad data")
        self.entries[(service, username)] = password

    def delete_password(self, service, username):
        del self.entries[(service, username)]

    def keys(self):
        return sorted(username for service, username in self.entries if service == SERVICE)


def value_of_length(length, fill="x"):
    """A dict whose JSON text is exactly *length* characters."""
    overhead = len(json.dumps({"v": ""}))
    value = {"v": fill * (length - overhead)}
    assert len(json.dumps(value)) == length
    return value


class VaultCase(unittest.TestCase):
    def setUp(self):
        self.backend = MemoryBackend()

    def vault(self):
        """A fresh vault (a new process: no session copies) on the same store."""
        store = CredentialVault()
        patcher = patch.object(store, "_backend", return_value=self.backend)
        patcher.start()
        self.addCleanup(patcher.stop)
        return store


class ChunkedStorageTests(VaultCase):
    def test_round_trip_below_at_and_above_the_chunk_size(self):
        for length in (20, CHUNK_SIZE - 1, CHUNK_SIZE, CHUNK_SIZE + 1, 2 * CHUNK_SIZE,
                       2 * CHUNK_SIZE + 1, 3000, 10 * CHUNK_SIZE + 7):
            with self.subTest(length=length):
                self.backend.entries.clear()
                value = value_of_length(length)
                self.vault().put(ACCOUNT, value)
                self.assertEqual(self.vault().get(ACCOUNT), value)
                parts = [key for key in self.backend.keys() if "#part" in key]
                expected = 0 if length <= CHUNK_SIZE else -(-length // CHUNK_SIZE)
                self.assertEqual(len(parts), expected)
                self.assertTrue(all(len(v) <= CHUNK_SIZE for v in self.backend.entries.values()))

    def test_non_ascii_values_round_trip(self):
        value = {"email": "zoë@example.test", "token": "ąćę€😀" * 400}
        self.vault().put(ACCOUNT, value)
        self.assertEqual(self.vault().get(ACCOUNT), value)

    def test_a_value_saved_by_an_older_version_still_reads(self):
        value = {"provider": "govee_account", "credentials": {"token": SECRET}}
        self.backend.entries[(SERVICE, ACCOUNT)] = json.dumps(value)
        self.assertEqual(self.vault().get(ACCOUNT), value)

    def test_a_shrinking_value_removes_its_unused_parts(self):
        store = self.vault()
        store.put(ACCOUNT, value_of_length(3500))
        self.assertEqual(len(self.backend.keys()), 5)
        store.put(ACCOUNT, value_of_length(1500))
        self.assertEqual(self.backend.keys(), [ACCOUNT, ACCOUNT + "#part0", ACCOUNT + "#part1"])
        store.put(ACCOUNT, value_of_length(100))
        self.assertEqual(self.backend.keys(), [ACCOUNT])
        self.assertEqual(self.vault().get(ACCOUNT), value_of_length(100))

    def test_delete_removes_every_part(self):
        self.vault().put(ACCOUNT, value_of_length(3000))
        self.vault().put("account/other", {"keep": True})
        self.vault().delete(ACCOUNT)
        self.assertEqual(self.backend.keys(), ["account/other"])
        self.assertEqual(self.vault().get(ACCOUNT), {})

    def test_delete_also_removes_parts_left_by_an_interrupted_save(self):
        self.backend.entries[(SERVICE, ACCOUNT)] = json.dumps({"old": True})
        self.backend.entries[(SERVICE, ACCOUNT + "#part0")] = "stray"
        self.backend.entries[(SERVICE, ACCOUNT + "#part1")] = "stray"
        self.vault().delete(ACCOUNT)
        self.assertEqual(self.backend.keys(), [])

    def test_a_torn_write_reads_as_nothing_saved(self):
        self.vault().put(ACCOUNT, value_of_length(3000, "a"))
        for failing in (ACCOUNT + "#part1", ACCOUNT):  # mid-parts, and the header itself
            with self.subTest(failing=failing):
                self.backend.fail_on = lambda username, failing=failing: username == failing
                with self.assertRaises(AccountError), self.assertLogs("lumisync.accounts.secrets", "WARNING"):
                    self.vault().put(ACCOUNT, value_of_length(3000, "b"))
                self.backend.fail_on = None
                with self.assertLogs("lumisync.accounts.secrets", "WARNING") as logs:
                    self.assertEqual(self.vault().get(ACCOUNT), {})
                self.assertIn("incomplete", "\n".join(logs.output))

    def test_a_corrupt_part_reads_as_nothing_saved(self):
        self.vault().put(ACCOUNT, value_of_length(3000))
        self.backend.entries[(SERVICE, ACCOUNT + "#part2")] = "x" * CHUNK_SIZE
        with self.assertLogs("lumisync.accounts.secrets", "WARNING"):
            self.assertEqual(self.vault().get(ACCOUNT), {})
        del self.backend.entries[(SERVICE, ACCOUNT + "#part1")]
        with self.assertLogs("lumisync.accounts.secrets", "WARNING"):
            self.assertEqual(self.vault().get(ACCOUNT), {})

    def test_a_store_that_rejects_long_values_saves_a_long_one_in_parts(self):
        self.backend.max_length = 1280  # Windows: 2560 bytes of UTF-16
        value = value_of_length(3000)
        # One entry of 3000 characters is what the store refused before.
        with self.assertRaises(StoreError):
            self.backend.set_password(SERVICE, ACCOUNT, json.dumps(value))
        self.vault().put(ACCOUNT, value)
        self.assertEqual(self.vault().get(ACCOUNT), value)


class FailureLoggingTests(VaultCase):
    def test_a_refused_save_logs_type_code_kind_and_length_but_no_secret(self):
        self.backend.fail_on = lambda _username: True
        value = {"credentials": {"token": SECRET, "email": "owner@example.test"}}
        with self.assertLogs("lumisync.accounts.secrets", "WARNING") as logs, \
                self.assertRaises(AccountError) as caught:
            self.vault().put(ACCOUNT, value)
        self.assertEqual(caught.exception.code, "credential_store")
        output = "\n".join(logs.output)
        self.assertIn("StoreError", output)
        self.assertIn("code=1783", output)
        self.assertIn("function=CredWrite", output)
        self.assertIn("kind=account", output)
        self.assertIn(f"length={len(json.dumps(value))}", output)
        for private in (SECRET, "owner@example.test", ACCOUNT, "stub received"):
            self.assertNotIn(private, output)

    def test_read_and_delete_failures_are_logged_without_secrets(self):
        backend = Mock()
        backend.get_password.side_effect = OSError(5, "Access is denied " + SECRET)
        store = CredentialVault()
        with patch.object(store, "_backend", return_value=backend):
            for action in (store.get, store.delete):
                with self.subTest(action=action.__name__), \
                        self.assertLogs("lumisync.accounts.secrets", "WARNING") as logs, \
                        self.assertRaises(AccountError):
                    action("tuya-local/fixture-device")
                output = "\n".join(logs.output)
                self.assertIn("OSError code=5", output)
                self.assertIn("kind=tuya-local", output)
                self.assertNotIn(SECRET, output)
                self.assertNotIn("fixture-device", output)


class SessionFallbackTests(VaultCase):
    def test_put_or_keep_holds_a_refused_value_for_this_session(self):
        self.backend.fail_on = lambda _username: True
        store = self.vault()
        with self.assertLogs("lumisync.accounts.secrets", "WARNING"):
            self.assertFalse(store.put_or_keep(ACCOUNT, {"token": SECRET}))
        self.assertTrue(store.is_session_only(ACCOUNT))
        self.assertEqual(store.get(ACCOUNT), {"token": SECRET})
        self.assertEqual(self.vault().get(ACCOUNT), {})  # Gone after a restart.
        self.backend.fail_on = None
        self.assertTrue(store.put_or_keep(ACCOUNT, {"token": SECRET}))
        self.assertFalse(store.is_session_only(ACCOUNT))

    def test_disconnecting_a_session_fallback_removes_the_older_saved_copy(self):
        self.vault().put(ACCOUNT, {"token": "older"})
        store = self.vault()
        self.backend.fail_on = lambda _username: True
        with self.assertLogs("lumisync.accounts.secrets", "WARNING"):
            store.put_or_keep(ACCOUNT, {"token": SECRET})
        self.backend.fail_on = None
        store.delete(ACCOUNT)
        self.assertEqual(self.backend.keys(), [])

    def test_sign_in_whose_save_fails_is_connected_for_this_session(self):
        self.backend.fail_on = lambda _username: True
        store = self.vault()
        manager = AccountManager()
        client = SimpleNamespace(credentials={"token": SECRET}, close=Mock())
        with patch("lumisync.accounts.manager.vault", store), \
                self.assertLogs("lumisync.accounts.secrets", "WARNING"):
            metadata = manager.register("govee_account", client, remember=True)
            self.assertIs(manager.client_for({"account_id": metadata["id"]}), client)
        self.assertTrue(metadata["remember"])
        self.assertTrue(store.is_session_only(metadata["credential_ref"]))
        self.assertEqual(store.get(metadata["credential_ref"])["credentials"], {"token": SECRET})

    def test_a_refreshed_token_that_cannot_be_saved_never_raises(self):
        store = self.vault()
        manager = AccountManager()
        for remember in (True, False):
            client = SimpleNamespace(credentials={"token": "first"}, close=Mock())
            with self.subTest(remember=remember), patch("lumisync.accounts.manager.vault", store):
                metadata = manager.register("govee_account", client, remember=remember)
                with patch.object(store, "put", side_effect=RuntimeError(SECRET)), \
                        patch.object(store, "put_or_keep", side_effect=RuntimeError(SECRET)), \
                        self.assertLogs("lumisync.accounts.manager", "WARNING") as logs:
                    client.credentials_updated({"token": SECRET})
                output = "\n".join(logs.output)
                self.assertIn("RuntimeError", output)
                self.assertNotIn(SECRET, output)
                manager.disconnect(metadata["id"])

    def test_a_refreshed_token_the_store_refuses_is_kept_for_the_session(self):
        store = self.vault()
        manager = AccountManager()
        client = SimpleNamespace(credentials={"token": "first"}, close=Mock())
        with patch("lumisync.accounts.manager.vault", store):
            metadata = manager.register("govee_account", client, remember=True)
            self.backend.fail_on = lambda _username: True
            with self.assertLogs("lumisync.accounts.secrets", "WARNING"):
                client.credentials_updated({"token": SECRET})
        self.assertEqual(store.get(metadata["credential_ref"])["credentials"], {"token": SECRET})


class ReconnectTests(VaultCase):
    def test_reconnect_rewrites_the_same_entry_and_leaves_nothing_behind(self):
        store = self.vault()
        manager = AccountManager()
        with patch("lumisync.accounts.manager.vault", store):
            first = manager.register("govee_account", SimpleNamespace(credentials={"token": "a" * 600}, close=Mock()))
            entries = self.backend.keys()
            again = manager.register("govee_account", SimpleNamespace(credentials={"token": "b" * 2500}, close=Mock()),
                                     identity=first["id"])
            self.assertEqual(again["id"], first["id"])
            self.assertEqual({key.split("#")[0] for key in self.backend.keys()}, set(entries))
            manager.disconnect(first["id"])
        self.assertEqual(self.backend.keys(), [])

    def test_reconnect_without_remember_removes_the_saved_copy(self):
        store = self.vault()
        manager = AccountManager()
        with patch("lumisync.accounts.manager.vault", store):
            first = manager.register("govee_account", SimpleNamespace(credentials={"token": "a" * 2500}, close=Mock()))
            manager.register("govee_account", SimpleNamespace(credentials={"token": "b"}, close=Mock()),
                             identity=first["id"], remember=False)
        self.assertEqual(self.backend.keys(), [])
        self.assertEqual(store.get(first["credential_ref"])["credentials"], {"token": "b"})


class SignInNoticeTests(VaultCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_verified_sign_in_with_a_refusing_store_connects_with_one_notice(self):
        from lumisync.gui.dialogs.accounts_dialog import AccountsDialog

        self.backend.fail_on = lambda _username: True
        store = self.vault()
        manager = AccountManager()
        controller = SimpleNamespace(get_accounts=lambda: [], _closing=False,
                                     import_account_devices=Mock(return_value=True), remove_account=Mock())
        with patch("lumisync.gui.dialogs.accounts_dialog.suggested_account_country", return_value=("PL", "Windows region")):
            dialog = AccountsDialog(controller)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.reject)
        dialog.provider_buttons["govee"].click()
        dialog._provider_pending, dialog._remember_pending = "govee_account", True
        client = SimpleNamespace(credentials={"email": "owner@example.test", "token": SECRET}, close=Mock())
        with patch("lumisync.accounts.manager.vault", store), \
                patch("lumisync.accounts.secrets.vault", store), \
                patch("lumisync.gui.dialogs.accounts_dialog.vault", store), \
                patch("lumisync.gui.dialogs.accounts_dialog.account_manager", manager), \
                self.assertLogs("lumisync_accounts", "WARNING") as logs, \
                self.assertLogs("lumisync.accounts.secrets", "WARNING"):
            dialog._save_connection((client, []))
        client.close.assert_not_called()
        controller.import_account_devices.assert_called_once()
        metadata = controller.import_account_devices.call_args.args[0]
        self.assertTrue(store.is_session_only(metadata["credential_ref"]))
        self.assertIs(manager.client_for({"account_id": metadata["id"]}), client)
        text = dialog.status.text()
        self.assertIn("Connected for now", text)
        self.assertIn("sign in again", text)
        self.assertIn("log folder", text)
        self.assertNotIn(SECRET, text)
        self.assertEqual(len([line for line in logs.output if "session only" in line]), 1)


class TestIsolationTests(unittest.TestCase):
    def test_the_suite_cannot_reach_the_real_credential_store(self):
        with self.assertRaises(AccountError) as caught:
            CredentialVault._backend()
        self.assertEqual(caught.exception.code, "credential_store")


if __name__ == "__main__":
    unittest.main()
