"""Devices page after the 1.0 feedback: calm search, account issues, colours.

Everything runs on fakes: no device, account or network I/O.
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lumisync.gui.app_settings import app_settings
from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.controllers.discovery import AccountFailure, DeviceStatusWorker
from lumisync.gui.dialogs.accounts_dialog import AccountsDialog
from lumisync.gui.main_window import LumiSyncMainWindow
from lumisync.gui.views.devices_view import DevicesView
from lumisync.gui.widgets.color_flyout import (
    RECENT_COLORS_KEY,
    WARM_WHITE_KELVIN,
    WARM_WHITE_RGB,
    ColorFlyout,
    SwatchRow,
    preset_choices,
    recent_colors,
)
from lumisync.gui.widgets.device_card import DeviceCard, describe_device_status
from lumisync.gui.widgets.device_inspector import DeviceInspector
from lumisync.utils.colors import kelvin_to_rgb

LSC = {"id": "lsc", "provider": "lsc_account", "label": "LSC Smart Connect · m•••@•••.com", "remember": True}
GOVEE = {"id": "govee", "provider": "govee_account", "label": "Govee Home · a•••@•••.com", "remember": True}
LAMP = {"mac": "fixture-lamp", "name": "Desk lamp", "model": "H619C", "sku": "H619C", "transport": "lan",
        "ip": "192.0.2.5"}
RGB_ONLY = {"transport": "govee_cloud", "device_id": "fixture-rgb", "mac": "fixture-rgb", "name": "Shelf",
            "cloud_capabilities": [{"instance": "colorRgb"}]}
PANEL = {"mac": "fixture-panel", "name": "Panel", "transport": "ble", "ble_address": "AA:BB:CC:DD:EE:FF",
         "matrix_size": "32x32"}
PLUG = {"transport": "tuya", "device_kind": "smart_plug", "device_id": "fixture-plug", "mac": "tuya:fixture-plug",
        "name": "PC Power Plug", "ip": "192.0.2.9", "tuya_functions": {"switch_1": {"type": "bool", "dp_id": 1}}}
STRIP = {"transport": "tuya", "device_id": "fixture-strip", "mac": "tuya:fixture-strip", "ip": "192.0.2.10",
         "name": "Battletron", "local_key_ref": "fixture-key-reference", "cloud_transport": "tuya_cloud",
         "account_id": "lsc"}

SAVE_PATCHES = ("lumisync.gui.controllers.device_controller.devices.writeJSON",)


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._widgets = []
        settings = app_settings()
        settings.remove(RECENT_COLORS_KEY)
        settings.sync()

    def tearDown(self):
        for widget in self._widgets:
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def keep(self, widget):
        self._widgets.append(widget)
        return widget

    def controller(self, devices=()):
        with patch("lumisync.gui.controllers.device_controller.devices.get_data",
                   return_value={"devices": [], "selectedDevice": 0}):
            controller = DeviceController()
        self.addCleanup(controller.shutdown)
        controller.devices = [dict(device) for device in devices]
        self.notices = []
        self.statuses = []
        controller.notice.connect(lambda message, kind: self.notices.append((message, kind)))
        controller.status_updated.connect(self.statuses.append)
        return controller

    def view(self, controller, width=1100, height=760):
        with patch.object(controller, "get_groups", return_value=[]), \
                patch.object(controller, "refresh_device_states"):
            view = self.keep(DevicesView(controller))
            view.resize(width, height)
            view.show()
            self.app.processEvents()
        return view


class FirmwareRemovalTests(_Base):
    def test_firmware_checking_is_gone_from_the_inspector_view_and_controller(self):
        inspector = self.keep(DeviceInspector())
        inspector.set_device(0, dict(STRIP), {}, primary=False)
        self.assertFalse(hasattr(inspector, "firmware_button"))
        self.assertFalse(hasattr(DeviceInspector, "firmware_requested"))
        self.assertFalse(hasattr(DeviceController, "check_firmware_at"))
        self.assertFalse(hasattr(DevicesView, "_on_firmware_requested"))
        texts = [button.text() for button in inspector.findChildren(type(inspector.remove_button))]
        self.assertFalse(any("firmware" in text.lower() for text in texts))


class FindDevicesTests(_Base):
    def run_search(self, controller, accounts, network=True):
        tasks = []

        def start(action, completed):
            tasks.append((action, completed))
            return Mock()

        with patch.object(controller, "get_accounts", return_value=accounts), \
                patch.object(controller, "_local_network_available", return_value=network), \
                patch.object(controller, "_start_lan_discovery", return_value=True), \
                patch.object(controller, "_start_ble_scan", return_value=True), \
                patch("lumisync.gui.controllers.device_controller.start_task", side_effect=start):
            controller.find_devices()
        return tasks

    def finish(self, controller, tasks, results, accounts):
        """Complete every account refresh, then the local transports."""
        with patch.object(controller, "get_accounts", return_value=accounts), \
                patch.object(controller, "_load_settings_safe", return_value={"devices": [], "accounts": accounts}), \
                patch(SAVE_PATCHES[0]), \
                patch.object(controller, "refresh_device_states"):
            for (_action, completed), (value, error) in zip(tasks[:len(accounts)], results):
                completed(value, error)
            controller._on_tuya_scan_finished([], None)
            controller._search_results["lan"].update({"available": True, "found": 0})
            controller._finish_search_transport("lan")
            controller._search_results["bluetooth"].update({"available": True, "found": 0})
            controller._finish_search_transport("bluetooth")

    def test_search_start_and_account_imports_raise_no_toasts_and_new_devices_one(self):
        controller = self.controller([LAMP])
        tasks = self.run_search(controller, [LSC])
        self.assertEqual(self.notices, [], "a search must not toast when it starts")
        self.assertTrue(controller._combined_search_active)
        new = {"device_id": "fixture-new-plug", "mac": "tuya:fixture-new-plug", "name": "Kettle",
               "transport": "tuya_cloud", "account_id": "lsc", "device_kind": "smart_plug"}
        self.finish(controller, tasks, [([new], None)], [LSC])
        self.assertFalse(controller._combined_search_active)
        self.assertEqual(self.notices, [("Found 1 new device", "success")])
        self.assertTrue(any(message.startswith("Imported") for message in self.statuses))
        self.assertFalse(controller.account_issues)

    def test_a_search_that_finds_nothing_new_is_silent(self):
        controller = self.controller([LAMP, dict(STRIP)])
        tasks = self.run_search(controller, [LSC])
        known = {"device_id": "fixture-strip", "mac": "tuya:fixture-strip", "name": "Battletron",
                 "transport": "tuya_cloud", "account_id": "lsc"}
        self.finish(controller, tasks, [([known], None)], [LSC])
        self.assertEqual(self.notices, [])
        self.assertEqual(len(controller.devices), 2)

    def test_a_failing_account_becomes_an_issue_named_by_its_label_not_a_toast(self):
        controller = self.controller([LAMP])
        tasks = self.run_search(controller, [LSC, GOVEE])
        expired = AccountFailure("Your LSC Smart Connect session has expired. Sign in again.", "authentication")
        self.finish(controller, tasks, [(expired, None), ([], None)], [LSC, GOVEE])
        self.assertEqual(self.notices, [])
        self.assertEqual(list(controller.account_issues), ["lsc"])
        self.assertEqual(controller.account_issues["lsc"],
                         "LSC Smart Connect · m•••@•••.com: Your LSC Smart Connect session has expired. Sign in again.")
        self.assertEqual(controller.account_issue("lsc")[0], "LSC Smart Connect · m•••@•••.com")

    def test_the_account_refresh_keeps_the_account_error_code(self):
        from lumisync.accounts.errors import AccountError

        client = Mock()
        client.list_devices.side_effect = AccountError("Tuya's request limit was reached.", "rate_limit")
        with patch("lumisync.accounts.manager.account_manager.client_for", return_value=client):
            failure = DeviceController._list_account_devices(LSC)
        self.assertEqual(failure, AccountFailure("Tuya's request limit was reached.", "rate_limit"))
        self.assertFalse(failure.breaks_account)

    def test_rate_limits_and_timeouts_are_not_account_issues(self):
        controller = self.controller([LAMP])
        tasks = self.run_search(controller, [LSC, GOVEE])
        self.finish(controller, tasks, [(AccountFailure("Tuya's request limit was reached.", "rate_limit"), None),
                                        (None, "The request timed out.")], [LSC, GOVEE])
        self.assertEqual(controller.account_issues, {})
        self.assertEqual(self.notices, [])
        self.assertTrue(any("2 account(s) couldn't refresh" in message for message in self.statuses), self.statuses)
        self.assertFalse(controller.record_account_failure(LSC, AccountFailure("Server error", "service"), network=True))
        self.assertFalse(controller.record_account_failure(LSC, AccountFailure("Device offline", "offline"), network=True))
        self.assertEqual(controller.account_issues, {})

    def test_a_search_without_a_local_network_records_no_account_issue(self):
        controller = self.controller([LAMP])
        tasks = self.run_search(controller, [LSC], network=False)
        self.finish(controller, tasks, [(AccountFailure("Sign in again.", "authentication"), None)], [LSC])
        self.assertEqual(controller.account_issues, {})
        with patch.object(controller, "_local_network_available", return_value=False):
            self.assertFalse(controller.record_account_failure(LSC, AccountFailure("Sign in again.", "authentication")))
        self.assertEqual(controller.account_issues, {})
        self.assertTrue(controller.record_account_failure(LSC, AccountFailure("Sign in again.", "credential_store"),
                                                          network=True))
        self.assertEqual(list(controller.account_issues), ["lsc"])

    def test_a_refresh_whose_save_fails_gives_exactly_one_notice(self):
        controller = self.controller([LAMP])
        tasks = self.run_search(controller, [LSC])
        new = {"device_id": "fixture-new-plug", "mac": "tuya:fixture-new-plug", "name": "Kettle",
               "transport": "tuya_cloud", "account_id": "lsc", "device_kind": "smart_plug"}
        with patch.object(controller._store, "write", side_effect=OSError("fixture: disk full")):
            self.finish(controller, tasks, [([new], None)], [LSC])
        self.assertFalse(controller._combined_search_active)
        self.assertEqual(len(self.notices), 1, self.notices)
        self.assertEqual(self.notices[0][1], "error")
        self.assertIn("Could not save the account's devices.", self.notices[0][0])
        self.assertEqual(controller.account_issues, {}, "a save failure says nothing about the account")
        self.assertEqual(len(controller.devices), 1)

    def test_search_button_and_progress_line_show_progress_without_a_status_panel(self):
        controller = self.controller([LAMP])
        view = self.view(controller)
        self.assertFalse(hasattr(view, "search_status"))
        self.assertFalse(hasattr(view, "_set_transport_status"))
        self.assertEqual(view.search_progress.height(), 2)
        controller.device_search_started.emit()
        self.assertEqual(view.find_devices_button.text(), "Searching…")
        self.assertFalse(view.find_devices_button.isEnabled())
        self.assertTrue(view.search_progress.is_running())
        self.assertTrue(view.info_host.isHidden())
        controller.device_search_finished.emit({"lan": {"available": True}, "bluetooth": {"available": True}})
        self.assertEqual(view.find_devices_button.text(), "Find devices")
        self.assertTrue(view.find_devices_button.isEnabled())
        self.assertFalse(view.search_progress.is_running())
        self.assertTrue(view.info_host.isHidden())


class AccountIssueTests(_Base):
    def test_issue_shows_in_the_info_bar_and_on_the_accounts_button_until_the_account_works(self):
        controller = self.controller([LAMP, dict(STRIP)])
        view = self.view(controller)
        self.assertTrue(view.account_info_bar.isHidden())
        self.assertFalse(view.accounts_button.has_attention())
        with patch.object(controller, "get_accounts", return_value=[LSC]):
            controller.set_account_issue(LSC, "Sign-in expired. Reconnect in Accounts.")
        self.assertFalse(view.account_info_bar.isHidden())
        self.assertEqual(view.account_info_bar.title(), "Couldn't refresh LSC Smart Connect · m•••@•••.com")
        self.assertEqual(view.account_info_bar.message(), "Sign-in expired. Reconnect in Accounts.")
        self.assertEqual(view.account_info_bar.action_button.text(), "Open Accounts")
        self.assertTrue(view.accounts_button.has_attention())
        self.assertIn("LSC Smart Connect", view.accounts_button.toolTip())

        with patch.object(controller, "_load_settings_safe", return_value={"devices": [], "accounts": [LSC]}), \
                patch(SAVE_PATCHES[0]):
            self.assertTrue(controller.import_account_devices(LSC, []))
        self.assertEqual(controller.account_issues, {})
        self.assertTrue(view.account_info_bar.isHidden())
        self.assertFalse(view.accounts_button.has_attention())

    def test_several_issues_are_listed_and_closing_hides_until_the_set_changes(self):
        controller = self.controller([LAMP])
        view = self.view(controller)
        controller.set_account_issue(LSC, "Sign in again.")
        controller.set_account_issue(GOVEE, "Sign in again.")
        self.assertEqual(view.account_info_bar.title(), "2 accounts couldn't refresh")
        self.assertIn("Govee Home", view.account_info_bar.message())
        view.account_info_bar.close_button.click()
        self.assertTrue(view.account_info_bar.isHidden())
        view._update_info_bars()
        self.assertTrue(view.account_info_bar.isHidden(), "a closed bar stays closed for the same issues")
        self.assertTrue(view.accounts_button.has_attention(), "the button keeps pointing at the problem")
        controller.clear_account_issue("govee")
        self.assertFalse(view.account_info_bar.isHidden())
        self.assertEqual(view.account_info_bar.title(), "Couldn't refresh LSC Smart Connect · m•••@•••.com")

    def test_open_accounts_action_selects_the_failing_account(self):
        controller = self.controller([LAMP])
        view = self.view(controller)
        controller.set_account_issue(LSC, "Sign in again.")
        with patch.object(view, "_open_accounts") as opened:
            view.account_info_bar.action_button.click()
        opened.assert_called_once_with("lsc")

    def test_removing_an_account_clears_its_issue(self):
        controller = self.controller([LAMP])
        controller.set_account_issue(LSC, "Sign in again.")
        with patch.object(controller, "_load_settings_safe", return_value={"devices": [], "accounts": [LSC]}), \
                patch(SAVE_PATCHES[0]):
            self.assertTrue(controller.remove_account("lsc"))
        self.assertEqual(controller.account_issues, {})

    def test_status_poll_records_broken_sign_in_but_not_an_unreachable_device(self):
        controller = self.controller([dict(STRIP)])
        key = controller._device_key(STRIP)
        controller._merge_device_state(0, {"device_key": key, "online": False, "status_source": "offline",
                                           "status_origin": "cloud", "last_error": "No status reply"})
        self.assertEqual(controller.account_issues, {})
        controller._merge_device_state(0, {
            "device_key": key, "online": False, "status_source": "offline", "status_origin": "cloud",
            "last_error": "This device is offline.", "account_error_code": "offline"})
        self.assertEqual(controller.account_issues, {})
        with patch.object(controller, "_local_network_available", return_value=True):
            controller._merge_device_state(0, {
                "device_key": key, "online": False, "status_source": "offline", "status_origin": "cloud",
                "last_error": "Your LSC Smart Connect session has expired. Sign in again from Devices → Accounts.",
                "account_error_code": "authentication"})
        self.assertIn("session has expired", controller.account_issues["lsc"])
        self.assertNotIn("account_error_code", controller.get_device_state_at(0))
        controller._merge_device_state(0, {"device_key": key, "online": True, "status_source": "confirmed",
                                           "status_origin": "cloud", "power_on": True})
        self.assertEqual(controller.account_issues, {})

    def test_an_account_only_device_read_counts_as_the_account_working(self):
        account_only = {"transport": "tuya_cloud", "device_id": "fixture-cloud", "mac": "tuya:fixture-cloud",
                        "name": "Cloud bulb", "account_id": "lsc"}
        adapter = Mock()
        adapter.query_status.return_value = {"power_on": True, "online": True}
        updates = []
        worker = DeviceStatusWorker([(0, account_only)])
        worker.state_updated.connect(lambda _index, state: updates.append(state))
        with patch("lumisync.gui.controllers.discovery.pool.acquire", return_value=adapter), \
                patch("lumisync.gui.controllers.discovery.pool.is_pooled", return_value=False), \
                patch("lumisync.gui.controllers.discovery.create_adapter",
                      side_effect=AssertionError("an account-only device has no separate fallback")):
            worker.run()
        self.assertEqual(len(updates), 1, updates)
        self.assertEqual(updates[0]["status_origin"], "cloud")
        controller = self.controller([account_only])
        controller.set_account_issue(LSC, "Sign in again.")
        controller._merge_device_state(0, updates[0])
        self.assertEqual(controller.account_issues, {})

    def test_network_and_bluetooth_info_bars(self):
        controller = self.controller([LAMP])
        view = self.view(controller)
        view._on_search_finished({"lan": {"available": False}, "bluetooth": {"available": False}})
        self.assertFalse(view.network_info_bar.isHidden())
        self.assertEqual(view.network_info_bar.title(), "No local network connection")
        self.assertTrue(view.bluetooth_info_bar.isHidden(), "Bluetooth only matters with Bluetooth devices")
        view.network_info_bar.close_button.click()
        view._on_search_finished({"lan": {"available": False}, "bluetooth": {"available": False}})
        self.assertTrue(view.network_info_bar.isHidden())
        view._on_search_finished({"lan": {"available": True}, "bluetooth": {"available": True}})
        view._on_search_finished({"lan": {"available": False}, "bluetooth": {"available": True}})
        self.assertFalse(view.network_info_bar.isHidden(), "a new outage shows again")
        with patch.object(controller, "_local_network_available", return_value=True):
            view._recheck_network()
        self.assertTrue(view.network_info_bar.isHidden())

        controller.devices = [LAMP, PANEL]
        view._on_search_finished({"lan": {"available": True}, "bluetooth": {"available": False}})
        self.assertFalse(view.bluetooth_info_bar.isHidden())
        self.assertEqual(view.bluetooth_info_bar.title(), "Bluetooth is off")


class AccountsDialogTests(_Base):
    def dialog(self, controller, **kwargs):
        with patch("lumisync.gui.dialogs.accounts_dialog.suggested_account_country", return_value=("PL", "Windows region")):
            dialog = self.keep(AccountsDialog(controller, **kwargs))
        dialog.show()
        self.app.processEvents()
        return dialog

    def test_rows_show_connection_health_and_the_selected_issue_offers_reconnect(self):
        controller = self.controller([dict(STRIP)])
        with patch.object(controller, "get_accounts", return_value=[GOVEE, LSC]):
            controller.set_account_issue(LSC, "Your session has expired.")
            dialog = self.dialog(controller, select_account="lsc")
            self.assertEqual(dialog._selected_account()["id"], "lsc")
            self.assertEqual(dialog.account_row_status(0), "Connected · 0 devices")
            self.assertEqual(dialog.account_row_status(1), "Your session has expired.")
            self.assertFalse(dialog.account_issue_bar.isHidden())
            self.assertIn("Your session has expired.", dialog.account_issue_bar.message())
            self.assertFalse(dialog.reconnect_button.isHidden())
            dialog.reconnect_button.click()
            self.assertEqual(dialog.provider.currentData(), "lsc_account")
            self.assertIn("Sign in again", dialog.status.text())
            dialog.accounts_list.setCurrentRow(0)
            self.assertTrue(dialog.account_issue_bar.isHidden())
            self.assertTrue(dialog.reconnect_button.isHidden())
            dialog.reject()

    def test_a_failed_refresh_records_the_issue_and_a_good_one_clears_it(self):
        controller = self.controller([dict(STRIP)])
        pending = []

        def start(action, completed):
            pending.append((action, completed))
            return Mock()

        with patch.object(controller, "get_accounts", return_value=[LSC]), \
                patch.object(controller, "_local_network_available", return_value=True), \
                patch("lumisync.gui.dialogs.accounts_dialog.start_task", side_effect=start):
            dialog = self.dialog(controller)
            self.assertEqual(dialog.account_row_status(0), "Connected · 1 device")
            # A timeout or a rate limit is shown here, never as an account issue.
            dialog.refresh_button.click()
            pending.pop()[1](None, "The request timed out.")
            self.assertEqual(dialog.status.text(), "The request timed out.")
            dialog.refresh_button.click()
            pending.pop()[1](AccountFailure("Tuya's request limit was reached.", "rate_limit"), None)
            self.assertEqual(dialog.status.text(), "Tuya's request limit was reached.")
            self.assertEqual(controller.account_issues, {})
            dialog.refresh_button.click()
            pending.pop()[1](AccountFailure("Your session has expired. Sign in again.", "authentication"), None)
            self.assertIn("session has expired", controller.account_issues["lsc"])
            self.assertEqual(dialog.account_row_status(0), "Your session has expired. Sign in again.")
            dialog.refresh_button.click()
            with patch.object(controller, "_load_settings_safe", return_value={"devices": [], "accounts": [LSC]}), \
                    patch(SAVE_PATCHES[0]):
                pending.pop()[1]([], None)
            self.assertEqual(controller.account_issues, {})
            self.assertTrue(dialog.account_row_status(0).startswith("Connected"))
            self.assertEqual(dialog.status.property("feedbackState"), "success")
            dialog.reject()

    def test_a_refresh_whose_save_fails_reports_it_once_and_records_no_issue(self):
        controller = self.controller([dict(STRIP)])
        pending = []

        def start(action, completed):
            pending.append((action, completed))
            return Mock()

        with patch.object(controller, "get_accounts", return_value=[LSC]), \
                patch("lumisync.gui.dialogs.accounts_dialog.start_task", side_effect=start):
            dialog = self.dialog(controller)
            dialog.refresh_button.click()
            with patch.object(controller._store, "write", side_effect=OSError("fixture: disk full")):
                pending.pop()[1]([], None)
            self.assertEqual(len(self.notices), 1, self.notices)
            self.assertEqual(self.notices[0][1], "error")
            self.assertEqual(controller.account_issues, {})
            self.assertIn("could not be saved", dialog.status.text())
            dialog.reject()

    def sign_in_again(self, controller, dialog, email, saved_email=None):
        """Reconnect the selected account and finish a (fake) sign-in.

        *saved_email* is what the (fake) credential store holds for the old
        account; None means its entry cannot be read.
        """
        pending = []

        def start(action, completed):
            pending.append((action, completed))
            return Mock()

        dialog.reconnect_button.click()
        dialog.entries["email"].setText(email)
        dialog.entries["password"].setText("fixture-password")
        if not dialog.country.currentData():
            dialog.country.setCurrentIndex(1)
        with patch("lumisync.gui.dialogs.accounts_dialog.start_task", side_effect=start):
            dialog._connect()
        self.assertEqual(len(pending), 1, dialog.status.text())

        def register(provider, _client, *, remember=True, identity=None):
            identity = identity or "fixture-new-account"
            return {"id": identity, "provider": provider, "credential_ref": "account/" + identity,
                    "remember": remember}

        manager = Mock()
        manager.register.side_effect = register
        manager.descriptors.return_value = []
        client = SimpleNamespace(credentials={"email": email}, close=Mock())
        vault = Mock()
        if saved_email is None:
            from lumisync.accounts.errors import AccountError

            vault.get.side_effect = AccountError("Could not read the saved connection.", "credential_store")
        else:
            vault.get.return_value = {"provider": "lsc_account", "credentials": {"email": saved_email}}
        with patch("lumisync.gui.dialogs.accounts_dialog.account_manager", manager), \
                patch("lumisync.gui.dialogs.accounts_dialog.vault", vault), \
                patch.object(controller._store, "read", return_value={"devices": [], "accounts": [dict(LSC)]}), \
                patch(SAVE_PATCHES[0]) as write:
            pending.pop()[1]((client, []), None)
        vault.get.assert_called_once_with("account/lsc")
        saved = write.call_args.args[0] if write.called else {}
        return manager, [account["id"] for account in saved.get("accounts", [])]

    def test_reconnect_updates_the_same_account_instead_of_adding_one(self):
        controller = self.controller([dict(STRIP)])
        with patch.object(controller, "get_accounts", return_value=[LSC]):
            controller.set_account_issue(LSC, "Your session has expired.")
            dialog = self.dialog(controller, select_account="lsc")
            manager, saved_ids = self.sign_in_again(controller, dialog, "Me@Example.com", saved_email="me@example.com")
            self.assertEqual(manager.register.call_args.kwargs["identity"], "lsc")
            manager.disconnect.assert_not_called()
            self.assertEqual(saved_ids, ["lsc"], "the reconnected account must not be saved twice")
            self.assertEqual(controller.account_issues, {})
            self.assertEqual(dialog.status.property("feedbackState"), "success")
            self.assertIsNone(dialog._reconnect_account)
            dialog.reject()

    def test_reconnect_with_another_email_adds_a_new_account(self):
        # The saved credentials tell even look-alike emails apart; without
        # them the label's masked email still catches an obvious mismatch.
        for email, saved_email in (("mx@example.com", "me@example.com"), ("other@example.org", None)):
            with self.subTest(email=email):
                controller = self.controller([dict(STRIP)])
                with patch.object(controller, "get_accounts", return_value=[LSC]):
                    controller.set_account_issue(LSC, "Your session has expired.")
                    dialog = self.dialog(controller, select_account="lsc")
                    manager, saved_ids = self.sign_in_again(controller, dialog, email, saved_email)
                    self.assertIsNone(manager.register.call_args.kwargs["identity"])
                    self.assertEqual(saved_ids, ["lsc", "fixture-new-account"])
                    self.assertIn("lsc", controller.account_issues, "the old account is still broken")
                    dialog.reject()

    def test_reconnect_with_an_unreadable_credential_store_matches_the_masked_email(self):
        controller = self.controller([dict(STRIP)])
        with patch.object(controller, "get_accounts", return_value=[LSC]):
            controller.set_account_issue(LSC, "Could not read the saved connection.")
            dialog = self.dialog(controller, select_account="lsc")
            manager, saved_ids = self.sign_in_again(controller, dialog, "me@example.com")
            self.assertEqual(manager.register.call_args.kwargs["identity"], "lsc")
            self.assertEqual(saved_ids, ["lsc"])
            self.assertEqual(controller.account_issues, {})
            dialog.reject()


class NotificationPolicyTests(_Base):
    def test_successful_commands_are_silent_and_failures_name_the_device(self):
        controller = self.controller([LAMP])
        key = controller._device_key(LAMP)
        with patch.object(controller, "_schedule_status_refresh"):
            controller._on_control_finished({"key": key, "transport": "LAN", "updates": {"power_on": True},
                                             "output": "On", "error": None}, None)
        self.assertEqual(self.notices, [])
        self.assertFalse(any("command sent" in message for message in self.statuses))
        controller._on_control_finished({"key": key, "error": "The device didn't respond."}, None)
        self.assertEqual(self.notices, [("Desk lamp: The device didn't respond.", "error")])

    def test_raw_socket_errors_are_not_shown(self):
        from lumisync.gui.controllers.device_controller import CONTROL_UNREACHABLE, _control_error_text

        self.assertEqual(_control_error_text(OSError(10060, "WSAETIMEDOUT 192.0.2.5"), LAMP), CONTROL_UNREACHABLE)

    def test_a_command_through_the_account_fallback_is_marked_on_the_status(self):
        controller = self.controller([dict(STRIP)])
        key = controller._device_key(STRIP)
        with patch.object(controller, "_schedule_status_refresh"):
            controller._on_control_finished({"key": key, "transport": "Wi-Fi cloud", "updates": {"power_on": True},
                                             "output": "On", "error": None}, None)
        self.assertEqual(self.notices, [])
        state = controller.get_device_state_at(0)
        text = describe_device_status(state).text
        self.assertEqual(text, "On · via account")
        self.assertNotIn("cloud", text.lower())
        controller._merge_device_state(0, {"device_key": key, "online": True, "status_source": "confirmed",
                                           "status_origin": "local", "power_on": True})
        self.assertEqual(describe_device_status(controller.get_device_state_at(0)).text, "On")

    def test_status_messages_never_toast_and_notices_do(self):
        window = MagicMock()
        window.settings.value.return_value = "false"
        LumiSyncMainWindow.show_status(window, "Imported 4 account device(s)")
        window.toasts.show.assert_not_called()
        window.statusBar.return_value.showMessage.assert_not_called()
        window.settings.value.return_value = "true"
        LumiSyncMainWindow.show_status(window, "Searching for devices…")
        window.statusBar.return_value.showMessage.assert_called_once()
        window.toasts.show.assert_not_called()
        LumiSyncMainWindow.show_notice(window, "Found 2 new devices", "success")
        window.toasts.success.assert_called_once_with("Found 2 new devices")
        LumiSyncMainWindow.show_notice(window, "Desk lamp: failed", "error")
        window.toasts.error.assert_called_once_with("Desk lamp: failed")
        LumiSyncMainWindow.show_notice(window, "Bluetooth is off", "warning")
        window.toasts.warning.assert_called_once_with("Bluetooth is off")
        window.toasts.show.assert_not_called()


class ColourTests(_Base):
    def card(self, device, index=0):
        card = self.keep(DeviceCard(index, dict(device)))
        card.resize(320, 140)
        card.show()
        self.app.processEvents()
        return card

    def test_swatch_shows_the_state_colour_or_white_temperature(self):
        card = self.card(LAMP)
        self.assertTrue(card.color_swatch.isVisibleTo(card))
        self.assertIsNone(card.current_color())
        self.assertIn("pick a colour", card.color_swatch.toolTip())
        card.set_state({"online": True, "status_source": "confirmed", "power_on": True, "brightness": 80,
                        "color": (255, 136, 0)})
        self.assertEqual(card.current_color().name(), "#ff8800")
        self.assertEqual(card.color_swatch.toolTip(), "Colour #FF8800 · click to change")
        self.assertEqual(card.status_label.text(), "On · 80%")
        card.set_state({"online": True, "status_source": "confirmed", "power_on": True, "color_temp": 2700,
                        "color": (1, 2, 3)})
        self.assertEqual(card.current_color(), QColor(*kelvin_to_rgb(2700)))
        self.assertEqual(card.color_swatch.kelvin(), 2700)
        self.assertEqual(card.color_swatch.toolTip(), "White 2700K · click to change")
        plug = self.card(PLUG, 1)
        self.assertFalse(plug.color_swatch.isVisibleTo(plug))

    def test_flyout_preset_applies_a_colour_and_closes(self):
        card = self.card(LAMP)
        picked = []
        card.color_picked.connect(lambda index, color: picked.append((index, color.name())))
        card._open_color_flyout()
        flyout = card.color_flyout()
        self.assertIsInstance(flyout, ColorFlyout)
        self.assertTrue(flyout.isVisible())
        red = next(button for button in flyout.presets.buttons() if button.accessibleName() == "Red")
        red.click()
        self.app.processEvents()
        self.assertEqual(picked, [(0, red.color().name())])
        self.assertEqual(card.current_color().name(), red.color().name())
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertIsNone(card.color_flyout())

    def test_flyout_is_kept_on_the_screen_under_the_swatch(self):
        card = self.card(LAMP)
        screen = self.app.primaryScreen()
        with patch("lumisync.gui.widgets.color_flyout.QGuiApplication") as gui:
            gui.screenAt.return_value = screen
            card._open_color_flyout()
        swatch = card.color_swatch
        gui.screenAt.assert_called_once_with(swatch.mapToGlobal(swatch.rect().center()))
        flyout = card.color_flyout()
        area = screen.availableGeometry().adjusted(-ColorFlyout.SHADOW, -ColorFlyout.SHADOW,
                                                   ColorFlyout.SHADOW, ColorFlyout.SHADOW)
        self.assertGreaterEqual(flyout.x(), area.left())
        self.assertLessEqual(flyout.x() + flyout.width() - 1, area.right())
        flyout.close()

    def test_warm_white_uses_the_temperature_path_when_the_light_has_one(self):
        card = self.card(LAMP)
        temps, colours = [], []
        card.color_temp_changed.connect(lambda index, kelvin: temps.append((index, kelvin)))
        card.color_picked.connect(lambda index, color: colours.append(color.name()))
        card._open_color_flyout()
        card.color_flyout().presets.buttons()[0].click()
        self.assertEqual(temps, [(0, WARM_WHITE_KELVIN)])
        self.assertEqual(colours, [])
        self.assertEqual(card.color_swatch.kelvin(), WARM_WHITE_KELVIN)

        rgb = self.card(RGB_ONLY, 3)
        rgb_temps, rgb_colours = [], []
        rgb.color_temp_changed.connect(lambda *args: rgb_temps.append(args))
        rgb.color_picked.connect(lambda index, color: rgb_colours.append(color.name().upper()))
        rgb._open_color_flyout()
        rgb.color_flyout().presets.buttons()[0].click()
        self.assertEqual(rgb_temps, [])
        self.assertEqual(rgb_colours, [WARM_WHITE_RGB])

    def page(self, devices):
        controller = self.controller(devices)
        for name in ("refresh_device_states", "set_color_at"):
            patcher = patch.object(controller, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        return controller, self.view(controller)

    def test_more_colours_records_recent_custom_colours(self):
        controller, view = self.page([LAMP])
        card = view._cards[0]
        for value in ("#123456", "#ABCDEF", "#123456"):
            with patch("lumisync.gui.views.devices_view.QColorDialog.getColor", return_value=QColor(value)):
                card.custom_color_requested.emit(0)
        self.assertEqual([call.args for call in controller.set_color_at.call_args_list],
                         [(0, 0x12, 0x34, 0x56), (0, 0xAB, 0xCD, 0xEF), (0, 0x12, 0x34, 0x56)])
        self.assertEqual(card.current_color().name(), "#123456")
        self.assertEqual([color.name() for color in recent_colors()], ["#123456", "#abcdef"])
        card._open_color_flyout()
        flyout = card.color_flyout()
        self.assertFalse(flyout.recents.isHidden())
        self.assertEqual([button.color().name() for button in flyout.recents.buttons()], ["#123456", "#abcdef"])
        flyout.close()
        for index in range(7):
            with patch("lumisync.gui.views.devices_view.QColorDialog.getColor",
                       return_value=QColor(10 * index, 20, 30)):
                card.custom_color_requested.emit(0)
        self.assertEqual(len(recent_colors()), 5)
        with patch("lumisync.gui.views.devices_view.QColorDialog.getColor", return_value=QColor()):
            card.custom_color_requested.emit(0)
        self.assertEqual(controller.set_color_at.call_count, 10, "a cancelled dialog changes nothing")

    def test_more_colours_from_the_flyout_and_escape_close_it(self):
        card = self.card(LAMP)
        card._open_color_flyout()
        flyout = card.color_flyout()
        QTest.keyClick(flyout, Qt.Key.Key_Escape)
        self.assertFalse(flyout.isVisible())
        card._open_color_flyout()
        flyout = card.color_flyout()
        requested = []
        card.custom_color_requested.connect(requested.append)
        flyout.more_button.click()
        QTest.qWait(20)
        self.assertEqual(requested, [0])

    def test_cards_rebuilt_while_the_colour_dialog_is_open_are_not_touched(self):
        controller, view = self.page([LAMP, PLUG])
        old_card = view._cards[0]
        lamp_key = controller._device_key(LAMP)

        def rebuild_then_choose(*_args):
            # A search finishing meanwhile reorders the list; the dialog's own
            # event loop rebuilds and deletes every card.
            controller.devices = [dict(PLUG), dict(LAMP)]
            controller.devices_discovered.emit(controller.devices)
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            return QColor("#336699")

        old_card._open_color_flyout()
        with patch("lumisync.gui.views.devices_view.QColorDialog.getColor", side_effect=rebuild_then_choose):
            old_card.color_flyout().more_button.click()
            QTest.qWait(20)
        self.assertFalse(shiboken6.isValid(old_card))
        controller.set_color_at.assert_called_once_with(1, 0x33, 0x66, 0x99)
        self.assertEqual(recent_colors()[0].name(), "#336699")
        index, card = view._card_for_key(lamp_key)
        self.assertEqual(index, 1)
        self.assertEqual(card.current_color().name(), "#336699")

    def test_swatch_rows_are_keyboard_accessible(self):
        row = self.keep(SwatchRow(columns=5))
        row.set_choices(preset_choices((2000, 9000)))
        row.show()
        self.app.processEvents()
        chosen = []
        row.chosen.connect(chosen.append)
        buttons = row.buttons()
        self.assertEqual(len(buttons), 10)
        self.assertIs(row._neighbour(buttons[0], Qt.Key.Key_Right), buttons[1])
        self.assertIs(row._neighbour(buttons[1], Qt.Key.Key_Left), buttons[0])
        self.assertIs(row._neighbour(buttons[1], Qt.Key.Key_Down), buttons[6])
        self.assertIs(row._neighbour(buttons[6], Qt.Key.Key_Up), buttons[1])
        QTest.keyClick(buttons[2], Qt.Key.Key_Return)
        self.assertEqual([choice.name for choice in chosen], ["Red"])
        self.assertEqual([button.accessibleName() for button in buttons[:2]], ["Warm white", "Cool white"])

    def test_inspector_shows_the_current_colour_and_inline_presets(self):
        inspector = self.keep(DeviceInspector())
        inspector.resize(420, 700)
        inspector.show()
        picked, temps = [], []
        inspector.color_picked.connect(lambda index, color: picked.append((index, color.name())))
        inspector.color_temp_changed.connect(lambda index, kelvin: temps.append((index, kelvin)))
        inspector.set_device(2, dict(LAMP), {"online": True, "status_source": "confirmed", "power_on": True,
                                              "color": (0, 128, 255)}, primary=False)
        self.assertEqual(inspector.status_swatch.color().name(), "#0080ff")
        self.assertEqual(inspector.color_button.color().name(), "#0080ff")
        self.assertEqual(inspector.color_hint.text(), "Colour #0080FF")
        presets = inspector.color_presets.buttons()
        self.assertGreaterEqual(len(presets), 10)
        presets[0].click()
        self.assertEqual(temps, [(2, WARM_WHITE_KELVIN)])
        blue = next(button for button in presets if button.accessibleName() == "Blue")
        blue.click()
        self.assertEqual(picked, [(2, blue.color().name())])
        with patch("lumisync.gui.widgets.device_inspector.QColorDialog.getColor", return_value=QColor("#13579B")):
            inspector.color_button.click()
        self.assertEqual(picked[-1], (2, "#13579b"))
        self.assertEqual(recent_colors()[0].name(), "#13579b")
        self.assertIn("#13579B", [button.color().name().upper() for button in inspector.color_presets.buttons()])


class PowerStateTests(_Base):
    def test_offline_devices_never_show_the_on_fill(self):
        card = self.keep(DeviceCard(0, dict(PLUG)))
        card.set_state({"online": False, "status_source": "offline", "power_on": True})
        self.assertEqual(card.power_button.property("powerState"), "offline")
        self.assertIn("last known: On", card.power_button.toolTip())
        self.assertEqual(card.status_label.text(), "Offline")
        card.set_state({"online": True, "status_source": "confirmed", "power_on": True})
        self.assertEqual(card.power_button.property("powerState"), "on")
        panel = self.keep(DeviceCard(1, dict(PANEL)))
        panel.set_state({"online": False, "status_source": "not_seen"})
        self.assertEqual(panel.power_button.property("powerState"), "offline")
        inspector = self.keep(DeviceInspector())
        inspector.set_device(0, dict(PLUG), {"online": False, "status_source": "offline", "power_on": False},
                             primary=False)
        self.assertEqual(inspector.power_button.property("powerState"), "offline")
        self.assertIn("last known: Off", inspector.power_button.toolTip())

    def test_status_dot_is_green_only_for_reachable_devices(self):
        from lumisync.gui.theme import qcolor

        card = self.keep(DeviceCard(0, dict(LAMP)))
        card.set_state({"online": True, "status_source": "confirmed", "power_on": False})
        self.assertEqual(card.status_dot._color, qcolor("fl_success"))
        card.set_state({"online": False, "status_source": "unknown", "readback_supported": True})
        self.assertNotEqual(card.status_dot._color, qcolor("fl_success"))
        card.set_state({"status_source": "error", "last_error": "Rejected"})
        self.assertEqual(card.status_dot._color, qcolor("fl_caution"))


if __name__ == "__main__":
    unittest.main()
