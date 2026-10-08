"""Recognizable hardware types based on saved metadata, never nicknames."""

from ...drivers.registry import capabilities_for_device
from ...sku_catalog import capabilities_for
from ..resources.icons import IconKey


def device_identity(device: dict) -> tuple[str, IconKey]:
    kind = str(device.get("device_kind") or "").lower()
    known = {
        "smart_plug": ("Smart plug", IconKey.SMART_PLUG),
        "light_switch": ("Light switch", IconKey.LIGHT_SWITCH),
        "led_strip": ("LED strip", IconKey.LED_STRIP),
        "light_strip": ("LED strip", IconKey.LED_STRIP),
        "light_bulb": ("Light bulb", IconKey.LIGHT_BULB),
    }
    if kind in known:
        return known[kind]
    cap = capabilities_for_device(device)
    if cap.is_matrix:
        return "Matrix panel", IconKey.MATRIX_PANEL
    model = capabilities_for(device.get("sku") or device.get("model"))
    if model:
        name = model.name.lower()
        if "strip" in name:
            return "LED strip", IconKey.LED_STRIP
        if "backlight" in name:
            return "TV backlight", IconKey.LED_STRIP
        if "bulb" in name:
            return "Light bulb", IconKey.LIGHT_BULB
    return "Light", IconKey.LIGHT_BULB
