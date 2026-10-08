"""Regression coverage for transport routing, async controls, and panel bytes."""

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtWidgets import QApplication

from lumisync import devices
from lumisync.accounts.secrets import CredentialVault
from lumisync.accounts.tuya import normalize_devices
from lumisync.drivers import pool
from lumisync.drivers.idotmatrix_ble import (
    IDotMatrixBleAdapter, _BleLoop, build_clock_frame, build_countdown_frame,
    build_rotation_frame, build_scoreboard_frame, build_time_frame,
)
from lumisync.drivers.registry import create_adapter
from lumisync.drivers.tuya_lan import TuyaLightAdapter
from lumisync.gui.controllers.background import BackgroundTask
from lumisync.gui.controllers.device_controller import DeviceController, DeviceStatusWorker
from lumisync.gui.controllers.sync_controller import SyncController
from lumisync.gui.dialogs.accounts_dialog import AccountsDialog
from lumisync.gui.views.draw_view import DrawView
from lumisync.gui.views.modes_view import ModesView
from lumisync.gui.widgets.device_card import DeviceCard
from lumisync.gui.widgets.device_inspector import DeviceInspector
from lumisync.utils.file_operations import write_json


class PanelTests(unittest.TestCase):
    def test_new_modes_match_supplied_apk_command_bytes(self):
        self.assertEqual(build_time_frame(datetime(2026, 10, 8, 12, 34, 56)), bytes([11, 0, 1, 128, 26, 10, 8, 4, 12, 34, 56]))
        self.assertEqual(build_time_frame(datetime(2026, 10, 11))[7], 7)  # Sunday
        self.assertEqual(build_rotation_frame(True), bytes([5, 0, 6, 128, 1]))
        self.assertEqual(build_clock_frame(2, True), bytes([8, 0, 6, 1, 130, 255, 255, 255]))
        self.assertEqual(build_clock_frame(2, False)[4], 2)
        self.assertEqual(build_countdown_frame(5, 30), bytes([7, 0, 8, 128, 1, 5, 30]))
        self.assertEqual(build_scoreboard_frame(256, 511), bytes([8, 0, 10, 128, 1, 0, 1, 255]))

    def test_black_pixels_are_sent_when_drawing_without_clearing(self):
        adapter = IDotMatrixBleAdapter({"matrix_size": "16x16"})
        with patch.object(adapter, "_write") as write:
            adapter.draw_grid([[(0, 0, 0)]], clear=False)
        packets = [call.args[0] for call in write.call_args_list]
        self.assertEqual(len(packets), 3)
        self.assertEqual(packets[1][5:8], b"\x00\x00\x00")
        self.assertEqual(packets[-1], bytes([5, 0, 4, 1, 2]))

    def test_diy_mode_is_closed_on_a_failed_colour_packet(self):
        adapter = IDotMatrixBleAdapter({})
        with patch.object(adapter, "_write", side_effect=[None, RuntimeError("write failed"), None]) as write:
            with self.assertRaises(RuntimeError):
                adapter.draw_grid([[(255, 0, 0)]])
        self.assertEqual(write.call_args.args[0], bytes([5, 0, 4, 1, 2]))

    def test_ble_writes_obey_the_negotiated_att_mtu(self):
        client = SimpleNamespace(mtu_size=23, write_gatt_char=Mock())
        writes = []
        async def write(_uuid, data, response):
            writes.append((data, response))
        client.write_gatt_char = write
        runner = SimpleNamespace(run=lambda coro, timeout: asyncio.run(coro))
        adapter = IDotMatrixBleAdapter({})
        with patch.object(adapter, "_ensure_client", return_value=client), patch.object(_BleLoop, "instance", return_value=runner):
            adapter._write(bytes(range(45)))
        self.assertEqual([len(data) for data, _ in writes], [20, 20, 5])
        self.assertTrue(all(response for _, response in writes))

    def test_ble_timeout_cancels_the_outstanding_operation(self):
        future = Mock()
        future.result.side_effect = TimeoutError
        runner = object.__new__(_BleLoop)
        runner._loop = object()
        with patch("asyncio.run_coroutine_threadsafe", return_value=future), self.assertRaisesRegex(RuntimeError, "timed out"):
            runner.run(object(), timeout=1)
        future.cancel.assert_called_once()


