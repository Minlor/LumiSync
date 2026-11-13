import os
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QWidget

from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.main_window import LumiSyncMainWindow
from lumisync.gui.theme import qcolor
from lumisync.gui.widgets.device_card import DeviceCard, describe_device_status, device_subline
from lumisync.gui.widgets.device_inspector import DeviceInspector
from lumisync.gui.widgets.navigation_shell import NavigationShell, NavRailItemDelegate, RAIL_ITEM_HEIGHT


class DeviceUiStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._existing_widgets = set(self.app.topLevelWidgets())
        self._controllers = []

    def tearDown(self):
        for controller in self._controllers:
            controller.shutdown()
            controller.deleteLater()
        for widget in set(self.app.topLevelWidgets()) - self._existing_widgets:
            widget.close()
            widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def _controller(self):
        with patch(
            "lumisync.gui.controllers.device_controller.devices.get_data",
            return_value={"devices": [], "selectedDevice": 0},
        ):
            controller = DeviceController()
            self._controllers.append(controller)
            return controller

    def test_bluetooth_default_state_is_unknown_not_offline(self):
        controller = self._controller()
        state = controller._default_state(
            {"transport": "ble", "mac": "AA:BB", "model": "IDM"}
        )

        self.assertEqual(state["status_source"], "unknown")
        self.assertFalse(state["readback_supported"])
        self.assertFalse(state["stale"])

    def test_lan_status_failure_before_any_confirmation_is_unknown(self):
        # Govee LAN commands are fire-and-forget; some models (H7020, issue
        # #49) never answer status queries. Until one reply is confirmed, a
        # failed poll must not paint the device as Offline.
        controller = self._controller()
        controller.devices = [
            {"mac": "CC:DD", "ip": "192.168.1.4", "model": "H7020"}
        ]

        controller._merge_device_state(
            0,
            {
                "online": False,
                "stale": True,
                "status_source": "offline",
                "readback_supported": True,
                "last_error": "No status reply",
            },
        )

        state = controller.get_device_state_at(0)
        self.assertEqual(state["status_source"], "unknown")
        self.assertTrue(state["stale"])
        self.assertFalse(state["readback_seen"])

    def test_lan_status_misses_are_tolerated_before_showing_offline(self):
        # LSC strips answer status polls with change-only echoes while they
        # stream paint; a single empty poll must not flash "Offline".
        controller = self._controller()
        controller.devices = [
            {"mac": "CC:DD", "ip": "192.168.1.4", "model": "H619C"}
        ]

        controller._merge_device_state(
            0,
            {
                "online": True,
                "stale": False,
                "status_source": "confirmed",
                "power_on": True,
                "readback_supported": True,
                "last_error": None,
            },
        )
        for _ in range(controller.STATUS_MISS_TOLERANCE - 1):
            controller._merge_device_state(
                0,
                {
                    "online": False,
                    "stale": True,
                    "status_source": "offline",
                    "readback_supported": True,
                    "last_error": "No status reply",
                },
            )
            state = controller.get_device_state_at(0)
            self.assertTrue(state["readback_seen"])
            self.assertEqual(state["status_source"], "pending")
            self.assertTrue(state["online"])

        controller._merge_device_state(
            0,
            {
                "online": False,
                "stale": True,
                "status_source": "offline",
                "readback_supported": True,
                "last_error": "No status reply",
            },
        )
        state = controller.get_device_state_at(0)
        self.assertTrue(state["readback_seen"])
        self.assertEqual(state["status_source"], "offline")
        self.assertFalse(state["online"])

    def test_confirmation_resets_status_misses(self):
        controller = self._controller()
        controller.devices = [
            {"mac": "CC:DD", "ip": "192.168.1.4", "model": "H619C"}
        ]
        with patch.object(controller, "_start_status_refresh"):
            controller._merge_device_state(
                0,
                {"online": True, "status_source": "confirmed",
                 "power_on": True, "readback_supported": True},
            )
            for _ in range(controller.STATUS_MISS_TOLERANCE):
                controller._merge_device_state(
                    0,
                    {"online": False, "status_source": "offline",
                     "last_error": "No status reply", "stale": True},
                )
            controller._merge_device_state(
                0,
                {"online": True, "status_source": "confirmed",
                 "power_on": True, "readback_supported": True},
            )
            for _ in range(controller.STATUS_MISS_TOLERANCE - 1):
                controller._merge_device_state(
                    0,
                    {"online": False, "status_source": "offline",
                     "last_error": "No status reply", "stale": True},
                )
        state = controller.get_device_state_at(0)
        self.assertEqual(state["status_source"], "pending")
        self.assertTrue(state["online"])

    def test_local_status_poll_skips_account_and_bluetooth_devices(self):
        controller = self._controller()
        controller.devices = [
            {"transport": "lan", "ip": "192.168.1.4", "mac": "lan"},
            {"transport": "tuya", "device_id": "dev", "mac": "tuya"},
            {"transport": "tuya_cloud", "account_id": "acct", "mac": "cloud"},
            {"transport": "ble", "mac": "ble"},
        ]
        with patch.object(controller, "_start_status_refresh") as start:
            controller.refresh_local_device_states()
        start.assert_called_once_with([0, 1])

    def test_account_status_poll_reads_account_devices_and_only_failing_linked_strips(self):
        controller = self._controller()
        controller.devices = [
            {"transport": "lan", "ip": "192.168.1.4", "mac": "lan"},
            {
                "transport": "tuya",
                "device_id": "battletron",
                "account_id": "acct",
                "cloud_transport": "tuya_cloud",
                "mac": "tuya",
            },
            {
                "transport": "tuya_cloud",
                "device_id": "cloud",
                "account_id": "acct",
                "cloud_transport": "tuya_cloud",
                "mac": "cloud",
            },
            {
                "transport": "ble",
                "account_id": "acct",
                "cloud_transport": "tuya_cloud",
                "mac": "ble",
            },
            {"transport": "tuya", "device_id": "local-only", "mac": "local-only"},
            {
                "transport": "lan",
                "ip": "192.168.1.9",
                "account_id": "acct",
                "cloud_transport": "govee_cloud",
                "mac": "govee-lan",
            },
            # Imported account-only device that never recorded cloud_transport.
            {"transport": "tuya_cloud", "device_id": "imported", "account_id": "acct", "mac": "imported"},
        ]
        with patch.object(controller, "_start_status_refresh") as start:
            controller.refresh_account_device_states()

        start.assert_called_once()
        # The linked strip's LAN works, so the 15 s local poll owns it.
        self.assertEqual(start.call_args.args[0], [2, 6])
        self.assertTrue(start.call_args.kwargs["prefer_cloud"])

        # While its LAN path is failing it is read through the account too.
        controller._cloud_preferred_until[controller._device_key(controller.devices[1])] = time.time() + 60
        with patch.object(controller, "_start_status_refresh") as start:
            controller.refresh_account_device_states()
        self.assertEqual(start.call_args.args[0], [1, 2, 6])

    def _linked_strip(self, controller):
        device = {
            "transport": "tuya",
            "device_id": "battletron",
            "mac": "tuya:battletron",
            "account_id": "acct",
            "cloud_transport": "tuya_cloud",
        }
        controller.devices = [device]
        return device

    def test_account_first_reads_never_mark_a_working_strip_as_cloud_only(self):
        controller = self._controller()
        device = self._linked_strip(controller)
        cloud = {"online": True, "status_source": "confirmed", "status_origin": "cloud"}

        # The 60 s account poll asks the account first and never tries the
        # LAN; repeating it must not lock the strip onto the cloud.
        for _ in range(3):
            controller._merge_device_state(0, dict(cloud))
        self.assertFalse(controller._cloud_preferred(device))
        self.assertFalse(controller.is_cloud_preferred(device))

    def test_cloud_status_after_a_lan_failure_marks_cloud_preferred_and_local_status_clears_it(self):
        controller = self._controller()
        device = self._linked_strip(controller)
        key = controller._device_key(device)

        # Inline fallback: the worker read the account because the LAN failed.
        controller._merge_device_state(
            0,
            {"online": True, "status_source": "confirmed", "status_origin": "cloud", "lan_failed": True},
        )
        self.assertTrue(controller._cloud_preferred(device))
        self.assertNotIn("lan_failed", controller.device_states[key])

        controller._merge_device_state(
            0,
            {"online": True, "status_source": "confirmed", "status_origin": "local"},
        )
        self.assertFalse(controller._cloud_preferred(device))

        # Deferred fallback: the local pass handed the strip to the account pass.
        with patch.object(controller, "_start_account_pass"):
            controller._on_account_status_needed([key])
        controller._merge_device_state(
            0,
            {"online": True, "status_source": "confirmed", "status_origin": "cloud"},
        )
        self.assertTrue(controller._cloud_preferred(device))
        self.assertNotIn(key, controller._lan_failed_keys)

        # A cloud read that also fails marks nothing and forgets the failure.
        controller._cloud_preferred_until.clear()
        with patch.object(controller, "_start_account_pass"):
            controller._on_account_status_needed([key])
        controller._merge_device_state(
            0,
            {"online": False, "status_source": "offline", "status_origin": "cloud"},
        )
        self.assertFalse(controller._cloud_preferred(device))
        self.assertNotIn(key, controller._lan_failed_keys)

    def test_cloud_confirmed_state_is_labelled_as_account(self):
        state = {
            "online": True,
            "stale": False,
            "status_source": "confirmed",
            "status_origin": "cloud",
            "power_on": True,
            "readback_supported": True,
        }
        card = DeviceCard(0, {"model": "Battletron", "transport": "tuya"})
        card.set_state(state)

        self.assertEqual(card.status_label.text(), "On")
        self.assertIn("vendor account", card.status_label.toolTip())
        reachable = dict(state, power_on=None)
        self.assertEqual(describe_device_status(reachable).text, "Online (account)")

    def test_card_shows_no_status_yet_and_a_grey_dot_for_unanswered_lan_status(self):
        card = DeviceCard(0, {"model": "H7020", "ip": "192.168.1.4"})

        card.set_state(
            {
                "online": False,
                "stale": True,
                "status_source": "unknown",
                "readback_supported": True,
                "last_error": "No status reply",
            }
        )

        self.assertEqual(card.status_label.text(), "No status yet")
        self.assertIn("commands may still work", card.status_label.toolTip())
        self.assertEqual(card.status_label.property("statusState"), "unknown")
        self.assertNotEqual(card.status_dot._color, qcolor("success"))
        self.assertFalse(card.status_dot._active)

    def test_inspector_uses_the_same_status_line_as_the_card(self):
        state = {
            "online": False,
            "stale": True,
            "status_source": "unknown",
            "readback_supported": True,
            "last_error": "No status reply",
        }
        inspector = DeviceInspector()
        inspector.set_device(0, {"model": "H7020", "ip": "192.168.1.4"}, state, primary=False)

        self.assertEqual(inspector.status_label.text(), "No status yet")
        self.assertFalse(hasattr(inspector, "power_label"))
        self.assertFalse(hasattr(inspector, "output_label"))
        self.assertEqual(inspector.brightness_value.text(), "—")

    def test_merged_status_phrase_covers_power_output_readings_and_reachability(self):
        lamp = {"online": True, "status_source": "confirmed", "power_on": True}
        cases = [
            (dict(lamp, brightness=80), "On · 80%", "online"),
            (dict(lamp, brightness=80, color=(255, 136, 0)), "On · 80%", "online"),
            (dict(lamp, power_on=False, brightness=80), "Off", "online"),
            (dict(lamp, power_w=12.3), "On", "online"),
            ({"status_source": "offline", "online": False}, "Offline", "offline"),
            (dict(lamp, active_output="Monitor sync"), "Syncing · Monitor", "online"),
            ({"status_source": "error", "last_error": "Rejected"}, "Command failed", "warning"),
            ({"status_source": "pending", "stale": True}, "Checking…", "unknown"),
        ]
        for state, text, tone in cases:
            with self.subTest(text=text):
                status = describe_device_status(state)
                self.assertEqual(status.text, text)
                self.assertEqual(status.tone, tone)
        plug = describe_device_status(dict(lamp, power_w=12.3), meters=("power_w",))
        self.assertEqual(plug.text, "On · 12.3 W")
        self.assertEqual(describe_device_status({"status_source": "error", "last_error": "Rejected"}).tooltip,
                         "Rejected")

    def test_bluetooth_command_is_last_sent_without_fake_readback(self):
        controller = self._controller()
        controller.devices = [
            {"transport": "ble", "mac": "AA:BB", "model": "IDM"}
        ]

        controller._record_command_state(
            0, {"color": (10, 20, 30)}, output="#0A141E"
        )
        state = controller.get_device_state_at(0)

        self.assertEqual(state["status_source"], "commanded")
        self.assertFalse(state["readback_supported"])
        self.assertFalse(state["stale"])
        status = describe_device_status(state)
        self.assertEqual(status.text, "Colour sent")
        self.assertIn("can't report its state", status.tooltip)

    def test_bluetooth_search_marks_unseen_saved_panel_without_guessing_offline(self):
        controller = self._controller()
        controller.devices = [
            {"transport": "ble", "mac": "AA:BB", "model": "IDM"}
        ]
        controller._merge_device_state(
            0, {"online": True, "stale": False, "status_source": "seen"}
        )

        controller._on_ble_scan_finished([])
        state = controller.get_device_state_at(0)

        self.assertFalse(state["online"])
        self.assertEqual(state["status_source"], "not_seen")

    def test_confirmed_and_active_output_copy_are_distinct(self):
        confirmed = {
            "online": True,
            "status_source": "confirmed",
            "power_on": True,
            "brightness": 42,
            "color": (12, 34, 56),
        }
        active = dict(confirmed, active_output="Music sync")

        self.assertEqual(describe_device_status(confirmed).text, "On · 42%")
        self.assertEqual(describe_device_status(active).text, "Syncing · Music")

    def test_concurrent_lan_result_preserves_bluetooth_device(self):
        controller = self._controller()
        bluetooth = {"transport": "ble", "mac": "AA:BB", "model": "IDM"}
        lan = {"mac": "CC:DD", "ip": "192.168.1.4", "model": "H619C"}
        controller.devices = [bluetooth]
        controller.selected_device_index = 0
        controller._combined_search_active = True
        controller._search_pending = {"lan", "bluetooth"}
        controller._search_results = {
            "lan": {"available": True, "found": 0, "error": None},
            "bluetooth": {"available": None, "found": 0, "error": None},
        }

        with patch("lumisync.gui.controllers.device_controller.devices.writeJSON"):
            controller._on_discovery_finished([lan], 0, 1)

        self.assertEqual(
            {controller._device_key(device) for device in controller.devices},
            {"AA:BB", "CC:DD"},
        )
        self.assertEqual(controller.get_selected_device()["mac"], "AA:BB")

    def test_combined_search_emits_partial_availability_summary_once(self):
        controller = self._controller()
        summaries = []
        controller.device_search_finished.connect(summaries.append)
        controller._combined_search_active = True
        controller._search_pending = {"bluetooth"}
        controller._search_results = {
            "lan": {
                "available": False,
                "found": 0,
                "error": "No active local network connection",
            },
            "bluetooth": {
                "available": True,
                "found": 0,
                "seen": 0,
                "error": None,
            },
        }

        with patch.object(controller, "refresh_device_states"):
            controller._finish_search_transport("bluetooth")

        self.assertEqual(len(summaries), 1)
        self.assertFalse(summaries[0]["lan"]["available"])
        self.assertTrue(summaries[0]["bluetooth"]["available"])

    def test_card_power_button_matches_reported_state(self):
        card = DeviceCard(
            0,
            {"model": "H619C", "ip": "192.168.0.28", "transport": "lan"},
        )

        self.assertEqual(card.power_button.property("powerState"), "unknown")
        self.assertIn("unknown", card.power_button.toolTip().lower())
        self.assertEqual(card.power_button.accessibleDescription(), "Unknown")

        card.set_state(
            {
                "online": True,
                "status_source": "confirmed",
                "power_on": True,
                "brightness": 64,
                "color": (12, 80, 220),
            }
        )
        self.assertEqual(card.power_button.property("powerState"), "on")
        self.assertEqual(card.power_button.toolTip(), "Turn off")
        self.assertEqual(card.brightness_summary.text(), "64%")
        self.assertEqual(card.power_button.accessibleDescription(), "On")

        card.set_state(
            {
                "online": True,
                "status_source": "confirmed",
                "power_on": False,
            }
        )
        self.assertEqual(card.power_button.property("powerState"), "off")
        self.assertEqual(card.power_button.toolTip(), "Turn on")
        self.assertEqual(card.power_button.accessibleDescription(), "Off")

    def test_card_subline_names_type_connection_and_one_detail(self):
        card = DeviceCard(
            0,
            {
                "model": "H619C",
                "ip": "192.168.0.28",
                "transport": "lan",
                "segment_count_override": 10,
            },
        )

        self.assertEqual(card.device_summary.text(), "LED strip · Local network · 10 zones")
        self.assertNotIn("192.168.0.28", card.device_summary.text())
        self.assertFalse(hasattr(card, "checkbox"))
        self.assertFalse(hasattr(card, "primary_label"))
        self.assertEqual(
            device_subline({"transport": "ble", "mac": "AA", "matrix_size": "32x32"}),
            "Matrix panel · Bluetooth · 32×32",
        )
        self.assertEqual(
            device_subline({"transport": "tuya_cloud", "device_kind": "smart_plug", "device_id": "p"}),
            "Smart plug · Account",
        )
        self.assertEqual(
            device_subline({"transport": "tuya", "device_kind": "smart_plug", "device_id": "p"}),
            "Smart plug · Local Wi-Fi",
        )

    def test_card_brightness_is_a_direct_debounced_control(self):
        card = DeviceCard(3, {"model": "H619C", "transport": "lan"})
        changes = []
        card.brightness_changed.connect(
            lambda index, value: changes.append((index, value))
        )

        card.brightness_slider.setValue(37)
        self.assertEqual(card.brightness_summary.text(), "37%")
        card._brightness_timer.stop()
        card._commit_brightness()

        self.assertEqual(changes, [(3, 37)])

    def test_unknown_brightness_shows_a_dash_and_power_only_cards_hide_the_slider(self):
        light = DeviceCard(0, {"model": "H619C", "transport": "lan"})
        light.set_state({"online": True, "status_source": "confirmed", "power_on": True})
        self.assertEqual(light.brightness_summary.text(), "—")
        self.assertFalse(light.brightness_controls.isHidden())
        plug = DeviceCard(1, {"transport": "tuya", "device_kind": "smart_plug", "device_id": "p",
                              "tuya_functions": {"switch_1": {"type": "bool"}}})
        self.assertTrue(plug.brightness_controls.isHidden())

    def test_inspector_exposes_details_and_capability_controls(self):
        inspector = DeviceInspector()
        inspector.set_device(
            1,
            {"model": "H619C", "ip": "192.168.0.28", "transport": "lan"},
            {
                "online": True,
                "status_source": "confirmed",
                "power_on": True,
                "brightness": 47,
                "color": (120, 20, 60),
                "color_temp": 3300,
            },
            primary=False,
        )

        self.assertEqual(inspector.title_label.text(), "H619C")
        self.assertEqual(inspector.connection_value.text(), "Local network")
        self.assertEqual(inspector.address_value.text(), "192.168.0.28")
        self.assertEqual(inspector.port_value.text(), "4003")
        self.assertEqual(inspector.power_button.property("powerState"), "on")
        self.assertEqual(inspector.readback_value.text(), "Live")
        self.assertEqual(inspector.brightness_value.text(), "47%")
        self.assertEqual(inspector.temperature_value.text(), "3300K")
        self.assertTrue(inspector.temperature_slider.isVisibleTo(inspector))
        self.assertTrue(inspector.default_button.isEnabled())

    def test_navigation_rail_expands_for_every_top_level_page(self):
        shell = NavigationShell(icon_only=True)
        for key, title in (
            ("devices", "Devices"),
            ("monitor", "Monitor Sync"),
            ("music", "Music Sync"),
            ("draw", "Draw"),
        ):
            shell.add_page(
                key=key,
                title=title,
                icon=QIcon(),
                widget=QWidget(),
            )

        self.assertEqual(shell.nav_list.count(), 4)
        self.assertEqual(shell.nav_list.height(), 4 * RAIL_ITEM_HEIGHT)

    def test_hidden_rail_page_shrinks_the_rail_and_leaves_the_current_page(self):
        shell = NavigationShell(icon_only=True)
        pages = {}
        for key, title, label in (
            ("devices", "Devices", None),
            ("monitor", "Monitor Sync", "Monitor"),
            ("draw", "Draw", None),
        ):
            pages[key] = QWidget()
            shell.add_page(key=key, title=title, icon=QIcon(), widget=pages[key], label=label)
        item = shell.nav_list.item(1)
        self.assertEqual(item.toolTip(), "Monitor Sync")
        self.assertEqual(item.data(NavRailItemDelegate.LABEL_ROLE), "Monitor")

        shell.set_current_by_key("draw")
        self.assertEqual(shell.nav_list.currentRow(), 2)
        shell.set_page_visible("draw", False)
        self.assertFalse(shell.is_page_visible("draw"))
        self.assertEqual(shell.nav_list.height(), 2 * RAIL_ITEM_HEIGHT)
        self.assertEqual(shell.nav_list.currentRow(), 0)
        shell.set_current_by_key("draw")
        self.assertEqual(shell.nav_list.currentRow(), 0)
        shell.set_page_visible("draw", True)
        self.assertEqual(shell.nav_list.height(), 3 * RAIL_ITEM_HEIGHT)
        shell.set_current_by_key("draw")
        self.assertEqual(shell.nav_list.currentRow(), 2)

    def test_draw_page_is_offered_only_with_a_matrix_panel(self):
        shell = NavigationShell(icon_only=True)
        for key in ("devices", "draw"):
            shell.add_page(key=key, title=key.title(), icon=QIcon(), widget=QWidget())
        window = SimpleNamespace(
            nav_shell=shell,
            device_controller=SimpleNamespace(devices=[{"transport": "lan", "model": "H619C"}]),
        )

        LumiSyncMainWindow._update_draw_page_visibility(window)  # type: ignore[arg-type]
        self.assertFalse(shell.is_page_visible("draw"))
        window.device_controller.devices.append({"transport": "ble", "mac": "AA:BB", "model": "IDM"})
        LumiSyncMainWindow._update_draw_page_visibility(window)  # type: ignore[arg-type]
        self.assertTrue(shell.is_page_visible("draw"))
        window.device_controller.devices.pop()
        LumiSyncMainWindow._update_draw_page_visibility(window)  # type: ignore[arg-type]
        self.assertFalse(shell.is_page_visible("draw"))


if __name__ == "__main__":
    unittest.main()
