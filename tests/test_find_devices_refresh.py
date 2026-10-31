"""Combined discovery refreshes every linked account without hardware writes."""

import os
import threading
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.accounts.tuya import normalize_devices
from lumisync.gui.views.devices_view import DevicesView


class FindDevicesRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def controller(self):
        with patch("lumisync.gui.controllers.device_controller.devices.get_data", return_value={"devices": [], "selectedDevice": 0}):
            return DeviceController()

    def start_search(self, controller, accounts):
        tasks = []

        def start(action, completed):
            tasks.append((action, completed))
            return Mock()

        with patch.object(controller, "get_accounts", return_value=accounts), \
                patch.object(controller, "_local_network_available", return_value=False), \
                patch.object(controller, "_start_ble_scan", return_value=False), \
                patch("lumisync.gui.controllers.device_controller.start_task", side_effect=start):
            controller.find_devices()
            controller.find_devices()  # Repeated presses cannot duplicate the requests.
        return tasks

    def test_find_devices_reads_every_account_and_finishes_with_a_status_refresh(self):
        controller = self.controller()
        accounts = [{"id": provider, "provider": provider, "remember": True}
                    for provider in ("govee_account", "tuya_account", "lsc_account")]
        tasks = self.start_search(controller, accounts + accounts[:1])
        self.assertEqual(len(tasks), 3)
        finished = []
        controller.device_search_finished.connect(finished.append)
        clients = {account["id"]: Mock() for account in accounts}
        for identity, client in clients.items():
            client.list_devices.return_value = [{"mac": "fixture-" + identity}]
        with patch("lumisync.accounts.manager.account_manager") as manager, \
                patch.object(controller, "get_accounts", return_value=accounts), \
                patch.object(controller, "import_account_devices") as imported, \
                patch.object(controller, "refresh_device_states") as refreshed:
            manager.client_for.side_effect = lambda device: clients[device["account_id"]]
            manager.descriptors.side_effect = lambda metadata, devices: devices
            for index, (action, completed) in enumerate(tasks):
                completed(action(), None)
                self.assertEqual(len(finished), int(index == 2))
            self.assertEqual(imported.call_count, 3)
            refreshed.assert_called_once_with()
        self.assertEqual(finished[0]["accounts"]["refreshed"], 3)
        self.assertEqual(finished[0]["accounts"]["found"], 3)
        for client in clients.values():
            client.list_devices.assert_called_once_with()
            client.send_commands.assert_not_called()
        self.assertFalse(controller._account_refresh_tasks)
        self.assertFalse(controller._search_pending)

    def test_one_failed_account_does_not_drop_successes_or_reimport_a_disconnected_account(self):
        controller = self.controller()
        accounts = [{"id": "good", "provider": "tuya_account", "country_iso": "PL"},
                    {"id": "failed", "provider": "lsc_account"},
                    {"id": "removed", "provider": "govee_account"}]
        tasks = self.start_search(controller, accounts)
        current = [{**accounts[0], "country_iso": "CA"}, accounts[1]]
        finished = []
        controller.device_search_finished.connect(finished.append)
        with patch.object(controller, "get_accounts", return_value=current), \
                patch.object(controller, "import_account_devices") as imported, \
                patch.object(controller, "refresh_device_states"):
            tasks[0][1]([{"mac": "fixture-good"}], None)
            tasks[1][1](None, "Sign in again to refresh this account.")
            tasks[2][1]([{"mac": "fixture-removed"}], None)
        imported.assert_called_once_with(current[0], [{"mac": "fixture-good"}])
        summary = finished[0]["accounts"]
        self.assertEqual(summary["refreshed"], 1)
        self.assertEqual(summary["found"], 1)
        self.assertTrue(summary["available"])
        self.assertEqual(summary["errors"], ["LSC: Sign in again to refresh this account."])

    def test_real_background_account_results_are_applied_on_the_gui_thread(self):
        controller = self.controller()
        metadata = {"id": "fixture", "provider": "lsc_account", "remember": False}
        threads = []
        client = Mock()
        client.list_devices.return_value = []
        with patch.object(controller, "get_accounts", return_value=[metadata]), \
                patch.object(controller, "_local_network_available", return_value=False), \
                patch.object(controller, "_start_ble_scan", return_value=False), \
                patch.object(controller, "import_account_devices", side_effect=lambda *_args: threads.append(QThread.currentThread())), \
                patch.object(controller, "refresh_device_states"), \
                patch("lumisync.accounts.manager.account_manager") as manager:
            manager.client_for.return_value = client
            manager.descriptors.return_value = []
            controller.find_devices()
            for _ in range(100):
                if not controller._combined_search_active:
                    break
                QTest.qWait(10)
            self.assertFalse(controller._combined_search_active)
        self.assertEqual(threads, [self.app.thread()])

    def test_find_devices_without_accounts_still_finishes_local_refresh(self):
        controller = self.controller()
        finished = []
        controller.device_search_finished.connect(finished.append)
        with patch.object(controller, "refresh_device_states") as refreshed:
            self.assertEqual(self.start_search(controller, []), [])
        self.assertGreaterEqual(len(finished), 1)
        self.assertEqual(finished[0]["accounts"]["total"], 0)
        self.assertTrue(refreshed.called)

    def test_refresh_all_waits_for_an_inflight_query_and_includes_new_imports(self):
        controller = self.controller()
        with patch.object(controller, "_status_running", return_value=True):
            controller.refresh_device_states()
        self.assertTrue(controller._refresh_all_pending)
        controller.devices = [{"mac": "fixture-old"}, {"mac": "fixture-new"}]
        with patch("lumisync.gui.controllers.device_controller.QTimer.singleShot") as later:
            controller._clear_status_refs()
        self.assertFalse(controller._refresh_all_pending)
        with patch.object(controller, "_start_status_refresh") as started:
            later.call_args.args[1]()
        started.assert_called_once_with([0, 1])
        controller._closing = True
        controller._refresh_all_pending = True
        with patch("lumisync.gui.controllers.device_controller.QTimer.singleShot") as later:
            controller._clear_status_refs()
        later.assert_not_called()

    def test_account_devices_imported_after_the_lan_scan_still_receive_local_connections(self):
        controller = self.controller()
        metadata = {"id": "fixture", "provider": "tuya_account", "remember": True}
        switch = normalize_devices({"id": "fixture-wall", "category": "kg"}, {"switch_1": {"type": "bool", "dp_id": 1}})[0]
        switch["local_key_ref"] = "fixture-key-reference"
        controller._combined_search_active = True
        controller._search_pending = {"tuya", "account:fixture"}
        controller._search_results = {"accounts": {"available": False, "total": 1, "refreshed": 0, "found": 0, "errors": []}}
        with patch.object(controller, "get_accounts", return_value=[metadata]), \
                patch.object(controller, "_load_settings_safe", return_value={"devices": [], "accounts": [metadata]}), \
                patch("lumisync.gui.controllers.device_controller.devices.writeJSON"), \
                patch.object(controller, "refresh_device_states"):
            controller._on_tuya_scan_finished([{"device_id": "fixture-wall", "ip": "192.0.2.1", "protocol_version": "3.5"}], None)
            self.assertEqual(controller.devices, [])
            controller._on_account_refresh_finished(metadata, [switch], None)
        self.assertEqual(len(controller.devices), 1)
        self.assertEqual(controller.devices[0]["ip"], "192.0.2.1")
        self.assertEqual(controller.devices[0]["transport"], "tuya")
        self.assertEqual(controller.devices[0]["cloud_transport"], "tuya_cloud")
        self.assertFalse(controller._combined_search_active)

    def _strip(self, ip="192.168.0.44"):
        return {"transport": "tuya", "device_id": "strip-1", "ip": ip,
                "name": "Battletron", "local_key_ref": "fixture-key-reference"}

    def test_presence_beat_from_a_new_address_saves_the_moved_light(self):
        controller = self.controller()
        controller.devices = [self._strip()]
        statuses = []
        controller.status_updated.connect(statuses.append)
        with patch.object(controller, "_load_settings_safe", return_value={"devices": []}),                 patch("lumisync.gui.controllers.device_controller.devices.writeJSON") as write,                 patch("lumisync.gui.controllers.device_controller.pool.close") as closed:
            controller._on_tuya_device_seen("strip-1", "192.168.0.19")
            controller._on_tuya_device_seen("strip-1", "192.168.0.19")  # later beats change nothing
            controller._on_tuya_device_seen("someone-else", "192.168.0.50")
        self.assertEqual(controller.devices[0]["ip"], "192.168.0.19")
        write.assert_called_once()
        self.assertEqual(write.call_args.args[0]["devices"][0]["ip"], "192.168.0.19")
        closed.assert_called_once()
        self.assertEqual(statuses, ["Battletron: now at 192.168.0.19"])

    def test_presence_beat_waits_while_the_light_is_streaming(self):
        controller = self.controller()
        controller.devices = [self._strip()]
        with patch.object(controller, "is_device_busy", return_value=True),                 patch("lumisync.gui.controllers.device_controller.devices.writeJSON") as write:
            controller._on_tuya_device_seen("strip-1", "192.168.0.19")
        write.assert_not_called()
        self.assertEqual(controller.devices[0]["ip"], "192.168.0.44")

    def test_presence_beats_reach_the_controller_from_the_listener_thread(self):
        from lumisync.drivers import tuya_presence

        controller = self.controller()
        with patch.object(controller, "_on_tuya_device_seen") as seen:
            # Queued connections re-bind the slot at emit time; reconnect it.
            controller.tuya_device_seen.disconnect()
            controller.tuya_device_seen.connect(seen)
            thread = threading.Thread(target=tuya_presence._notify, args=("strip-1", "192.168.0.19"))
            thread.start()
            thread.join()
            QTest.qWait(50)
        seen.assert_called_once_with("strip-1", "192.168.0.19")
        controller.shutdown()
        self.assertNotIn(controller._presence_callback, tuya_presence._subscribers)

    def test_tuya_lan_scan_frees_the_presence_port_and_shares_what_it_found(self):
        from lumisync.drivers import tuya_presence

        events = []

        def scan(**kwargs):
            events.append("scan")
            return {"192.168.0.19": {"gwId": "strip-1", "ip": "192.168.0.19", "version": "3.5"}}

        registry = tuya_presence.PresenceRegistry()
        released = Mock()
        released.return_value.__enter__ = lambda *_: events.append("released")
        released.return_value.__exit__ = lambda *_: events.append("listening")
        with patch("tinytuya.deviceScan", side_effect=scan),                 patch.object(tuya_presence, "released_port", released),                 patch.object(tuya_presence, "registry", registry):
            found = DeviceController._scan_tuya_lan()
        self.assertEqual(events, ["released", "scan", "listening"])
        self.assertEqual(found, [{"device_id": "strip-1", "ip": "192.168.0.19", "protocol_version": "3.5"}])
        self.assertEqual(registry.address("strip-1"), "192.168.0.19")

    def test_compact_devices_view_opens_details_without_squeezing_the_card_grid(self):
        controller = self.controller()
        controller.devices = [{"mac": "fixture-switch", "name": "Hall switch", "device_kind": "light_switch",
                               "transport": "tuya_cloud", "tuya_functions": {"switch_1": {"type": "bool"}}}]
        with patch.object(controller, "get_groups", return_value=[]), \
                patch.object(controller, "refresh_device_states"), \
                patch.object(controller, "refresh_device_state_at"):
            view = DevicesView(controller)
            view.resize(760, 680)
            view.show()
            self.app.processEvents()
            view._open_inspector(0)
            self.assertTrue(view.main_column.isHidden())
            self.assertTrue(view.inspector_scroll.isVisible())
            self.assertGreaterEqual(view.inspector.width(), 414)
            view.inspector.details_toggle.click()
            self.assertFalse(view.inspector.details_panel.isHidden())
            view.inspector.close_button.click()
            self.assertFalse(view.main_column.isHidden())
            self.assertTrue(view.inspector_scroll.isHidden())
            view.resize(1280, 900)
            self.app.processEvents()
            view._open_inspector(0)
            QTest.qWait(240)
            self.assertFalse(view.main_column.isHidden())
            self.assertTrue(view.inspector_scroll.isVisible())
            view._on_search_finished({"accounts": {"total": 3, "refreshed": 2, "found": 4, "errors": ["LSC: Sign in again."]}})
            self.assertIn("2 of 3", view.accounts_status.text())
            self.assertIn("Sign in again", view.accounts_status.toolTip())
            view._stop_inspector_animation()
            view.close()
            view.deleteLater()

    def test_refresh_reordering_preserves_the_open_device_and_group_choices(self):
        controller = self.controller()
        first = {"model": "Same model", "device_id": "first", "transport": "lan", "ip": "192.0.2.1"}
        second = {"model": "Same model", "device_id": "second", "transport": "lan", "ip": "192.0.2.2"}
        controller.devices = [first, second]
        with patch.object(controller, "get_groups", return_value=[]), \
                patch.object(controller, "refresh_device_states"), \
                patch.object(controller, "refresh_device_state_at"):
            view = DevicesView(controller)
            view.resize(760, 680)
            view.show()
            self.app.processEvents()
            view._open_inspector(1)
            view._selected = {1}
            controller.devices = [second, first]
            view._rebuild_cards()
            self.assertEqual(view._inspected_index, 0)
            self.assertEqual(view._selected, {0})
            self.assertEqual(view.inspector._device["device_id"], "second")
            controller.devices = [first]
            view._rebuild_cards()
            self.assertEqual(view._inspected_index, -1)
            self.assertFalse(view._selected)
            self.assertTrue(view.main_column.isVisible())
            view.close()
            view.deleteLater()
