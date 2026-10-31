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


class DeviceAdminMixin:

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
                self.status_updated.emit(f"Invalid IP address: {ip}")
                return False

            # Generate MAC if not provided
            if not mac:
                ip_parts = ip.split('.')
                mac = f"00:00:{ip_parts[0]:0>2}:{ip_parts[1]:0>2}:{ip_parts[2]:0>2}:{ip_parts[3]:0>2}"

            # Check for duplicates
            for device in self.devices:
                if device.get("ip") == ip or device.get("mac") == mac:
                    self.status_updated.emit("Device already exists")
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
            try:
                settings = devices.get_data(refresh=False)
            except Exception:
                settings = {"devices": [], "selectedDevice": 0, "time": 0}

            settings["devices"] = self.devices + [new_device]
            devices.writeJSON(settings)
            self.devices = [as_device(d).copy() for d in settings["devices"]]

            # Emit signals
            self.device_added.emit(new_device)
            self.devices_discovered.emit(self.devices)
            self.status_updated.emit(f"Added device: {model} ({ip})")
            self.refresh_device_state_at(len(self.devices) - 1)

            return True

        except Exception as e:
            self.status_updated.emit(f"Error adding device: {str(e)}")
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
                    self.status_updated.emit("A Bluetooth address is required.")
                return False

            for device in self.devices:
                if device.get("ble_address") == address or device.get("mac") == address:
                    if announce:
                        self.status_updated.emit("Device already exists")
                    return False

            new_device = {
                "mac": address,
                "ble_address": address,
                "model": model or "iDotMatrix",
                "transport": "ble",
                "matrix_size": matrix_size or "32x32",
                "manual": True,
            }
            try:
                settings = devices.get_data(refresh=False)
            except Exception:
                settings = {"devices": [], "selectedDevice": 0, "time": 0}
            settings["devices"] = self.devices + [new_device]
            devices.writeJSON(settings)
            self.devices = [as_device(d).copy() for d in settings["devices"]]

            self.device_added.emit(new_device)
            self.devices_discovered.emit(self.devices)
            if announce:
                self.status_updated.emit(
                    f"Added Bluetooth device: {model} ({address})"
                )
            return True
        except Exception as e:
            self.status_updated.emit(f"Error adding device: {str(e)}")
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
                self.status_updated.emit(
                    "Tuya devices need an IP, device ID and local key."
                )
                return False

            try:
                ipaddress.IPv4Address(ip)
            except ipaddress.AddressValueError:
                self.status_updated.emit("Enter a valid IPv4 address.")
                return False
            if len(local_key.encode("utf-8")) != 16 or protocol_version not in ("3.1", "3.2", "3.3", "3.4", "3.5"):
                self.status_updated.emit("Tuya requires a 16-byte local key and a supported protocol version.")
                return False
            if dp_schema not in ("v1", "v2"):
                self.status_updated.emit("Choose the modern or legacy light data point schema.")
                return False

            for device in self.devices:
                if device.get("device_id") == device_id or device.get("ip") == ip:
                    self.status_updated.emit("Device already exists")
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

            try:
                settings = devices.get_data(refresh=False)
            except Exception:
                settings = {"devices": [], "selectedDevice": 0, "time": 0}
            settings["devices"] = self.devices + [new_device]
            devices.writeJSON(settings)
            self.devices = [as_device(d).copy() for d in settings["devices"]]

            self.device_added.emit(new_device)
            self.devices_discovered.emit(self.devices)
            self.status_updated.emit(f"Added Tuya device: {model} ({ip})")
            return True
        except Exception as e:
            self.status_updated.emit(f"Error adding device: {str(e)}")
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
                self.status_updated.emit("Stop this device's active output before removing it.")
                return False
            updated = self.devices[:index] + self.devices[index + 1:]
            selected_index = self.selected_device_index

            # Adjust selected index
            if selected_index >= len(updated):
                selected_index = max(0, len(updated) - 1)
            elif selected_index > index:
                selected_index -= 1

            # Save settings
            try:
                settings = devices.get_data(refresh=False)
            except Exception:
                settings = {"devices": [], "selectedDevice": 0, "time": 0}

            settings["devices"] = updated
            settings["selectedDevice"] = selected_index
            devices.writeJSON(settings)
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

        except Exception as e:
            self.status_updated.emit(f"Error removing device: {str(e)}")
            return False

    def get_accounts(self) -> list[dict]:
        return self._load_settings_safe().get("accounts", [])

    def import_account_devices(self, metadata: dict, imported: list[dict]) -> None:
        settings = self._load_settings_safe()
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
        accounts = [a for a in settings.get("accounts", []) if a.get("id") != metadata["id"]]
        accounts.append(dict(metadata))
        settings.update({"devices": merged, "accounts": accounts, "selectedDevice": self.selected_device_index})
        devices.writeJSON(settings)
        self.devices = merged
        self.devices_discovered.emit(self.devices)
        self.status_updated.emit(f"Imported {len(imported)} account device(s)")

    def remove_account(self, identity: str) -> None:
        settings = self._load_settings_safe()
        settings["accounts"] = [a for a in settings.get("accounts", []) if a.get("id") != identity]
        updated = [as_device(d).copy() for d in self.devices]
        for device in updated:
            if device.get("account_id") == identity:
                device.pop("account_id", None)
        settings["devices"] = updated
        devices.writeJSON(settings)
        self.devices = updated
        self.devices_discovered.emit(self.devices)

    def set_connection_at(self, index: int, transport: str) -> None:
        device = self._device_at(index)
        if not device or transport not in (device.get("local_transport"), device.get("cloud_transport")):
            return
        if self.get_device_state_at(index).get("active_output") or self._device_key(device) in self._command_tasks:
            self.status_updated.emit("Finish the current output before changing this device's connection.")
            self.device_updated.emit(index, dict(device))
            return
        updated = [as_device(d).copy() for d in self.devices]
        updated[index]["transport"] = transport
        updated[index]["connection_preference"] = "cloud" if is_cloud(updated[index]) else "local"
        settings = self._load_settings_safe()
        settings["devices"] = updated
        devices.writeJSON(settings)
        pool.close(device)
        self.devices = updated
        self.devices_discovered.emit(self.devices)
        self.refresh_device_state_at(index)

