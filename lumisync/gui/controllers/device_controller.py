"""
Device controller for the LumiSync GUI.
This module handles device discovery and management with PyQt6 signals.
"""

import socket
import time
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal, QThread

from ... import connection, devices


class DeviceDiscoveryWorker(QObject):
    """Worker object for device discovery in a separate thread."""

    # Signals
    finished = pyqtSignal(list, int)  # devices, selected_index
    error = pyqtSignal(str)  # error message

    def run(self):
        """Run device discovery in background thread."""
        try:
            # Request device data
            server = devices.request()

            # Listen for responses
            messages = devices.listen(server)

            # Parse messages
            settings = devices.parseMessages(messages)

            # Close server after discovery
            server.close()

            # Write settings to file
            devices.writeJSON(settings)

            # Emit success signal with discovered devices
            self.finished.emit(
                settings["devices"],
                settings["selectedDevice"]
            )

        except Exception as e:
            # Emit error signal
            self.error.emit(str(e))


class DeviceController(QObject):
    """Controller for device management with PyQt6 signals."""

    # Signals
    status_updated = pyqtSignal(str)  # Status message
    devices_discovered = pyqtSignal(list)  # List of devices
    device_selected = pyqtSignal(dict)  # Selected device info
    device_added = pyqtSignal(dict)  # Newly added device
    device_removed = pyqtSignal(int)  # Index of removed device
    discovery_started = pyqtSignal()  # Discovery process started
    discovery_finished = pyqtSignal()  # Discovery process finished

    def __init__(self):
        """Initialize the device controller."""
        super().__init__()

        self.devices: List[Dict[str, Any]] = []
        self.selected_device_index: int = 0
        self.discovery_thread: Optional[QThread] = None
        self.discovery_worker: Optional[DeviceDiscoveryWorker] = None
        self.server: Optional[socket.socket] = None

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

    def discover_devices(self):
        """Start device discovery in background thread."""
        if self.discovery_thread and self.discovery_thread.isRunning():
            self.status_updated.emit("Discovery already in progress...")
            return

        self.discovery_started.emit()
        self.status_updated.emit("Discovering devices...")

        # Create thread and worker
        self.discovery_thread = QThread()
        self.discovery_worker = DeviceDiscoveryWorker()
        self.discovery_worker.moveToThread(self.discovery_thread)

        # Connect signals
        self.discovery_thread.started.connect(self.discovery_worker.run)
        self.discovery_worker.finished.connect(self._on_discovery_finished)
        self.discovery_worker.error.connect(self._on_discovery_error)
        self.discovery_worker.finished.connect(self.discovery_thread.quit)
        self.discovery_thread.finished.connect(self.discovery_thread.deleteLater)

        # Start thread
        self.discovery_thread.start()

    def _on_discovery_finished(self, discovered_devices: List[Dict], selected_index: int):
        """Handle discovery completion.

        Args:
            discovered_devices: List of discovered devices
            selected_index: Index of the selected device
        """
        self.devices = discovered_devices
        self.selected_device_index = selected_index

        self.status_updated.emit(f"Found {len(self.devices)} device(s)")
        self.devices_discovered.emit(self.devices)
        self.discovery_finished.emit()

        # Emit selected device
        device = self.get_selected_device()
        if device:
            self.device_selected.emit(device)

    def _on_discovery_error(self, error_message: str):
        """Handle discovery error.

        Args:
            error_message: Error message from discovery
        """
        self.status_updated.emit(f"Discovery error: {error_message}")
        self.discovery_finished.emit()

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
            except:
                settings = {"devices": [], "selectedDevice": 0, "time": 0}

            settings["devices"] = self.devices
            devices.writeJSON(settings)

            # Emit signals
            self.device_added.emit(new_device)
            self.devices_discovered.emit(self.devices)
            self.status_updated.emit(f"Added device: {model} ({ip})")

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

            removed = self.devices.pop(index)

            # Adjust selected index
            if self.selected_device_index >= len(self.devices):
                self.selected_device_index = max(0, len(self.devices) - 1)
            elif self.selected_device_index > index:
                self.selected_device_index -= 1

            # Save settings
            try:
                settings = devices.get_data()
            except:
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

            self._ensure_server()
            connection.switch(self.server, device, on)
            self.status_updated.emit(f"Device turned {'on' if on else 'off'}")
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
            connection.switch_razer(self.server, device, on)
            self.status_updated.emit(f"Razer mode {'enabled' if on else 'disabled'}")
        except Exception as e:
            self.status_updated.emit(f"Error: {str(e)}")

    def _ensure_server(self):
        """Ensure server socket exists."""
        if self.server is None:
            self.server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server.bind(("", connection.CONNECTION.default.listen_port))
            self.server.settimeout(connection.CONNECTION.default.timeout)

    def __del__(self):
        """Clean up resources when the controller is deleted."""
        if self.server:
            try:
                self.server.close()
                self.server = None
            except:
                pass
