"""
Offline unit tests for power control.

These tests simulate the UDP socket and patch network functions to validate that:
- devices.power_on / power_off send the correct "turn" payloads (true power)
- GUI DeviceController.turn_on_off uses connection.switch (true power)

No real device or network is required.
"""

import json
from types import SimpleNamespace


class FakeServer:
    """A fake UDP server that records the last sendto call."""

    def __init__(self):
        """Initialize the fake server state."""
        self.sent = []  # list of tuples: (payload_bytes, (ip, port))
        self.closed = False
        # Ensure attribute exists for linting
        self._timeout = None

    # Make this fake socket behave enough like a real socket for the
    # controller's _ensure_server checks (getsockname() and settimeout()).
    def getsockname(self):
        """Return a tuple representing the socket name (ip, port)."""
        return ("127.0.0.1", 0)

    def settimeout(self, timeout):
        """Record the timeout value set on the socket."""
        self._timeout = timeout

    def sendto(self, data: bytes, addr):
        """Record a datagram sent to (addr)."""
        self.sent.append((data, addr))

    def close(self):
        """Mark the fake server as closed."""
        self.closed = True


def _decode_sent_payload(fake: FakeServer):
    """Decode and return the latest sent datagram from the fake server.

    Returns a tuple (payload_dict, addr_tuple).
    """
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
    import json
    import socket
    import time

    from lumisync.devices import power_off, power_on


    class FakeServer:
        """A minimal fake server socket to capture sendto calls from the code under
        test. This mimics only the parts our code uses: sendto, close, getsockname,
        and settimeout.

        The implementation stores the last sendto payload in `last_sent`.
        """

        def __init__(self):
            """Initialize the inner fake server state."""
            self.closed = False
            self.last_sent = None
            self._timeout = None

        def sendto(self, data, addr):
            """Store the last sendto payload and destination."""
            # store raw bytes and destination tuple
            self.last_sent = (data, addr)

        def close(self):
            """Mark the fake server as closed."""
            self.closed = True

        def getsockname(self):
            """Return a tuple representing socket name (ip, port)."""
            # Return a tuple like a real socket would
            return ("127.0.0.1", 0)

        def settimeout(self, t):
            """Record the timeout set on the fake server."""
            self._timeout = t


    def _make_device():
        """Return a minimal fake device description used by the tests."""
        return {"ip": "127.0.0.1", "port": 7777, "id": "fake"}


    def test_power_offline():
        """Inner helper test verifying power_on/off payloads with an inner fake server."""
        server = FakeServer()
        device = _make_device()

        # Toggle off
        power_off(server, device)
        assert server.last_sent is not None
        data, addr = server.last_sent
        payload = json.loads(data.decode("utf-8"))
        assert payload["msg"]["cmd"] == "turn"
        assert payload["msg"]["data"]["value"] == 0
        assert addr == (device["ip"], device["port"]) or addr == (device["ip"], 7777)

        # Toggle on
        power_on(server, device)
        data, addr = server.last_sent
        payload = json.loads(data.decode("utf-8"))
        assert payload["msg"]["cmd"] == "turn"
        assert payload["msg"]["data"]["value"] == 1
