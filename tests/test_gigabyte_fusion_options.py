"""RGB Fusion 2 options: colour correction, per-zone layout, inspector, icons.

Hardware is never touched: adapters run against the recording fake HID
backend from :mod:`tests.test_gigabyte_fusion`.
"""

import os
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from lumisync.drivers import gigabyte_fusion as fusion
from lumisync.drivers import hid_feature, pool, registry
from lumisync.utils.colors import resample_colors_to_count
from tests.test_gigabyte_fusion import (
    RAW_DEVICE,
    USB_DEVICE,
    ZONED_DEVICE,
    FusionTestCase,
    UntouchableHid,
    packet,
)

SRGB = fusion.SRGB_TO_LINEAR
B550 = {**USB_DEVICE, "motherboard": "B550 AORUS ELITE"}
B550_NAMES = ("I/O cover", "LED_C1 header", "LED_CPU header", "PCIe accent",
              "LED_C2 header", "D_LED1 header", "D_LED2 header")
SEVEN = [(10 * i, 20 + i, 200 - i) for i in range(7)]


def corrected(color):
    """sRGB curve, then the white balance."""
    return tuple(round(SRGB[c] * gain) for c, gain in zip(color, fusion.WHITE_BALANCE))


class ColourCorrectionTableTests(unittest.TestCase):
    def test_golden_values_of_the_standard_srgb_transfer(self):
        self.assertEqual(len(SRGB), 256)
        golden = {0: 0, 1: 0, 6: 0, 7: 1, 50: 8, 64: 13, 100: 32, 127: 54, 128: 55,
                  187: 127, 188: 128, 200: 147, 254: 253, 255: 255}
        for value, expected in golden.items():
            with self.subTest(value=value):
                self.assertEqual(SRGB[value], expected)

    def test_the_table_is_the_piecewise_curve_not_a_bare_power(self):
        def piecewise(value):
            c = value / 255
            return round((c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4) * 255)

        self.assertEqual(SRGB, tuple(piecewise(v) for v in range(256)))
        self.assertTrue(all(a <= b for a, b in zip(SRGB, SRGB[1:])))
        # A plain 2.2 power curve differs in the dark end and the middle.
        power = tuple(round((v / 255) ** 2.2 * 255) for v in range(256))
        self.assertNotEqual(SRGB, power)

    def test_black_and_red_are_unchanged(self):
        for color in ((0, 0, 0), (255, 0, 0)):
            with self.subTest(color=color):
                self.assertEqual(fusion.correct_color(color), color)

    def test_white_balance_tames_green_and_blue(self):
        # Set by eye on the B550 AORUS ELITE: equal drive looked icy blue.
        self.assertEqual(fusion.WHITE_BALANCE, (1.0, 0.84, 0.59))
        self.assertEqual(fusion.correct_color((255, 255, 255)), (255, 214, 150))
        self.assertEqual(fusion.correct_color((0, 255, 0)), (0, 214, 0))
        self.assertEqual(fusion.correct_color((0, 0, 255)), (0, 0, 150))
        # The colours confirmed on the board.
        self.assertEqual(fusion.correct_color((255, 105, 180)), (255, 30, 68))
        self.assertEqual(fusion.correct_color((255, 128, 0)), (255, 46, 0))

    def test_brightness_is_applied_before_correction(self):
        self.assertEqual(fusion.wire_color((200, 100, 50), 50), corrected((100, 50, 25)))
        self.assertEqual(fusion.wire_color((200, 100, 50), 50), (32, 7, 1))
        # The other order would be a different colour.
        scaled_after = fusion.scale_color(fusion.correct_color((200, 100, 50)), 50)
        self.assertNotEqual(fusion.wire_color((200, 100, 50), 50), scaled_after)

    def test_off_switch_sends_srgb_unchanged(self):
        self.assertEqual(fusion.correct_color((128, 64, 1), fusion.COLOR_CORRECTION_NONE), (128, 64, 1))
        self.assertEqual(fusion.wire_color((200, 100, 50), 50, "none"), (100, 50, 25))

    def test_descriptor_field(self):
        self.assertEqual(fusion.color_correction(USB_DEVICE), "srgb")
        self.assertEqual(fusion.color_correction(None), "srgb")
        self.assertEqual(fusion.color_correction({"color_correction": "None"}), "none")
        self.assertEqual(fusion.color_correction({"color_correction": "bogus"}), "srgb")


