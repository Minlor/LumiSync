"""Subprocess regression for rapid Qt device commands; no real network I/O."""

import faulthandler
import gc
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PySide6.QtCore import QThread, QThreadPool, QTimer
from PySide6.QtWidgets import QApplication

from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.theme import apply_theme
from lumisync.gui.views.devices_view import DevicesView
from tests.test_device_inventory_ui import inventory_fixtures

faulthandler.enable()
app = QApplication([])
apply_theme(app)
with patch.object(DeviceController, "_init_devices"):
    controller = DeviceController()
controller.devices = [inventory_fixtures()[0]]
key = controller._device_key(controller.devices[0])
controller.device_states[key] = {"online": True, "power_on": True, "brightness": 100,
                                 "status_source": "confirmed"}
physical = {"power_on": True, "brightness": 100}
writes, queries, deliveries = [], [], []
controller.device_state_updated.connect(lambda *_: deliveries.append(QThread.currentThread() is app.thread()))


class Adapter:
    def set_power(self, value):
        time.sleep(.003)
        physical["power_on"] = value
        writes.append(("power", value))

    def set_brightness(self, value):
        time.sleep(.002)
        physical["brightness"] = value
        writes.append(("brightness", value))

    def close(self):
        pass


def query(*args, **kwargs):
    queries.append(QThread.currentThread() is not app.thread())
    snapshot = dict(physical)
    time.sleep(.008)
    return snapshot


with patch("lumisync.gui.controllers.device_controller.create_adapter", side_effect=lambda _: Adapter()), \
        patch("lumisync.gui.controllers.device_controller.connection.query_status", side_effect=query), \
        patch("lumisync.gui.controllers.device_controller.connection.create_lan_socket"):
    view = DevicesView(controller)
    view.resize(1055, 760)
    view.show()
    view._open_inspector(0)
    ticks, peaks = [0], [0]
    timer = QTimer()

    def tick():
        ticks[0] += 1
        n = ticks[0]
        if n <= 1200:
            view._cards[0].brightness_slider.setValue(n % 101)
            if n % 3 == 0:
                view._cards[0]._commit_brightness()
            if n % 4 == 0:
                view._cards[0].power_button.click()
            if n % 7 == 0:
                view.inspector.power_button.click()
            if n % 17 == 0:
                controller.refresh_device_state_at(0)
            if n % 80 == 0:
                gc.collect()
            peaks[0] = max(peaks[0], len(controller._command_queue.get(key, [])))
        elif n == 1201:
            view._cards[0]._brightness_timer.stop()
            controller.turn_on_off_at(0, False)
            controller.set_brightness_at(0, 73)
        elif (not controller._command_tasks and not controller._confirmation_timers
              and controller.status_thread is None and not QThreadPool.globalInstance().activeThreadCount()):
            timer.stop()
            state = controller.get_device_state_at(0)
            assert physical == {"power_on": False, "brightness": 73}, physical
            assert state["power_on"] is False and state["brightness"] == 73, state
            assert len(queries) >= 30 and all(queries), len(queries)
            assert deliveries and all(deliveries)
            assert peaks[0] <= 2, peaks[0]
            assert controller.shutdown()
            view.close()
            print(json.dumps({"pass": True, "ticks": 1200, "writes": len(writes),
                              "queries": len(queries), "maximum_pending": peaks[0],
                              "final_state": physical}), flush=True)
            app.quit()

    timer.timeout.connect(tick)
    timer.start(4)
    QTimer.singleShot(20000, lambda: os._exit(2))
    app.exec()
