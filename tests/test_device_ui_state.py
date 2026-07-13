import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

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

    def _controller(self):
        with patch(
            "lumisync.gui.controllers.device_controller.devices.get_data",
            return_value={"devices": [], "selectedDevice": 0},
        ):
            return DeviceController()

    def test_bluetooth_default_state_is_unknown_not_offline(self):
        controller = self._controller()
        state = controller._default_state(
            {"transport": "ble", "mac": "AA:BB", "model": "IDM"}
        )

        self.assertEqual(state["status_source"], "unknown")
        self.assertFalse(state["readback_supported"])
        self.assertFalse(state["stale"])

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
        self.assertFalse(card._power_shadow.isEnabled())

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
        self.assertGreater(card._power_shadow.blurRadius(), 0)
        self.assertTrue(card._power_shadow.isEnabled())

        card.set_state(
            {
                "online": True,
                "status_source": "confirmed",
                "power_on": False,
            }
        )
        self.assertEqual(card.power_button.property("powerState"), "off")
        self.assertEqual(card.power_button.toolTip(), "Turn on")
        self.assertEqual(card._power_shadow.blurRadius(), 0)
        self.assertFalse(card._power_shadow.isEnabled())

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
