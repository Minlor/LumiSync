"""Shared persistent connections for BLE panels and encrypted Tuya sessions.

A BLE panel typically allows only one central connection and takes seconds to
connect, so opening a fresh link for every action (and for sync) is slow and
makes the panel visibly re-link each time. This pool hands out one long-lived
adapter per BLE device, shared by manual controls and the sync engine.

LAN (Govee) devices are cheap to open and are not pooled — callers manage their
own short-lived sockets there, exactly as before.
"""

from __future__ import annotations

from typing import Any, Dict
import threading

from .base import TransportAdapter
from .registry import create_adapter


def _is_ble(device: Dict[str, Any]) -> bool:
    return str(device.get("transport", "")).lower() == "ble"


def _ble_key(device: Dict[str, Any]) -> str:
    if str(device.get("transport", "")).lower() == "tuya":
        return "tuya:" + str(device.get("device_id") or device.get("devId")) + ":" + str(device.get("tuya_channel", ""))
    return str(device.get("ble_address") or device.get("mac") or id(device))


# Persistent BLE and Tuya adapters keyed by address or device ID.
_ble_adapters: Dict[str, TransportAdapter] = {}
_pool_lock = threading.RLock()


def _tuya_connection_key(device):
    return tuple(device.get(key) for key in ("device_id", "devId", "ip", "protocol_version", "local_key_ref", "local_key", "localKey"))


def acquire(device: Dict[str, Any], server=None) -> TransportAdapter:
    """Return a shared adapter for a device.

    BLE and Tuya devices get a cached persistent adapter. Govee LAN and cloud
    wrappers are fresh; their caller closes the wrapper/socket.
    """
    if is_pooled(device):
        key = _ble_key(device)
        with _pool_lock:
            adapter = _ble_adapters.get(key)
            if adapter is not None and adapter.device != device:
                adapter.close()
                adapter = None
            if adapter is None:
                adapter = create_adapter(dict(device))
                if str(device.get("transport", "")).lower() == "tuya":
                    for other in _ble_adapters.values():
                        if str(other.device.get("transport", "")).lower() == "tuya" and _tuya_connection_key(other.device) == _tuya_connection_key(device):
                            adapter.share_connection(other)
                            break
                _ble_adapters[key] = adapter
        return adapter
    return create_adapter(device, server)


def is_pooled(device: Dict[str, Any]) -> bool:
    return _is_ble(device) or str(device.get("transport", "")).lower() == "tuya"


def close(device: Dict[str, Any]) -> None:
    """Close and drop the pooled adapter for a device, if any."""
    with _pool_lock:
        adapter = _ble_adapters.pop(_ble_key(device), None)
    if adapter is not None:
        try:
            adapter.close()
        except Exception:
            pass


def close_all() -> None:
    """Close every pooled adapter (call after workers stop on shutdown)."""
    with _pool_lock:
        adapters = list(_ble_adapters.values())
        _ble_adapters.clear()
    for adapter in adapters:
        try:
            adapter.close()
        except Exception:
            pass