class ColourCorrectionAdapterTests(FusionTestCase):
    def test_on_by_default(self):
        adapter = self.adapter(USB_DEVICE)
        adapter.set_color(128, 128, 128)
        adapter.set_color(255, 255, 255)
        adapter.set_color(255, 0, 0)
        self.assertEqual(self.frames(self.hid.reports[3:]), [(55, 46, 32), (255, 214, 150), (255, 0, 0)])

    def test_brightness_then_correction_on_the_wire(self):
        adapter = self.adapter(USB_DEVICE)
        adapter.set_color(200, 100, 50)
        adapter.set_brightness(50)
        self.assertEqual(self.frames(self.hid.reports[3:]), [corrected((200, 100, 50)), (32, 7, 1)])
        self.assertTrue(all(r[12] == 0xFF and r[13] == 0 for r in self.hid.reports[3:] if r[1] < 0x28))

    def test_remembered_state_stays_in_srgb_terms(self):
        adapter = self.adapter(USB_DEVICE)
        adapter.set_color(128, 64, 32)
        adapter.set_brightness(40)
        controller = adapter._controller()
        self.assertEqual(controller.color, (128, 64, 32))
        self.assertEqual(controller.brightness, 40)
        adapter.set_power(False)
        adapter.set_power(True)
        self.assertEqual(self.frames(self.hid.reports[3:])[-1], corrected(fusion.scale_color((128, 64, 32), 40)))

    def test_off_switch(self):
        adapter = self.adapter(RAW_DEVICE)
        adapter.set_color(128, 128, 128)
        self.assertEqual(self.frames(self.hid.reports[3:]), [(128, 128, 128)])

    def test_sync_frames_are_corrected_after_averaging_and_brightness(self):
        adapter = self.adapter(USB_DEVICE)
        adapter.set_brightness(50)
        adapter.set_segments([(200, 0, 0), (0, 0, 100)])
        self.assertEqual(self.frames(self.hid.reports[3:])[-1], corrected((50, 0, 25)))

    def test_toggle_changes_the_bytes_so_the_skip_lets_it_through(self):
        adapter = self.adapter(USB_DEVICE)
        adapter.set_color(128, 128, 128)
        adapter.update_device(dict(RAW_DEVICE))
        adapter.set_color(128, 128, 128)  # Same request, different bytes: sent.
        adapter.set_color(128, 128, 128)  # Same bytes again: skipped.
        adapter.update_device(dict(USB_DEVICE))
        adapter.set_color(128, 128, 128)
        self.assertEqual(self.frames(self.hid.reports[3:]), [(55, 46, 32), (128, 128, 128), (55, 46, 32)])

    def test_dark_values_that_round_to_black_are_skipped_as_black(self):
        adapter = self.adapter(USB_DEVICE)
        adapter.set_color(0, 0, 0)
        adapter.set_color(3, 3, 3)  # Corrects to (0, 0, 0): the same bytes.
        self.assertEqual(self.frames(self.hid.reports[3:]), [(0, 0, 0)])


class ZoneTableTests(unittest.TestCase):
    def test_verified_board(self):
        zones = fusion.zones_for(B550)
        self.assertEqual(tuple(z.name for z in zones), B550_NAMES)
        self.assertEqual(tuple(z.register for z in zones), tuple(range(7)))
        self.assertEqual(fusion.zone_names({"motherboard": "  b550 aorus elite "}), B550_NAMES)

    def test_other_boards_get_eight_generic_zones(self):
        for device in (USB_DEVICE, {"motherboard": "X570 AORUS MASTER"}, None):
            with self.subTest(device=device):
                zones = fusion.zones_for(device)
                self.assertEqual([z.name for z in zones], [f"Zone {i}" for i in range(1, 9)])
                self.assertEqual([z.register for z in zones], list(range(8)))

    def test_tables_only_use_zone_registers(self):
        for zones in (*fusion.BOARD_ZONES.values(), fusion.GENERIC_ZONES):
            registers = [z.register for z in zones]
            self.assertEqual(len(registers), len(set(registers)))
            self.assertTrue(all(0x20 + r in fusion.ALLOWED_COMMANDS for r in registers))

    def test_layout_field(self):
        self.assertEqual(fusion.layout(USB_DEVICE), "board")
        self.assertEqual(fusion.layout({"rgb_fusion_layout": "ZONES"}), "zones")
        self.assertEqual(fusion.layout({"rgb_fusion_layout": "per-led"}), "board")

    def test_zone_frame_leaves_unlisted_registers_black(self):
        frame = fusion.zone_frame(SEVEN, fusion.zones_for(B550))
        self.assertEqual(frame, (*SEVEN, (0, 0, 0)))
        self.assertEqual(fusion.board_frame((1, 2, 3)), ((1, 2, 3),) * 8)
        with self.assertRaises(ValueError):
            fusion.build_frame([(0, 0, 0)] * 7)


