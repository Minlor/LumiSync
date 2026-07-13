"""Sync groups: named sets of devices that sync together as one.

Govee Desktop models every mode (DreamView/Music/Razer) as one instance fanned
out to a ``devices[]`` array with per-device brightness. LumiSync mirrors that:
a group is a name plus the devices it contains and an optional per-device
brightness override. Groups persist in ``settings.json`` next to the device list.

This module is pure data logic (no I/O, no Qt) so it is easy to test; the
controller layer handles persistence and signals.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def device_key(device: Dict[str, Any]) -> str:
    """Stable identity for a device within a group (MAC preferred)."""
    return str(device.get("mac") or device.get("ip") or device.get("model") or "")


def make_group(
    name: str,
    devices: List[Dict[str, Any]],
    brightness: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Create a group record from a name and the devices it should contain."""
    keys = [device_key(d) for d in devices if device_key(d)]
    # De-duplicate while preserving order.
    seen: set = set()
    ordered = [k for k in keys if not (k in seen or seen.add(k))]
    return {"name": str(name), "devices": ordered, "brightness": dict(brightness or {})}


def normalize_group(raw: Any) -> Optional[Dict[str, Any]]:
    """Coerce a stored value into a valid group record, or None if unusable."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not name:
        return None
    devices = raw.get("devices")
    keys = [str(k) for k in devices if k] if isinstance(devices, list) else []
    brightness = raw.get("brightness")
    brightness = brightness if isinstance(brightness, dict) else {}
    return {"name": str(name), "devices": keys, "brightness": dict(brightness)}


def list_groups(settings: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the normalized groups stored in a settings dict."""
    raw = settings.get("groups")
    if not isinstance(raw, list):
        return []
    return [g for g in (normalize_group(item) for item in raw) if g is not None]


def find_group(groups: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    lowered = str(name).strip().lower()
    for group in groups:
        if group.get("name", "").strip().lower() == lowered:
            return group
    return None


def upsert_group(
    groups: List[Dict[str, Any]], group: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Add ``group``, replacing any existing group with the same name."""
    name = group.get("name", "").strip().lower()
    result = [g for g in groups if g.get("name", "").strip().lower() != name]
    result.append(group)
    return result


def remove_group(groups: List[Dict[str, Any]], name: str) -> List[Dict[str, Any]]:
    lowered = str(name).strip().lower()
    return [g for g in groups if g.get("name", "").strip().lower() != lowered]


def resolve_devices(
    group: Dict[str, Any], devices: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Return the device dicts a group refers to, in the group's stored order."""
    by_key = {device_key(d): d for d in devices}
    resolved = []
    for key in group.get("devices", []):
        device = by_key.get(key)
        if device is not None:
            resolved.append(device)
    return resolved
