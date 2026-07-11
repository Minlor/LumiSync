"""Pick the right driver for a device descriptor."""

from __future__ import annotations

from typing import Any, Dict

from .base import TransportAdapter
from .govee_lan import GoveeLanAdapter
from .idotmatrix_ble import IDotMatrixBleAdapter


def create_adapter(device: Dict[str, Any], server=None) -> TransportAdapter:
    """Build a :class:`TransportAdapter` for ``device``.

    Selection is by explicit ``transport``/``type`` hints on the descriptor,
    defaulting to the Govee LAN path (LumiSync's original behavior).
    """
    transport = str(device.get("transport", "")).lower()
    kind = str(device.get("type", "")).lower()

    if transport == "ble" or kind in ("idotmatrix", "idotmatrix_ble"):
        return IDotMatrixBleAdapter(device)
    return GoveeLanAdapter(device, server)
