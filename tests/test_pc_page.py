"""The PC page: lighting inside or attached to this computer.

PC hardware (USB lighting such as RGB Fusion 2 boards) has its own rail page
and never shows on the Devices page. No test here touches real hardware:
discovery runs through a patched ``start_task`` and status reads are patched.
"""

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

from lumisync import devices
from lumisync.device import Device
from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.main_window import LumiSyncMainWindow
from lumisync.gui.utils.device_identity import is_pc_hardware
from lumisync.gui.views.devices_view import PC_EMPTY_TEXT, PC_FOOTNOTE, DevicesView
from lumisync.gui.widgets.navigation_shell import NavRailItemDelegate

BOARD = {
    "device_id": "rgbfusion2-048d-5702",
    "name": "B550 AORUS ELITE lighting",
    "model": "RGB Fusion 2",
    "transport": "usb",
    "type": "rgb_fusion2",
    "device_kind": "motherboard",
}
KEYBOARD = {
    "device_id": "lorgar-320f-5075",
    "name": "Lorgar AZAR",
    "model": "Lorgar AZAR",
    "transport": "usb",
    "type": "lorgar_keyboard",
    "device_kind": "keyboard",
    "usb_vendor_id": 0x320F,
    "usb_product_id": 0x5075,
}
STRIP_A = {"mac": "fixture-strip-a", "name": "Desk strip", "model": "H619C", "transport": "lan", "ip": "192.0.2.10"}
STRIP_B = {"mac": "fixture-strip-b", "name": "Shelf strip", "model": "H619C", "transport": "lan", "ip": "192.0.2.11"}


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def controller(self, device_list=()):
        with patch("lumisync.gui.controllers.device_controller.devices.get_data",
                   return_value={"devices": [], "selectedDevice": 0}):
            controller = DeviceController()
        self.addCleanup(controller.shutdown)
        controller.devices = [dict(device) for device in device_list]
        # No status read may reach real hardware or the network.
        for name in ("refresh_device_states", "refresh_pc_hardware_states",
                     "refresh_local_device_states", "refresh_account_device_states"):
            patcher = patch.object(controller, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(controller, "get_groups", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)
        return controller

    def view(self, controller, **kwargs):
        view = DevicesView(controller, **kwargs)
        self.addCleanup(view.deleteLater)
        view.resize(1280, 900)
        view._apply_device_list()
        return view

    def window(self, devices_list=(), *, hid_supported=True):
        controller = self.controller(devices_list)
        window = LumiSyncMainWindow.__new__(LumiSyncMainWindow)
        QMainWindow.__init__(window)
        self.addCleanup(window.deleteLater)
        window.device_controller = controller
        window.sync_controller = Mock()
        window.firewall_monitor = None  # pyright: ignore[reportAttributeAccessIssue]
        window.settings = Mock()

        def page(*_args, **_kwargs):
            return QWidget()

        with patch("lumisync.gui.views.modes_view.ModesView", side_effect=page), \
                patch("lumisync.gui.views.draw_view.DrawView", side_effect=page), \
                patch("lumisync.gui.views.settings_page.SettingsPage", side_effect=page), \
                patch("lumisync.drivers.hid_feature.supported", return_value=hid_supported):
            window.setup_ui()
        return window


class PredicateTests(unittest.TestCase):
    def test_usb_lighting_is_pc_hardware(self):
        self.assertTrue(is_pc_hardware(BOARD))
        self.assertTrue(is_pc_hardware({"transport": " USB "}))
        self.assertTrue(is_pc_hardware(Device.from_dict(BOARD)))

    def test_network_bluetooth_cloud_and_odd_values_are_not(self):
        for device in (STRIP_A, {"transport": "ble", "mac": "AA:BB"}, {"transport": "tuya"},
                       {"transport": "govee_account"}, {}, {"transport": None}):
            self.assertFalse(is_pc_hardware(device), device)
        self.assertFalse(is_pc_hardware(Device.from_dict(STRIP_A)))
        self.assertFalse(is_pc_hardware(None))
        self.assertFalse(is_pc_hardware("usb"))


class LocalHardwareHelperTests(unittest.TestCase):
    def test_usb_is_the_only_local_hardware_transport_today(self):
        from lumisync.device import LOCAL_HARDWARE_TRANSPORTS, is_local_hardware

        self.assertEqual(LOCAL_HARDWARE_TRANSPORTS, frozenset({"usb"}))
        self.assertTrue(is_local_hardware(BOARD))
        self.assertTrue(Device.from_dict(BOARD).is_local_hardware)
        self.assertTrue(Device.from_dict(BOARD).is_pooled)
        for device in (STRIP_A, {"transport": "ble"}, {"transport": "tuya"}, {}, None, "usb"):
            self.assertFalse(is_local_hardware(device), device)
        self.assertFalse(Device.from_dict(STRIP_A).is_local_hardware)

    def test_each_local_hardware_driver_is_asked_separately(self):
        import sys
        from types import SimpleNamespace

        mouse = {**BOARD, "device_id": "fixture-mouse", "device_kind": "mouse"}
        working = SimpleNamespace(discover=lambda: [mouse])
        broken = SimpleNamespace(discover=Mock(side_effect=RuntimeError("boom")))
        with patch.dict(sys.modules, {"lumisync.drivers._fixture_working": working,
                                      "lumisync.drivers._fixture_broken": broken}), \
                patch.object(devices, "LOCAL_HARDWARE_DRIVERS", ("_fixture_broken", "_fixture_working")):
            self.assertEqual(devices.discover_local_hardware(), [mouse])
        with patch.dict(sys.modules, {"lumisync.drivers._fixture_working": working}), \
                patch.object(devices, "LOCAL_HARDWARE_DRIVERS", ("_fixture_working", "_fixture_missing")):
            self.assertEqual(devices.discover_local_hardware(), [mouse])
        self.assertEqual(devices.LOCAL_HARDWARE_DRIVERS, ("gigabyte_fusion", "logitech_hidpp", "lorgar_keyboard"))


class PcHardwareLabelTests(unittest.TestCase):
    def test_mouse_and_keyboard_kinds_get_their_own_label_and_icon(self):
        from lumisync.gui.resources.icons import _ICON_FILES, IconKey
        from lumisync.gui.utils.device_identity import device_identity
        from lumisync.gui.widgets.device_card import device_subline
        from lumisync.gui.widgets.device_chip import light_icon

        mouse = {**BOARD, "device_id": "fixture-mouse", "type": "fixture_mouse", "device_kind": "mouse"}
        keyboard = {**mouse, "device_id": "fixture-keyboard", "device_kind": "keyboard"}
        self.assertEqual(device_identity(mouse), ("Mouse lighting", IconKey.MOUSE))
        self.assertEqual(device_identity(keyboard), ("Keyboard lighting", IconKey.KEYBOARD))
        self.assertEqual(device_subline(mouse), "Mouse lighting · USB")
        self.assertEqual(light_icon(mouse), "mouse.svg")
        self.assertEqual(light_icon(keyboard), "keyboard.svg")
        self.assertEqual((_ICON_FILES[IconKey.MOUSE], _ICON_FILES[IconKey.KEYBOARD]), ("mouse.svg", "keyboard.svg"))

    def test_icons_follow_the_house_style(self):
        from lumisync.gui.resources import ResourceManager

        for name in ("mouse.svg", "keyboard.svg"):
            path = ResourceManager.get_icon_path(name)
            self.assertIsNotNone(path, name)
            with open(str(path), encoding="utf-8") as handle:
                text = handle.read()
            for attribute in ('viewBox="0 0 32 32"', 'fill="none"', 'stroke="currentColor"', 'stroke-width="2"'):
                self.assertIn(attribute, text, name)


class ScopedViewTests(_QtCase):
    def test_each_device_shows_on_exactly_one_page_with_its_controller_index(self):
        controller = self.controller([STRIP_A, BOARD, STRIP_B])
        lights = self.view(controller)
        pc = self.view(controller, scope="pc")
        self.assertEqual([card._index for card in lights._cards], [0, 2])
        self.assertEqual([card._index for card in pc._cards], [1])
        self.assertEqual(pc._cards[0].device()["device_id"], BOARD["device_id"])
        self.assertFalse(any(is_pc_hardware(card.device()) for card in lights._cards))

    def test_controls_on_a_pc_card_reach_the_right_device(self):
        controller = self.controller([STRIP_A, BOARD, STRIP_B])
        with patch.object(controller, "toggle_power_at") as toggled, \
                patch.object(controller, "set_color_at") as coloured:
            pc = self.view(controller, scope="pc")
            card = pc._cards[0]
            card.power_button.click()
            card.color_picked.emit(card._index, QColor(255, 0, 0))
        toggled.assert_called_once_with(1)
        coloured.assert_called_once_with(1, 255, 0, 0)

    def test_inspector_and_state_updates_follow_controller_indexes(self):
        controller = self.controller([STRIP_A, BOARD, STRIP_B])
        lights = self.view(controller)
        pc = self.view(controller, scope="pc")
        lights._open_inspector(1)  # The Devices page never opens PC hardware.
        self.assertEqual(lights._inspected_index, -1)
        pc._open_inspector(1)
        self.assertEqual(pc._inspected_index, 1)
        self.assertFalse(pc.inspector.usb_options_panel.isHidden())
        pc._card_by_index[1].set_state = Mock()
        lights._card_by_index[2].set_state = Mock()
        controller.device_state_updated.emit(1, {"online": True})
        pc._card_by_index[1].set_state.assert_called_once()
        lights._card_by_index[2].set_state.assert_not_called()

    def test_a_pc_device_moving_ahead_rebuilds_with_new_indexes(self):
        controller = self.controller([STRIP_A, STRIP_B])
        lights = self.view(controller)
        self.assertEqual([card._index for card in lights._cards], [0, 1])
        controller.devices = [dict(BOARD), dict(STRIP_A), dict(STRIP_B)]
        lights._apply_device_list()
        self.assertEqual([card._index for card in lights._cards], [1, 2])

    def test_empty_states_count_only_the_devices_each_page_shows(self):
        controller = self.controller([BOARD])
        lights = self.view(controller)
        pc = self.view(controller, scope="pc")
        self.assertFalse(lights.empty_label.isHidden())
        self.assertTrue(lights.content_host.isHidden())
        self.assertTrue(pc.empty_label.isHidden())

        controller = self.controller([STRIP_A])
        pc = self.view(controller, scope="pc")
        self.assertFalse(pc.empty_label.isHidden())
        self.assertEqual(pc.empty_label.text(), PC_EMPTY_TEXT)
        self.assertTrue(pc.empty_label.text().startswith("No PC lighting found yet\n\n"))

    def test_pc_page_header_footnote_and_no_network_bars(self):
        controller = self.controller([BOARD])
        pc = self.view(controller, scope="pc")
        lights = self.view(controller)
        self.assertEqual(pc.title_label.text(), "PC")
        self.assertEqual(pc.find_devices_button.text(), "Scan this PC")
        self.assertTrue(pc.add_button.isHidden())
        self.assertTrue(pc.accounts_button.isHidden())
        self.assertIsNotNone(pc.footnote_label)
        assert pc.footnote_label is not None
        self.assertEqual(pc.footnote_label.text(), PC_FOOTNOTE)
        self.assertIsNone(lights.footnote_label)
        self.assertEqual(lights.title_label.text(), "Devices")
        self.assertEqual(lights.find_devices_button.text(), "Find devices")
        pc._network_problem = True
        with patch.object(controller, "account_issue", return_value=("Govee", "Sign in again")), \
                patch.object(type(controller), "account_issues", new={"govee": "Govee: Sign in again"}):
            pc._update_info_bars()
        self.assertTrue(pc.info_host.isHidden())
        self.assertTrue(all(bar.isHidden() for bar in pc._info_bars()))

    def test_pc_page_polls_only_pc_hardware(self):
        controller = self.controller([BOARD, STRIP_A])
        pc = self.view(controller, scope="pc")
        with patch.object(pc, "isVisible", return_value=True):
            for _ in range(4):
                pc._poll_status()
        self.assertEqual(controller.refresh_pc_hardware_states.call_count >= 4, True)
        controller.refresh_account_device_states.assert_not_called()
        controller.refresh_local_device_states.assert_not_called()

    def test_unknown_scope_is_refused(self):
        controller = self.controller()
        with self.assertRaises(ValueError):
            DevicesView(controller, scope="mice")


class ScanThisPcTests(_QtCase):
    def scan(self, controller, found, error=None):
        actions = []

        def start(action, completed):
            actions.append(action)
            completed(found, error)
            return Mock()

        with patch.object(controller, "_start_lan_discovery") as lan, \
                patch.object(controller, "_start_ble_scan") as ble, \
                patch.object(controller, "_scan_tuya_lan") as tuya, \
                patch.object(controller, "get_accounts") as accounts, \
                patch.object(controller._store, "save"), \
                patch("lumisync.gui.controllers.device_controller.start_task", side_effect=start):
            started = controller.scan_local_hardware()
        for network_search in (lan, ble, tuya, accounts):
            network_search.assert_not_called()
        return started, actions

    def test_scan_merges_a_found_board_without_any_network_search(self):
        controller = self.controller([STRIP_A])
        pc = self.view(controller, scope="pc")
        events = []
        controller.local_hardware_scan_started.connect(lambda: events.append("started"))
        controller.local_hardware_scan_finished.connect(events.append)
        controller.device_search_started.connect(lambda: events.append("network search"))

        started, actions = self.scan(controller, [dict(BOARD)])

        self.assertTrue(started)
        self.assertEqual(actions, [devices.discover_local_hardware])
        self.assertEqual([d.get("transport") for d in controller.devices], ["lan", "usb"])
        self.assertEqual(events[0], "started")
        self.assertEqual(events[1], {"found": 1, "new_devices": 1, "error": None})
        self.assertNotIn("network search", events)
        self.assertFalse(pc.find_devices_button.is_searching())
        self.assertFalse(controller._local_scan_active)
        pc._apply_device_list()
        self.assertEqual([card._index for card in pc._cards], [1])

    def test_scanning_again_finds_nothing_new(self):
        controller = self.controller([STRIP_A, BOARD])
        finished = []
        controller.local_hardware_scan_finished.connect(finished.append)
        self.scan(controller, [dict(BOARD)])
        self.assertEqual(len(controller.devices), 2)
        self.assertEqual(finished[0]["new_devices"], 0)

    def test_scan_button_spins_while_scanning(self):
        controller = self.controller()
        pc = self.view(controller, scope="pc")
        with patch("lumisync.gui.controllers.device_controller.start_task", return_value=Mock()):
            controller.scan_local_hardware()
        self.assertTrue(pc.find_devices_button.is_searching())
        self.assertEqual(pc.find_devices_button.text(), "Scanning…")
        controller._on_local_scan_finished([], None, set())
        self.assertFalse(pc.find_devices_button.is_searching())

    def test_scan_and_find_devices_never_overlap(self):
        controller = self.controller()
        controller._combined_search_active = True
        with patch("lumisync.gui.controllers.device_controller.start_task") as started:
            self.assertFalse(controller.scan_local_hardware())
        started.assert_not_called()
        controller._combined_search_active = False
        controller._local_scan_active = True
        with patch.object(controller, "_local_network_available") as probe:
            controller.find_devices()
        probe.assert_not_called()


class KeyboardOnPcPageTests(_QtCase):
    """The Lorgar keyboard: a PC page card with the keyboard label, manual only."""

    def test_keyboard_card_label_and_icon(self):
        from lumisync.gui.resources.icons import IconKey
        from lumisync.gui.utils.device_identity import device_identity
        from lumisync.gui.widgets.device_card import device_subline
        from lumisync.gui.widgets.device_chip import light_icon

        self.assertTrue(is_pc_hardware(KEYBOARD))
        self.assertEqual(device_identity(KEYBOARD), ("Keyboard lighting", IconKey.KEYBOARD))
        self.assertEqual(device_subline(KEYBOARD), "Keyboard lighting · USB")
        self.assertEqual(light_icon(KEYBOARD), "keyboard.svg")

    def test_keyboard_shows_on_the_pc_page_only(self):
        controller = self.controller([STRIP_A, KEYBOARD, BOARD])
        lights = self.view(controller)
        pc = self.view(controller, scope="pc")
        self.assertEqual([card._index for card in lights._cards], [0])
        self.assertEqual([card._index for card in pc._cards], [1, 2])
        self.assertEqual(pc._card_by_index[1].device()["device_id"], KEYBOARD["device_id"])

    def test_keyboard_controls_reach_the_keyboard(self):
        controller = self.controller([STRIP_A, KEYBOARD])
        with patch.object(controller, "toggle_power_at") as toggled, \
                patch.object(controller, "set_color_at") as coloured:
            pc = self.view(controller, scope="pc")
            card = pc._card_by_index[1]
            card.power_button.click()
            card.color_picked.emit(card._index, QColor(0, 255, 0))
        toggled.assert_called_once_with(1)
        coloured.assert_called_once_with(1, 0, 255, 0)

    def test_inspector_has_the_keyboard_lighting_card_and_no_sync_default(self):
        controller = self.controller([STRIP_A, KEYBOARD])
        pc = self.view(controller, scope="pc")
        pc._open_inspector(1)
        inspector = pc.inspector
        self.assertEqual(pc._inspected_index, 1)
        self.assertFalse(inspector.usb_options_panel.isHidden())
        self.assertFalse(inspector.usb_options_label.isHidden())
        self.assertEqual(inspector.usb_options_label.text(), "Keyboard lighting")
        # The keyboard has effects only: no colour correction, no layout.
        self.assertTrue(inspector.color_correction_row.isHidden())
        self.assertTrue(inspector.layout_row.isHidden())
        self.assertFalse(inspector.effect_row.isHidden())
        self.assertEqual(
            [inspector.effect_combo.itemText(row) for row in range(inspector.effect_combo.count())],
            ["Wave", "Cloud", "Vortex", "Colour palette", "Breathing", "Static", "Reaction", "Stone",
             "Traverse", "Stars", "Firework", "Scrolling", "Wave motion", "Cartoon", "Rain", "Scanning",
             "Acceleration"],
        )
        self.assertEqual(inspector.effect_combo.currentText(), "Static")
        self.assertTrue(inspector.default_button.isHidden())
        self.assertTrue(inspector._info_rows["Layout"].isHidden())
        self.assertEqual(inspector.zones_value.text(), "Whole device · one colour")
        self.assertFalse(inspector.color_controls.isHidden())
        self.assertFalse(inspector.brightness_controls.isHidden())

    def test_effect_rows_follow_the_chosen_effect(self):
        controller = self.controller([STRIP_A, KEYBOARD])
        pc = self.view(controller, scope="pc")
        pc._open_inspector(1)
        inspector = pc.inspector
        # Static is shown first: no speed, direction or multicolour.
        self.assertTrue(inspector.effect_speed_row.isHidden())
        self.assertTrue(inspector.effect_direction_row.isHidden())
        self.assertTrue(inspector.effect_multicolor_row.isHidden())
        expected = {
            # effect: (speed, direction labels or None, multicolour)
            "wave": (True, ("Left to right", "Right to left"), True),
            "stars": (True, None, False),
            "colour_palette": (False, ("Outside", "Inside"), False),
            "static": (False, None, False),
        }
        with patch.object(controller, "set_lighting_effect_at", return_value=True) as chosen:
            for key, (speed, directions, multicolor) in expected.items():
                with self.subTest(effect=key):
                    inspector.effect_combo.setCurrentIndex(inspector.effect_combo.findData(key))
                    self.assertEqual(not inspector.effect_speed_row.isHidden(), speed)
                    self.assertEqual(not inspector.effect_direction_row.isHidden(), directions is not None)
                    if directions is not None:
                        self.assertEqual((inspector.effect_direction_combo.itemText(0),
                                          inspector.effect_direction_combo.itemText(1)), directions)
                    self.assertEqual(not inspector.effect_multicolor_row.isHidden(), multicolor)
                    chosen.assert_called_with(1, key)
        # A saved effect shows its own rows when the inspector opens.
        controller.devices[1] = {**KEYBOARD, "lighting_effect": "wave", "lighting_effect_speed": 5,
                                 "lighting_effect_reversed": True, "lighting_effect_multicolor": True}
        pc._populate_inspector()
        self.assertEqual(inspector.effect_combo.currentText(), "Wave")
        self.assertEqual(inspector.effect_speed_slider.value(), 5)
        self.assertEqual(inspector.effect_direction_combo.currentText(), "Right to left")
        self.assertTrue(inspector.effect_multicolor_switch.isChecked())

    def test_choosing_an_effect_reaches_the_keyboard_and_is_saved(self):
        from lumisync.drivers import lorgar_keyboard as lorgar
        from lumisync.drivers import pool
        from tests.test_lorgar_keyboard import BEGIN, END, WAVE_BLUE, FakeClock, FakeKeyboard

        keyboard, clock = FakeKeyboard(), FakeClock()
        for name, value in (("_hid", keyboard), ("_monotonic", clock.monotonic), ("_wait", clock.wait)):
            patcher = patch.object(lorgar, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(lorgar._controllers.clear)
        self.addCleanup(lorgar.close_all)
        self.addCleanup(pool.close, KEYBOARD)

        controller = self.controller([STRIP_A, KEYBOARD])
        write = Mock()
        patcher = patch.object(controller._store, "write", write)
        patcher.start()
        self.addCleanup(patcher.stop)
        pool.acquire(KEYBOARD).set_color(0, 80, 255)  # The keyboard is open and on.
        keyboard.writes.clear()

        pc = self.view(controller, scope="pc")
        pc._open_inspector(1)
        inspector = pc.inspector
        with patch("lumisync.gui.controllers.device_controller.start_task",
                   side_effect=lambda action, done: (done(action(), None), Mock())[1]):
            inspector.effect_combo.setCurrentIndex(inspector.effect_combo.findData("wave"))
        self.assertEqual(keyboard.writes, [BEGIN, WAVE_BLUE, END])
        saved = write.call_args.args[0]["devices"][1]
        self.assertEqual(saved["lighting_effect"], "wave")
        self.assertEqual(controller.devices[1]["lighting_effect"], "wave")

        # The other options save the same way; the defaults are stored as absent.
        with patch("lumisync.gui.controllers.device_controller.start_task",
                   side_effect=lambda action, done: (done(action(), None), Mock())[1]):
            self.assertTrue(controller.set_lighting_effect_speed_at(1, 5))
            self.assertTrue(controller.set_lighting_effect_reversed_at(1, True))
            self.assertTrue(controller.set_lighting_effect_multicolor_at(1, True))
            saved = dict(write.call_args.args[0]["devices"][1])
            self.assertEqual((saved["lighting_effect_speed"], saved["lighting_effect_reversed"],
                              saved["lighting_effect_multicolor"]), (5, True, True))
            self.assertEqual(keyboard.sets()[-1][9:17].hex(" "), "01 04 00 00 01 00 00 00")
            self.assertTrue(controller.set_lighting_effect_at(1, "static"))
            self.assertTrue(controller.set_lighting_effect_speed_at(1, 3))
        saved = dict(write.call_args.args[0]["devices"][1])
        self.assertNotIn("lighting_effect", saved)
        self.assertNotIn("lighting_effect_speed", saved)
        self.assertFalse(controller.set_lighting_effect_at(1, "disco"))
        self.assertFalse(controller.set_lighting_effect_speed_at(1, 6))
        self.assertFalse(controller.set_lighting_effect_at(0, "wave"))  # A LAN strip has no effects.

    def test_keyboard_has_no_separate_colour_row(self):
        controller = self.controller([STRIP_A, KEYBOARD])
        pc = self.view(controller, scope="pc")
        pc._open_inspector(1)
        self.assertTrue(pc.inspector.secondary_color_row.isHidden())
        self.assertTrue(pc.inspector.secondary_color_value_row.isHidden())

    def test_keyboard_cannot_join_a_sync_group(self):
        controller = self.controller([STRIP_A, KEYBOARD])
        with patch.object(controller, "_persist_groups", return_value=True) as persisted:
            self.assertTrue(controller.save_group("Desk", [0, 1]))
            change = persisted.call_args[0][0]
            saved = change([])
        self.assertEqual(len(saved), 1)
        self.assertNotIn(KEYBOARD["device_id"], repr(saved))
        with patch.object(controller, "_persist_groups", return_value=True) as persisted:
            self.assertFalse(controller.save_group("Keys", [1]))
        persisted.assert_not_called()

    def _sync_page(self):
        controller = self.controller([STRIP_A, KEYBOARD])
        write = Mock()
        patcher = patch.object(controller._store, "write", write)
        patcher.start()
        self.addCleanup(patcher.stop)
        from lumisync.groups import device_key

        selections = {"sync/monitor/devices": f"{device_key(STRIP_A)},{device_key(KEYBOARD)}"}
        settings = Mock()
        settings.value.side_effect = lambda name, default=None: selections.get(name, default)
        settings.setValue.side_effect = selections.__setitem__
        patcher = patch("lumisync.gui.app_settings.app_settings", return_value=settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        pc = self.view(controller, scope="pc")
        pc._open_inspector(1)
        return controller, pc, write, selections

    def test_sync_row_is_off_by_default_with_the_wear_note(self):
        from lumisync.drivers import lorgar_keyboard as lorgar

        _controller, pc, _write, _selections = self._sync_page()
        inspector = pc.inspector
        self.assertFalse(inspector.sync_row.isHidden())
        self.assertFalse(inspector.sync_switch.isChecked())
        self.assertEqual(inspector.sync_hint.text(), lorgar.SYNC_DESCRIPTION)
        self.assertEqual(inspector.sync_hint.text(), (
            "Off by default. This keyboard saves every colour change to its internal memory, "
            "which wears out with use."))
        self.assertTrue(inspector.sync_interval_row.isHidden())

    def test_cancel_keeps_sync_off_and_saves_nothing(self):
        controller, pc, write, _selections = self._sync_page()
        with patch("lumisync.gui.views.devices_view.confirm_lighting_sync", return_value=False) as asked:
            pc.inspector.sync_switch.setChecked(True)
        asked.assert_called_once()
        self.assertEqual(asked.call_args.args[1].confirm_title, "Allow sync for this keyboard?")
        write.assert_not_called()
        self.assertFalse(pc.inspector.sync_switch.isChecked())
        self.assertTrue(pc.inspector.sync_interval_row.isHidden())
        self.assertNotIn("lighting_sync_enabled", controller.devices[1])

    def test_allow_saves_sync_and_the_interval_and_the_pickers_follow(self):
        from lumisync.drivers.registry import capabilities_for_device

        controller, pc, write, selections = self._sync_page()
        inspector = pc.inspector
        self.assertFalse(capabilities_for_device(controller.devices[1]).supports_streaming)
        with patch("lumisync.gui.views.devices_view.confirm_lighting_sync", return_value=True) as asked:
            inspector.sync_switch.setChecked(True)
        asked.assert_called_once()
        self.assertIs(write.call_args.args[0]["devices"][1]["lighting_sync_enabled"], True)
        self.assertTrue(inspector.sync_switch.isChecked())
        self.assertFalse(inspector.sync_interval_row.isHidden())
        self.assertEqual(
            [inspector.sync_interval_combo.itemText(row) for row in range(inspector.sync_interval_combo.count())],
            ["Every 10 seconds (least wear)", "Every 2 seconds", "Up to 4 a second (most wear)"],
        )
        self.assertEqual(inspector.sync_interval_combo.currentData(), "10s")
        # Now a sync target: the picker filter and groups accept it.
        self.assertTrue(capabilities_for_device(controller.devices[1]).supports_streaming)
        with patch.object(controller, "_persist_groups", return_value=True):
            self.assertTrue(controller.save_group("Keys", [1]))

        inspector.sync_interval_combo.setCurrentIndex(inspector.sync_interval_combo.findData("2s"))
        self.assertEqual(write.call_args.args[0]["devices"][1]["lighting_sync_interval"], "2s")
        self.assertEqual(controller.devices[1]["lighting_sync_interval"], "2s")
        self.assertFalse(controller.set_lighting_sync_interval_at(1, "hourly"))
        self.assertTrue(controller.set_lighting_sync_interval_at(1, "10s"))
        self.assertIsNone(controller.devices[1].get("lighting_sync_interval"))

        # Off needs no confirmation and drops it from the pickers and saved selections.
        with patch("lumisync.gui.views.devices_view.confirm_lighting_sync") as asked:
            inspector.sync_switch.setChecked(False)
        asked.assert_not_called()
        self.assertIsNone(controller.devices[1].get("lighting_sync_enabled"))
        self.assertTrue(inspector.sync_interval_row.isHidden())
        self.assertFalse(capabilities_for_device(controller.devices[1]).supports_streaming)
        from lumisync.groups import device_key

        self.assertEqual(selections["sync/monitor/devices"], device_key(STRIP_A))
        with patch.object(controller, "_persist_groups", return_value=True) as persisted:
            self.assertFalse(controller.save_group("Keys", [1]))
        persisted.assert_not_called()

    def test_devices_without_the_opt_in_have_no_sync_row(self):
        controller = self.controller([STRIP_A, BOARD])
        pc = self.view(controller, scope="pc")
        pc._open_inspector(1)
        self.assertTrue(pc.inspector.sync_row.isHidden())
        self.assertTrue(pc.inspector.sync_interval_row.isHidden())
        self.assertFalse(controller.set_lighting_sync_enabled_at(1, True))

    def test_scan_merges_a_found_keyboard(self):
        controller = self.controller([STRIP_A])
        pc = self.view(controller, scope="pc")
        finished = []
        controller.local_hardware_scan_finished.connect(finished.append)
        with patch.object(controller, "_start_lan_discovery"), patch.object(controller, "_start_ble_scan"), \
                patch.object(controller, "_scan_tuya_lan"), patch.object(controller, "get_accounts"), \
                patch.object(controller._store, "save"), \
                patch("lumisync.gui.controllers.device_controller.start_task",
                      side_effect=lambda action, done: (done([dict(KEYBOARD)], None), Mock())[1]):
            self.assertTrue(controller.scan_local_hardware())
        self.assertEqual(finished[0], {"found": 1, "new_devices": 1, "error": None})
        pc._apply_device_list()
        self.assertEqual([card.device()["device_id"] for card in pc._cards], [KEYBOARD["device_id"]])


MOUSE = {
    "device_id": "hidpp-046d-c08b",
    "name": "G502 HERO",
    "model": "Logitech G502 HERO",
    "transport": "usb",
    "type": "logitech_hidpp",
    "device_kind": "mouse",
    "usb_vendor_id": 0x046D,
    "usb_product_id": 0xC08B,
    "color_correction": "none",  # Exact bytes on the fake mouse.
}


def run_now(action, done):
    """``start_task`` stand-in: run the background action at once."""
    done(action(), None)
    return Mock()


class MouseOnPcPageTests(_QtCase):
    """The G502 HERO's "Mouse lighting" card: effects, speed and a separate logo colour."""

    def test_rows_follow_the_chosen_effect(self):
        controller = self.controller([STRIP_A, MOUSE])
        pc = self.view(controller, scope="pc")
        pc._open_inspector(1)
        inspector = pc.inspector
        self.assertEqual(inspector.usb_options_label.text(), "Mouse lighting")
        self.assertEqual([inspector.effect_combo.itemText(i) for i in range(inspector.effect_combo.count())],
                         ["Fixed colour", "Breathing", "Colour cycle"])
        self.assertEqual(inspector.effect_combo.currentData(), "fixed")
        self.assertEqual(inspector.secondary_color_title.text(), "Separate logo colour")
        self.assertEqual(inspector.secondary_color_hint.text(),
                         "Give the logo its own colour. The DPI lights keep the main colour.")
        self.assertEqual(inspector.secondary_color_value_title.text(), "Logo colour")
        self.assertFalse(inspector.secondary_color_switch.isChecked())
        expected = {
            # effect: (speed row, logo colour row)
            "breathing": (True, True),
            "colour_cycle": (True, False),
            "fixed": (False, True),
        }
        with patch.object(controller, "set_lighting_effect_at", return_value=True) as chosen:
            for key, (speed, logo) in expected.items():
                with self.subTest(effect=key):
                    inspector.effect_combo.setCurrentIndex(inspector.effect_combo.findData(key))
                    chosen.assert_called_with(1, key)
                    self.assertEqual(not inspector.effect_speed_row.isHidden(), speed)
                    self.assertTrue(inspector.effect_direction_row.isHidden())
                    self.assertTrue(inspector.effect_multicolor_row.isHidden())
                    self.assertEqual(not inspector.secondary_color_row.isHidden(), logo)
                    self.assertTrue(inspector.secondary_color_value_row.isHidden())  # Switch off.
        # Saved settings: the colour row shows while the switch is on, except for colour cycle.
        controller.devices[1] = {**MOUSE, "lighting_effect": "breathing", "lighting_effect_speed": 5,
                                 "lighting_logo_color_enabled": True, "lighting_logo_color": [255, 0, 0]}
        pc._populate_inspector()
        self.assertEqual(inspector.effect_combo.currentText(), "Breathing")
        self.assertEqual(inspector.effect_speed_slider.value(), 5)
        self.assertTrue(inspector.secondary_color_switch.isChecked())
        self.assertFalse(inspector.secondary_color_value_row.isHidden())
        self.assertEqual(inspector.secondary_color_value_hint.text(), "Colour #FF0000")
        controller.devices[1] = {**controller.devices[1], "lighting_effect": "colour_cycle"}
        pc._populate_inspector()
        self.assertTrue(inspector.secondary_color_row.isHidden())
        self.assertTrue(inspector.secondary_color_value_row.isHidden())

    def test_logo_colour_is_saved_and_reaches_the_mouse(self):
        from lumisync.drivers import logitech_hidpp as hidpp
        from lumisync.drivers import pool
        from tests.test_logitech_hidpp import FakeMouse

        mouse = FakeMouse()
        for target in (patch.object(hidpp, "_hid", mouse), patch.object(hidpp, "_monotonic", lambda: 1000.0),
                       patch.dict(hidpp._controllers, clear=True)):
            target.start()
            self.addCleanup(target.stop)
        self.addCleanup(pool.close, MOUSE)

        controller = self.controller([STRIP_A, MOUSE])
        write = Mock()
        patcher = patch.object(controller._store, "write", write)
        patcher.start()
        self.addCleanup(patcher.stop)
        pool.acquire(MOUSE).set_color(0, 0, 255)  # The mouse is open and on.

        pc = self.view(controller, scope="pc")
        pc._open_inspector(1)
        inspector = pc.inspector
        with patch("lumisync.gui.controllers.device_controller.start_task", side_effect=run_now):
            inspector.secondary_color_switch.setChecked(True)
        self.assertFalse(inspector.secondary_color_value_row.isHidden())
        saved = write.call_args.args[0]["devices"][1]
        self.assertIs(saved["lighting_logo_color_enabled"], True)
        self.assertEqual(mouse.zone_colours, {0: (0, 0, 255), 1: (0, 0, 255)})  # Follows the main colour.

        with patch("lumisync.gui.widgets.device_inspector.QColorDialog.getColor",
                   return_value=QColor(255, 0, 0)) as dialog, \
                patch("lumisync.gui.widgets.device_inspector.remember_recent_color"), \
                patch("lumisync.gui.controllers.device_controller.start_task", side_effect=run_now):
            inspector.secondary_color_button.click()
        dialog.assert_called_once()
        saved = write.call_args.args[0]["devices"][1]
        self.assertEqual(saved["lighting_logo_color"], [255, 0, 0])
        self.assertEqual(controller.devices[1]["lighting_logo_color"], [255, 0, 0])
        self.assertEqual(mouse.zone_colours, {0: (0, 0, 255), 1: (255, 0, 0)})

        # Switching it off saves the default (absent) and gives the logo the main colour.
        with patch("lumisync.gui.controllers.device_controller.start_task", side_effect=run_now):
            self.assertTrue(controller.set_lighting_secondary_color_enabled_at(1, False))
        saved = write.call_args.args[0]["devices"][1]
        self.assertIsNone(saved.get("lighting_logo_color_enabled"))
        self.assertEqual(saved["lighting_logo_color"], [255, 0, 0])
        self.assertEqual(mouse.zone_colours, {0: (0, 0, 255), 1: (0, 0, 255)})
        # Devices without the option refuse it.
        self.assertFalse(controller.set_lighting_secondary_color_enabled_at(0, True))
        self.assertFalse(controller.set_lighting_secondary_color_at(0, 1, 2, 3))

    def test_choosing_breathing_reaches_the_mouse(self):
        from lumisync.drivers import logitech_hidpp as hidpp
        from lumisync.drivers import pool
        from tests.test_logitech_hidpp import FakeMouse, set_breathing

        mouse = FakeMouse()
        for target in (patch.object(hidpp, "_hid", mouse), patch.object(hidpp, "_monotonic", lambda: 1000.0),
                       patch.dict(hidpp._controllers, clear=True)):
            target.start()
            self.addCleanup(target.stop)
        self.addCleanup(pool.close, MOUSE)
        controller = self.controller([STRIP_A, MOUSE])
        patcher = patch.object(controller._store, "write", Mock())
        patcher.start()
        self.addCleanup(patcher.stop)
        pool.acquire(MOUSE).set_color(0, 0, 255)
        with patch("lumisync.gui.controllers.device_controller.start_task", side_effect=run_now):
            self.assertTrue(controller.set_lighting_effect_at(1, "breathing"))
            self.assertEqual(mouse.sets()[-2:], [set_breathing(0, 0, 0, 255, 3000), set_breathing(1, 0, 0, 255, 3000)])
            self.assertTrue(controller.set_lighting_effect_speed_at(1, 1))
            self.assertEqual(mouse.sets()[-1], set_breathing(1, 0, 0, 255, 8000))
        self.assertFalse(controller.set_lighting_effect_at(1, "wave"))


class AutoScanTests(_QtCase):
    """The PC page finds hardware by itself, quietly, with an enumerate-only scan."""

    def fake_keyboard(self, plugged=True):
        from lumisync.drivers import lorgar_keyboard as lorgar
        from tests.test_lorgar_keyboard import FakeClock, FakeKeyboard

        keyboard, clock = FakeKeyboard(), FakeClock()
        if not plugged:
            keyboard.infos = []
        for target, name, value in ((lorgar, "_hid", keyboard), (lorgar, "_monotonic", clock.monotonic),
                                    (lorgar, "_wait", clock.wait),
                                    (devices, "LOCAL_HARDWARE_DRIVERS", ("lorgar_keyboard",))):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(lorgar._controllers.clear)
        self.addCleanup(lorgar.close_all)
        return keyboard

    def run_tasks(self, controller):
        """start_task runs the enumerate-only scan inline; returns the started actions."""
        actions = []

        def start(action, done):
            actions.append(action)
            done(action(), None)
            return Mock()

        for target in (patch("lumisync.gui.controllers.device_controller.start_task", side_effect=start),
                       patch.object(controller._store, "save")):
            target.start()
            self.addCleanup(target.stop)
        return actions

    def test_a_tick_while_the_page_is_shown_runs_one_scan(self):
        controller = self.controller([STRIP_A])
        pc = self.view(controller, scope="pc")
        with patch.object(controller, "auto_scan_local_hardware", return_value=True) as scan, \
                patch.object(pc, "isVisible", return_value=True):
            pc._auto_scan_tick()
        scan.assert_called_once_with()

    def test_no_scan_while_hidden_minimised_or_on_the_devices_page(self):
        controller = self.controller([STRIP_A])
        pc = self.view(controller, scope="pc")
        lights = self.view(controller)
        with patch.object(controller, "auto_scan_local_hardware") as scan:
            pc._auto_scan_tick()  # Not visible.
            with patch.object(pc, "isVisible", return_value=True), \
                    patch.object(pc, "window", return_value=Mock(isMinimized=Mock(return_value=True))):
                pc._auto_scan_tick()
            with patch.object(lights, "isVisible", return_value=True):
                lights._auto_scan_tick()
                self.assertFalse(lights.auto_scan())
        scan.assert_not_called()

    def test_the_timer_runs_only_while_the_pc_page_is_shown(self):
        from lumisync.gui.views.devices_view import PC_AUTO_SCAN_INTERVAL_MS

        self.assertEqual(PC_AUTO_SCAN_INTERVAL_MS, 5_000)
        window = self.window([STRIP_A])
        pc = window.pc_view
        self.assertEqual(pc._auto_scan_timer.interval(), PC_AUTO_SCAN_INTERVAL_MS)
        stack = window.nav_shell.content_stack
        with patch.object(window.device_controller, "auto_scan_local_hardware", return_value=True):
            window.show()
            self.assertFalse(pc._auto_scan_timer.isActive())  # The Devices page is shown.
            stack.setCurrentWidget(pc)
            self.assertTrue(pc._auto_scan_timer.isActive())
            stack.setCurrentWidget(window.devices_view)
            self.assertFalse(pc._auto_scan_timer.isActive())
            stack.setCurrentWidget(pc)
            self.assertTrue(pc._auto_scan_timer.isActive())
            window.hide()  # Hidden to the tray.
            self.assertFalse(pc._auto_scan_timer.isActive())
            window.show()
            self.assertTrue(pc._auto_scan_timer.isActive())
            pc.stop_auto_scan()
            self.assertFalse(pc._auto_scan_timer.isActive())
            window.hide()

    def test_ticks_never_overlap_a_running_scan(self):
        controller = self.controller([STRIP_A])
        with patch("lumisync.gui.controllers.device_controller.start_task", return_value=Mock()) as started:
            self.assertTrue(controller.auto_scan_local_hardware())
            self.assertFalse(controller.auto_scan_local_hardware())
            self.assertFalse(controller.auto_scan_local_hardware())
        self.assertEqual(started.call_count, 1)
        self.assertIs(started.call_args.args[0], devices.discover_local_hardware)
        controller._on_auto_scan_finished([], None)
        # A manual scan or a Find devices search running: the tick is skipped too.
        for flag in ("_local_scan_active", "_combined_search_active"):
            setattr(controller, flag, True)
            with patch("lumisync.gui.controllers.device_controller.start_task") as started:
                self.assertFalse(controller.auto_scan_local_hardware())
            started.assert_not_called()
            setattr(controller, flag, False)

    def test_new_hardware_gets_a_card_quietly_and_nothing_is_opened(self):
        keyboard = self.fake_keyboard(plugged=False)
        controller = self.controller([STRIP_A])
        actions = self.run_tasks(controller)
        pc = self.view(controller, scope="pc")
        events = []
        controller.notice.connect(lambda *args: events.append(("notice", args)))
        controller.status_updated.connect(lambda text: events.append(("status", text)))
        controller.local_hardware_scan_started.connect(lambda: events.append("started"))
        controller.local_hardware_scan_finished.connect(lambda summary: events.append("finished"))
        controller.refresh_pc_hardware_states.reset_mock()  # Building the page reads states once.

        self.assertTrue(controller.auto_scan_local_hardware())
        self.assertEqual(len(controller.devices), 1)
        controller.refresh_pc_hardware_states.assert_not_called()

        from tests.test_lorgar_keyboard import COLLECTIONS

        keyboard.infos = list(COLLECTIONS)
        with patch.object(pc.find_devices_button, "set_searching") as spinner:
            self.assertTrue(controller.auto_scan_local_hardware())
        spinner.assert_not_called()
        self.assertEqual([d.get("device_id") for d in controller.devices][1:], [KEYBOARD["device_id"]])
        controller.refresh_pc_hardware_states.assert_called_once()
        pc._apply_device_list()
        self.assertEqual([card.device()["device_id"] for card in pc._cards], [KEYBOARD["device_id"]])
        self.assertEqual(events, [])  # No toast, no status text, no spinner signals.
        self.assertEqual(actions, [devices.discover_local_hardware] * 2)
        self.assertGreaterEqual(keyboard.enumerations, 2)
        self.assertEqual(keyboard.events, [])  # Nothing was opened.
        self.assertEqual(keyboard.writes, [])

    def test_unplugging_refreshes_states_once_and_keeps_the_device(self):
        from tests.test_lorgar_keyboard import COLLECTIONS

        keyboard = self.fake_keyboard()
        controller = self.controller([STRIP_A, KEYBOARD])
        self.run_tasks(controller)
        controller.auto_scan_local_hardware()  # Baseline: present.
        controller.auto_scan_local_hardware()
        controller.refresh_pc_hardware_states.assert_not_called()

        keyboard.infos = []
        controller.auto_scan_local_hardware()
        controller.refresh_pc_hardware_states.assert_called_once()
        self.assertEqual(len(controller.devices), 2)  # Not removed.
        controller.auto_scan_local_hardware()
        controller.refresh_pc_hardware_states.assert_called_once()  # Unchanged: quiet.

        keyboard.infos = list(COLLECTIONS)
        controller.auto_scan_local_hardware()
        self.assertEqual(controller.refresh_pc_hardware_states.call_count, 2)
        self.assertEqual(keyboard.events, [])

    def test_startup_scan_runs_once_on_the_pc_page(self):
        window = self.window([STRIP_A])
        with patch.object(window.device_controller, "auto_scan_local_hardware", return_value=True) as scan:
            window.auto_scan_pc_on_startup()
        scan.assert_called_once_with()
        window = self.window([STRIP_A], hid_supported=False)
        with patch.object(window.device_controller, "auto_scan_local_hardware") as scan:
            window.auto_scan_pc_on_startup()
        scan.assert_not_called()


class RailTests(_QtCase):
    @staticmethod
    def keys(shell):
        return [shell.nav_list.item(row).data(Qt.ItemDataRole.UserRole) for row in range(shell.nav_list.count())]

    def test_pc_page_sits_after_music_and_before_draw(self):
        window = self.window([STRIP_A])
        shell = window.nav_shell
        self.assertEqual(self.keys(shell), ["devices", "monitor", "music", "pc", "draw"])
        item = shell.nav_list.item(3)
        self.assertEqual(item.toolTip(), "PC")
        self.assertEqual(item.data(NavRailItemDelegate.LABEL_ROLE), "PC")
        self.assertEqual(item.data(NavRailItemDelegate.SVG_ROLE), "pc.svg")
        self.assertTrue(shell.is_page_visible("pc"))  # Even before any PC hardware is found.
        self.assertIsInstance(window.pc_view, DevicesView)
        self.assertTrue(window.pc_view.is_pc_page())
        self.assertFalse(window.devices_view.is_pc_page())

    def test_pc_page_is_hidden_without_hid_support(self):
        window = self.window([BOARD], hid_supported=False)
        self.assertFalse(window.nav_shell.is_page_visible("pc"))

    def badge(self, window):
        shell = window.nav_shell
        return bool(shell.nav_list.item(3).data(NavRailItemDelegate.BADGE_ROLE))

    def test_new_pc_hardware_from_find_devices_badges_the_rail_until_opened(self):
        window = self.window([STRIP_A])
        controller = window.device_controller
        controller.device_search_started.emit()
        controller.devices = [dict(STRIP_A), dict(BOARD)]
        controller.device_search_finished.emit({})
        self.assertTrue(self.badge(window))
        window.nav_shell.set_current_by_key("pc")
        self.assertFalse(self.badge(window))

    def test_no_badge_for_known_hardware_or_while_on_the_pc_page(self):
        window = self.window([STRIP_A, BOARD])
        controller = window.device_controller
        controller.device_search_started.emit()
        controller.device_search_finished.emit({})
        self.assertFalse(self.badge(window))  # Already known.

        controller.devices = [dict(STRIP_A)]
        window.nav_shell.set_current_by_key("pc")
        controller.device_search_started.emit()
        controller.devices = [dict(STRIP_A), dict(BOARD)]
        controller.device_search_finished.emit({})
        self.assertFalse(self.badge(window))  # The user is looking at it.

    def test_new_lan_lights_do_not_badge_the_pc_page(self):
        window = self.window([STRIP_A])
        controller = window.device_controller
        controller.device_search_started.emit()
        controller.devices = [dict(STRIP_A), dict(STRIP_B)]
        controller.device_search_finished.emit({})
        self.assertFalse(self.badge(window))


if __name__ == "__main__":
    unittest.main()


class UnsupportedSavedDeviceTests(unittest.TestCase):
    """A saved device this version has no driver for is left out, not fatal."""

    STALE = {"device_id": "dynlight-046d-c08b", "name": "G502 HERO",
             "transport": "dynamic_lighting", "type": "dynamic_lighting"}

    def test_registry_knows_which_descriptors_it_can_drive(self):
        from lumisync.drivers.registry import is_supported_device

        for device in (
            {}, {"transport": "lan"}, {"transport": "tuya"}, {"transport": "ble"},
            {"transport": "tuya_cloud"}, {"transport": "usb", "type": "rgb_fusion2"},
            {"transport": "usb", "type": "logitech_hidpp"}, {"type": "lsc"},
        ):
            with self.subTest(device=device):
                self.assertTrue(is_supported_device(device))
        self.assertFalse(is_supported_device(self.STALE))
        self.assertFalse(is_supported_device({"transport": "from-the-future"}))
        self.assertFalse(is_supported_device(None))

    def test_settings_load_leaves_the_stale_device_out(self):
        from lumisync import devices

        board = {"device_id": "rgbfusion2-048d-5702", "transport": "usb", "type": "rgb_fusion2"}
        with self.assertLogs("lumisync_devices", level="WARNING") as logs:
            loaded = devices._normalize_settings(
                {"devices": [{"ip": "192.168.0.9", "model": "H6199"}, board, dict(self.STALE)],
                 "selectedDevice": 2}
            )
        self.assertEqual([d.get("transport") for d in loaded["devices"]][1:], ["usb"])
        self.assertEqual(len(loaded["devices"]), 2)
        self.assertLess(loaded["selectedDevice"], 2)
        self.assertIn("G502 HERO", logs.output[0])
