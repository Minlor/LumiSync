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

    if transport in ("govee_cloud", "govee_account", "tuya_cloud"):
        from .cloud import CloudLightAdapter
        return CloudLightAdapter(device)
    if transport == "ble" or kind in ("idotmatrix", "idotmatrix_ble"):
        return IDotMatrixBleAdapter(device)
    if transport == "tuya" or kind in ("tuya", "lsc"):
        from .tuya_lan import TuyaLightAdapter

        return TuyaLightAdapter(device)
    if transport not in ("", "lan", "govee", "govee_lan"):
        raise ValueError(f"Unsupported device transport: {transport}")
    return GoveeLanAdapter(device, server)


def capabilities_for_device(device: Dict[str, Any]):
    """Read descriptor capabilities without opening any network connection."""
    return create_adapter(device, server=object()).capabilities


def is_cloud(device: Dict[str, Any]) -> bool:
    return str(device.get("transport", "")).lower() in ("govee_cloud", "govee_account", "tuya_cloud")
