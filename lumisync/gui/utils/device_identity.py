"""Recognizable hardware types based on saved metadata, never nicknames."""

from ...device import is_local_hardware
from ...drivers.registry import capabilities_for_device
from ...sku_catalog import capabilities_for
from ..resources.icons import IconKey


def is_pc_hardware(device) -> bool:
    """Whether *device* is lighting inside or attached to this PC.

    Such devices live on the PC page instead of the Devices page. Today that
    is USB lighting (RGB Fusion 2 motherboards); see
    :data:`lumisync.device.LOCAL_HARDWARE_TRANSPORTS`. Network and Bluetooth
    lights, and anything unreadable, are not PC hardware.
    """
    return is_local_hardware(device)


def device_identity(device: dict) -> tuple[str, IconKey]:
    kind = str(device.get("device_kind") or "").lower()
    known = {
        "smart_plug": ("Smart plug", IconKey.SMART_PLUG),
        "light_switch": ("Light switch", IconKey.LIGHT_SWITCH),
        "led_strip": ("LED strip", IconKey.LED_STRIP),
        "light_strip": ("LED strip", IconKey.LED_STRIP),
        "light_bulb": ("Light bulb", IconKey.LIGHT_BULB),
        "motherboard": ("Motherboard lighting", IconKey.MOTHERBOARD),
        "mouse": ("Mouse lighting", IconKey.MOUSE),
        "keyboard": ("Keyboard lighting", IconKey.KEYBOARD),
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
