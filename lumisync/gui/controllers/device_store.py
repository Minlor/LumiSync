"""Persistence boundary for the GUI device inventory."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, Callable, Dict, Iterable, List, Optional

from ... import devices
from ...device import Device, DeviceLike, as_device
from ...utils.logging import get_logger

logger = get_logger(__name__)

# Top-level keys owned by the device inventory. Every other key in
# settings.json (accounts, groups, sync, ...) belongs to someone else and is
# carried over from the file as it is on disk at write time.
INVENTORY_KEYS = ("devices", "selectedDevice")


def protect_keys_in_place(device_list: Iterable[Any]) -> None:
    """Move plaintext local keys of live descriptors into the OS vault.

    The descriptor then carries only ``local_key_ref``, so the next save does
    not put the same key into the vault again. When the credential store is
    unavailable the descriptor is left as it is; ``writeJSON`` then keeps the
    device's saved key form instead of failing the whole write.
    """
    from ...accounts.errors import AccountError
    from ...accounts.secrets import protect_device

    for device in device_list:
        if not isinstance(device, MutableMapping) or not as_device(device).get("local_key"):
            continue
        try:
            protected = protect_device(as_device(device).to_dict())
        except AccountError:
            continue
        reference = protected.get("local_key_ref")
        if not reference:
            continue
        device["local_key_ref"] = reference
        for spelling in ("local_key", "localKey"):
            device.pop(spelling, None)


class SettingsReloaded(devices.SettingsUnavailable):
    """A write was dropped because the inventory was just reloaded from disk.

    Only raised after an earlier read failed: the change was computed against
    an inventory that never reflected the file, so writing it would erase the
    saved devices.
    """


class DeviceStore:
    """Own the saved device list and selected index.

    The controller keeps behaviour (signals, command dispatch); this class only
    loads, normalizes and saves the inventory. Keeping it separate makes it
    obvious which operations touch disk.

    Every write re-reads settings.json first and replaces only the keys the
    caller owns. When a read fails the store refuses to write until a later
    read succeeds; it never saves an inventory invented from a failed read.
    """

    def __init__(self) -> None:
        self.devices: List[Device] = []
        self.selected_index = 0
        # The last read of settings.json failed; writes are refused until a
        # later read (attempted on the next save) succeeds.
        self.unavailable = False
        # The startup load failed, so the in-memory inventory does not reflect
        # the file. The next successful read replaces it instead of saving it.
        self._load_failed = False
        self._loaded_once = False
        # Set by the controller: the inventory was reloaded from disk.
        self.on_reloaded: Optional[Callable[[], None]] = None
        # Set by the controller: a one-time notice produced by a save.
        self.on_notice: Optional[Callable[[str], None]] = None

    def read(self, *, refresh: bool = False) -> Dict[str, Any]:
        return devices.get_data(refresh=refresh)

    def _adopt(self, settings: Dict[str, Any]) -> None:
        self.devices = [as_device(device).copy() for device in settings.get("devices", [])]
        try:
            self.selected_index = int(settings.get("selectedDevice", 0))
        except (TypeError, ValueError):
            self.selected_index = 0
        if self.devices:
            self.selected_index = max(0, min(self.selected_index, len(self.devices) - 1))
        else:
            self.selected_index = 0

    def load(self, *, refresh: bool = False) -> Dict[str, Any]:
        try:
            settings = self.read(refresh=refresh)
        except devices.SettingsUnavailable:
            self.unavailable = True
            # An inventory loaded earlier still reflects the file; only a
            # store that never loaded must not save what it holds.
            self._load_failed = self._load_failed or not self._loaded_once
            raise
        self.unavailable = False
        self._load_failed = False
        self._loaded_once = True
        self._adopt(settings)
        return settings

    @property
    def needs_reload(self) -> bool:
        """The inventory was never read from disk because the read failed."""
        return self._load_failed

    def retry_load(self) -> Optional[Dict[str, Any]]:
        """Retry a failed startup load; return the settings if it recovered."""
        if not self._load_failed:
            return None
        try:
            settings = self.load(refresh=False)
        except devices.SettingsUnavailable:
            return None
        if self.on_reloaded is not None:
            self.on_reloaded()
        return settings

    def load_safe(self) -> Dict[str, Any]:
        """Read settings for display only; never pass the result to a write.

        A failed read returns an empty view (no accounts, no groups) and marks
        the store unavailable so no save is built on top of it.
        """
        try:
            return self.read(refresh=False)
        except devices.SettingsUnavailable:
            self.unavailable = True
        except Exception:
            logger.warning("Could not read settings for display", exc_info=True)
        return {"devices": [], "selectedDevice": 0, "time": 0}

    def _read_for_write(self) -> tuple[Dict[str, Any], bool]:
        """Re-read settings.json before a write; raise instead of guessing.

        Returns the settings and whether the inventory was just reloaded
        because the startup read had failed.
        """
        try:
            settings = self.read(refresh=False)
        except devices.SettingsUnavailable:
            self.unavailable = True
            raise
        if self.unavailable or self._load_failed:
            logger.info("Settings are readable again; saving resumes")
        self.unavailable = False
        if not self._load_failed:
            return settings, False
        self._load_failed = False
        self._loaded_once = True
        self._adopt(settings)
        if self.on_reloaded is not None:
            self.on_reloaded()
        return settings, True

    @staticmethod
    def _reloaded_error() -> SettingsReloaded:
        return SettingsReloaded(
            "LumiSync can read its settings again and reloaded your saved "
            "devices. Repeat the last change."
        )

    def write(
        self,
        updates: Dict[str, Any] | Callable[[Dict[str, Any]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Re-read settings.json, replace only the caller's keys and save.

        *updates* is a dict of top-level keys, or a function that receives the
        freshly read settings and returns that dict (for read-modify-write of
        lists such as ``accounts``). Raises
        :class:`lumisync.devices.SettingsUnavailable` without writing when the
        file cannot be read; write errors escape unchanged.
        """
        settings, reloaded = self._read_for_write()
        changes = updates(settings) if callable(updates) else updates
        if reloaded and any(key in changes for key in INVENTORY_KEYS):
            raise self._reloaded_error()
        device_list = changes.get("devices")
        if isinstance(device_list, list):
            protect_keys_in_place(device_list)
        settings.update(changes)
        devices.writeJSON(settings)
        notice = devices.consume_save_notice()
        if notice and self.on_notice is not None:
            self.on_notice(notice)
        return settings

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
        """Save the in-memory inventory (plus *extra* keys) over the file."""
        if self.unavailable or self._load_failed:
            # Retry the failed read before touching memory: a recovered load
            # replaces the inventory and drops this change.
            if self._read_for_write()[1]:
                raise self._reloaded_error()
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
        updates = dict(extra or {})
        updates["devices"] = list(self.devices)
        updates["selectedDevice"] = self.selected_index
        return self.write(updates)
