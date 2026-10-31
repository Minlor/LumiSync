"""Persistence boundary for the GUI device inventory."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from ... import devices
from ...device import Device, DeviceLike, as_device


class DeviceStore:
    """Own the saved device list and selected index.

    The controller keeps behaviour (signals, command dispatch); this class only
    loads, normalizes and saves the inventory. Keeping it separate makes it
    obvious which operations touch disk.
    """

    def __init__(self) -> None:
        self.devices: List[Device] = []
        self.selected_index = 0

    def read(self, *, refresh: bool = False) -> Dict[str, Any]:
        return devices.get_data(refresh=refresh)

    def load(self, *, refresh: bool = False) -> Dict[str, Any]:
        settings = self.read(refresh=refresh)
        self.devices = [as_device(device).copy() for device in settings.get("devices", [])]
        try:
            self.selected_index = int(settings.get("selectedDevice", 0))
        except (TypeError, ValueError):
            self.selected_index = 0
        if self.devices:
            self.selected_index = max(0, min(self.selected_index, len(self.devices) - 1))
        else:
            self.selected_index = 0
        return settings

    def load_safe(self) -> Dict[str, Any]:
        try:
            return self.read(refresh=False)
        except Exception:
            return {"devices": [], "selectedDevice": 0, "time": 0}

    def replace(self, new_devices: Iterable[DeviceLike], selected: Optional[int] = None) -> None:
        self.devices = [as_device(device).copy() for device in new_devices]
        if selected is not None:
            self.selected_index = int(selected)
        if self.devices:
            self.selected_index = max(0, min(self.selected_index, len(self.devices) - 1))
        else:
            self.selected_index = 0

    def save(
        self,
        *,
        devices_list: Optional[Iterable[DeviceLike]] = None,
        selected: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            settings = self.read(refresh=False)
        except Exception:
            settings = {"devices": [], "selectedDevice": 0, "time": 0}
        if devices_list is not None:
            self.replace(devices_list, selected)
        elif selected is not None:
            self.selected_index = int(selected)
            if self.devices:
                self.selected_index = max(
                    0, min(self.selected_index, len(self.devices) - 1)
                )
            else:
                self.selected_index = 0
        if extra:
            settings.update(extra)
        settings["devices"] = list(self.devices)
        settings["selectedDevice"] = self.selected_index
        devices.writeJSON(settings)
        return settings