class StaticCapabilityTests(unittest.TestCase):
    def test_capabilities_follow_the_layout_without_hardware(self):
        cases = [
            (USB_DEVICE, 1),
            ({**USB_DEVICE, "rgb_fusion_layout": "board"}, 1),
            ({**B550, "rgb_fusion_layout": "zones"}, 7),
            ({**USB_DEVICE, "rgb_fusion_layout": "zones"}, 8),
        ]
        with patch.object(fusion, "_hid", UntouchableHid()), \
                patch.object(hid_feature, "_windows_api", side_effect=AssertionError("ctypes used")):
            for device, count in cases:
                with self.subTest(device=device):
                    cap = registry.capabilities_for_device(device)
                    self.assertEqual(cap.segment_count, count)
                    self.assertEqual(fusion.GigabyteFusionAdapter(dict(device)).capabilities, cap)
                    self.assertTrue(cap.supports_segments and cap.supports_streaming)
                    self.assertEqual((cap.transport, cap.max_update_hz), ("usb", 30.0))
                    self.assertFalse(cap.is_matrix)


class PerZoneAdapterTests(FusionTestCase):
    def test_each_zone_gets_its_colour_on_its_register_then_one_apply(self):
        adapter = self.adapter(ZONED_DEVICE)
        adapter.set_segments(list(SEVEN))
        frame = self.hid.reports[3:]
        self.assertEqual(len(frame), 9)
        for register, color in enumerate(SEVEN):
            self.assertEqual(frame[register], fusion.build_zone_static(register, color))
        self.assertEqual(frame[7], fusion.build_zone_static(7, (0, 0, 0)))  # Unused on this board.
        self.assertEqual(frame[8], packet("cc 28 ff"))

    def test_frames_are_resized_to_the_zone_count(self):
        adapter = self.adapter(ZONED_DEVICE)
        wide = [(i * 18, 255 - i * 18, 7) for i in range(14)]
        adapter.set_segments(wide)
        adapter.set_segments([(9, 8, 7)])
        frames = self.register_frames(self.hid.reports[3:])
        self.assertEqual(frames[0], (*resample_colors_to_count(wide, 7), (0, 0, 0)))
        self.assertEqual(frames[1], ((9, 8, 7),) * 7 + ((0, 0, 0),))

    def test_set_color_paints_every_zone(self):
        adapter = self.adapter(ZONED_DEVICE)
        adapter.set_color(1, 2, 3)
        adapter.set_power(False)
        self.assertEqual(self.register_frames(self.hid.reports[3:]),
                         [((1, 2, 3),) * 7 + ((0, 0, 0),), ((0, 0, 0),) * 8])

    def test_generic_board_uses_all_eight_registers(self):
        adapter = self.adapter({**RAW_DEVICE, "rgb_fusion_layout": "zones"})
        eight = [(i, i, i) for i in range(1, 9)]
        adapter.set_segments(eight)
        self.assertEqual(self.register_frames(self.hid.reports[3:]), [tuple(eight)])

    def test_brightness_and_correction_apply_per_zone(self):
        adapter = self.adapter({**ZONED_DEVICE, "color_correction": "srgb"})
        adapter.set_brightness(50)
        adapter.set_segments(list(SEVEN))
        expected = tuple(corrected(fusion.scale_color(c, 50)) for c in SEVEN) + ((0, 0, 0),)
        self.assertEqual(self.register_frames(self.hid.reports[3:])[-1], expected)

    def test_skip_compares_the_whole_frame(self):
        adapter = self.adapter(ZONED_DEVICE)
        adapter.set_segments(list(SEVEN))
        adapter.set_segments(list(SEVEN))
        changed = list(SEVEN)
        changed[4] = (1, 1, 1)
        adapter.set_segments(changed)
        frames = self.register_frames(self.hid.reports[3:])
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[1][4], (1, 1, 1))

    def test_every_method_stays_inside_the_allow_list(self):
        adapter = self.adapter(ZONED_DEVICE)
        adapter.begin_stream()
        adapter.set_power(True)
        adapter.set_segments(list(SEVEN))
        adapter.set_brightness(30)
        adapter.set_color(4, 5, 6)
        adapter.set_color_temperature(3000)
        adapter.reapply()
        adapter.set_power(False)
        commands = {report[1] for report in self.hid.reports}
        self.assertTrue(commands <= fusion.ALLOWED_COMMANDS, commands)
        self.assertFalse(commands & fusion.NEVER_SEND_COMMANDS)
        self.assertEqual(fusion.ALLOWED_COMMANDS, frozenset({0x60, 0x31, 0x32, 0x28, *range(0x20, 0x28)}))
        self.assertEqual(fusion.NEVER_SEND_COMMANDS, frozenset({0x33, 0x34, 0x47, 0x48, 0x5E}))

    def test_whole_board_mode_still_averages(self):
        adapter = self.adapter(RAW_DEVICE)
        adapter.set_segments([(200, 0, 0), (0, 0, 100)])
        self.assertEqual(self.frames(self.hid.reports[3:]), [(100, 0, 50)])


