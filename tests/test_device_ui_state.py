import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QWidget

from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.widgets.device_card import DeviceCard, format_device_output
from lumisync.gui.widgets.device_inspector import DeviceInspector
from lumisync.gui.widgets.navigation_shell import NavigationShell, RAIL_ITEM_HEIGHT


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

    def test_card_shows_unknown_copy_for_unanswered_lan_status(self):
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

        self.assertEqual(card.state_detail_label.text(), "State unknown")
        self.assertIn("No status reply yet", card.state_detail_label.toolTip())

    def test_inspector_shows_unknown_copy_for_unanswered_lan_status(self):
        copy = DeviceInspector._status_copy(
            {
                "online": False,
                "stale": True,
                "status_source": "unknown",
                "readback_supported": True,
                "last_error": "No status reply",
            }
        )

        self.assertEqual(copy[0], "State unknown")

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
        self.assertEqual(format_device_output(state), "Last sent · #0A141E")

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
            "status_source": "confirmed",
            "power_on": True,
            "brightness": 42,
            "color": (12, 34, 56),
        }
        active = dict(confirmed, active_output="Monitor sync")

        self.assertEqual(format_device_output(confirmed), "Showing · #0C2238 · 42%")
        self.assertEqual(format_device_output(active), "Live output · Monitor sync")

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

    def test_compact_card_hides_address_and_uses_transport_icon(self):
        card = DeviceCard(
            0,
            {
                "model": "H619C",
                "ip": "192.168.0.28",
                "transport": "lan",
            },
        )

        compact_copy = card.device_summary.text().lower()
        self.assertNotIn("192.168.0.28", compact_copy)
        self.assertNotIn("lan", compact_copy)
        self.assertNotIn("bluetooth", compact_copy)
        self.assertFalse(card.transport_icon.pixmap().isNull())
        self.assertFalse(hasattr(card, "checkbox"))

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

    def test_group_mode_turns_the_whole_card_into_a_safe_selector(self):
        card = DeviceCard(0, {"model": "Panel", "transport": "ble"})

        card.set_group_selection_mode(True)
        card.set_checked(True)

        self.assertTrue(card.property("groupSelection"))
        self.assertTrue(card.property("selected"))
        self.assertTrue(card.group_badge.isVisibleTo(card))
        self.assertFalse(card.power_button.isEnabled())
        self.assertFalse(card.brightness_slider.isEnabled())

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


if __name__ == "__main__":
    unittest.main()
