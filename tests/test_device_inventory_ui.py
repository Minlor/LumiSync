"""Inventory geometry and visible-device refresh behavior, with no vendor I/O."""

import os
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QPoint, QThread, QTimer, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from lumisync.accounts.tuya import normalize_devices
from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.resources.icons import IconKey
from lumisync.gui.theme import apply_theme
from lumisync.gui.utils.device_identity import device_identity
from lumisync.gui.views.devices_view import DevicesView
from lumisync.gui.widgets.device_energy import DeviceEnergy, READINGS_REFRESH_MS
from lumisync.gui.widgets.device_inspector import DeviceInspector


def inventory_fixtures():
    relay = {"switch_1": {"type": "bool", "dp_id": 1}}
    meter = {"cur_power": {"dp_id": 19, "values": {"unit": "W", "scale": 1}},
             "cur_voltage": {"dp_id": 20, "values": {"unit": "V", "scale": 1}},
             "cur_current": {"dp_id": 18, "values": {"unit": "mA", "scale": 0}},
             "add_ele": {"dp_id": 17, "values": {"unit": "kWh", "scale": 3}}}
    plugs = []
    for index in range(3):
        plug = normalize_devices({"id": f"fixture-plug-{index}", "category": "cz", "name": f"Fixture smart plug {index + 1}"},
                                 relay, status_functions=meter)[0]
        plug.update(account_id="fixture-account", vendor_account="lsc")
        plugs.append(plug)
    switch = normalize_devices({"id": "fixture-switch", "category": "kg", "name": "Fixture wall switch"}, relay)[0]
    return [{"mac": "fixture-strip", "name": "Fixture LED strip", "model": "H619C", "sku": "H619C", "transport": "lan", "ip": "192.0.2.1"},
            {"mac": "fixture-matrix", "name": "Fixture matrix", "transport": "ble", "ble_address": "AA:BB:CC:DD:EE:FF", "matrix_size": "32x32"},
            switch, *plugs]


class DeviceInventoryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if os.name == "nt":
            for name in ("segoeui.ttf", "segoeuib.ttf"):
                QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + name)

    def setUp(self):
        self.widgets = []
        self.previous_style, self.previous_font = self.app.styleSheet(), self.app.font()
        self.app.setFont(QFont("Segoe UI", 10))
        apply_theme(self.app)
        self.app.setStyleSheet(self.app.styleSheet().replace(
            '"Segoe UI Variable", "Inter", "Segoe UI", sans-serif', '"Segoe UI"'))

    def tearDown(self):
        for widget in self.widgets:
            widget.close()
            widget.deleteLater()
        self.app.processEvents()
        self.app.setStyleSheet(self.previous_style)
        self.app.setFont(self.previous_font)

    def view(self, width, height):
        controller = Mock()
        controller.devices = inventory_fixtures()
        controller.selected_device_index = 0
        controller.get_groups.return_value = []
        controller.get_device_state_at.return_value = {"online": True, "status_source": "confirmed", "power_on": True,
                                                       "brightness": 65, "power_w": 215.1, "voltage_v": 233.8,
                                                       "current_a": 1.023, "last_seen": time.time()}
        view = DevicesView(controller)
        self.widgets.append(view)
        view.resize(width, height)
        view.show()
        QTest.qWait(30)
        return view, controller

    def assert_controls_fit(self, view):
        for card in view._cards:
            for control in (card.power_button, card.type_label, card.brightness_slider):
                if not control.isVisible():
                    continue
                origin = control.mapTo(card, QPoint(0, 0))
                self.assertGreaterEqual(origin.x(), 0)
                self.assertGreaterEqual(origin.y(), 0)
                self.assertLessEqual(origin.x() + control.width(), card.width())
                self.assertLessEqual(origin.y() + control.height(), card.height())
        self.assertEqual(view.scroll.horizontalScrollBar().maximum(), 0)

    def test_six_devices_fill_three_columns_without_basic_controls_scrolling(self):
        view, _ = self.view(1055, 760)  # 1143 px app minus its navigation rail.
        cards = view._cards
        self.assertEqual(len({card.y() for card in cards}), 2)
        self.assertEqual(len({card.x() for card in cards}), 3)
        self.assertLessEqual(max(card.width() for card in cards) - min(card.width() for card in cards), 1)
        self.assertEqual(cards[2].geometry().right(), view.scroll.viewport().width() - 1)
        self.assertEqual(view.scroll.verticalScrollBar().maximum(), 0)
        self.assert_controls_fit(view)

    def test_inspector_keeps_the_inventory_and_two_columns_at_the_reported_window_sizes(self):
        view, _ = self.view(1055, 760)
        for width in (1032, 1055, 1209, 1512):
            view.resize(width, 760)
            view._open_inspector(4)
            QTest.qWait(35)
            self.assertTrue(view.main_column.isVisible())
            self.assertTrue(view.inspector_scroll.isVisible())
            self.assertGreaterEqual(len({card.x() for card in view._cards}), 2)
            self.assert_controls_fit(view)
            self.assertEqual(view.inspector_scroll.horizontalScrollBar().maximum(), 0)
        # Narrow views deliberately give the inspector enough room.
        view.resize(712, 520)
        QTest.qWait(20)
        self.assertTrue(view.main_column.isHidden())
        self.assertTrue(view.inspector.energy._poll.isActive())
        view.inspector.close_button.click()
        QTest.qWait(20)
        self.assertTrue(view.main_column.isVisible())
        self.assertFalse(view.inspector.energy._poll.isActive())

    def test_opening_and_closing_an_inspector_does_not_move_page_text_or_buttons(self):
        view, _ = self.view(1032, 760)
        actions = [view.find_devices_button, view.add_button, view.accounts_button,
                   view.summary_label, view.group_mode_button]
        actions.extend(label for label in view.findChildren(QLabel)
                       if label.text() == "Devices" or label.text().startswith("Control your lights"))
        before = [widget.mapTo(view, QPoint(0, 0)) for widget in actions]
        for index in (4, 0, 2):
            view._open_inspector(index)
            QTest.qWait(30)
            self.assertEqual([widget.mapTo(view, QPoint(0, 0)) for widget in actions], before)
            self.assertEqual(view.scroll.mapTo(view, QPoint(0, 0)).y(),
                             view.inspector_scroll.mapTo(view, QPoint(0, 0)).y())
        view._close_inspector()
        QTest.qWait(30)
        self.assertEqual([widget.mapTo(view, QPoint(0, 0)) for widget in actions], before)

    def test_six_devices_and_meter_controls_fit_the_users_1120_px_window(self):
        view, _ = self.view(1032, 760)
        view._open_inspector(4)
        QTest.qWait(30)
        self.assertEqual(len({card.x() for card in view._cards}), 2)
        self.assertEqual(view.scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(view.inspector_scroll.verticalScrollBar().maximum(), 0)
        self.assertLessEqual(abs(view._cards[-1].geometry().bottom() - view.scroll.viewport().rect().bottom()), 2)

    def test_readback_does_not_move_brightness_while_the_user_is_dragging(self):
        view, _ = self.view(1032, 760)
        view._open_inspector(0)
        for widget, slider, timer in ((view._cards[0], view._cards[0].brightness_slider, view._cards[0]._brightness_timer),
                                      (view.inspector, view.inspector.brightness_slider, view.inspector._brightness_timer)):
            slider.setSliderDown(True)
            slider.setValue(73)
            widget.set_state({"brightness": 15})
            self.assertEqual(slider.value(), 73)
            slider.setSliderDown(False)
            timer.stop()
            widget.set_state({"brightness": 74})
            self.assertEqual(slider.value(), 74)

    def test_group_action_uses_the_first_toolbar_row_when_it_fits(self):
        view, _ = self.view(1209, 760)
        view._open_inspector(4)
        QTest.qWait(30)
        self.assertEqual(view.group_mode_button.y(), view.find_devices_button.y())

    def test_wide_inventory_balances_rows_without_adding_extra_scrolling(self):
        view, _ = self.view(1512, 868)
        self.assertEqual(len({card.x() for card in view._cards}), 3)
        self.assertEqual(len({card.y() for card in view._cards}), 2)
        self.assertEqual(view._cards[-1].geometry().right(), view.scroll.viewport().width() - 1)
        self.assertEqual(view.scroll.verticalScrollBar().maximum(), 0)

    def test_short_switch_inspector_fits_its_content_and_expands_for_details(self):
        view, _ = self.view(1055, 760)
        view._open_inspector(2)
        QTest.qWait(30)
        compact_height = view.inspector.height()
        self.assertLess(compact_height, view.inspector_scroll.viewport().height() - 100)
        view.inspector.details_toggle.click()
        QTest.qWait(30)
        self.assertGreater(view.inspector.height(), compact_height)
        self.assertEqual(view.inspector_scroll.horizontalScrollBar().maximum(), 0)
        view.inspector.details_toggle.click()
        QTest.qWait(30)
        self.assertEqual(view.inspector.height(), compact_height)

    def test_short_side_inspector_leaves_space_for_its_vertical_scrollbar(self):
        view, _ = self.view(1055, 548)
        view._open_inspector(4)
        panel = view.inspector.energy
        month = panel.month.currentData()
        panel._show_history({"month": month, "total_kwh": 2.14, "reported_days": 1, "expected_days": 8,
                             "days": [{"date": month + "-01", "energy_kwh": 2.14}]})
        QTest.qWait(40)
        self.assertTrue(view.main_column.isHidden())
        self.assertGreater(view.inspector_scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(view.inspector_scroll.horizontalScrollBar().maximum(), 0)
        viewport = view.inspector_scroll.viewport()
        for control in (view.inspector.power_button, panel.refresh_button, panel.load_button):
            corner = control.mapTo(viewport, control.rect().bottomRight())
            self.assertTrue(viewport.rect().contains(corner), control.accessibleName() or control.text())

    def test_types_use_hardware_metadata_and_card_keyboard_opens_the_inspector(self):
        view, _ = self.view(1055, 760)
        self.assertEqual([card.type_label.text() for card in view._cards],
                         ["LED strip", "Matrix panel", "Light switch", "Smart plug", "Smart plug", "Smart plug"])
        self.assertEqual(len({card._type_icon for card in view._cards}), 4)
        for card in view._cards:
            self.assertFalse(card.type_icon.pixmap().isNull())
            self.assertIn(card.type_label.text(), card.accessibleName())
        card = view._cards[2]
        card.setFocus()
        QTest.keyClick(card, Qt.Key.Key_Return)
        self.assertEqual(view._inspected_index, 2)
        self.assertEqual(view.inspector.type_label.text(), "Light switch")
        self.assertTrue(view.inspector.brightness_controls.isHidden())
        self.assertEqual(device_identity({"model": "Unknown", "name": "My smart plug LED strip"}), ("Light", IconKey.LIGHT_BULB))

    def test_meter_requests_immediately_and_repeatedly_without_metadata_postponing_it(self):
        panel = DeviceEnergy()
        self.widgets.append(panel)
        requests = []
        panel.refresh_requested.connect(lambda: requests.append(True))
        plug = inventory_fixtures()[3]
        state = {"power_w": 215.1, "online": True, "last_seen": time.time()}
        panel.set_device(plug, state)
        panel.show()
        self.app.processEvents()
        self.assertTrue(requests)
        self.assertEqual(panel._poll.interval(), READINGS_REFRESH_MS)
        self.assertEqual(READINGS_REFRESH_MS, 5000)
        panel._poll.setInterval(100)
        requests.clear()
        for _ in range(4):
            QTest.qWait(20)
            panel.set_device(plug, state)
        QTest.qWait(50)
        self.assertTrue(requests, "Repeated metadata updates must not restart the polling countdown")
        panel.set_state({**state, "power_w": 218.4})
        self.assertEqual(panel.values["power_w"].text(), "218.4 W")
        self.assertIn("Checked", panel.readings_note.text())
        panel.hide()
        requests.clear()
        QTest.qWait(130)
        self.assertFalse(requests)
        self.assertFalse(panel._poll.isActive())
        panel.show()
        self.app.processEvents()
        self.assertTrue(requests)

    def test_offline_status_does_not_claim_to_be_permanently_refreshing(self):
        view, _ = self.view(1055, 760)
        state = {"status_source": "offline", "online": False, "stale": True, "power_w": 215.1}
        view._on_device_state_updated(4, state)
        self.assertEqual(view._cards[4].state_detail_label.text(), "Offline")
        self.assertEqual(DeviceInspector._status_copy(state)[0], "Offline")

    def test_queued_targeted_reads_coalesce_and_follow_identity_after_reordering(self):
        with patch.object(DeviceController, "_init_devices"):
            controller = DeviceController()
        first, second, third = inventory_fixtures()[3:]
        controller.devices = [first, second, third]
        with patch.object(controller, "_status_running", return_value=True):
            for _ in range(4):
                controller.refresh_device_state_at(1)
            controller.refresh_device_states()
        self.assertEqual(len(controller._refresh_keys_pending), 1)
        controller.devices = [third, first, second]
        with patch("lumisync.gui.controllers.device_controller.QTimer.singleShot") as later:
            controller._clear_status_refs()
        with patch.object(controller, "_start_status_refresh") as started:
            later.call_args.args[1]()
        started.assert_called_once_with([2, 0, 1])
        self.assertFalse(controller._refresh_keys_pending)
        self.assertFalse(controller._refresh_all_pending)

    def test_queued_read_for_removed_device_does_not_query_its_replacement(self):
        with patch.object(DeviceController, "_init_devices"):
            controller = DeviceController()
        controller.devices = inventory_fixtures()[3:]
        with patch.object(controller, "_status_running", return_value=True):
            controller.refresh_device_state_at(0)
        controller.devices.pop(0)
        with patch.object(controller, "_start_status_refresh") as started:
            controller._drain_status_refresh()
        started.assert_called_once_with([])

    def test_open_meter_updates_through_real_background_workers_and_stops_when_hidden(self):
        def wait(milliseconds):
            # Use the production event loop so Python workers can make
            # progress reliably in the Windows test environment.
            loop = QEventLoop()
            QTimer.singleShot(milliseconds, loop.quit)
            loop.exec()

        with patch.object(DeviceController, "_init_devices"):
            controller = DeviceController()
        controller.devices = [inventory_fixtures()[3]]
        controller.get_groups = Mock(return_value=[])
        reads, delivered_on = [], []
        controller.device_state_updated.connect(lambda *_args: delivered_on.append(QThread.currentThread()))
        adapter = Mock()

        def query():
            reads.append(QThread.currentThread())
            return {"power_w": float(200 + len(reads)), "voltage_v": 230.0, "online": True}

        adapter.query_status.side_effect = query
        with patch("lumisync.gui.controllers.device_controller.pool.acquire", return_value=adapter), \
                patch("lumisync.gui.controllers.device_controller.pool.is_pooled", return_value=False):
            view = DevicesView(controller)
            self.widgets.append(view)
            view.resize(1055, 760)
            view.show()
            view._open_inspector(0)
            view.inspector.energy._poll.setInterval(250)
            try:
                for _ in range(100):
                    wait(10)
                    if len(reads) >= 3 and view.inspector.energy.values["power_w"].text() == f"{200 + len(reads):.1f} W":
                        break
                self.assertGreaterEqual(len(reads), 3)
                self.assertEqual(view.inspector.energy.values["power_w"].text(), f"{200 + len(reads):.1f} W")
                self.assertTrue(all(thread is not self.app.thread() for thread in reads))
                self.assertTrue(all(thread is self.app.thread() for thread in delivered_on))
                view.hide()
                wait(80)  # Let any already queued read finish.
                count = len(reads)
                wait(300)
                self.assertEqual(len(reads), count)
            finally:
                view.hide()
                controller.shutdown()
                for _ in range(100):
                    if controller.status_thread is None:
                        break
                    wait(10)
