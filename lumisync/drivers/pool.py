"""Shared persistent connections for BLE panels and encrypted Tuya sessions.

A BLE panel typically allows only one central connection and takes seconds to
connect, so opening a fresh link for every action (and for sync) is slow and
makes the panel visibly re-link each time. This pool hands out one long-lived
adapter per BLE device, shared by manual controls and the sync engine.

LAN (Govee) devices are cheap to open and are not pooled — callers manage their
own short-lived sockets there, exactly as before.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, Iterable
import hashlib
import threading

from ..device import DeviceLike, as_device
from .base import TransportAdapter
from .registry import create_adapter


def _is_ble(device: DeviceLike) -> bool:
    return as_device(device).is_ble


def _ble_key(device: DeviceLike) -> str:
    typed = as_device(device)
    if typed.transport == "tuya":
        return f"tuya:{typed.device_id}:{typed.get('tuya_channel', '')}"
    return str(typed.ble_address or typed.mac or id(device))


# Persistent BLE and Tuya adapters keyed by address or device ID.
_pooled_adapters: Dict[str, TransportAdapter] = {}
# Backwards-compatible alias for integrations that inspected the old name.
_ble_adapters = _pooled_adapters
_pool_lock = threading.RLock()


def _tuya_connection_key(raw_device):
    """Connection identity of a Tuya descriptor.

    A vault reference stands for its key: the key behind it can be refreshed
    (rotated by the device, re-read from the account) without the descriptor
    — or the identity — changing. Only legacy plaintext descriptors fall
    back to the key itself, as a digest so the tuple never carries a secret.
    """
    device = as_device(raw_device)
    reference = str(device.get("local_key_ref") or "")
    plaintext = "" if reference else str(device.get("local_key") or "")
    digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()[:16] if plaintext else ""
    return (
        device.device_id,
        device.ip,
        device.protocol_version,
        reference,
        digest,
    )


def _same_connection_target(adapter, incoming) -> bool:
    """Whether a pooled adapter still targets the same physical connection.

    Comparing whole device dicts made every discovery stamp (paint DP, colour
    format, zone grid) recycle the adapter — reopening the Tuya session on the
    next slider tick. Only connection-relevant fields matter for reuse.

    A Tuya adapter also updates its own descriptor copy (it follows a device
    to its new DHCP address); callers keep the saved descriptor. Either the
    identity the adapter was created with or its live one counts as a match,
    so neither a followed address nor a refreshed key recycles the session.
    """
    stored = getattr(adapter, "device", None)
    if str(incoming.get("transport", "")).lower() == "tuya":
        wanted = _tuya_connection_key(incoming)
        created = getattr(adapter, "_pool_identity", None)
        live = _tuya_connection_key(stored) if isinstance(stored, Mapping) else None
        return wanted in (created, live)
    return stored == incoming


def _retire(adapter: TransportAdapter) -> None:
    """Close a replaced adapter for good (outside the pool lock).

    A sync session may still hold it; ``_retired`` makes a Tuya adapter stop
    instead of reconnecting next to its replacement.
    """
    try:
        setattr(adapter, "_retired", True)
    except Exception:
        pass
    try:
        adapter.close()
    except Exception:
        pass


def acquire(device: DeviceLike, server=None) -> TransportAdapter:
    """Return a shared adapter for a device.

    BLE and Tuya devices get a cached persistent adapter. Govee LAN and cloud
    wrappers are fresh; their caller closes the wrapper/socket.
    """
    if is_pooled(device):
        key = _ble_key(device)
        device = as_device(device).copy()
        replaced = None
        with _pool_lock:
            adapter = _pooled_adapters.get(key)
            if adapter is not None and not _same_connection_target(adapter, device):
                # Close it after releasing the lock: closing waits for any
                # in-flight command, which must not stall every other device.
                replaced = _pooled_adapters.pop(key)
                adapter = None
            if adapter is None:
                adapter = create_adapter(device)
                if device.transport == "tuya":
                    identity = _tuya_connection_key(device)
                    try:
                        setattr(adapter, "_pool_identity", identity)
                    except Exception:
                        pass
                    for other in _pooled_adapters.values():
                        if as_device(other.device).transport == "tuya" and _same_connection_target(other, device):
                            adapter.share_connection(other)
                            break
                _pooled_adapters[key] = adapter
        if replaced is not None:
            _retire(replaced)
        return adapter
    return create_adapter(device, server)


def is_pooled(device: DeviceLike) -> bool:
    return as_device(device).is_pooled


def learned_fields(
    device: DeviceLike, fields: Iterable[str]
) -> Dict[str, Any]:
    """Return fields a pooled adapter learned, without opening a connection.

    Sync streams can discover a Tuya paint data point or zone grid while the
    controller still has a stale descriptor. Reading it back here lets the
    caller persist that knowledge when sync stops instead of losing it until
    the next status poll.
    """
    typed = as_device(device)
    if not typed.is_pooled:
        return {}
    with _pool_lock:
        adapter = _pooled_adapters.get(_ble_key(typed))
    source = getattr(adapter, "device", None) if adapter is not None else None
    if not isinstance(source, Mapping):
        return {}
    learned = {}
    for field in fields:
        value = source.get(field)
        if value is not None and typed.get(field) != value:
            learned[field] = value
    return learned


def close(device: DeviceLike) -> None:
    """Close and drop the pooled adapter for a device, if any."""
    with _pool_lock:
        adapter = _pooled_adapters.pop(_ble_key(device), None)
    if adapter is not None:
        try:
            adapter.close()
        except Exception:
            pass


def close_all() -> None:
    """Close every pooled adapter (call after workers stop on shutdown)."""
    with _pool_lock:
        adapters = list(_pooled_adapters.values())
        _pooled_adapters.clear()
    for adapter in adapters:
        try:
            adapter.close()
        except Exception:
            pass
