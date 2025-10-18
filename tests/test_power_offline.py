"""
Offline unit tests for power control.

These tests simulate the UDP socket and patch network functions to validate that:
- devices.power_on / power_off send the correct "turn" payloads (true power)
- GUI DeviceController.turn_on_off uses connection.switch (true power)

No real device or network is required.
"""

import json
from types import SimpleNamespace

import pytest


class FakeServer:
    """A fake UDP server that records the last sendto call."""

    def __init__(self):
        self.sent = []  # list of tuples: (payload_bytes, (ip, port))
        self.closed = False

    # Make this fake socket behave enough like a real socket for the
    # controller's _ensure_server checks (getsockname() and settimeout()).
    def getsockname(self):
        return ("127.0.0.1", 0)

    def settimeout(self, timeout):
        self._timeout = timeout

    def sendto(self, data: bytes, addr):
        self.sent.append((data, addr))

    def close(self):
        self.closed = True


def _decode_sent_payload(fake: FakeServer):
    assert fake.sent, "No datagrams were sent"
    payload_bytes, addr = fake.sent[-1]
    payload = json.loads(payload_bytes.decode("utf-8"))
    return payload, addr


def test_devices_power_on_off_payloads(monkeypatch):
    """devices.power_on/off should send cmd=turn with value 1/0 to device IP:port."""
    from lumisync import devices as devices_mod

    # Patch devices.connect (note: devices.py imports connect directly from connection)
    fake_server = FakeServer()
    monkeypatch.setattr(devices_mod, "connect", lambda: (fake_server, []))

    # Use a simple device dict; omit Device_Port to exercise default 4003
    device = {"ip": "127.0.0.1", "mac": "AA:BB:CC:DD:EE:FF"}

    # Power ON
    devices_mod.power_on(device)
    payload, addr = _decode_sent_payload(fake_server)
    assert payload == {"msg": {"cmd": "turn", "data": {"value": 1}}}
    assert addr == ("127.0.0.1", 4003)

    # Power OFF
    devices_mod.power_off(device)
    payload, addr = _decode_sent_payload(fake_server)
    assert payload == {"msg": {"cmd": "turn", "data": {"value": 0}}}
    assert addr == ("127.0.0.1", 4003)


def test_device_controller_turn_on_off_uses_switch(monkeypatch):
    """GUI controller should invoke connection.switch with proper args and not crash."""
    from lumisync.gui.controllers.device_controller import DeviceController
    from lumisync import connection as connection_mod
    from lumisync import devices as devices_mod

    calls = []

    # Patch connection.switch to capture calls
    def fake_switch(server, device, on=False):
        calls.append(SimpleNamespace(server=server, device=device, on=on))

    monkeypatch.setattr(connection_mod, "switch", fake_switch)

    # Create controller with a fake server (avoid real sockets)
    # Prevent DeviceController init from trying to read/refresh real settings
    monkeypatch.setattr(
        devices_mod,
        "get_data",
        lambda: {"devices": [], "selectedDevice": 0, "time": 10**12},
    )

    controller = DeviceController(status_callback=None)
    controller.server = FakeServer()
    controller.devices = [
        {"ip": "127.0.0.1", "mac": "AA:BB:CC:DD:EE:FF", "model": "TEST"}
    ]
    controller.selected_device_index = 0

    # Turn on then off
    controller.turn_on_off(True)
    controller.turn_on_off(False)

    assert len(calls) >= 2
    assert calls[0].on is True
    assert calls[1].on is False
    # Ensure device dict is passed through
    assert calls[0].device["mac"] == "AA:BB:CC:DD:EE:FF"