class TuyaLanTests(unittest.TestCase):
    def test_false_power_is_preserved_and_failed_status_is_offline(self):
        adapter = TuyaLightAdapter({"transport": "tuya"})
        adapter._dev = Mock()
        adapter._dev.status.return_value = {"dps": {"20": False}}
        self.assertFalse(adapter.query_status()["power_on"])
        adapter._dev.status.return_value = {"Error": "network error"}
        self.assertIsNone(adapter.query_status())

    def test_write_error_is_not_reported_as_success(self):
        adapter = TuyaLightAdapter({})
        adapter._dev = Mock()
        adapter._dev.set_value.return_value = {"Err": "914", "Error": "wrong key"}
        with self.assertRaisesRegex(RuntimeError, "did not accept"):
            adapter.set_power(True)

    def test_imported_white_light_does_not_send_colour_or_mode_dps(self):
        functions = {"switch_led": {}, "bright_value_v2": {}, "temp_value_v2": {}}
        adapter = TuyaLightAdapter({"tuya_functions": functions, "dp_map": {"power": 101, "temperature": 103}})
        adapter._dev = Mock()
        cap = adapter.capabilities
        self.assertFalse(cap.supports_color)
        self.assertFalse(cap.supports_streaming)
        adapter.set_color_temperature(6500)
        adapter._dev.set_multiple_values.assert_called_once_with({103: 1000})
        with self.assertRaises(RuntimeError):
            adapter.set_color(255, 0, 0)

    def test_colour_mode_brightness_preserves_hue_instead_of_white_dp(self):
        adapter = TuyaLightAdapter({})
        adapter._dev = Mock()
        adapter._dev.status.return_value = {"dps": {"21": "colour", "24": "007803e803e8"}}
        adapter.set_brightness(50)
        self.assertEqual(adapter._dev.set_multiple_values.call_args.args[0][24], "007803e801f6")
        adapter._dev.set_value.assert_not_called()


