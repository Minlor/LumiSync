"""Shared adapter pool so BLE devices keep one persistent connection.

A BLE panel typically allows only one central connection and takes seconds to
connect, so opening a fresh link for every action (and for sync) is slow and
makes the panel visibly re-link each time. This pool hands out one long-lived
adapter per BLE device, shared by manual controls and the sync engine.

LAN (Govee) devices are cheap to open and are not pooled — callers manage their
own short-lived sockets there, exactly as before.
"""

from __future__ import annotations

from typing import Any, Dict

from .base import TransportAdapter
from .registry import create_adapter


def _is_ble(device: Dict[str, Any]) -> bool:
    return str(device.get("transport", "")).lower() == "ble"


def _ble_key(device: Dict[str, Any]) -> str:
    return str(device.get("ble_address") or device.get("mac") or id(device))


# Persistent BLE adapters keyed by address. LAN devices never land here.
_ble_adapters: Dict[str, TransportAdapter] = {}


def acquire(device: Dict[str, Any], server=None) -> TransportAdapter:
    """Return a shared adapter for a device.

    BLE devices get a cached, persistent adapter. LAN devices get a fresh
    adapter bound to ``server`` (the caller closes it).
    """
    if _is_ble(device):
        key = _ble_key(device)
        adapter = _ble_adapters.get(key)
        if adapter is None:
            adapter = create_adapter(device)
            _ble_adapters[key] = adapter
        return adapter
    return create_adapter(device, server)


def is_pooled(device: Dict[str, Any]) -> bool:
    return _is_ble(device)


def close(device: Dict[str, Any]) -> None:
    """Close and drop the pooled adapter for a device, if any."""
    adapter = _ble_adapters.pop(_ble_key(device), None)
    if adapter is not None:
        try:
            adapter.close()
        except Exception:
            pass


def close_all() -> None:
    """Close every pooled BLE adapter (call on app shutdown)."""
    for adapter in list(_ble_adapters.values()):
        try:
            adapter.close()
        except Exception:
            pass
    _ble_adapters.clear()
