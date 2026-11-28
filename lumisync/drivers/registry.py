"""Pick the right driver for a device descriptor."""

from __future__ import annotations

from typing import Any, Tuple

from ..device import DeviceLike
from .base import TransportAdapter
from .govee_lan import GoveeLanAdapter
from .idotmatrix_ble import IDotMatrixBleAdapter

# USB lighting has one transport ("usb") and one descriptor type per driver.
# A "usb" descriptor without a type predates the Logitech driver and is a
# Gigabyte board.
RGB_FUSION_TYPE = "rgb_fusion2"
LOGITECH_HIDPP_TYPE = "logitech_hidpp"
LORGAR_KEYBOARD_TYPE = "lorgar_keyboard"
ASUS_LAPTOP_TYPE = "asus_laptop"


def _type(device: Any) -> str:
    try:
        return str(device.get("type") or "").strip().lower()
    except AttributeError:
        return ""


def is_rgb_fusion(device: Any) -> bool:
    """Whether a descriptor is a Gigabyte RGB Fusion 2 board (board-only options apply)."""
    return _type(device) == RGB_FUSION_TYPE


def lighting_options(device: Any):
    """The per-device lighting options a USB lighting descriptor has, or None.

    Each driver describes its own (``lighting_options(device)``): which
    options exist (colour correction, layout, built-in effects), their
    descriptor fields and labels. Other devices have none.
    """
    kind = _type(device)
    if kind == LOGITECH_HIDPP_TYPE:
        from . import logitech_hidpp

        return logitech_hidpp.lighting_options(device)
    if kind == LORGAR_KEYBOARD_TYPE:
        from . import lorgar_keyboard

        return lorgar_keyboard.lighting_options(device)
    if kind == ASUS_LAPTOP_TYPE:
        from . import asus_laptop

        return asus_laptop.lighting_options(device)
    if kind == RGB_FUSION_TYPE:
        from . import gigabyte_fusion

        return gigabyte_fusion.lighting_options(device)
    return None


def local_hardware_presence(device: DeviceLike) -> Tuple[bool, str]:
    """Whether a USB lighting device is attached, and the message when it is not.

    Asks the descriptor's own driver, which only lists HID collections: no
    handle is opened for IO and no report is exchanged.
    """
    if _type(device) == LOGITECH_HIDPP_TYPE:
        from . import logitech_hidpp

        return logitech_hidpp.device_present(device), logitech_hidpp.NOT_FOUND_MESSAGE
    if _type(device) == LORGAR_KEYBOARD_TYPE:
        from . import lorgar_keyboard

        return lorgar_keyboard.device_present(device), lorgar_keyboard.NOT_FOUND_MESSAGE
    if _type(device) == ASUS_LAPTOP_TYPE:
        from . import asus_laptop

        return asus_laptop.device_present(device), asus_laptop.NOT_FOUND_MESSAGE
    from . import gigabyte_fusion

    return gigabyte_fusion.controller_present(device), gigabyte_fusion.NOT_FOUND_MESSAGE


# Every ``transport`` value create_adapter can build an adapter for.
SUPPORTED_TRANSPORTS = frozenset({
    "", "lan", "govee", "govee_lan",
    "govee_cloud", "govee_account", "tuya_cloud",
    "ble", "tuya", "usb",
})
_TYPES_WITH_ADAPTERS = frozenset({
    "idotmatrix", "idotmatrix_ble", "tuya", "lsc", LOGITECH_HIDPP_TYPE, LORGAR_KEYBOARD_TYPE, RGB_FUSION_TYPE, ASUS_LAPTOP_TYPE,
})


def is_supported_device(device: Any) -> bool:
    """Whether this version has a driver for a (saved) descriptor.

    Settings written by another version can name a transport this one does
    not know; such a device must be left out rather than crash the app.
    """
    try:
        transport = str(device.get("transport") or "").strip().lower()
    except AttributeError:
        return False
    return transport in SUPPORTED_TRANSPORTS or _type(device) in _TYPES_WITH_ADAPTERS


def create_adapter(device: DeviceLike, server=None) -> TransportAdapter:
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
    if kind == LOGITECH_HIDPP_TYPE:
        from .logitech_hidpp import LogitechHidppAdapter

        return LogitechHidppAdapter(device)
    if kind == LORGAR_KEYBOARD_TYPE:
        from .lorgar_keyboard import LorgarKeyboardAdapter

        return LorgarKeyboardAdapter(device)
    if kind == ASUS_LAPTOP_TYPE:
        from .asus_laptop import AsusLaptopAdapter

        return AsusLaptopAdapter(device)
    if transport == "usb" or kind == RGB_FUSION_TYPE:
        from .gigabyte_fusion import GigabyteFusionAdapter

        return GigabyteFusionAdapter(device)
    if transport not in ("", "lan", "govee", "govee_lan"):
        raise ValueError(f"Unsupported device transport: {transport}")
    return GoveeLanAdapter(device, server)


def capabilities_for_device(device: DeviceLike):
    """Read descriptor capabilities without opening any network connection."""
    transport = str(device.get("transport", "")).lower()
    kind = str(device.get("type", "")).lower()
    # USB lighting: static, no enumeration, no handle (adapters open lazily anyway).
    if kind == LOGITECH_HIDPP_TYPE:
        from .logitech_hidpp import LogitechHidppAdapter

        return LogitechHidppAdapter.capabilities_for(device)
    if kind == LORGAR_KEYBOARD_TYPE:
        from .lorgar_keyboard import LorgarKeyboardAdapter

        return LorgarKeyboardAdapter.capabilities_for(device)
    if kind == ASUS_LAPTOP_TYPE:
        from .asus_laptop import AsusLaptopAdapter

        return AsusLaptopAdapter.capabilities_for(device)
    if transport == "usb" or kind == RGB_FUSION_TYPE:
        from .gigabyte_fusion import GigabyteFusionAdapter

        return GigabyteFusionAdapter.capabilities_for(device)
    if transport in ("", "lan", "govee", "govee_lan") and kind not in (
        "idotmatrix",
        "idotmatrix_ble",
        "tuya",
        "lsc",
    ):
        return GoveeLanAdapter.capabilities_for(device)
    return create_adapter(device).capabilities


def is_cloud(device: DeviceLike) -> bool:
    return str(device.get("transport", "")).lower() in ("govee_cloud", "govee_account", "tuya_cloud")