class LiveOptionChangeTests(FusionTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(pool.close, RAW_DEVICE)

    def test_layout_switch_on_a_live_pooled_adapter(self):
        board = pool.acquire(dict(B550, color_correction="none"))
        board.set_color(1, 2, 3)
        self.assertEqual(board.capabilities.segment_count, 1)
        zoned = pool.acquire(dict(ZONED_DEVICE))
        self.assertIs(zoned, board)  # Same adapter, same handle.
        self.assertEqual(zoned.capabilities.segment_count, 7)
        zoned.set_segments(list(SEVEN))
        pool.acquire(dict(B550, color_correction="none")).set_segments(list(SEVEN))
        self.assertEqual(self.hid.opened, 1)
        self.assertEqual(self.hid.closed, 0)
        frames = self.register_frames(self.hid.reports[3:])
        self.assertEqual(frames[0], ((1, 2, 3),) * 8)
        self.assertEqual(frames[1], (*SEVEN, (0, 0, 0)))
        average = fusion.average_color(SEVEN)
        self.assertEqual(frames[2], (average,) * 8)

    def test_refresh_reaches_an_adapter_a_sync_session_holds(self):
        held = pool.acquire(dict(B550, color_correction="none"))  # As a sync session would.
        self.assertTrue(pool.refresh(dict(ZONED_DEVICE)))
        self.assertEqual(held.capabilities.segment_count, 7)
        held.set_segments(list(SEVEN))
        self.assertEqual(self.register_frames(self.hid.reports[3:]), [(*SEVEN, (0, 0, 0))])

    def test_refresh_never_creates_or_opens_anything(self):
        with patch.object(fusion, "_hid", UntouchableHid()):
            self.assertFalse(pool.refresh(dict(ZONED_DEVICE)))
            self.assertFalse(pool.refresh({"transport": "lan", "mac": "AA"}))
        self.assertNotIn("usb:rgbfusion2-048d-5702", pool._pooled_adapters)

    def test_update_device_ignores_another_controller(self):
        adapter = self.adapter(RAW_DEVICE)
        adapter.update_device({**ZONED_DEVICE, "usb_product_id": 0x8297})
        self.assertEqual(adapter.capabilities.segment_count, 1)

    def test_reapply_only_resends_on_an_open_lit_board(self):
        adapter = self.adapter(USB_DEVICE)
        with patch.object(fusion, "_hid", UntouchableHid()):
            adapter.reapply()  # Never opened: nothing at all.
        adapter.set_color(128, 128, 128)
        adapter.update_device(dict(RAW_DEVICE))
        adapter.reapply()
        adapter.set_power(False)
        adapter.reapply()  # Off: stays off.
        self.assertEqual(self.frames(self.hid.reports[3:]), [(55, 46, 32), (128, 128, 128), (0, 0, 0)])


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()


class InspectorOptionTests(_QtCase):
    def inspector(self, device):
        from lumisync.gui.widgets.device_inspector import DeviceInspector

        inspector = DeviceInspector()
        self.addCleanup(inspector.deleteLater)
        inspector.set_device(0, dict(device), {}, primary=False)
        return inspector

    def test_options_show_for_usb_lighting_only(self):
        inspector = self.inspector(B550)
        self.assertFalse(inspector.usb_options_panel.isHidden())
        self.assertTrue(inspector.color_correction_switch.isChecked())
        self.assertEqual(inspector.layout_combo.currentData(), "board")
        self.assertEqual(inspector.layout_combo.itemText(1), "Per zone (7)")
        self.assertEqual(inspector.zones_value.text(), "Whole board · one colour")
        lan = {"mac": "fixture-lamp", "name": "Lamp", "model": "H619C", "transport": "lan", "ip": "192.0.2.5"}
        inspector.set_device(1, lan, {}, primary=False)
        self.assertTrue(inspector.usb_options_panel.isHidden())
        self.assertTrue(inspector.usb_options_label.isHidden())

    def test_controls_reflect_the_saved_options(self):
        inspector = self.inspector({**B550, "color_correction": "none", "rgb_fusion_layout": "zones"})
        self.assertFalse(inspector.color_correction_switch.isChecked())
        self.assertEqual(inspector.layout_combo.currentData(), "zones")
        self.assertEqual(inspector.zones_value.text(), "7 zones · per zone")
        self.assertEqual(inspector.layout_hint.text(), "7 zones, one colour each")
        self.assertIn("LED_CPU header", inspector.layout_combo.toolTip())

    def test_changes_are_emitted_and_set_device_emits_nothing(self):
        inspector = self.inspector(B550)
        corrections, layouts = [], []
        inspector.color_correction_changed.connect(lambda *args: corrections.append(args))
        inspector.lighting_layout_changed.connect(lambda *args: layouts.append(args))
        inspector.set_device(0, {**B550, "rgb_fusion_layout": "zones", "color_correction": "none"}, {},
                             primary=False)
        self.assertEqual((corrections, layouts), ([], []))
        inspector.color_correction_switch.setChecked(True)
        inspector.layout_combo.setCurrentIndex(0)
        self.assertEqual(corrections, [(0, True)])
        self.assertEqual(layouts, [(0, "board")])

    def test_summaries_show_the_zone_count_in_per_zone_mode(self):
        from lumisync.gui.widgets.device_card import device_subline
        from lumisync.gui.widgets.device_chip import light_summary

        zoned = {**B550, "rgb_fusion_layout": "zones"}
        self.assertEqual(light_summary(zoned), "Motherboard lighting · USB · 7 zones")
        self.assertEqual(device_subline(zoned), "Motherboard lighting · USB · 7 zones")
        self.assertEqual(light_summary(B550), "Motherboard lighting · USB")
        self.assertEqual(device_subline(B550), "Motherboard lighting · USB")


class ControllerOptionTests(_QtCase):
    def controller(self, device=B550):
        from lumisync.gui.controllers.device_controller import DeviceController

        with patch("lumisync.gui.controllers.device_controller.devices.get_data",
                   return_value={"devices": [], "selectedDevice": 0}):
            controller = DeviceController()
        self.addCleanup(controller.shutdown)
        controller.devices = [dict(device)]
        self.write = Mock()
        patcher = patch.object(controller._store, "write", self.write)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.notices = []
        controller.notice.connect(lambda message, kind: self.notices.append((message, kind)))
        return controller

    def saved_device(self):
        return dict(self.write.call_args.args[0]["devices"][0])

    def test_colour_correction_is_saved_and_the_default_is_stored_as_absent(self):
        controller = self.controller()
        with patch.object(pool, "refresh", return_value=False) as refresh:
            self.assertTrue(controller.set_color_correction_at(0, False))
            self.assertEqual(self.saved_device()["color_correction"], "none")
            self.assertEqual(controller.devices[0]["color_correction"], "none")
            self.assertTrue(controller.set_color_correction_at(0, True))
        self.assertNotIn("color_correction", self.saved_device())
        self.assertEqual(refresh.call_count, 2)
        self.assertEqual(refresh.call_args.args[0]["device_id"], USB_DEVICE["device_id"])

    def test_layout_is_saved_and_reaches_the_pooled_adapter(self):
        controller = self.controller()
        updated = []
        with patch.object(pool, "refresh", side_effect=lambda d: updated.append(dict(d)) or True), \
                patch("lumisync.gui.controllers.device_controller.start_task") as started:
            self.assertTrue(controller.set_rgb_fusion_layout_at(0, "zones"))
        self.assertEqual(self.saved_device()["rgb_fusion_layout"], "zones")
        self.assertEqual(updated[0]["rgb_fusion_layout"], "zones")
        started.assert_called_once()  # The idle board re-shows its colour.
        self.assertEqual(controller.get_capabilities_at(0).segment_count, 7)
        with patch.object(pool, "refresh", return_value=False):
            self.assertTrue(controller.set_rgb_fusion_layout_at(0, "board"))
        self.assertNotIn("rgb_fusion_layout", self.saved_device())
        self.assertFalse(controller.set_rgb_fusion_layout_at(0, "diagonal"))

    def test_layout_waits_for_a_running_output_but_correction_does_not(self):
        controller = self.controller()
        with patch.object(controller, "is_device_busy", return_value=True), \
                patch.object(pool, "refresh", return_value=True), \
                patch("lumisync.gui.controllers.device_controller.start_task") as started:
            self.assertFalse(controller.set_rgb_fusion_layout_at(0, "zones"))
            self.assertTrue(controller.set_color_correction_at(0, False))
        started.assert_not_called()  # Sync's next frame carries the change.
        self.assertEqual(self.write.call_count, 1)
        self.assertNotIn("rgb_fusion_layout", controller.devices[0])

    def test_other_devices_are_refused(self):
        controller = self.controller({"mac": "fixture-lamp", "transport": "lan", "ip": "192.0.2.5"})
        self.assertFalse(controller.set_color_correction_at(0, False))
        self.assertFalse(controller.set_rgb_fusion_layout_at(0, "zones"))
        self.write.assert_not_called()

    def test_a_failed_save_changes_nothing(self):
        controller = self.controller()
        self.write.side_effect = OSError("fixture: disk full")
        with patch.object(pool, "refresh") as refresh:
            self.assertFalse(controller.set_rgb_fusion_layout_at(0, "zones"))
        refresh.assert_not_called()
        self.assertNotIn("rgb_fusion_layout", controller.devices[0])

    def test_reapply_task_goes_through_the_pool(self):
        controller = self.controller()
        tasks = []
        adapter = Mock()
        with patch.object(pool, "refresh", return_value=True), \
                patch("lumisync.gui.controllers.device_controller.start_task",
                      side_effect=lambda action, done: tasks.append((action, done))):
            controller.set_color_correction_at(0, False)
        with patch.object(pool, "acquire", return_value=adapter) as acquire:
            tasks[0][0]()
        adapter.reapply.assert_called_once_with()
        self.assertEqual(acquire.call_args.args[0]["color_correction"], "none")


class DevicesViewOptionTests(_QtCase):
    def test_inspector_switch_saves_through_the_controller(self):
        from lumisync.gui.controllers.device_controller import DeviceController
        from lumisync.gui.views.devices_view import DevicesView

        with patch("lumisync.gui.controllers.device_controller.devices.get_data",
                   return_value={"devices": [], "selectedDevice": 0}):
            controller = DeviceController()
        self.addCleanup(controller.shutdown)
        controller.devices = [dict(B550)]
        write = Mock()
        with patch.object(controller, "get_groups", return_value=[]), \
                patch.object(controller, "refresh_device_states"), \
                patch.object(controller, "refresh_pc_hardware_states"), \
                patch.object(controller._store, "write", write), \
                patch.object(pool, "refresh", return_value=False):
            view = DevicesView(controller, scope="pc")  # USB lighting lives on the PC page.
            self.addCleanup(view.deleteLater)
            view.resize(1100, 760)
            view._apply_device_list()
            view._open_inspector(0)
            view.inspector.color_correction_switch.setChecked(False)
            view.inspector.layout_combo.setCurrentIndex(1)
            self.app.processEvents()
            view._apply_device_list()
        saved = write.call_args.args[0]["devices"][0]
        self.assertEqual((saved["color_correction"], saved["rgb_fusion_layout"]), ("none", "zones"))
        self.assertEqual(view.inspector.zones_value.text(), "7 zones · per zone")

    def test_a_refused_layout_change_shows_the_saved_layout_again(self):
        from lumisync.gui.controllers.device_controller import DeviceController
        from lumisync.gui.views.devices_view import DevicesView

        with patch("lumisync.gui.controllers.device_controller.devices.get_data",
                   return_value={"devices": [], "selectedDevice": 0}):
            controller = DeviceController()
        self.addCleanup(controller.shutdown)
        controller.devices = [dict(B550)]
        with patch.object(controller, "get_groups", return_value=[]), \
                patch.object(controller, "refresh_device_states"), \
                patch.object(controller, "refresh_pc_hardware_states"), \
                patch.object(controller, "is_device_busy", return_value=True):
            view = DevicesView(controller, scope="pc")
            self.addCleanup(view.deleteLater)
            view._apply_device_list()
            view._open_inspector(0)
            view.inspector.layout_combo.setCurrentIndex(1)
        self.assertEqual(view.inspector.layout_combo.currentData(), "board")


class IconTests(_QtCase):
    ICON_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "lumisync", "gui", "resources", "icons")

    def test_motherboard_kind_uses_the_motherboard_icon(self):
        from lumisync.gui.resources.icons import _ICON_FILES, IconKey
        from lumisync.gui.utils.device_identity import device_identity
        from lumisync.gui.widgets.device_chip import light_icon

        self.assertEqual(_ICON_FILES[IconKey.MOTHERBOARD], "motherboard.svg")
        self.assertEqual(_ICON_FILES[IconKey.LED_STRIP], "led-strip.svg")
        self.assertEqual(device_identity(USB_DEVICE)[1], IconKey.MOTHERBOARD)
        self.assertEqual(light_icon(USB_DEVICE), "motherboard.svg")
        self.assertEqual(device_identity({"device_kind": "led_strip"})[1], IconKey.LED_STRIP)

    def test_both_svgs_follow_the_house_style_and_render(self):
        from PySide6.QtGui import QColor
        from PySide6.QtSvg import QSvgRenderer

        from lumisync.gui.resources import ResourceManager
        from lumisync.gui.resources.icons import IconKey, tinted_pixmap

        for key, name in ((IconKey.LED_STRIP, "led-strip.svg"), (IconKey.MOTHERBOARD, "motherboard.svg")):
            with self.subTest(icon=name):
                path = os.path.join(self.ICON_DIR, name)
                root = ET.parse(path).getroot()
                self.assertEqual(root.get("viewBox"), "0 0 32 32")
                self.assertEqual((root.get("fill"), root.get("stroke"), root.get("stroke-width")),
                                 ("none", "currentColor", "2"))
                self.assertEqual((root.get("stroke-linecap"), root.get("stroke-linejoin")), ("round", "round"))
                self.assertIsNotNone(ResourceManager.get_icon_path(name))
                self.assertTrue(QSvgRenderer(path).isValid())
                image = tinted_pixmap(key, QColor("#FFFFFF"), 40).toImage()
                lit = sum(1 for x in range(40) for y in range(40) if image.pixelColor(x, y).alpha() > 128)
                self.assertGreater(lit, 60)

    def test_icons_ship_with_the_package_and_the_frozen_app(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "pyproject.toml"), encoding="utf-8") as handle:
            self.assertIn('"icons/*.svg"', handle.read())
        with open(os.path.join(root, "packaging", "pyinstaller", "lumisync_onedir.spec"), encoding="utf-8") as handle:
            self.assertIn('"lumisync" / "gui" / "resources" / "icons"', handle.read())


if __name__ == "__main__":
    unittest.main()
