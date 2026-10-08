"""Capability boundaries and electrical metering, without hardware commands."""

from datetime import date
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from lumisync.accounts.errors import AccountError
from lumisync.accounts.tuya import decode_status, normalize_devices
from lumisync.accounts.tuya_energy import (
    decode_energy_history, decode_metering, energy_history_spec, meter_fields, month_bounds,
)
from lumisync.drivers.cloud import CloudLightAdapter
from lumisync.drivers.tuya_lan import TuyaLightAdapter
from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.widgets.device_card import DeviceCard, format_device_output
from lumisync.gui.widgets.device_energy import DeviceEnergy
from lumisync.gui.widgets.device_inspector import DeviceInspector


METER = {
    code: {"dp_id": dp, "type": "value", "values": {"unit": unit, "scale": scale}}
    for code, dp, unit, scale in (("cur_power", 19, "W", 1), ("cur_voltage", 20, "V", 1),
                                 ("cur_current", 18, "mA", 0), ("add_ele", 17, "kWh", 3))
}


def plug(**extra):
    result = normalize_devices({"id": "socket", "name": "Desk plug", "category": "cz"},
                               {"switch_1": {"type": "bool", "dp_id": 1}}, status_functions=METER)[0]
    return {**result, "account_id": "owner", "vendor_account": "lsc", **extra}


class MeteringTests(unittest.TestCase):
    def test_plugs_and_power_strips_keep_only_their_own_power_controls(self):
        for category in ("cz", "wf_ble_cz", "pc"):
            raw = {"id": "socket", "category": category}
            functions = {"switch_2": {"type": "bool", "dp_id": 2}, "switch_1": {"type": "bool", "dp_id": 1}}
            devices = normalize_devices(raw, functions)
            self.assertEqual([d["dp_map"]["power"] for d in devices], [1, 2])
            self.assertEqual(len({d["mac"] for d in devices}), 2)
            for device in devices:
                self.assertEqual(device["device_kind"], "smart_plug")
                self.assertIn("Outlet", device["name"])
                for adapter in (CloudLightAdapter(device), TuyaLightAdapter(device)):
                    cap = adapter.capabilities
                    self.assertTrue(cap.supports_power)
                    self.assertFalse(cap.supports_brightness)
                    self.assertFalse(cap.supports_color)
                    self.assertFalse(cap.supports_white)
                    self.assertFalse(cap.supports_streaming)

    def test_metering_uses_reported_scales_and_units_and_preserves_zero(self):
        readings = decode_status(plug(), {"switch_1": False, "cur_power": 0, "cur_voltage": 2309, "cur_current": 560, "add_ele": 50}, True)
        self.assertEqual(readings, {"online": True, "power_on": False, "power_w": 0, "voltage_v": 230.9, "current_a": 0.56})
        self.assertNotIn("energy_kwh", meter_fields(plug()))
        self.assertIsNotNone(energy_history_spec(plug()))
        custom = {"tuya_status_functions": {
            "cur_power": {"values": '{"unit":"kW","scale":3}'},
            "total_forward_energy": {"values": {"unit": "Wh", "scale": 1}},
        }}
        self.assertEqual(decode_metering(custom, {"cur_power": 123, "total_forward_energy": 12500}), {"power_w": 123, "energy_kwh": 1.25})

    def test_missing_and_invalid_metering_clear_values_without_becoming_zero(self):
        device = plug()
        for value in (None, True, -1, "bad", float("nan"), float("inf")):
            with self.subTest(value=value):
                self.assertIsNone(decode_metering(device, {"cur_power": value})["power_w"])
        invalid_scale = {"tuya_status_functions": {"cur_power": {"values": {"scale": "bad"}}}}
        self.assertIsNone(decode_metering(invalid_scale, {"cur_power": 123})["power_w"])

    def test_local_readings_use_the_same_schema_without_writing_metering_dps(self):
        adapter = TuyaLightAdapter(plug(transport="tuya", ip="192.0.2.1"))
        handle = adapter._dev = Mock()
        handle.status.return_value = {"dps": {"1": True, "18": 500, "19": 1234, "20": 2310}}
        self.assertEqual(adapter.query_status(), {"online": True, "power_on": True, "power_w": 123.4, "voltage_v": 231, "current_a": 0.5})
        handle.set_value.return_value = {"dps": {"1": False}}
        adapter.set_power(False)
        handle.set_value.assert_called_once_with(1, False)
        with self.assertRaises(RuntimeError):
            adapter.set_brightness(20)
        handle.set_value.assert_called_once()
        adapter.close()

    def test_month_range_handles_leap_year_and_month_to_date(self):
        self.assertEqual(month_bounds("2024-02", today=date(2024, 3, 1)), (date(2024, 2, 1), date(2024, 2, 29)))
        self.assertEqual(month_bounds("2026-10", today=date(2026, 10, 8)), (date(2026, 10, 1), date(2026, 10, 8)))
        for month in ("bad", "2026-13", "2026-11"):
            with self.assertRaises(AccountError):
                month_bounds(month, today=date(2026, 10, 8))

    def test_statistics_keep_vendor_scaled_units_and_missing_day_coverage(self):
        raw = {"result": {"20261001": "1.250", "20261002": "#", "20261004": "0", "20260930": "9999"}}
        result = decode_energy_history(raw, "2026-10", METER["add_ele"], today=date(2026, 10, 4))
        self.assertEqual(result["total_kwh"], 1.25)
        self.assertEqual(result["reported_days"], 2)
        self.assertEqual(result["expected_days"], 4)
        self.assertEqual([d["energy_kwh"] for d in result["days"]], [1.25, None, None, 0])
        empty = decode_energy_history({}, "2026-10", METER["add_ele"], today=date(2026, 10, 4))
        self.assertIsNone(empty["total_kwh"])
        self.assertEqual(empty["reported_days"], 0)
        watt_hours = decode_energy_history({"20261001": "1250"}, "2026-10", {"values": {"unit": "Wh"}}, today=date(2026, 10, 1))
        self.assertEqual(watt_hours["total_kwh"], 1.25)
        for raw in (None, [], {"result": []}):
            with self.assertRaises(AccountError):
                decode_energy_history(raw, "2026-10", METER["add_ele"])

    def test_cumulative_counters_are_not_summed_as_monthly_energy(self):
        device = {"tuya_status_functions": {"total_forward_energy": {"dp_id": 17}}}
        self.assertIsNone(energy_history_spec(device))


class DeviceCapabilityUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_switch_hides_lighting_and_stale_colour_state(self):
        device = plug(tuya_status_functions={})
        state = {"power_on": True, "brightness": 100, "color": (255, 0, 0), "color_temp": 4000, "status_source": "confirmed"}
        inspector = DeviceInspector()
        inspector.set_device(0, device, state, primary=False)
        self.assertFalse(inspector.brightness_slider.isVisibleTo(inspector))
        self.assertFalse(inspector.color_button.isVisibleTo(inspector))
        self.assertFalse(inspector.temperature_slider.isVisibleTo(inspector))
        self.assertTrue(inspector.controls_panel.isHidden())
        self.assertTrue(inspector.default_button.isHidden())
        self.assertTrue(inspector.zones_button.isHidden())
        self.assertEqual(format_device_output(state, CloudLightAdapter(device).capabilities), "Showing · On")
        with patch("lumisync.gui.widgets.device_inspector.QColorDialog.getColor") as picker:
            inspector._pick_color()
        picker.assert_not_called()
        inspector.deleteLater()

    def test_reused_inspector_restores_only_supported_controls_and_cancels_pending_sliders(self):
        inspector = DeviceInspector()
        light = {"transport": "tuya_cloud", "tuya_functions": {"switch_led": {}, "bright_value_v2": {}, "temp_value_v2": {}}}
        inspector.set_device(0, light, {}, primary=False)
        self.assertTrue(inspector.brightness_slider.isVisibleTo(inspector))
        self.assertTrue(inspector.temperature_slider.isVisibleTo(inspector))
        self.assertFalse(inspector.color_button.isVisibleTo(inspector))
        brightness, temperature = [], []
        inspector.brightness_changed.connect(lambda *args: brightness.append(args))
        inspector.color_temp_changed.connect(lambda *args: temperature.append(args))
        inspector.brightness_slider.setValue(50)
        self.assertTrue(inspector._brightness_timer.isActive())
        inspector.set_device(1, plug(), {}, primary=False)
        inspector._commit_brightness()
        inspector._commit_temperature()
        self.assertFalse(inspector._brightness_timer.isActive())
        self.assertEqual(brightness, [])
        self.assertEqual(temperature, [])
        rgb = {"transport": "govee_cloud", "cloud_capabilities": [{"instance": "colorRgb"}]}
        inspector.set_device(2, rgb, {}, primary=False)
        self.assertTrue(inspector.color_button.isVisibleTo(inspector))
        self.assertFalse(inspector.brightness_slider.isVisibleTo(inspector))
        self.assertTrue(inspector.power_button.isHidden())
        inspector.deleteLater()

    def test_metered_plug_cards_and_details_show_readings_without_light_controls(self):
        device = plug()
        state = {"online": True, "status_source": "confirmed", "power_on": True, "power_w": 12.3, "voltage_v": 230.1, "current_a": 0.056}
        card = DeviceCard(0, device)
        card.set_state(state)
        self.assertEqual(card.name_label.text(), "Desk plug")
        self.assertEqual(card.metering_summary.text(), "12.3 W · 230.1 V")
        self.assertTrue(card.brightness_controls.isHidden())
        card.set_group_selection_mode(True)
        self.assertFalse(card.isEnabled())
        card.set_group_selection_mode(False)
        self.assertTrue(card.isEnabled())
        panel = DeviceEnergy()
        panel.set_device(device, state)
        panel.show()
        self.assertEqual(panel.values["current_a"].text(), "0.056 A")
        self.assertTrue(panel._poll.isActive())
        panel.hide()
        self.assertFalse(panel._poll.isActive())
        panel.set_device({"transport": "ble"}, {})
        self.assertTrue(panel.isHidden())
        card.deleteLater()
        panel.deleteLater()

    def test_energy_history_cannot_land_on_a_different_device_or_month(self):
        panel = DeviceEnergy()
        panel.set_device(plug(), {})
        with patch("lumisync.gui.widgets.device_energy.start_task", return_value=Mock()) as task:
            panel.load_history()
        self.assertEqual(task.call_count, 1)
        context = panel._request_context
        panel.set_device(plug(device_id="different", account_id="another"), {})
        history = {"month": context[-1], "total_kwh": 1.25, "reported_days": 1, "expected_days": 1,
                   "days": [{"date": context[-1] + "-01", "energy_kwh": 1.25}]}
        panel._history_finished(history, None)
        self.assertNotIn("1.250", panel.total.text())
        self.assertTrue(panel.load_button.isEnabled())
        panel.set_device(plug(), {})
        panel.load_history()
        self.assertIn("1.250", panel.total.text())
        panel.month.setCurrentIndex(1)
        self.assertNotIn("1.250", panel.total.text())
        panel.deleteLater()

    def test_controller_rejects_unsupported_controls_before_queueing(self):
        with patch("lumisync.gui.controllers.device_controller.devices.get_data", return_value={"devices": [], "selectedDevice": 0}):
            controller = DeviceController()
        controller.devices = [plug()]
        with patch.object(controller, "_start_control") as send:
            controller.set_brightness_at(0, 40)
            controller.set_color_at(0, 255, 0, 0)
            controller.set_color_temperature_at(0, 4000)
            send.assert_not_called()
            controller.turn_on_off_at(0, False)
            send.assert_called_once()
            self.assertEqual(send.call_args.args[1][1:3], ("set_power", (False,)))

    def test_govee_catalogue_hides_rgb_and_sync_actions_for_white_only_models(self):
        from lumisync.drivers.registry import capabilities_for_device
        from lumisync.sku_catalog import SkuCapabilities
        cap = SkuCapabilities("H6000", supports_color=False, supports_razer=False, color_temp_min=2700, color_temp_max=6500)
        device = {"transport": "lan", "model": "Friendly name", "sku": "H6000"}
        inspector = DeviceInspector()
        with patch("lumisync.drivers.govee_lan.sku_catalog.capabilities_for", return_value=cap) as catalogue:
            available = capabilities_for_device(device)
            catalogue.assert_called_with("H6000")
            self.assertFalse(available.supports_color)
            self.assertFalse(available.supports_streaming)
            inspector.set_device(0, device, {}, primary=False)
        self.assertFalse(inspector.color_button.isVisibleTo(inspector))
        self.assertTrue(inspector.temperature_slider.isVisibleTo(inspector))
        self.assertTrue(inspector.default_button.isHidden())
        self.assertTrue(inspector.zones_button.isHidden())
        with patch("lumisync.drivers.cloud.capabilities_for", return_value=cap):
            self.assertFalse(CloudLightAdapter({"transport": "govee_account", "sku": "H6000"}).capabilities.supports_color)
        inspector.deleteLater()
