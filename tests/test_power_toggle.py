"""
Manual-ish test to verify power on/off commands reach the selected device.
This test does not assert hardware state but ensures the code path executes
without exceptions. To observe the light, run this test with your device on.
"""

import time

from lumisync.devices import get_data, power_on, power_off
from lumisync import connection


def test_power_toggle_smoke():
    data = get_data()
    devices = data.get("devices", [])

    # Fallback to discovery if needed
    if not devices:
        server, discovered = connection.connect()
        devices = discovered
        server.close()

    assert isinstance(devices, list)
    if not devices:
        print("No devices discovered; skipping power toggle smoke test.")
        return

    device = devices[data.get("selectedDevice", 0) if devices else 0]

    # Try off then on with brief delays
    power_off(device)
    time.sleep(1.0)
    power_on(device)
    time.sleep(1.0)
