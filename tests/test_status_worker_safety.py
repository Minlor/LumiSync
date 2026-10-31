"""Status polling must not mutate live devices or use stale list positions."""

import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.controllers.discovery import DeviceStatusWorker


class StatusWorkerSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def controller(self):
        with patch(
            "lumisync.gui.controllers.device_controller.devices.get_data",
            return_value={"devices": [], "selectedDevice": 0},
        ):
            return DeviceController()

    def test_worker_reads_a_copy_and_returns_learned_fields(self):
        device = {
            "transport": "tuya",
            "device_id": "bulb",
            "mac": "tuya:bulb",
            "ip": "192.0.2.1",
        }
        worker = DeviceStatusWorker([(0, device)])
        adapter = Mock()
        adapter.device = {"tuya_paint_dp": 61, "tuya_paint_zones": 20}
        adapter.query_status.return_value = {"online": True, "power_on": True}
        updates = []
        worker.state_updated.connect(lambda _index, state: updates.append(state))

        with (
            patch(
                "lumisync.gui.controllers.discovery.pool.acquire",
                return_value=adapter,
            ),
            patch(
                "lumisync.gui.controllers.discovery.pool.is_pooled",
                return_value=True,
            ),
        ):
            worker.run()

        self.assertNotIn("tuya_paint_dp", device)
        self.assertEqual(updates[0]["learned"]["tuya_paint_dp"], 61)
        self.assertEqual(updates[0]["learned"]["tuya_paint_zones"], 20)

    def test_reply_is_resolved_by_identity_after_reordering(self):
        controller = self.controller()
        controller.devices = [{"mac": "second"}, {"mac": "first"}]
        key = controller._device_key({"mac": "first"})

        controller._merge_device_state(
            0, {"device_key": key, "power_on": True, "online": True}
        )

        self.assertIsNone(controller.get_device_state_at(0)["power_on"])
        self.assertTrue(controller.get_device_state_at(1)["power_on"])

    def test_pooled_discovery_is_persisted_when_sync_stops(self):
        controller = self.controller()
        controller.devices = [
            {
                "transport": "tuya",
                "device_id": "strip",
                "ip": "192.0.2.1",
            }
        ]
        with (
            patch(
                "lumisync.gui.controllers.device_controller.pool.learned_fields",
                return_value={"tuya_paint_dp": 61},
            ),
            patch.object(controller._store, "save") as save,
        ):
            changed = controller._adopt_pooled_discovery()

        self.assertTrue(changed)
        self.assertEqual(controller.devices[0]["tuya_paint_dp"], 61)
        save.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
