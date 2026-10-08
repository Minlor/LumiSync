"""Account and meter interaction checks using isolated fixture data."""

import os
from types import SimpleNamespace
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QLocale, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QBoxLayout

from lumisync.gui.dialogs.accounts_dialog import AccountsDialog, SignInFailure
from lumisync.accounts.errors import AccountError
from lumisync.gui.utils.account_country import suggested_account_country
from lumisync.gui.widgets.device_energy import DeviceEnergy
from lumisync.gui.widgets.energy_chart import DailyEnergyChart


MOBILE_PLUG = {
    "transport": "tuya_cloud", "device_id": "fixture-plug", "account_id": "fixture",
    "vendor_account": "lsc", "tuya_functions": {"switch_1": {"type": "bool", "dp_id": 1}},
    "tuya_status_functions": {"cur_power": {"dp_id": 19, "values": {"unit": "W", "scale": 1}},
                              "add_ele": {"dp_id": 17, "values": {"unit": "kWh", "scale": 3}}},
}


class AccountCountryTests(unittest.TestCase):
    def test_windows_home_region_takes_precedence_over_formatting_locale(self):
        with patch("lumisync.gui.utils.account_country._windows_home_country", return_value="PL"), \
                patch("lumisync.gui.utils.account_country.QLocale.system", return_value=QLocale("en_US")):
            self.assertEqual(suggested_account_country(), ("PL", "Windows region"))

    def test_unknown_region_falls_back_to_locale_without_defaulting_to_the_us(self):
        with patch("lumisync.gui.utils.account_country._windows_home_country", return_value="150"), \
                patch("lumisync.gui.utils.account_country.QLocale.system", return_value=QLocale("en_CA")):
            self.assertEqual(suggested_account_country(), ("CA", "system region"))
        with patch("lumisync.gui.utils.account_country._windows_home_country", return_value=""), \
                patch("lumisync.gui.utils.account_country.QLocale.system", return_value=QLocale.c()):
            self.assertEqual(suggested_account_country(), ("", ""))


class AccountDeviceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.widgets = []

    def tearDown(self):
        for widget in self.widgets:
            if isinstance(widget, AccountsDialog):
                widget._set_busy(False)
                widget.reject()
            widget.close()
        self.app.processEvents()
        for widget in self.widgets:
            widget.deleteLater()

    def dialog(self, accounts=()):
        controller = SimpleNamespace(get_accounts=lambda: list(accounts), _closing=False,
                                     import_account_devices=Mock(), remove_account=Mock())
        with patch("lumisync.gui.dialogs.accounts_dialog.suggested_account_country", return_value=("PL", "Windows region")):
            dialog = AccountsDialog(controller)
        self.widgets.append(dialog)
        dialog.show()
        self.app.processEvents()
        return dialog

    def test_brand_and_method_controls_keep_the_correct_password_and_alternative_flows(self):
        dialog = self.dialog()
        for brand, provider in (("tuya", "tuya_account"), ("lsc", "lsc_account"), ("govee", "govee_account")):
            QTest.mouseClick(dialog.provider_buttons[brand], Qt.MouseButton.LeftButton)
            self.assertEqual(dialog.provider.currentData(), provider)
            self.assertTrue(dialog.provider_buttons[brand].isChecked())
            self.assertFalse(dialog.field_rows["password"].isHidden())
            self.assertEqual(dialog.country_row.isHidden(), brand == "govee")
        dialog.method.setCurrentIndex(dialog.method.findData("govee_api"))
        self.assertEqual(dialog.provider.currentData(), "govee_api")
        self.assertTrue(dialog.field_rows["password"].isHidden())
        self.assertFalse(dialog.field_rows["api_key"].isHidden())
        dialog.provider_buttons["tuya"].click()
        dialog.method.setCurrentIndex(dialog.method.findData("tuya_qr"))
        self.assertFalse(dialog.field_rows["user_code"].isHidden())
        self.assertTrue(dialog.country_row.isHidden())
        self.assertIn("Home Assistant", dialog.help.text())

    def test_small_account_window_keeps_primary_actions_visible_and_preserves_input(self):
        accounts = [{"id": "fixture", "provider": "lsc_account", "label": "LSC · preview@example.com"}]
        dialog = self.dialog(accounts)
        dialog.resize(560, 620)
        dialog.provider_buttons["lsc"].click()
        dialog.entries["email"].setText("preview@example.com")
        dialog.entries["password"].setText("fixture-password")
        self.app.processEvents()
        self.assertTrue(dialog.page_switch.isVisible())
        self.assertTrue(dialog.connected_panel.isHidden())
        self.assertTrue(dialog.connect_button.isVisible())
        self.assertTrue(dialog.rect().contains(dialog.connect_button.mapTo(dialog, dialog.connect_button.rect().bottomRight())))
        dialog.page_buttons["connected"].click()
        self.app.processEvents()
        self.assertFalse(dialog.connected_panel.isHidden())
        self.assertTrue(dialog.signin_panel.isHidden())
        self.assertTrue(dialog.signin_actions.isHidden())
        self.assertTrue(dialog.close_button.isVisible())
        self.assertTrue(dialog.refresh_button.isVisible())
        dialog.page_buttons["signin"].click()
        dialog.resize(1000, 850)
        self.app.processEvents()
        self.assertTrue(dialog.page_switch.isHidden())
        self.assertFalse(dialog.connected_panel.isHidden())
        self.assertTrue(dialog._action_footer.isHidden())
        self.assertEqual(dialog.entries["email"].text(), "preview@example.com")
        self.assertEqual(dialog.entries["password"].text(), "fixture-password")
        self.assertIs(dialog.connect_button.parentWidget(), dialog.signin_actions)

    def test_energy_reflows_for_short_viewports_without_losing_readings_or_daily_access(self):
        energy = DeviceEnergy()
        self.widgets.append(energy)
        energy.set_device(MOBILE_PLUG, {"online": True, "power_w": 0.0})
        month = energy.month.currentData()
        energy._show_history({"month": month, "total_kwh": 0.0, "reported_days": 1, "expected_days": 1,
                              "days": [{"date": month + "-01", "energy_kwh": 0.0}]})
        energy.set_available_size(800, 560)
        self.assertEqual(energy.layout().direction(), QBoxLayout.Direction.LeftToRight)
        self.assertTrue(energy.chart.isHidden())
        self.assertEqual(energy.values["power_w"].text(), "0.0 W")
        energy.breakdown_button.click()
        self.assertFalse(energy.chart.isHidden())
        self.assertFalse(energy.daily.isHidden())
        energy.set_available_size(420, 560)
        self.assertEqual(energy.layout().direction(), QBoxLayout.Direction.TopToBottom)
        energy.breakdown_button.click()
        energy.set_available_size(420, 850)
        self.assertFalse(energy.chart.isHidden())
        energy.set_state({"online": False, "power_w": 0.0})
        energy.set_available_size(800, 560)
        self.assertFalse(energy.readings_note.isHidden())
        self.assertIn("unavailable", energy.readings_note.text())

    def test_show_password_is_reset_when_provider_changes_and_empty_enter_validates(self):
        dialog = self.dialog()
        dialog.entries["password"].setText("fixture-password")
        QTest.mouseClick(dialog.show_password, Qt.MouseButton.LeftButton)
        self.assertEqual(dialog.entries["password"].echoMode(), QLineEdit.EchoMode.Normal)
        dialog.provider_buttons["tuya"].click()
        self.assertEqual(dialog.entries["password"].text(), "")
        self.assertEqual(dialog.entries["password"].echoMode(), QLineEdit.EchoMode.Password)
        with patch.object(dialog, "_run") as run:
            QTest.keyClick(dialog.entries["email"], Qt.Key.Key_Return)
            run.assert_not_called()
        self.assertIn("email", dialog.status.text())
        self.assertFalse(dialog.status.isHidden())
        self.assertEqual(dialog.status.property("feedbackState"), "error")
        self.app.processEvents()
        self.assertTrue(dialog.entries["email"].hasFocus())
        dialog.provider_buttons["lsc"].click()
        self.assertTrue(dialog.status.isHidden())

    def test_saved_country_and_manual_override_are_kept_separate_for_each_provider(self):
        dialog = self.dialog([{"id": "old-tuya", "provider": "tuya_account", "country_iso": "CA"},
                              {"id": "old-lsc", "provider": "lsc_account", "country_iso": "DE"}])
        dialog.provider_buttons["tuya"].click()
        self.assertEqual(dialog.country.currentData(Qt.ItemDataRole.UserRole + 1), "CA")
        self.assertEqual(dialog.country.currentData(), "1")
        dialog.country.setCurrentIndex(dialog.country.findData("GB", Qt.ItemDataRole.UserRole + 1))
        dialog.provider_buttons["lsc"].click()
        self.assertEqual(dialog.country.currentData(Qt.ItemDataRole.UserRole + 1), "DE")
        dialog.provider_buttons["tuya"].click()
        self.assertEqual(dialog.country.currentData(Qt.ItemDataRole.UserRole + 1), "GB")
        dialog.country.setCurrentIndex(0)
        dialog.provider_buttons["govee"].click()
        dialog.provider_buttons["tuya"].click()
        self.assertEqual(dialog.country.currentIndex(), 0)

    def test_successful_mobile_login_remembers_iso_country_without_saving_a_password(self):
        dialog = self.dialog()
        dialog.provider_buttons["lsc"].click()
        dialog.country.setCurrentIndex(dialog.country.findData("CA", Qt.ItemDataRole.UserRole + 1))
        dialog.entries["email"].setText("preview@example.test")
        dialog.entries["password"].setText("fixture-password")
        client = Mock(credentials={"profile": {}, "email": "preview@example.test"})
        client.list_devices.return_value = []
        with patch.object(dialog, "_run") as run, \
                patch("lumisync.gui.dialogs.accounts_dialog.make_client", return_value=client), \
                patch("lumisync.gui.dialogs.accounts_dialog.vault.put"):
            dialog.connect_button.click()
            value = run.call_args.args[0]()
        with patch("lumisync.gui.dialogs.accounts_dialog.account_manager") as manager:
            manager.register.return_value = {"id": "new", "provider": "lsc_account", "remember": True}
            manager.descriptors.return_value = []
            dialog._save_connection(value)
        metadata = dialog.controller.import_account_devices.call_args.args[0]
        self.assertEqual(metadata["country_iso"], "CA")
        self.assertNotIn("password", metadata)
        self.assertNotIn("preview@example.test", metadata["label"])
        self.assertEqual(dialog.entries["password"].text(), "")
        self.assertEqual(dialog.status.property("feedbackState"), "success")

    def test_empty_verification_does_not_send_or_drop_the_pending_login(self):
        dialog = self.dialog()
        dialog.entries["email"].setText("preview@example.test")
        dialog._context_pending = ("govee_account", "preview@example.test", "", "")
        dialog._deadline_pending = time.monotonic() + 900
        client = Mock()
        dialog._save_connection(SignInFailure("Code sent", "verification_required", client, "fixture-password"))
        self.assertTrue(dialog.field_rows["password"].isHidden())
        self.assertIn("Verify", dialog.signin_title.text())
        self.assertNotIn("preview@example.test", dialog.verification_help.text())
        with patch.object(dialog, "_run") as run:
            dialog.connect_button.click()
            run.assert_not_called()
        client.close.assert_not_called()
        self.assertIs(dialog._pending_client, client)
        self.assertIn("verification code", dialog.status.text())

    def test_account_rows_mask_old_addresses_and_busy_requests_block_duplicate_actions(self):
        dialog = self.dialog([{"id": "old", "provider": "tuya_account", "label": "Tuya Smart · preview@example.test"}])
        self.assertEqual(dialog._selected_account()["id"], "old")
        item = dialog.accounts_list.item(0)
        labels = dialog.accounts_list.itemWidget(item).findChildren(QLabel)
        self.assertEqual(len(labels), 2)
        self.assertTrue(all("preview@example.test" not in label.text() for label in labels))
        dialog._set_busy(True)
        self.assertFalse(dialog.refresh_button.isEnabled())
        self.assertFalse(dialog.provider_buttons["lsc"].isEnabled())
        dialog.reject()
        self.assertTrue(dialog.isVisible())
        dialog._set_busy(False)
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        self.assertFalse(dialog.isVisible())

    def test_verification_back_button_discards_the_session_and_restores_the_form(self):
        dialog = self.dialog()
        dialog.provider_buttons["lsc"].click()
        dialog.entries["email"].setText("preview@example.test")
        dialog._context_pending = ("lsc_account", "preview@example.test", "48", "")
        dialog._deadline_pending = time.monotonic() + 900
        client = Mock()
        dialog._save_connection(SignInFailure("Code sent", "verification_required", client, "fixture-password"))
        self.assertTrue(dialog.field_rows["email"].isHidden())
        self.assertTrue(dialog.country_row.isHidden())
        dialog.restart_button.click()
        client.close.assert_called_once()
        self.assertEqual(dialog._pending_password, "")
        self.assertFalse(dialog.field_rows["email"].isHidden())
        self.assertFalse(dialog.field_rows["password"].isHidden())
        self.assertFalse(dialog.country_row.isHidden())
        self.assertTrue(dialog.verification_actions.isHidden())
        self.assertTrue(dialog.status.isHidden())

    def test_setup_picker_and_remember_control_have_real_behavior(self):
        dialog = self.dialog()
        dialog.provider_buttons["lsc"].click()
        dialog.connection_options.click()
        self.assertFalse(dialog.options_panel.isHidden())
        with patch("lumisync.gui.dialogs.accounts_dialog.QFileDialog.getOpenFileName", return_value=("C:/fixture/lsc.xapk", "")) as picker:
            dialog.browse_package.click()
        picker.assert_called_once()
        self.assertEqual(dialog.app_package.text(), "C:/fixture/lsc.xapk")
        dialog.connection_options.click()
        self.assertTrue(dialog.options_panel.isHidden())
        dialog.remember.click()
        self.assertFalse(dialog.remember.isChecked())
        dialog.remember.click()
        self.assertTrue(dialog.remember.isChecked())

    def test_signin_diagnostics_do_not_log_account_credentials_or_vendor_messages(self):
        dialog = self.dialog()
        dialog.provider_buttons["lsc"].click()
        message = "fixture@example.test fixture-password fixture-verification-code"
        with patch("lumisync.gui.dialogs.accounts_dialog.logger") as diagnostics:
            dialog._save_connection(SignInFailure(message, "authentication", password="fixture-password"))
        recorded = str(diagnostics.info.call_args_list)
        self.assertIn("lsc_account", recorded)
        self.assertIn("authentication", recorded)
        for private_value in message.split():
            self.assertNotIn(private_value, recorded)

    def test_connected_account_actions_refresh_then_remove_the_selected_fixture(self):
        accounts = [{"id": "fixture", "provider": "lsc_account", "remember": False}]
        dialog = self.dialog(accounts)
        dialog.controller.remove_account.side_effect = lambda _identity: accounts.clear()
        pending = []

        def start(action, completed):
            pending.append((action, completed))
            return Mock()

        with patch("lumisync.gui.dialogs.accounts_dialog.start_task", side_effect=start), \
                patch("lumisync.gui.dialogs.accounts_dialog.account_manager") as manager:
            manager.client_for.return_value.list_devices.return_value = []
            manager.descriptors.return_value = []
            dialog.refresh_button.click()
            self.assertTrue(dialog._busy)
            action, completed = pending.pop()
            completed(action(), None)
            self.assertFalse(dialog._busy)
            dialog.controller.import_account_devices.assert_called_once_with(accounts[0], [])
            dialog.disconnect_button.click()
            action, completed = pending.pop()
            completed(action(), None)
            manager.disconnect.assert_called_once_with("fixture")
        self.assertTrue(dialog.accounts_list.isHidden())
        self.assertFalse(dialog.accounts_empty.isHidden())

    def test_qr_buttons_request_a_code_then_complete_approval_with_the_same_client(self):
        dialog = self.dialog()
        dialog.provider_buttons["tuya"].click()
        dialog.method.setCurrentIndex(dialog.method.findData("tuya_qr"))
        QTest.keyClicks(dialog.entries["user_code"], "fixture-user-code")
        client = Mock(credentials={})
        client.request_qr.return_value = "https://example.invalid/fixture-not-a-login"
        client.finish_qr.side_effect = [False, True]
        client.list_devices.return_value = []
        pending = []

        def start(action, completed):
            pending.append((action, completed))
            return Mock()

        with patch("lumisync.gui.dialogs.accounts_dialog.start_task", side_effect=start), \
                patch("lumisync.gui.dialogs.accounts_dialog.TuyaSharingClient", return_value=client), \
                patch("lumisync.gui.dialogs.accounts_dialog.account_manager") as manager:
            manager.register.return_value = {"id": "fixture", "provider": "tuya_qr", "remember": True}
            manager.descriptors.return_value = []
            dialog.connect_button.click()
            action, completed = pending.pop()
            completed(action(), None)
            self.assertFalse(dialog.qr_label.pixmap().isNull())
            self.assertFalse(dialog.qr_confirm.isHidden())
            dialog.qr_confirm.click()
            action, completed = pending.pop()
            completed(action(), None)
            self.assertIn("pending", dialog.status.text())
            dialog.qr_confirm.click()
            action, completed = pending.pop()
            completed(action(), None)
        client.request_qr.assert_called_once_with("fixture-user-code")
        self.assertTrue(dialog.qr_confirm.isHidden())
        self.assertEqual(dialog.status.property("feedbackState"), "success")

    def test_resend_button_keeps_the_pending_email_session(self):
        dialog = self.dialog()
        dialog.entries["email"].setText("preview@example.test")
        dialog._context_pending = ("govee_account", "preview@example.test", "", "")
        dialog._deadline_pending = time.monotonic() + 900
        client = Mock()
        dialog._save_connection(SignInFailure("Code sent", "verification_required", client, "fixture-password"))
        with patch.object(dialog, "_run") as run:
            dialog.resend_button.click()
            action, completed = run.call_args.args
            completed(action())
        client.request_verification.assert_called_once_with("preview@example.test")
        self.assertIs(dialog._pending_client, client)
        self.assertIn("new Govee code", dialog.status.text())

    def test_daily_usage_preserves_gaps_and_zero_and_supports_keyboard_inspection(self):
        panel = DeviceEnergy()
        self.widgets.append(panel)
        panel.set_device(MOBILE_PLUG, {"power_w": 0, "online": True})
        month = panel.month.currentData()
        history = {"month": month, "total_kwh": 0.5, "reported_days": 2, "expected_days": 3,
                   "days": [{"date": month + f"-{day:02}", "energy_kwh": value}
                            for day, value in enumerate((0, None, 0.5), 1)]}
        panel._show_history(history)
        panel.show()
        self.app.processEvents()
        self.assertEqual(panel.values["power_w"].text(), "0.0 W")
        self.assertEqual(panel.daily.item(0, 1).text(), "0.000")
        self.assertEqual(panel.daily.item(1, 1).text(), "Not reported")
        self.assertTrue(panel.daily.isHidden())
        panel.breakdown_button.click()
        self.assertFalse(panel.daily.isHidden())
        panel.chart.setFocus()
        QTest.keyClick(panel.chart, Qt.Key.Key_Home)
        self.assertIn("0.000 kWh", panel.chart.accessibleDescription())
        QTest.keyClick(panel.chart, Qt.Key.Key_Right)
        self.assertIn("Not reported", panel.chart.accessibleDescription())
        self.assertNotIn("0.000 kWh", panel.chart.accessibleDescription())
        panel.month.setCurrentIndex(1)
        self.assertTrue(panel.chart.isHidden())
        self.assertTrue(panel.breakdown_button.isHidden())

    def test_history_error_hides_old_values_and_readings_remain_available(self):
        panel = DeviceEnergy()
        self.widgets.append(panel)
        panel.set_device(MOBILE_PLUG, {"power_w": 17.5})
        panel._request_context = panel._context()
        panel._history_finished(None, "History is unavailable.")
        self.assertIn("try again", panel.history_note.text())
        self.assertTrue(panel.load_button.isEnabled())
        self.assertTrue(panel.chart.isHidden())
        self.assertEqual(panel.values["power_w"].text(), "17.5 W")
        chart = DailyEnergyChart()
        self.widgets.append(chart)
        chart.set_days([{"date": "2026-10-01", "energy_kwh": float("nan")}])
        self.assertIn("Not reported", chart.day_description(0))

    def test_load_usage_and_refresh_buttons_keep_history_errors_separate_from_live_readings(self):
        panel = DeviceEnergy()
        self.widgets.append(panel)
        panel.set_device(MOBILE_PLUG, {"power_w": 4.5})
        refreshed = []
        panel.refresh_requested.connect(lambda: refreshed.append(True))
        panel.refresh_button.click()
        self.assertEqual(refreshed, [True])
        month = panel.month.currentData()
        history = {"month": month, "total_kwh": 0, "reported_days": 1, "expected_days": 1,
                   "days": [{"date": month + "-01", "energy_kwh": 0}]}
        pending = []

        def start(action, completed):
            pending.append((action, completed))
            return Mock()

        with patch("lumisync.gui.widgets.device_energy.start_task", side_effect=start), \
                patch("lumisync.gui.widgets.device_energy.account_manager") as manager:
            manager.client_for.return_value.query_energy_history.return_value = history
            panel.load_button.click()
            self.assertFalse(panel.load_button.isEnabled())
            action, completed = pending.pop()
            completed(action(), None)
            self.assertIn("0.000 kWh", panel.total.text())
            self.assertFalse(panel.chart.isHidden())
            panel.load_button.click()  # A cached month renders without another request.
            self.assertFalse(pending)
            panel.month.setCurrentIndex(1)
            manager.client_for.return_value.query_energy_history.side_effect = AccountError("Not available", "unsupported")
            panel.load_button.click()
            action, completed = pending.pop()
            with self.assertRaises(AccountError) as failure:
                action()
            completed(None, str(failure.exception))
        self.assertTrue(panel.chart.isHidden())
        self.assertEqual(panel.values["power_w"].text(), "4.5 W")
        self.assertIn("try again", panel.history_note.text())
