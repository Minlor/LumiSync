"""
Device controller for the LumiSync GUI.
This module handles device discovery and management with PySide6 signals.
"""

import socket
import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal, QThread, QTimer

from ... import connection, devices, groups
from ...drivers import pool
from ...drivers.registry import create_adapter


class DeviceDiscoveryWorker(QObject):
    """Worker object for device discovery in a separate thread."""

    # Signals
    finished = Signal(list, int, int)  # devices, selected_index, LAN replies
    error = Signal(str)  # error message

    def run(self):
        """Run device discovery in background thread.

        Falls back to a unicast subnet sweep when the multicast scan finds
        nothing, so devices are still discovered on multicast-hostile networks.
        """
        try:
            settings = devices.discover_lan_devices(preserve_existing=True)

            if int(settings.get("lastDiscoveryCount", 0)) == 0:
                settings = devices.discover_lan_devices(
                    preserve_existing=True, deep=True
                )

            # Emit success signal with discovered devices
            self.finished.emit(
                settings["devices"],
                settings["selectedDevice"],
                int(settings.get("lastDiscoveryCount", 0)),
            )

        except Exception as e:
            # Emit error signal
            self.error.emit(str(e))


class DeviceStatusWorker(QObject):
    """Query current LAN state for one or more devices off the GUI thread."""

    state_updated = Signal(int, dict)
    finished = Signal()
    error = Signal(str)

    def __init__(self, indexed_devices: List[tuple[int, Dict[str, Any]]]):
        super().__init__()
        self.indexed_devices = [(i, dict(d)) for i, d in indexed_devices]

    def run(self) -> None:
        server = None
        try:
            if any(device.get("ip") for _, device in self.indexed_devices):
                try:
                    server = connection.create_lan_socket(timeout=0.45)
                except OSError as exc:
                    server = None
                    self.error.emit(str(exc))

            for index, device in self.indexed_devices:
                try:
                    status = None
                    if server is not None and device.get("ip"):
                        status = connection.query_status(server, device, timeout=0.45)
                    if status is None:
                        self.state_updated.emit(
                            index,
                            {
                                "online": False,
                                "stale": True,
                                "status_source": "offline",
                                "readback_supported": True,
                                "last_error": "No status reply",
                            },
                        )
                    else:
                        status.update(
                            {
                                "online": True,
                                "stale": False,
                                "status_source": "confirmed",
                                "readback_supported": True,
                                "last_error": None,
                                "last_seen": time.time(),
                            }
                        )
                        self.state_updated.emit(index, status)
                except Exception as exc:
                    self.state_updated.emit(
                        index,
                        {
                            "online": False,
                            "stale": True,
                            "status_source": "offline",
                            "readback_supported": True,
                            "last_error": str(exc),
                        },
                    )
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            if server is not None:
                try:
                    server.close()
                except Exception:
                    pass
            self.finished.emit()


class BleScanWorker(QObject):
    """Run the (blocking, ~8s) BLE discovery scan off the GUI thread."""

    finished = Signal(list)  # raw discovery entries
    error = Signal(str)

    def run(self) -> None:
        try:
            from ...drivers.idotmatrix_ble import IDotMatrixBleAdapter

            self.finished.emit(IDotMatrixBleAdapter.discover(timeout=8.0))
        except Exception as e:
            self.error.emit(str(e))