class SettingsAndPoolTests(unittest.TestCase):
    def test_settings_write_migrates_legacy_key_without_mutating_the_input(self):
        store = CredentialVault()
        backend = Mock()
        settings = {"devices": [{"device_id": "bulb", "local_key": "abcdefghijklmnop"}], "selectedDevice": 0}
        with TemporaryDirectory() as directory, patch("lumisync.accounts.secrets.vault", store), patch.object(store, "_backend", return_value=backend), patch("lumisync.devices.settings_path", return_value=Path(directory) / "settings.json"):
            devices.writeJSON(settings)
            saved = json.loads((Path(directory) / "settings.json").read_text(encoding="utf-8"))
        self.assertNotIn("local_key", saved["devices"][0])
        self.assertIn("local_key_ref", saved["devices"][0])
        self.assertIn("local_key", settings["devices"][0])
        backend.set_password.assert_called_once()

    def test_failed_atomic_replace_keeps_the_previous_file(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"old":true}', encoding="utf-8")
            with patch("lumisync.utils.file_operations.os.replace", side_effect=OSError("test failure")), self.assertRaises(OSError):
                write_json({"new": True}, str(path))
            self.assertEqual(json.loads(path.read_text()), {"old": True})
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_pool_shares_one_adapter_across_simultaneous_acquisitions(self):
        device = {"transport": "tuya", "device_id": "pool-test", "ip": "192.0.2.1"}
        fake = SimpleNamespace(device=dict(device), close=Mock())
        with patch("lumisync.drivers.pool.create_adapter", return_value=fake) as create:
            with ThreadPoolExecutor(max_workers=8) as workers:
                results = list(workers.map(lambda _: pool.acquire(device), range(20)))
            self.assertTrue(all(adapter is fake for adapter in results))
            self.assertEqual(create.call_count, 1)
            pool.close(device)

    def test_changed_ip_drops_the_old_tuya_connection(self):
        device = {"transport": "tuya", "device_id": "pool-test", "ip": "192.0.2.1"}
        first = SimpleNamespace(device=dict(device), close=Mock())
        second = SimpleNamespace(device=dict(device, ip="192.0.2.2"), close=Mock())
        with patch("lumisync.drivers.pool.create_adapter", side_effect=[first, second]):
            self.assertIs(pool.acquire(device), first)
            self.assertIs(pool.acquire(second.device), second)
            first.close.assert_called_once()
            pool.close(device)

    def test_switch_gangs_share_tcp_but_keep_their_own_dp_mapping_and_identity(self):
        switches = normalize_devices({"id": "test-multi", "name": "Wall", "category": "kg"}, {
            "switch_1": {"type": "bool", "dp_id": 1}, "switch_2": {"type": "bool", "dp_id": 2},
            "switch_3": {"type": "bool", "dp_id": 3}})
        for switch in switches:
            switch.update(transport="tuya", ip="192.0.2.1", protocol_version="3.5", local_key_ref="test-key")
        first, second, third = [pool.acquire(switch) for switch in switches]
        try:
            self.assertIs(first._connection, second._connection)
            self.assertIs(first._lock, third._lock)
            first._dev = Mock()
            first._dev.status.return_value = {"dps": {"1": False, "2": True, "3": True}}
            first.set_power(False)
            second.set_power(True)
            self.assertEqual([call.args for call in first._dev.set_value.call_args_list], [(1, False), (2, True)])
            self.assertEqual(first.query_status(), {"online": True, "power_on": False})
            self.assertEqual(second.query_status(), {"online": True, "power_on": True})
            merged = devices.merge_discovered_devices(switches, switches)
            self.assertEqual(len(merged), 3)
            self.assertEqual(len({tuple(devices._device_keys(d)) for d in merged}), 3)
        finally:
            for switch in switches:
                pool.close(switch)

    def test_switch_connections_are_not_shared_across_different_local_keys(self):
        base = {"transport": "tuya", "device_id": "different-keys", "ip": "192.0.2.1"}
        one = dict(base, tuya_channel="switch_1", local_key_ref="first-key")
        two = dict(base, tuya_channel="switch_2", local_key_ref="second-key")
        first, second = pool.acquire(one), pool.acquire(two)
        try:
            self.assertIsNot(first._connection, second._connection)
        finally:
            pool.close(one)
            pool.close(two)

    def test_unknown_transport_is_rejected(self):
        with self.assertRaises(ValueError):
            create_adapter({"transport": "unsupported-vendor"})


class QtIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def controller(self):
        with patch("lumisync.gui.controllers.device_controller.devices.get_data", return_value={"devices": [], "selectedDevice": 0}):
            return DeviceController()

    def test_status_queries_use_tuya_adapter_instead_of_govee_udp(self):
        worker = DeviceStatusWorker([(0, {"transport": "tuya", "device_id": "bulb", "mac": "tuya:bulb", "ip": "192.0.2.1"})])
        adapter = Mock()
        adapter.query_status.return_value = {"online": True, "power_on": False}
        updates = []
        worker.state_updated.connect(lambda index, state: updates.append(state))
        with patch("lumisync.gui.controllers.device_controller.connection.create_lan_socket") as socket, patch("lumisync.gui.controllers.device_controller.pool.acquire", return_value=adapter):
            worker.run()
        socket.assert_not_called()
        self.assertFalse(updates[0]["power_on"])
        self.assertEqual(updates[0]["device_key"], "tuya:bulb")

    def test_reordered_device_does_not_inherit_an_old_status_reply(self):
        controller = self.controller()
        controller.devices = [{"mac": "second"}]
        controller._merge_device_state(0, {"device_key": "first", "power_on": True})
        self.assertIsNone(controller.get_device_state_at(0)["power_on"])

    def test_control_returns_without_waiting_and_coalesces_slider_changes(self):
        controller = self.controller()
        controller.devices = [{"mac": "async-panel", "transport": "ble"}]
        started, release = threading.Event(), threading.Event()
        sent = []
        def run(_device, action):
            started.set()
            release.wait(2)
            action(SimpleNamespace(set_brightness=lambda value: sent.append(value)))
            return "BLE"
        with patch.object(controller, "_run_adapter", side_effect=run), patch.object(controller, "_schedule_status_refresh"):
            controller.set_brightness_at(0, 10)
            self.assertTrue(started.wait(1))
            controller.set_brightness_at(0, 20)
            controller.set_brightness_at(0, 30)
            self.assertEqual(sent, [])
            release.set()
            for _ in range(3):
                QThreadPool.globalInstance().waitForDone(2000)
                self.app.processEvents()
        self.assertEqual(sent, [10, 30])
        self.assertEqual(controller.get_device_state_at(0)["brightness"], 30)

    def test_manual_add_does_not_publish_a_device_if_saving_fails(self):
        controller = self.controller()
        with patch("lumisync.gui.controllers.device_controller.devices.writeJSON", side_effect=OSError("failed")):
            self.assertFalse(controller.add_ble_device_manually("AA:BB"))
        self.assertEqual(controller.devices, [])

    def test_failed_command_replaces_previously_confirmed_online_status(self):
        controller = self.controller()
        device = {"mac": "panel", "transport": "ble"}
        controller.devices = [device]
        controller._merge_device_state(0, {"status_source": "confirmed", "online": True, "power_on": False})
        controller._command_tasks["panel"] = object()
        controller._on_control_finished({"key": "panel", "error": "Command rejected"}, None)
        state = controller.get_device_state_at(0)
        self.assertFalse(state["power_on"])
        self.assertEqual(state["status_source"], "error")
        self.assertEqual(DeviceInspector._status_copy(state), ("Command failed", "warning", "Command rejected"))
        card = DeviceCard(0, device)
        card.set_state(state)
        self.assertEqual(card.state_detail_label.text(), "Command failed")
        card.deleteLater()

    def test_failed_zone_save_keeps_the_in_memory_layout(self):
        controller = self.controller()
        original = {"mac": "strip", "model": "H619C", "segment_count_override": 10}
        controller.devices = [original]
        with patch("lumisync.gui.controllers.device_controller.devices.get_data", return_value={}), patch("lumisync.gui.controllers.device_controller.devices.writeJSON", side_effect=OSError("disk error")):
            self.assertFalse(controller.set_zone_count_at(0, 20))
        self.assertIs(controller.devices[0], original)
        self.assertEqual(original["segment_count_override"], 10)

    def test_zone_change_rejects_active_output_and_unsupported_transports(self):
        controller = self.controller()
        controller.devices = [{"mac": "strip", "model": "H619C"}]
        controller.mark_device_output(controller.devices[0], "Monitor sync", active=True)
        with patch("lumisync.gui.controllers.device_controller.devices.writeJSON") as write:
            self.assertFalse(controller.set_zone_count_at(0, 20))
            controller.devices = [{"mac": "panel", "transport": "ble"}]
            self.assertFalse(controller.set_zone_count_at(0, 20))
        write.assert_not_called()

    def test_drawing_cannot_replace_a_running_sync_or_pending_manual_command(self):
        controller = self.controller()
        device = {"mac": "panel", "transport": "ble"}
        controller.devices = [device]
        view = DrawView(controller)
        controller.mark_sync_active("monitor", [device])
        with patch("lumisync.gui.views.draw_view.QThread") as thread:
            view._send([[]])
            self.assertIn("Finish the current output", view.status.text())
            controller.clear_sync_activity()
            controller._command_tasks["panel"] = object()
            view._send([[]])
            thread.assert_not_called()
        controller._command_tasks.clear()
        view.deleteLater()

    def test_sync_guard_allows_mode_switching_but_waits_for_drawing(self):
        controller = self.controller()
        device = {"mac": "panel", "transport": "ble"}
        controller.devices = [device]
        view = SimpleNamespace(device_controller=controller, controller=Mock())
        controller.mark_sync_active("monitor", [device])
        self.assertTrue(ModesView._can_start_sync(view, [device]))
        controller.mark_device_output(device, "Pixel upload", active=True)
        self.assertFalse(ModesView._can_start_sync(view, [device]))
        view.controller.status_updated.emit.assert_called_once()

    def test_account_import_keeps_local_transport_without_modifying_unrelated_lights(self):
        controller = self.controller()
        controller.devices = [{"mac": "AA:BB", "model": "H619C", "ip": "192.0.2.1"}, {"mac": "CC:DD", "model": "H6199", "ip": "192.0.2.2"}]
        cloud = {"mac": "AA:BB", "device_id": "AA:BB", "model": "H619C", "account_id": "account", "transport": "govee_account"}
        with patch.object(controller, "_load_settings_safe", return_value={}), patch("lumisync.gui.controllers.device_controller.devices.writeJSON"):
            controller.import_account_devices({"id": "account", "provider": "govee_account"}, [cloud])
        self.assertEqual(controller.devices[0]["transport"], "lan")
        self.assertEqual(controller.devices[0]["cloud_transport"], "govee_account")
        self.assertNotIn("cloud_transport", controller.devices[1])

    def test_local_scan_attaches_only_authenticated_tuya_lights(self):
        controller = self.controller()
        controller.devices = [{"device_id": "bulb", "mac": "tuya:bulb", "transport": "tuya_cloud", "local_key_ref": "tuya-local/bulb"}]
        with patch.object(controller, "_load_settings_safe", return_value={}), patch("lumisync.gui.controllers.device_controller.devices.writeJSON"):
            controller._on_tuya_scan_finished([{"device_id": "bulb", "ip": "192.0.2.1", "protocol_version": "3.5"}, {"device_id": "unknown", "ip": "192.0.2.2"}], None)
        self.assertEqual(len(controller.devices), 1)
        self.assertEqual(controller.devices[0]["transport"], "tuya")
        self.assertEqual(controller.devices[0]["protocol_version"], "3.5")

    def test_account_refresh_and_local_scan_preserve_all_switch_gangs(self):
        controller = self.controller()
        switches = normalize_devices({"id": "wall", "category": "kg", "name": "Wall"}, {
            "switch_1": {"type": "bool", "dp_id": 1}, "switch_2": {"type": "bool", "dp_id": 2}})
        for device in switches:
            device["local_key_ref"] = "tuya-local/wall"
        with patch.object(controller, "_load_settings_safe", return_value={}), patch("lumisync.gui.controllers.device_controller.devices.writeJSON"):
            controller.import_account_devices({"id": "test", "provider": "tuya_account"}, switches)
            controller._on_tuya_scan_finished([{"device_id": "wall", "ip": "192.0.2.1", "protocol_version": "3.5"}], None)
            controller.import_account_devices({"id": "test", "provider": "tuya_account"}, switches)
        self.assertEqual(len(controller.devices), 2)
        self.assertEqual([d["transport"] for d in controller.devices], ["tuya", "tuya"])
        self.assertEqual([d["dp_map"]["power"] for d in controller.devices], [1, 2])

    def test_accounts_dialog_exposes_personal_and_linked_account_options(self):
        controller = self.controller()
        with patch.object(controller, "get_accounts", return_value=[]):
            dialog = AccountsDialog(controller)
        providers = [dialog.provider.itemData(i) for i in range(dialog.provider.count())]
        self.assertEqual(providers, ["govee_account", "tuya_account", "lsc_account", "govee_api", "tuya_qr"])
        self.assertFalse(hasattr(dialog, "region"))
        self.assertNotIn("access_secret", dialog.entries)
        self.assertTrue(dialog.entries["password"].echoMode() == dialog.entries["password"].EchoMode.Password)
        for provider, brand in (("tuya_account", "Tuya Smart"), ("lsc_account", "LSC Smart Connect")):
            dialog.provider.setCurrentIndex(dialog.provider.findData(provider))
            self.assertFalse(dialog.entries["email"].isHidden())
            self.assertFalse(dialog.entries["password"].isHidden())
            self.assertFalse(dialog.country.isHidden())
            self.assertTrue(dialog.entries["mfa_code"].isHidden())
            self.assertTrue(dialog.options_panel.isHidden())
            self.assertEqual(dialog.labels["email"].text(), "Email")
            self.assertIn(brand, dialog.entries["email"].accessibleName())
        dialog.provider.setCurrentIndex(dialog.provider.findData("tuya_qr"))
        self.assertIn("Home Assistant", dialog.help.text())
        dialog.reject()

    def test_cloud_device_cannot_start_monitor_sync(self):
        controller = SyncController()
        with patch.object(controller, "_ensure_server") as socket:
            controller.start_monitor_sync([{"transport": "tuya_cloud", "tuya_functions": {}}])
        socket.assert_not_called()
        self.assertFalse(controller.is_syncing())

    def test_password_form_uses_selected_vendor_and_clears_login_secrets(self):
        controller = self.controller()
        with patch.object(controller, "get_accounts", return_value=[]):
            dialog = AccountsDialog(controller)
        client = Mock()
        client.credentials = {"profile": {"package": "com.lscsmartconnection.smart"}}
        client.list_devices.return_value = []
        for provider in ("lsc_account", "tuya_account"):
            dialog.provider.setCurrentIndex(dialog.provider.findData(provider))
            dialog.entries["email"].setText("me@example.com")
            dialog.entries["password"].setText(" password with spaces ")
            dialog.country.setCurrentIndex(dialog.country.findData("48"))
            with patch.object(dialog, "_run") as task, patch("lumisync.gui.dialogs.accounts_dialog.make_client", return_value=client) as factory, patch("lumisync.gui.dialogs.accounts_dialog.vault.put") as save:
                dialog._connect()
                self.assertEqual(dialog.entries["password"].text(), "")
                self.assertEqual(dialog.entries["mfa_code"].text(), "")
                value = task.call_args.args[0]()
                factory.assert_called_once_with(provider, {})
                client.login.assert_called_with("me@example.com", " password with spaces ", "48", mfa_code="", region="auto")
                self.assertEqual(value, (client, []))
                self.assertNotIn("password", save.call_args.args[1])
        dialog.reject()

    def test_password_form_requires_country_before_attempting_mobile_login(self):
        controller = self.controller()
        with patch.object(controller, "get_accounts", return_value=[]):
            dialog = AccountsDialog(controller)
        dialog.provider.setCurrentIndex(dialog.provider.findData("lsc_account"))
        dialog.country.setCurrentIndex(0)
        dialog.entries["email"].setText("me@example.com")
        dialog.entries["password"].setText("test-password")
        with patch.object(dialog, "_run") as task:
            dialog._connect()
            task.assert_not_called()
        self.assertIn("Choose your account country", dialog.status.text())
        dialog.reject()

    def test_country_dropdown_disambiguates_shared_codes_and_includes_territories(self):
        controller = self.controller()
        with patch.object(controller, "get_accounts", return_value=[]):
            dialog = AccountsDialog(controller)
        self.assertGreater(dialog.country.count(), 200)
        poland = dialog.country.findData("48")
        self.assertIn("Poland", dialog.country.itemText(poland))
        self.assertIn("United Kingdom", dialog.country.itemText(dialog.country.findData("GB", Qt.ItemDataRole.UserRole + 1)))
        countries_with_one = [dialog.country.itemText(i) for i in range(dialog.country.count()) if dialog.country.itemData(i) == "1"]
        self.assertTrue(any("Canada" in name for name in countries_with_one))
        self.assertTrue(any("United States" in name for name in countries_with_one))
        self.assertGreater(dialog.country.findData("35818"), 0)
        self.assertFalse(hasattr(dialog, "mobile_region"))
        dialog.reject()

    def test_email_verification_is_requested_in_place_and_reuses_the_login_client(self):
        from lumisync.accounts.errors import AccountError
        from lumisync.gui.dialogs.accounts_dialog import SignInFailure
        controller = self.controller()
        with patch.object(controller, "get_accounts", return_value=[]):
            dialog = AccountsDialog(controller)
        client = Mock()
        client.credentials = {"email": "me@example.com"}
        client.login.side_effect = [AccountError("Code sent", "verification_required"), AccountError("Wrong code", "verification"), None]
        client.list_devices.return_value = []
        dialog.entries["email"].setText("me@example.com")
        dialog.entries["password"].setText(" password with spaces ")
        with patch.object(dialog, "_run") as task, patch("lumisync.gui.dialogs.accounts_dialog.GoveeAccountClient", return_value=client) as factory:
            for code in ("", "111111", "123456"):
                dialog.entries["mfa_code"].setText(code)
                dialog._connect()
                self.assertEqual(dialog.entries["password"].text(), "")
                self.assertEqual(dialog.entries["mfa_code"].text(), "")
                result = task.call_args.args[0]()
                if isinstance(result, SignInFailure):
                    dialog._save_connection(result)
                    self.assertFalse(dialog.entries["mfa_code"].isHidden())
                    self.assertTrue(dialog.entries["password"].isHidden())
                    self.assertEqual(dialog._pending_password, " password with spaces ")
                    self.assertFalse(dialog.resend_button.isHidden())
                    client.close.assert_not_called()
            self.assertEqual(result, (client, []))
            self.assertEqual(factory.call_count, 1)
        self.assertEqual(dialog._pending_password, "")
        self.assertEqual([call.kwargs["mfa_code"] for call in client.login.call_args_list], ["", "111111", "123456"])
        dialog.reject()
        client.close()

    def test_changing_provider_or_email_releases_the_pending_verification_client(self):
        from lumisync.accounts.errors import AccountError
        controller = self.controller()
        with patch.object(controller, "get_accounts", return_value=[]):
            dialog = AccountsDialog(controller)
        client = Mock()
        client.login.side_effect = AccountError("Code sent", "verification_required")
        dialog.entries["email"].setText("me@example.com")
        dialog.entries["password"].setText("password")
        with patch.object(dialog, "_run") as task, patch("lumisync.gui.dialogs.accounts_dialog.GoveeAccountClient", return_value=client):
            dialog._connect()
            dialog._save_connection(task.call_args.args[0]())
        dialog.entries["email"].textEdited.emit("different@example.com")
        client.close.assert_called_once()
        self.assertIsNone(dialog._pending_client)
        self.assertEqual(dialog._pending_password, "")
        self.assertTrue(dialog.entries["mfa_code"].isHidden())
        dialog.provider.setCurrentIndex(dialog.provider.findData("lsc_account"))
        self.assertTrue(dialog.resend_button.isHidden())
        dialog.reject()

    def test_verification_timeout_releases_password_and_session(self):
        from lumisync.accounts.errors import AccountError
        controller = self.controller()
        with patch.object(controller, "get_accounts", return_value=[]):
            dialog = AccountsDialog(controller)
        client = Mock()
        client.login.side_effect = AccountError("Code sent", "verification_required")
        dialog.entries["email"].setText("me@example.com")
        dialog.entries["password"].setText("private-password")
        with patch.object(dialog, "_run") as task, patch("lumisync.gui.dialogs.accounts_dialog.GoveeAccountClient", return_value=client):
            dialog._connect()
            result = task.call_args.args[0]()
            self.assertNotIn("private-password", repr(result))
            dialog._save_connection(result)
        self.assertTrue(dialog._verification_timer.isActive())
        dialog._expire_verification()
        client.close.assert_called_once()
        self.assertEqual(dialog._pending_password, "")
        self.assertFalse(dialog._verification_timer.isActive())
        self.assertFalse(dialog.entries["password"].isHidden())
        self.assertTrue(dialog.entries["mfa_code"].isHidden())
        self.assertIn("expired", dialog.status.text())
        dialog.reject()

    def test_app_configuration_error_reveals_connection_options(self):
        from lumisync.accounts.errors import AccountError
        controller = self.controller()
        with patch.object(controller, "get_accounts", return_value=[]):
            dialog = AccountsDialog(controller)
        dialog.provider.setCurrentIndex(dialog.provider.findData("lsc_account"))
        dialog.country.setCurrentIndex(dialog.country.findData("48"))
        dialog.entries["email"].setText("me@example.com")
        dialog.entries["password"].setText("password")
        client = Mock()
        client.load_profile.side_effect = AccountError("Select the LSC app file", "app_profile")
        with patch.object(dialog, "_run") as task, patch("lumisync.gui.dialogs.accounts_dialog.make_client", return_value=client):
            dialog._connect()
            dialog._save_connection(task.call_args.args[0]())
        self.assertTrue(dialog.connection_options.isChecked())
        self.assertFalse(dialog.options_panel.isHidden())
        self.assertTrue(dialog.entries["mfa_code"].isHidden())
        client.close.assert_called_once()
        dialog.reject()

    def test_account_list_masks_addresses_saved_by_older_versions(self):
        controller = self.controller()
        with patch.object(controller, "get_accounts", return_value=[{"id": "test", "provider": "tuya_account", "label": "Tuya Smart · minlor@example.com"}]):
            dialog = AccountsDialog(controller)
        self.assertEqual(dialog.accounts_list.item(0).text(), "Tuya Smart · m•••@•••.com")
        dialog.reject()

    def test_successful_connection_saves_a_masked_label_and_clears_the_email_input(self):
        controller = self.controller()
        with patch.object(controller, "get_accounts", return_value=[]), patch.object(controller, "import_account_devices") as imported:
            dialog = AccountsDialog(controller)
            dialog.entries["email"].setText("minlor@example.com")
            dialog._provider_pending, dialog._remember_pending = "govee_account", False
            client = SimpleNamespace(credentials={"email": "minlor@example.com"}, close=Mock())
            with patch("lumisync.gui.dialogs.accounts_dialog.account_manager") as manager:
                manager.register.return_value = {"id": "test", "remember": False}
                manager.descriptors.return_value = []
                dialog._save_connection((client, []))
            metadata = imported.call_args.args[0]
            self.assertEqual(metadata["label"], "Govee Home · m•••@•••.com")
            self.assertEqual(dialog.entries["email"].text(), "")
            dialog.reject()

    def test_light_switch_ui_exposes_power_without_colour_brightness_or_sync(self):
        device = normalize_devices({"id": "wall", "name": "Wall", "category": "kg"}, {"switch_1": {"type": "bool", "dp_id": 1}})[0]
        device.update(account_id="test", local_transport="tuya", cloud_transport="tuya_cloud", ip="192.0.2.1")
        card = DeviceCard(0, device)
        inspector = DeviceInspector()
        inspector.set_device(0, device, {"power_on": False, "online": True, "status_source": "confirmed"}, primary=False)
        self.assertTrue(card.power_button.isEnabled())
        self.assertFalse(card.brightness_slider.isEnabled())
        self.assertTrue(inspector.power_button.isEnabled())
        self.assertFalse(inspector.brightness_slider.isEnabled())
        self.assertFalse(inspector.color_button.isEnabled())
        self.assertTrue(card.brightness_controls.isHidden())
        self.assertTrue(inspector.controls_panel.isHidden())
        self.assertTrue(inspector.brightness_controls.isHidden())
        self.assertTrue(inspector.color_controls.isHidden())
        self.assertTrue(inspector.default_button.isHidden())
        self.assertTrue(inspector.energy.isHidden())
        self.assertEqual(inspector.brightness_value.text(), "Not reported")
        self.assertEqual(inspector.zones_value.text(), "Light switch · power control")
        self.assertIn("manual controls", inspector.connection_combo.itemText(0))
        card.deleteLater()
        inspector.deleteLater()

    def test_background_task_releases_login_closure(self):
        action = Mock(return_value="connected")
        task = BackgroundTask(action)
        task.run()
        self.assertIsNone(task.action)
        action.assert_called_once()

    def test_toggle_power_remembers_the_queued_target(self):
        controller = self.controller()
        controller.devices = [{"mac": "panel", "transport": "ble"}]
        with patch.object(controller, "_start_control", side_effect=lambda key, request: controller._command_tasks.update({key: request})):
            controller.toggle_power_at(0)
            controller.toggle_power_at(0)
        self.assertTrue(controller._command_tasks["panel"][2][0])
        self.assertFalse(controller._command_queue["panel"][0][2][0])

    def test_stop_timeout_retains_the_running_sync_thread(self):
        controller = SyncController()
        controller.sync_thread = Mock()
        controller.sync_thread.isRunning.return_value = True
        thread = controller.sync_thread
        controller.current_sync_mode = "monitor"
        controller.stop_sync()
        self.assertIs(controller.sync_thread, thread)
        controller.sync_thread = None  # Avoid destructor interactions with fake state.

    def test_headless_monitor_exits_and_closes_when_stop_is_requested(self):
        from lumisync.sync import monitor
        adapter = Mock()
        adapter.capabilities.segment_count = 1
        adapter.capabilities.max_update_hz = 5
        adapter.capabilities.supports_streaming = True
        event = threading.Event()
        event.set()
        with patch.object(monitor, "create_adapter", return_value=adapter), patch.object(monitor, "ScreenGrab"):
            monitor.start(object(), {}, event)
        adapter.set_segments.assert_not_called()
        adapter.end_stream.assert_called_once()
        adapter.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
