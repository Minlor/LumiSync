"""Device administration: manual add/remove and account imports.

Kept out of :mod:`device_controller` so the controller can focus on discovery
lifecycle, status state and command dispatch. The mixin uses the controller's
signals and live device list; it owns no Qt state of its own.
"""

from __future__ import annotations

import ipaddress

from ... import devices
from ...device import as_device
from ...drivers import pool
from ...drivers.registry import is_cloud
from ...utils.logging import get_logger

logger = get_logger(__name__)


def _saved_accounts(settings: dict) -> list[dict]:
    accounts = settings.get("accounts")
    if not isinstance(accounts, list):
        return []
    return [account for account in accounts if isinstance(account, dict)]


class DeviceAdminMixin:
    """Manual add/remove and account imports.

    Messages follow the controller's notification policy: confirmations are
    status-only (the new or removed card is the feedback), while a request
    that was refused or failed is a notice the user sees at once.
    """

    def _refuse(self, message: str) -> None:
        self._notify(message, "error")

    def add_device_manually(self, ip: str, model: str = "Manual Device",
                           mac: str = None, port: int = 4003) -> bool:
        """Manually add a device bypassing automatic discovery.

        Args:
            ip: IP address of the device
            model: Model name/identifier for the device
            mac: MAC address (optional, will be generated if not provided)
            port: Port number (default: 4003)

        Returns:
            True if device was added successfully, False otherwise
        """
        try:
            # Validate IP address
            try:
                ipaddress.IPv4Address(ip)
            except ipaddress.AddressValueError:
                self._refuse(f"Invalid IP address: {ip}")
                return False

            # Generate MAC if not provided
            if not mac:
                ip_parts = ip.split('.')
                mac = f"00:00:{ip_parts[0]:0>2}:{ip_parts[1]:0>2}:{ip_parts[2]:0>2}:{ip_parts[3]:0>2}"

            # Check for duplicates
            for device in self.devices:
                if device.get("ip") == ip or device.get("mac") == mac:
                    self._refuse("That device is already in your list.")
                    return False

            # Create device
            new_device = {
                "mac": mac,
                "model": model or "Manual Device",
                "ip": ip,
                "port": port or 4003,
                "manual": True
            }

            # Save settings
            updated = self.devices + [new_device]
            if not self._write_settings({"devices": updated}, failure="Could not save the new device."):
                return False
            self.devices = [as_device(d).copy() for d in updated]

            # Emit signals
            self.device_added.emit(new_device)
            self.devices_discovered.emit(self.devices)
            self.status_updated.emit(f"Added device: {model} ({ip})")
            self.refresh_device_state_at(len(self.devices) - 1)

            return True

        except Exception:
            logger.warning("Adding a device failed", exc_info=True)
            self._refuse("Could not add the device.")
            return False

    def add_ble_device_manually(
        self,
        address: str,
        model: str = "iDotMatrix",
        matrix_size: str = "32x32",
        *,
        announce: bool = True,
    ) -> bool:
        """Add a Bluetooth-LE device (e.g. an iDotMatrix pixel display)."""
        try:
            address = (address or "").strip()
            if not address:
                if announce:
                    self._refuse("A Bluetooth address is required.")
                return False

            for device in self.devices:
                if device.get("ble_address") == address or device.get("mac") == address:
                    if announce:
                        self._refuse("That device is already in your list.")
                    return False

            new_device = {
                "mac": address,
                "ble_address": address,
                "model": model or "iDotMatrix",
                "transport": "ble",
                "matrix_size": matrix_size or "32x32",
                "manual": True,
            }
            updated = self.devices + [new_device]
            if not self._write_settings({"devices": updated}, failure="Could not save the new device."):
                return False
            self.devices = [as_device(d).copy() for d in updated]

            self.device_added.emit(new_device)
            self.devices_discovered.emit(self.devices)
            if announce:
                self.status_updated.emit(
                    f"Added Bluetooth device: {model} ({address})"
                )
            return True
        except Exception:
            logger.warning("Adding a Bluetooth device failed", exc_info=True)
            self._refuse("Could not add the Bluetooth device.")
            return False

    def add_tuya_device_manually(
        self,
        ip: str,
        device_id: str,
        local_key: str,
        model: str = "LSC / Tuya Light",
        protocol_version: str = "3.3",
        *, dp_schema: str = "v2", remember: bool = True,
    ) -> bool:
        """Add a Tuya / LSC Smart Connect WiFi light for local control."""
        try:
            ip = (ip or "").strip()
            device_id = (device_id or "").strip()
            local_key = (local_key or "").strip()
            if not (ip and device_id and local_key):
                self._refuse("Tuya devices need an IP, device ID and local key.")
                return False

            try:
                ipaddress.IPv4Address(ip)
            except ipaddress.AddressValueError:
                self._refuse("Enter a valid IPv4 address.")
                return False
            if len(local_key.encode("utf-8")) != 16 or protocol_version not in ("3.1", "3.2", "3.3", "3.4", "3.5"):
                self._refuse("Tuya requires a 16-byte local key and a supported protocol version.")
                return False
            if dp_schema not in ("v1", "v2"):
                self._refuse("Choose the modern or legacy light data point schema.")
                return False

            for device in self.devices:
                if device.get("device_id") == device_id or device.get("ip") == ip:
                    self._refuse("That device is already in your list.")
                    return False

            new_device = {
                "ip": ip,
                "device_id": device_id,
                "mac": f"tuya:{device_id}",
                "local_key": local_key,
                "model": model or "LSC / Tuya Light",
                "transport": "tuya",
                "protocol_version": protocol_version or "3.3",
                "dp_schema": dp_schema,
                "manual": True,
            }
            from ...accounts.secrets import protect_device
            new_device = protect_device(new_device, remember=remember)

            updated = self.devices + [new_device]
            if not self._write_settings({"devices": updated}, failure="Could not save the new device."):
                return False
            self.devices = [as_device(d).copy() for d in updated]

            self.device_added.emit(new_device)
            self.devices_discovered.emit(self.devices)
            self.status_updated.emit(f"Added Tuya device: {model} ({ip})")
            return True
        except Exception:
            logger.warning("Adding a Tuya device failed", exc_info=True)
            self._refuse("Could not add the Tuya device.")
            return False

    def remove_device(self, index: int) -> bool:
        """Remove device by index.

        Args:
            index: Index of the device to remove

        Returns:
            True if device was removed successfully, False otherwise
        """
        try:
            if not (0 <= index < len(self.devices)):
                return False

            removed = self.devices[index]
            if self.get_device_state_at(index).get("active_output"):
                self._refuse("Stop this device's active output before removing it.")
                return False
            updated = self.devices[:index] + self.devices[index + 1:]
            selected_index = self.selected_device_index

            # Adjust selected index
            if selected_index >= len(updated):
                selected_index = max(0, len(updated) - 1)
            elif selected_index > index:
                selected_index -= 1

            # Save settings
            if not self._write_settings(
                {"devices": updated, "selectedDevice": selected_index},
                failure="Could not save the device list.",
            ):
                return False
            self.devices = updated
            self.selected_device_index = selected_index
            self._command_queue.pop(self._device_key(removed), None)
            pool.close(removed)

            # Emit signals
            self.device_removed.emit(index)
            self.devices_discovered.emit(self.devices)
            self.status_updated.emit(f"Removed: {removed.get('model', 'Unknown')}")

            # Emit new selected device
            device = self.get_selected_device()
            if device:
                self.device_selected.emit(device)

            return True

        except Exception:
            logger.warning("Removing a device failed", exc_info=True)
            self._refuse("Could not remove the device.")
            return False

    def get_accounts(self) -> list[dict]:
        return self._load_settings_safe().get("accounts", [])

    def import_account_devices(self, metadata: dict, imported: list[dict]) -> bool:
        """Merge an account's devices and save its metadata.

        Returns False (after a status message) when settings could not be
        saved; the in-memory inventory is then left unchanged.
        """
        merged = devices.merge_discovered_devices(self.devices, imported)
        for device in merged:
            previous = next((d for d in self.devices if self._device_key(d) == self._device_key(device)), None)
            was_imported = any(set(devices._device_keys(device)) & set(devices._device_keys(raw)) for raw in imported)
            if was_imported and previous and previous.get("ip") and not is_cloud(previous):
                device["cloud_transport"] = device["transport"]
                device["transport"] = previous.get("transport", "lan")
                device["local_transport"] = device["transport"]
            if was_imported and previous and previous.get("connection_preference") == "local" and previous.get("local_transport"):
                device["cloud_transport"] = next(raw["transport"] for raw in imported if set(devices._device_keys(device)) & set(devices._device_keys(raw)))
                device["transport"] = previous["local_transport"]
        def update(settings: dict) -> dict:
            # Built from the file as it is now, so accounts saved meanwhile
            # (another dialog, another device refresh) are kept.
            accounts = [a for a in _saved_accounts(settings) if a.get("id") != metadata["id"]]
            accounts.append(dict(metadata))
            return {"devices": merged, "accounts": accounts, "selectedDevice": self.selected_device_index}

        if not self._write_settings(update, failure="Could not save the account's devices."):
            return False
        self.devices = merged
        self.devices_discovered.emit(self.devices)
        # Status only: Find devices reports one outcome for the whole search
        # and the Accounts dialog shows its own result line.
        self.status_updated.emit(f"Imported {len(imported)} account device(s)")
        # The account answered, so whatever was wrong with it is fixed.
        self.clear_account_issue(metadata.get("id"))
        return True

    def _forget_accounts(self, identities: set[str], *, failure: str) -> bool:
        """Remove account metadata and unlink its devices, in one save."""
        updated = [as_device(d).copy() for d in self.devices]
        for device in updated:
            if device.get("account_id") in identities:
                device.pop("account_id", None)

        def update(settings: dict) -> dict:
            accounts = [a for a in _saved_accounts(settings) if a.get("id") not in identities]
            return {"accounts": accounts, "devices": updated}

        if not self._write_settings(update, failure=failure):
            return False
        self.devices = updated
        self.devices_discovered.emit(self.devices)
        for identity in identities:
            self.clear_account_issue(identity)
        return True

    def remove_account(self, identity: str) -> bool:
        """Forget an account's metadata, whether or not credentials were stored.

        Returns False (after a status message) when settings could not be
        saved.
        """
        return self._forget_accounts({identity}, failure="Could not remove the account from your settings.")

    def _prune_session_accounts(self, accounts) -> None:
        """Drop accounts connected for one session only (Remember off).

        Their credentials lived in memory and are gone after a restart, so the
        metadata would only show an account that can neither be used nor
        disconnected.
        """
        if not isinstance(accounts, list):
            return
        stale = {
            str(account["id"])
            for account in accounts
            if isinstance(account, dict) and account.get("id")
            and not account.get("remember", True)
        }
        if stale and self._forget_accounts(stale, failure="Could not remove expired session accounts."):
            logger.info("Removed %d session-only account(s) from settings", len(stale))

    def set_connection_at(self, index: int, transport: str) -> bool:
        device = self._device_at(index)
        if not device or transport not in (device.get("local_transport"), device.get("cloud_transport")):
            return False
        if self.get_device_state_at(index).get("active_output") or self._device_key(device) in self._command_tasks:
            self._refuse("Finish the current output before changing this device's connection.")
            self.device_updated.emit(index, dict(device))
            return False
        updated = [as_device(d).copy() for d in self.devices]
        updated[index]["transport"] = transport
        updated[index]["connection_preference"] = "cloud" if is_cloud(updated[index]) else "local"
        if not self._write_settings({"devices": updated}, failure="Could not save the connection change."):
            # Put the inspector's connection choice back to what is in use.
            self.device_updated.emit(index, dict(device))
            return False
        pool.close(device)
        self.devices = updated
        self.devices_discovered.emit(self.devices)
        self.refresh_device_state_at(index)
        return True