class DeviceController(QObject):
    """Controller for device management with PySide6 signals."""

    # Signals
    status_updated = Signal(str)  # Status message
    devices_discovered = Signal(list)  # List of devices
    device_selected = Signal(dict)  # Selected device info
    device_added = Signal(dict)  # Newly added device
    device_removed = Signal(int)  # Index of removed device
    device_updated = Signal(int, dict)  # Index, updated device
    device_state_updated = Signal(int, dict)  # Index, cached state
    discovery_started = Signal()  # Discovery process started
    discovery_finished = Signal()  # Discovery process finished
    ble_scan_started = Signal()  # Bluetooth scan started
    ble_scan_finished = Signal()  # Bluetooth scan finished
    device_search_started = Signal()  # Combined LAN + Bluetooth search started
    device_search_finished = Signal(dict)  # Per-transport availability/results
    groups_changed = Signal(list)  # Updated list of sync groups

    def __init__(self):
        """Initialize the device controller."""
        super().__init__()

        self.devices: List[Dict[str, Any]] = []
        self.selected_device_index: int = 0
        self.discovery_thread: Optional[QThread] = None
        self.discovery_worker: Optional[DeviceDiscoveryWorker] = None
        self.status_thread: Optional[QThread] = None
        self.status_worker: Optional[DeviceStatusWorker] = None
        self._ble_scan_thread: Optional[QThread] = None
        self._ble_scan_worker: Optional[BleScanWorker] = None
        self._combined_search_active = False
        self._search_pending: set[str] = set()
        self._search_results: Dict[str, Dict[str, Any]] = {}
        self.server = None
        self.device_states: Dict[str, Dict[str, Any]] = {}

        # Initialize devices on startup
        self._init_devices()

    def _init_devices(self):
        """Initialize with existing devices from settings."""
        try:
            settings = devices.get_data()
            self.devices = settings["devices"]
            self.selected_device_index = settings["selectedDevice"]

            if self.devices:
                self.status_updated.emit(f"Found {len(self.devices)} device(s)")
                self.devices_discovered.emit(self.devices)

                device = self.get_selected_device()
                if device:
                    self.device_selected.emit(device)
                    self.status_updated.emit(
                        f"Selected device: {device.get('model', 'Unknown')}"
                    )
        except Exception as e:
            self.status_updated.emit(f"Error loading devices: {str(e)}")

    def _discovery_running(self) -> bool:
        """Safe check that handles a stale Python ref to a deleted QThread."""
        if self.discovery_thread is None:
            return False
        try:
            return self.discovery_thread.isRunning()
        except RuntimeError:
            self.discovery_thread = None
            self.discovery_worker = None
            return False

    def _clear_discovery_refs(self) -> None:
        self.discovery_thread = None
        self.discovery_worker = None

    def _status_running(self) -> bool:
        if self.status_thread is None:
            return False
        try:
            return self.status_thread.isRunning()
        except RuntimeError:
            self.status_thread = None
            self.status_worker = None
            return False

    def _clear_status_refs(self) -> None:
        self.status_thread = None
        self.status_worker = None

    def _ble_scan_running(self) -> bool:
        if self._ble_scan_thread is None:
            return False
        try:
            return self._ble_scan_thread.isRunning()
        except RuntimeError:
            self._clear_ble_scan_refs()
            return False

    @staticmethod
    def _local_network_available() -> bool:
        """Return whether Windows exposes a usable non-loopback IPv4 address."""
        try:
            addresses = socket.getaddrinfo(
                socket.gethostname(), None, socket.AF_INET, socket.SOCK_DGRAM
            )
        except OSError:
            return False
        return any(
            address and not address.startswith("127.") and address != "0.0.0.0"
            for *_, sockaddr in addresses
            for address in [sockaddr[0]]
        )

    def find_devices(self) -> None:
        """Search LAN and Bluetooth together, then refresh all known states."""
        if (
            self._combined_search_active
            or self._discovery_running()
            or self._ble_scan_running()
        ):
            self.status_updated.emit("A device search is already in progress.")
            return

        has_network = self._local_network_available()
        self._combined_search_active = True
        self._search_pending = {"bluetooth"}
        self._search_results = {
            "lan": {
                "available": has_network,
                "found": 0,
                "error": None if has_network else "No active local network connection",
            },
            "bluetooth": {
                "available": None,
                "found": 0,
                "added": 0,
                "seen": 0,
                "error": None,
            },
        }
        if has_network:
            self._search_pending.add("lan")

        self.device_search_started.emit()
        self.status_updated.emit(
            "Finding devices on the local network and over Bluetooth..."
        )

        if has_network:
            if not self._start_lan_discovery(announce=False):
                self._search_results["lan"].update(
                    {"available": False, "error": "Search could not be started"}
                )
                self._finish_search_transport("lan")
        if not self._start_ble_scan(announce=False):
            self._search_results["bluetooth"].update(
                {"available": False, "error": "Search could not be started"}
            )
            self._finish_search_transport("bluetooth")

    def discover_devices(self):
        """Start device discovery in background thread."""
        self._start_lan_discovery(announce=True)

    def _start_lan_discovery(self, *, announce: bool) -> bool:
        if self._discovery_running():
            if announce:
                self.status_updated.emit("Local-network discovery is already in progress.")
            return False

        self.discovery_started.emit()
        if announce:
            self.status_updated.emit("Searching the local network for devices...")

        thread = QThread()
        worker = DeviceDiscoveryWorker()
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_discovery_finished)
        worker.error.connect(self._on_discovery_error)

        # Cleanup chain: worker done (success or error) → thread.quit() → both
        # objects are deleteLater'd → Python refs zeroed so a re-click sees
        # a clean slate.
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_discovery_refs)

        self.discovery_thread = thread
        self.discovery_worker = worker
        thread.start()
        return True

    def _on_discovery_finished(
        self,
        discovered_devices: List[Dict],
        selected_index: int,
        last_discovery_count: int,
    ):
        """Handle discovery completion.

        Args:
            discovered_devices: List of discovered devices
            selected_index: Index of the selected device
        """
        selected_key = None
        selected = self.get_selected_device()
        if selected:
            selected_key = self._device_key(selected)

        # LAN and Bluetooth search concurrently. Preserve a Bluetooth device
        # that may have been added while the LAN worker was still running.
        merged_devices = [dict(device) for device in discovered_devices]
        discovered_keys = {self._device_key(device) for device in merged_devices}
        for device in self.devices:
            key = self._device_key(device)
            if key not in discovered_keys:
                merged_devices.append(dict(device))
                discovered_keys.add(key)

        self.devices = merged_devices
        if selected_key:
            self.selected_device_index = next(
                (
                    index
                    for index, device in enumerate(self.devices)
                    if self._device_key(device) == selected_key
                ),
                min(selected_index, max(0, len(self.devices) - 1)),
            )
        else:
            self.selected_device_index = min(
                selected_index, max(0, len(self.devices) - 1)
            )

        if self._combined_search_active:
            self._search_results["lan"].update(
                {"available": True, "found": last_discovery_count, "error": None}
            )
        elif last_discovery_count == 0 and self.devices:
            self.status_updated.emit(
                f"No new LAN devices found; kept {len(self.devices)} saved device(s)"
            )
        elif not self._combined_search_active:
            self.status_updated.emit(f"Found {len(self.devices)} device(s)")
        self.devices_discovered.emit(self.devices)
        self.discovery_finished.emit()
        if self._combined_search_active:
            self._finish_search_transport("lan")
        else:
            self.refresh_device_states()

        # Emit selected device
        device = self.get_selected_device()
        if device:
            self.device_selected.emit(device)

    def _on_discovery_error(self, error_message: str):
        """Handle discovery error.

        Args:
            error_message: Error message from discovery
        """
        if self._combined_search_active:
            self._search_results["lan"].update(
                {"available": False, "found": 0, "error": error_message}
            )
        else:
            self.status_updated.emit(f"Local-network search unavailable: {error_message}")
        self.discovery_finished.emit()
        if self._combined_search_active:
            self._finish_search_transport("lan")

    def get_devices(self) -> List[Dict[str, Any]]:
        """Get the list of discovered devices.

        Returns:
            List of device dictionaries
        """
        try:
            settings = devices.get_data()
            self.devices = settings["devices"]
            self.selected_device_index = settings["selectedDevice"]
            return self.devices
        except Exception as e:
            self.status_updated.emit(f"Error getting devices: {str(e)}")
            return []

    def _device_key(self, device: Dict[str, Any]) -> str:
        return str(device.get("mac") or device.get("ip") or device.get("model") or "?")

    def _default_state(self, device: Dict[str, Any]) -> Dict[str, Any]:
        is_ble = self._is_ble(device)
        return {
            "power_on": None,
            "brightness": None,
            "color": None,
            "color_temp": None,
            "online": False,
            "stale": not is_ble,
            "status_source": "unknown" if is_ble else "pending",
            "readback_supported": not is_ble,
            "last_seen": None,
            "last_command_at": None,
            "last_output": None,
            "active_output": None,
            "last_error": None,
            "device_key": self._device_key(device),
        }

    def get_device_state_at(self, index: int) -> Dict[str, Any]:
        device = self._device_at(index)
        if not device:
            return {}
        key = self._device_key(device)
        if key not in self.device_states:
            self.device_states[key] = self._default_state(device)
        return dict(self.device_states[key])

    def _merge_device_state(self, index: int, updates: Dict[str, Any]) -> None:
        device = self._device_at(index)
        if not device:
            return
        key = self._device_key(device)
        state = self.device_states.get(key, self._default_state(device))
        for field, value in updates.items():
            if field == "color" and value is None and state.get("color") is not None:
                continue
            state[field] = value
        state["device_key"] = key
        self.device_states[key] = state
        self.device_state_updated.emit(index, dict(state))

    def _record_command_state(
        self,
        index: int,
        updates: Dict[str, Any],
        *,
        output: Optional[str] = None,
    ) -> None:
        """Cache a successful command without pretending BLE has readback."""
        device = self._device_at(index)
        if not device:
            return
        is_ble = self._is_ble(device)
        payload = {
            "online": True,
            "stale": not is_ble,
            "status_source": "commanded" if is_ble else "pending",
            "readback_supported": not is_ble,
            "last_seen": time.time(),
            "last_command_at": time.time(),
            "last_error": None,
        }
        payload.update(updates)
        if output:
            payload["last_output"] = output
        self._merge_device_state(index, payload)

    def mark_sync_active(
        self, mode: str, active_devices: List[Dict[str, Any]]
    ) -> None:
        """Expose active monitor/music output on matching device cards."""
        active_keys = {self._device_key(device) for device in active_devices}
        label = "Monitor sync" if mode == "monitor" else "Music sync"
        for index, device in enumerate(self.devices):
            if self._device_key(device) in active_keys:
                self._merge_device_state(
                    index,
                    {
                        "active_output": label,
                        "online": True,
                        "last_seen": time.time(),
                        "last_error": None,
                    },
                )

    def clear_sync_activity(self) -> None:
        for index in range(len(self.devices)):
            state = self.get_device_state_at(index)
            if state.get("active_output") in {"Monitor sync", "Music sync"}:
                self._merge_device_state(index, {"active_output": None})

    def mark_device_output(
        self,
        device: Dict[str, Any],
        label: str,
        *,
        active: bool = False,
    ) -> None:
        """Record output produced outside the standard color controls."""
        key = self._device_key(device)
        index = next(
            (
                candidate
                for candidate, saved in enumerate(self.devices)
                if self._device_key(saved) == key
            ),
            -1,
        )
        if index < 0:
            return
        updates: Dict[str, Any] = {
            "active_output": label if active else None,
            "last_output": None if active else label,
        }
        self._record_command_state(index, updates, output=None)

    def clear_device_activity(self, device: Dict[str, Any]) -> None:
        key = self._device_key(device)
        for index, saved in enumerate(self.devices):
            if self._device_key(saved) == key:
                self._merge_device_state(index, {"active_output": None})
                return

    def refresh_device_states(self) -> None:
        """Refresh current state for all known devices."""
        indexes = list(range(len(self.devices)))
        self._start_status_refresh(indexes)

    def refresh_device_state_at(self, index: int) -> None:
        """Refresh current state for one device."""
        self._start_status_refresh([index])

    def _schedule_status_refresh(self, index: int) -> None:
        """Confirm an optimistic command with a few short LAN status retries."""
        device = self._device_at(index)
        if not device or self._is_ble(device):
            return
        for delay_ms in (250, 700, 1300):
            QTimer.singleShot(
                delay_ms,
                lambda idx=index: self.refresh_device_state_at(idx),
            )

    def _start_status_refresh(self, indexes: List[int]) -> None:
        if self._status_running():
            return

        indexed_devices = []
        for idx in indexes:
            if not (0 <= idx < len(self.devices)):
                continue
            device = self.devices[idx]
            if self._is_ble(device):
                state = self.get_device_state_at(idx)
                if state.get("status_source") == "pending":
                    self._merge_device_state(
                        idx,
                        {
                            "stale": False,
                            "status_source": "unknown",
                            "readback_supported": False,
                            "last_error": None,
                        },
                    )
                continue
            indexed_devices.append((idx, device))
        if not indexed_devices:
            return

        thread = QThread()
        worker = DeviceStatusWorker(indexed_devices)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.state_updated.connect(self._merge_device_state)
        worker.error.connect(
            lambda msg: self.status_updated.emit(f"Status refresh error: {msg}")
        )
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_status_refs)

        self.status_thread = thread
        self.status_worker = worker
        thread.start()

    def select_device(self, index: int):
        """Select a device by index.

        Args:
            index: Index of the device to select
        """
        if not (0 <= index < len(self.devices)):
            return

        self.selected_device_index = index

        # Update settings
        try:
            settings = devices.get_data()
            settings["selectedDevice"] = index
            devices.writeJSON(settings)

            device = self.get_selected_device()
            self.device_selected.emit(device)
            self.status_updated.emit(
                f"Selected device: {device.get('model', 'Unknown')}"
            )
            self.refresh_device_state_at(index)
        except Exception as e:
            self.status_updated.emit(f"Error selecting device: {str(e)}")

    def get_selected_device(self) -> Optional[Dict[str, Any]]:
        """Get the currently selected device.

        Returns:
            Selected device dictionary or None if no device selected
        """
        if self.devices and 0 <= self.selected_device_index < len(self.devices):
            return self.devices[self.selected_device_index]
        return None

    def _can_try_lan(self, device: Dict[str, Any]) -> bool:
        return bool(device.get("ip"))

    def _is_ble(self, device: Dict[str, Any]) -> bool:
        return str(device.get("transport", "")).lower() == "ble"

    def _run_adapter(self, device: Dict[str, Any], action) -> str:
        """Run a control action through the device's transport adapter.

        BLE devices use a shared, persistent connection from the pool (no
        reconnect per action). Govee devices open a short-lived LAN socket per
        action, matching the previous behavior.
        """
        if self._is_ble(device):
            action(pool.acquire(device))
            return "BLE"

        if not self._can_try_lan(device):
            raise RuntimeError(
                "Device has no LAN IP. Discover it on the same network or add it manually."
            )
        adapter = create_adapter(device)
        try:
            action(adapter)
        finally:
            try:
                adapter.close()
            except Exception:
                pass
        return "LAN"

    def _send_turn(self, device: Dict[str, Any], on: bool) -> str:
        return self._run_adapter(device, lambda adapter: adapter.set_power(on))

    def _send_brightness(self, device: Dict[str, Any], brightness: int) -> str:
        return self._run_adapter(
            device, lambda adapter: adapter.set_brightness(brightness)
        )

    def _send_color(self, device: Dict[str, Any], r: int, g: int, b: int) -> str:
        return self._run_adapter(device, lambda adapter: adapter.set_color(r, g, b))

    def _send_color_temp(self, device: Dict[str, Any], kelvin: int) -> str:
        return self._run_adapter(
            device, lambda adapter: adapter.set_color_temperature(kelvin)
        )

    def get_capabilities_at(self, index: int):
        """Return the SKU capabilities for a device, or None."""
        from ...sku_catalog import capabilities_for

        device = self._device_at(index)
        if not device:
            return None
        return capabilities_for(device.get("model") or device.get("sku"))

    def supports_color_temp_at(self, index: int) -> bool:
        cap = self.get_capabilities_at(index)
        return bool(cap and cap.color_temp_max > cap.color_temp_min > 0)

    def set_color_temperature_at(self, index: int, kelvin: int) -> None:
        """Set a device's tunable-white color temperature (Kelvin)."""
        device = self._device_at(index)
        if not device:
            return
        try:
            cap = self.get_capabilities_at(index)
            if cap and cap.color_temp_max > 0:
                kelvin = max(cap.color_temp_min, min(cap.color_temp_max, int(kelvin)))
            transport = self._send_color_temp(device, int(kelvin))
            from ...utils.colors import kelvin_to_rgb

            self._record_command_state(
                index,
                {
                    "color": kelvin_to_rgb(int(kelvin)),
                    "color_temp": int(kelvin),
                },
                output=f"White {int(kelvin)}K",
            )
            self.status_updated.emit(
                f"{device.get('model', 'Device')}: white {int(kelvin)}K via {transport}"
            )
            self._schedule_status_refresh(index)
        except Exception as e:
            self.status_updated.emit(f"Error: {str(e)}")

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
                socket.inet_aton(ip)
            except socket.error:
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

            # Add to list
            self.devices.append(new_device)

            # Save settings
            try:
                settings = devices.get_data()
            except Exception:
                settings = {"devices": [], "selectedDevice": 0, "time": 0}

            settings["devices"] = self.devices
            devices.writeJSON(settings)

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
            self.devices.append(new_device)

            try:
                settings = devices.get_data()
            except Exception:
                settings = {"devices": [], "selectedDevice": 0, "time": 0}
            settings["devices"] = self.devices
            devices.writeJSON(settings)

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

            for device in self.devices:
                if device.get("device_id") == device_id or device.get("ip") == ip:
                    self.status_updated.emit("Device already exists")
                    return False

            new_device = {
                "ip": ip,
                "device_id": device_id,
                "local_key": local_key,
                "model": model or "LSC / Tuya Light",
                "transport": "tuya",
                "protocol_version": protocol_version or "3.3",
                "manual": True,
            }
            self.devices.append(new_device)

            try:
                settings = devices.get_data()
            except Exception:
                settings = {"devices": [], "selectedDevice": 0, "time": 0}
            settings["devices"] = self.devices
            devices.writeJSON(settings)

            self.device_added.emit(new_device)
            self.devices_discovered.emit(self.devices)
            self.status_updated.emit(f"Added Tuya device: {model} ({ip})")
            return True
        except Exception as e:
            self.status_updated.emit(f"Error adding device: {str(e)}")
            return False

    def scan_ble_devices(self) -> None:
        """Scan for iDotMatrix devices over BLE and add likely matches.

        The scan itself blocks for ~8 s, so it runs in a worker thread;
        results are processed back on the GUI thread. Auto-adds devices whose
        advertisement looks like an iDotMatrix panel; if none match, reports
        what was seen so the user can add by address instead.
        """
        self._start_ble_scan(announce=True)

    def _start_ble_scan(self, *, announce: bool) -> bool:
        if self._ble_scan_running():
            if announce:
                self.status_updated.emit("Bluetooth search is already in progress.")
            return False

        self.ble_scan_started.emit()
        if announce:
            self.status_updated.emit("Searching for nearby Bluetooth devices...")

        thread = QThread()
        worker = BleScanWorker()
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_ble_scan_finished)
        worker.error.connect(self._on_ble_scan_error)

        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_ble_scan_refs)

        self._ble_scan_thread = thread
        self._ble_scan_worker = worker
        thread.start()
        return True

    def _clear_ble_scan_refs(self) -> None:
        self._ble_scan_thread = None
        self._ble_scan_worker = None

    def _on_ble_scan_error(self, message: str) -> None:
        if self._combined_search_active:
            self._search_results["bluetooth"].update(
                {"available": False, "found": 0, "error": message}
            )
        else:
            self.status_updated.emit(f"Bluetooth search unavailable: {message}")
        self.ble_scan_finished.emit()
        if self._combined_search_active:
            self._finish_search_transport("bluetooth")

    def _on_ble_scan_finished(self, found: List[Dict[str, Any]]) -> None:
        likely = [d for d in found if d.get("likely")]
        added = 0
        for entry in likely:
            if self.add_ble_device_manually(
                entry.get("ble_address", ""),
                entry.get("model") or "iDotMatrix",
                announce=not self._combined_search_active,
            ):
                added += 1

        visible_addresses = {
            str(entry.get("ble_address") or "").casefold()
            for entry in found
            if entry.get("ble_address")
        }
        for index, device in enumerate(self.devices):
            if not self._is_ble(device):
                continue
            address = str(
                device.get("ble_address") or device.get("mac") or ""
            ).casefold()
            if address and address in visible_addresses:
                self._merge_device_state(
                    index,
                    {
                        "online": True,
                        "stale": False,
                        "status_source": "seen",
                        "readback_supported": False,
                        "last_seen": time.time(),
                        "last_error": None,
                    },
                )
                continue

            state = self.get_device_state_at(index)
            # A connected BLE panel often stops advertising. Keep a successful
            # command or active stream as the stronger signal; otherwise say
            # only that the latest search could not see it.
            if state.get("status_source") != "commanded" and not state.get(
                "active_output"
            ):
                self._merge_device_state(
                    index,
                    {
                        "online": False,
                        "stale": False,
                        "status_source": "not_seen",
                        "readback_supported": False,
                        "last_error": None,
                    },
                )

        if self._combined_search_active:
            self._search_results["bluetooth"].update(
                {
                    "available": True,
                    "found": len(likely),
                    "added": added,
                    "seen": len(found),
                    "error": None,
                }
            )
        elif added:
            self.status_updated.emit(
                f"Bluetooth scan added {added} iDotMatrix device(s)."
            )
        elif found:
            names = sorted(
                {
                    d["model"]
                    for d in found
                    if d.get("model") and d["model"] != "Unknown"
                }
            )
            seen = ", ".join(names[:8]) if names else "unnamed devices"
            self.status_updated.emit(
                f"No iDotMatrix panel matched among {len(found)} Bluetooth device(s). "
                f"Seen: {seen}. Disconnect the phone app from the panel and rescan, "
                f"or use 'Add Manually' with its Bluetooth address."
            )
        else:
            self.status_updated.emit(
                "No Bluetooth devices found. Turn Bluetooth on and make sure the "
                "panel is advertising (disconnect it from the phone app first)."
            )
        self.ble_scan_finished.emit()
        if self._combined_search_active:
            self._finish_search_transport("bluetooth")

    def _finish_search_transport(self, transport: str) -> None:
        self._search_pending.discard(transport)
        if self._search_pending or not self._combined_search_active:
            return

        summary = {
            name: dict(result) for name, result in self._search_results.items()
        }
        self._combined_search_active = False
        self.device_search_finished.emit(summary)

        lan = summary.get("lan", {})
        bluetooth = summary.get("bluetooth", {})
        issues = []
        if not lan.get("available"):
            issues.append("local network unavailable")
        if not bluetooth.get("available"):
            issues.append("Bluetooth unavailable")

        found = int(lan.get("found", 0)) + int(bluetooth.get("found", 0))
        if issues:
            self.status_updated.emit(
                "Device search complete — " + "; ".join(issues) + "."
            )
        elif found:
            self.status_updated.emit(
                f"Device search complete — found {found} supported "
                f"device{'s' if found != 1 else ''}."
            )
        else:
            self.status_updated.emit(
                "Device search complete — no new supported devices found."
            )
        self.refresh_device_states()

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

            removed = self.devices.pop(index)
            pool.close(removed)  # drop any persistent BLE connection

            # Adjust selected index
            if self.selected_device_index >= len(self.devices):
                self.selected_device_index = max(0, len(self.devices) - 1)
            elif self.selected_device_index > index:
                self.selected_device_index -= 1

            # Save settings
            try:
                settings = devices.get_data()
            except Exception:
                settings = {"devices": [], "selectedDevice": 0, "time": 0}

            settings["devices"] = self.devices
            settings["selectedDevice"] = self.selected_device_index
            devices.writeJSON(settings)

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

    def set_zone_count_at(self, index: int, zone_count: Optional[int]) -> bool:
        """Set or clear a per-device LED/zone count override."""
        device = self._device_at(index)
        if not device:
            return False

        try:
            if zone_count is None:
                device.pop("segment_count_override", None)
                message = f"{device.get('model', 'Device')}: using default zone count"
            else:
                count = int(zone_count)
                if not 1 <= count <= 255:
                    raise ValueError("Zone count must be between 1 and 255")
                device["segment_count_override"] = count
                message = f"{device.get('model', 'Device')}: zone count set to {count}"

            self.devices[index] = device
            try:
                settings = devices.get_data()
            except Exception:
                settings = {"devices": [], "selectedDevice": self.selected_device_index, "time": 0}

            settings["devices"] = self.devices
            settings["selectedDevice"] = self.selected_device_index
            devices.writeJSON(settings)

            self.device_updated.emit(index, dict(device))
            self.devices_discovered.emit(self.devices)
            selected = self.get_selected_device()
            if selected and index == self.selected_device_index:
                self.device_selected.emit(selected)
            self.status_updated.emit(message)
            return True
        except Exception as e:
            self.status_updated.emit(f"Error: {str(e)}")
            return False

    def turn_on_off(self, on: bool = True):
        """Turn the selected device on or off.

        Args:
            on: True to turn on, False to turn off
        """
        try:
            device = self.get_selected_device()
            if not device:
                self.status_updated.emit("No device selected")
                return

            transport = self._send_turn(device, on)
            self._record_command_state(
                self.selected_device_index,
                {
                    "power_on": on,
                },
                output="Off" if not on else "On",
            )
            self.status_updated.emit(
                f"Device turned {'on' if on else 'off'} via {transport}"
            )
            self._schedule_status_refresh(self.selected_device_index)
        except Exception as e:
            self.status_updated.emit(f"Error: {str(e)}")

    def set_razer_mode(self, on: bool = True):
        """Set the device to Razer mode.

        Args:
            on: True to enable Razer mode, False to disable
        """
        try:
            device = self.get_selected_device()
            if not device:
                self.status_updated.emit("No device selected")
                return

            self._ensure_server()
            try:
                connection.switch_razer(self.server, device, on)
            finally:
                self._close_server()
            self._record_command_state(
                self.selected_device_index,
                {},
                output="Razer mode" if on else "Standard mode",
            )
            self.status_updated.emit(f"Razer mode {'enabled' if on else 'disabled'}")
            self._schedule_status_refresh(self.selected_device_index)
        except Exception as e:
            self.status_updated.emit(f"Error: {str(e)}")

    def set_device_color(self, r: int, g: int, b: int):
        """Set the color of the selected device.

        Args:
            r: Red component (0-255)
            g: Green component (0-255)
            b: Blue component (0-255)
        """
        try:
            device = self.get_selected_device()
            if not device:
                self.status_updated.emit("No device selected")
                return

            transport = self._send_color(device, r, g, b)
            self._record_command_state(
                self.selected_device_index,
                {
                    "color": (r, g, b),
                },
                output=f"#{r:02X}{g:02X}{b:02X}",
            )
            self.status_updated.emit(f"Set color to ({r}, {g}, {b}) via {transport}")
            self._schedule_status_refresh(self.selected_device_index)
        except Exception as e:
            self.status_updated.emit(f"Error: {str(e)}")

    def set_device_brightness(self, brightness: int):
        """Set the brightness of the selected device.

        Args:
            brightness: Brightness value (0-100)
        """
        try:
            device = self.get_selected_device()
            if not device:
                self.status_updated.emit("No device selected")
                return

            bounded = max(0, min(100, brightness))
            transport = self._send_brightness(device, bounded)
            self._record_command_state(
                self.selected_device_index,
                {
                    "brightness": bounded,
                },
            )
            self.status_updated.emit(f"Set brightness to {bounded}% via {transport}")
            self._schedule_status_refresh(self.selected_device_index)
        except Exception as e:
            self.status_updated.emit(f"Error: {str(e)}")

    # --- Per-index variants (used by multi-device card UI) ------------------
    # These act on a specific device without changing the "primary" selection.

    def _device_at(self, index: int) -> Optional[Dict[str, Any]]:
        if 0 <= index < len(self.devices):
            return self.devices[index]
        return None

    def turn_on_off_at(self, index: int, on: bool = True) -> None:
        device = self._device_at(index)
        if not device:
            return
        try:
            transport = self._send_turn(device, on)
            self._record_command_state(
                index,
                {
                    "power_on": on,
                },
                output="Off" if not on else "On",
            )
            self.status_updated.emit(
                f"{device.get('model', 'Device')} turned {'on' if on else 'off'} via {transport}"
            )
            self._schedule_status_refresh(index)
        except Exception as e:
            self.status_updated.emit(f"Error: {str(e)}")

    def toggle_power_at(self, index: int) -> None:
        state = self.get_device_state_at(index)
        current = state.get("power_on")
        self.turn_on_off_at(index, True if current is None else not bool(current))

    def set_color_at(self, index: int, r: int, g: int, b: int) -> None:
        device = self._device_at(index)
        if not device:
            return
        try:
            transport = self._send_color(device, r, g, b)
            self._record_command_state(
                index,
                {
                    "color": (r, g, b),
                },
                output=f"#{r:02X}{g:02X}{b:02X}",
            )
            self.status_updated.emit(
                f"{device.get('model', 'Device')}: color ({r}, {g}, {b}) via {transport}"
            )
            self._schedule_status_refresh(index)
        except Exception as e:
            self.status_updated.emit(f"Error: {str(e)}")

    def set_brightness_at(self, index: int, brightness: int) -> None:
        device = self._device_at(index)
        if not device:
            return
        try:
            bounded = max(0, min(100, brightness))
            transport = self._send_brightness(device, bounded)
            self._record_command_state(
                index,
                {
                    "brightness": bounded,
                },
            )
            self.status_updated.emit(
                f"{device.get('model', 'Device')}: brightness {bounded}% via {transport}"
            )
            self._schedule_status_refresh(index)
        except Exception as e:
            self.status_updated.emit(f"Error: {str(e)}")

    # --- Sync groups -------------------------------------------------------

    def _load_settings_safe(self) -> Dict[str, Any]:
        try:
            return devices.load_settings()
        except Exception:
            return {"devices": [], "selectedDevice": 0, "time": 0}

    def get_groups(self) -> List[Dict[str, Any]]:
        """Return the saved sync groups."""
        return groups.list_groups(self._load_settings_safe())

    def _persist_groups(self, updated: List[Dict[str, Any]]) -> None:
        settings = self._load_settings_safe()
        settings["devices"] = self.devices
        settings["selectedDevice"] = self.selected_device_index
        settings["groups"] = updated
        devices.writeJSON(settings)
        self.groups_changed.emit(updated)

    def save_group(self, name: str, indices: List[int]) -> bool:
        """Create or replace a group from the given device indices."""
        name = (name or "").strip()
        members = [self.devices[i] for i in indices if 0 <= i < len(self.devices)]
        if not name or not members:
            self.status_updated.emit("Group needs a name and at least one device.")
            return False
        existing = self.get_groups()
        updated = groups.upsert_group(existing, groups.make_group(name, members))
        self._persist_groups(updated)
        self.status_updated.emit(f"Saved group '{name}' ({len(members)} device(s))")
        return True

    def delete_group(self, name: str) -> bool:
        updated = groups.remove_group(self.get_groups(), name)
        self._persist_groups(updated)
        self.status_updated.emit(f"Deleted group '{name}'")
        return True

    def get_group_devices(self, name: str) -> List[Dict[str, Any]]:
        """Resolve a group name to the current device dicts it contains."""
        group = groups.find_group(self.get_groups(), name)
        if not group:
            return []
        return groups.resolve_devices(group, self.devices)

    def get_group_indices(self, name: str) -> List[int]:
        """Return the current device indices belonging to a group."""
        group = groups.find_group(self.get_groups(), name)
        if not group:
            return []
        keys = set(group.get("devices", []))
        return [
            i for i, device in enumerate(self.devices)
            if groups.device_key(device) in keys
        ]

    def _ensure_server(self):
        """Ensure server socket exists."""
        if self.server is None:
            self.server = connection.create_lan_socket()

    def _close_server(self) -> None:
        if self.server is not None:
            try:
                self.server.close()
            finally:
                self.server = None

    def __del__(self):
        """Clean up resources when the controller is deleted."""
        try:
            pool.close_all()
        except Exception:
            pass
        for thread_attr in (
            "status_thread",
            "discovery_thread",
            "_ble_scan_thread",
        ):
            thread = getattr(self, thread_attr, None)
            if thread is not None:
                try:
                    if thread.isRunning():
                        thread.quit()
                        thread.wait(1000)
                except Exception:
                    pass
        if self.server:
            try:
                self.server.close()
                self.server = None
            except Exception:
                pass
