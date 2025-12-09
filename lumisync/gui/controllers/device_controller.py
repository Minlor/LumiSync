"""
Device controller for the LumiSync GUI.
This module handles device discovery and management.
"""

import socket
import threading
import time
from typing import Any, Callable, Dict, List

from ... import connection, devices


class DeviceController:
    """Controller for managing devices."""

    def __init__(self, status_callback: Callable[[str], None] = None):
        """
        Initialize the device controller.

        Args:
            status_callback: Callback function to update status messages
        """
        self.status_callback = status_callback
        self.devices = []
        self.selected_device_index = 0
        self.discovery_thread = None
        self.is_discovering = False
        self.server = None
        self.sync_controller = None  # Will be set by the app

        # Initialize devices on startup
        self._init_device()

    def _init_device(self):
        """Initialize with available device if any."""
        try:
            self.devices = self.get_devices()
            if self.devices:
                self.set_status(f"Found {len(self.devices)} device(s)")
                device = self.get_selected_device()
                if device:
                    self.set_status(
                        f"Selected device: {device.get('model', 'Unknown')}"
                    )
        except Exception as e:
            self.set_status(f"Error initializing device: {str(e)}")

    def set_status(self, message: str) -> None:
        """Set the status message."""
        if self.status_callback:
            self.status_callback(message)

    def discover_devices(
        self, callback: Callable[[List[Dict[str, Any]]], None] = None
    ) -> None:
        """
        Discover devices on the network.

        Args:
            callback: Callback function to be called when devices are discovered
        """
        if self.is_discovering:
            self.set_status("Device discovery already in progress...")
            return

        self.is_discovering = True
        self.set_status("Discovering devices...")

        def discovery_task():
            try:
                # Request device data
                server = devices.request()

                # Listen for responses
                messages = devices.listen(server)

                # Parse messages
                settings = devices.parseMessages(messages)

                # Store devices
                self.devices = settings["devices"]
                self.selected_device_index = settings["selectedDevice"]

                # Close server after discovery
                server.close()

                # Write settings to file
                devices.writeJSON(settings)

                # Update status
                self.set_status(f"Found {len(self.devices)} device(s)")

                # Call callback if provided
                if callback:
                    callback(self.devices)
            except Exception as e:
                self.set_status(f"Error discovering devices: {str(e)}")
            finally:
                self.is_discovering = False

        self.discovery_thread = threading.Thread(target=discovery_task)
        self.discovery_thread.daemon = True
        self.discovery_thread.start()

    def get_devices(self) -> List[Dict[str, Any]]:
        """Get the list of discovered devices."""
        try:
            settings = devices.get_data()
            self.devices = settings["devices"]
            self.selected_device_index = settings["selectedDevice"]
            return self.devices
        except Exception as e:
            self.set_status(f"Error getting devices: {str(e)}")
            return []

    def select_device(self, index: int) -> None:
        """
        Select a device by index.

        Args:
            index: Index of the device to select
        """
        if 0 <= index < len(self.devices):
            self.selected_device_index = index

            # Update settings
            try:
                settings = devices.get_data()
                settings["selectedDevice"] = index
                devices.writeJSON(settings)

                # Get the selected device
                selected_device = self.get_selected_device()

                # Update the sync controller if available
                if self.sync_controller:
                    self.sync_controller.set_device(selected_device)

                self.set_status(
                    f"Selected device: {selected_device.get('model', 'Unknown')}"
                )
            except Exception as e:
                self.set_status(f"Error selecting device: {str(e)}")

    def get_selected_device(self) -> Dict[str, Any]:
        """Get the currently selected device."""
        if self.devices and 0 <= self.selected_device_index < len(self.devices):
            return self.devices[self.selected_device_index]
        return None

    def add_device_manually(
        self,
        ip: str,
        model: str = "Manual Device",
        mac: str = None,
        port: int = 4003,
        callback: Callable[[List[Dict[str, Any]]], None] = None
    ) -> bool:
        """
        Manually add a device bypassing automatic discovery.

        Args:
            ip: IP address of the device
            model: Model name/identifier for the device
            mac: MAC address (optional, will be generated if not provided)
            port: Port number (default: 4003)
            callback: Callback function to be called after device is added

        Returns:
            True if device was added successfully, False otherwise
        """
        try:
            # Validate IP address
            try:
                socket.inet_aton(ip)
            except socket.error:
                self.set_status(f"Invalid IP address: {ip}")
                return False

            # Generate a MAC address if not provided (using IP as base)
            if not mac:
                ip_parts = ip.split('.')
                mac = f"00:00:{ip_parts[0]:0>2}:{ip_parts[1]:0>2}:{ip_parts[2]:0>2}:{ip_parts[3]:0>2}"

            # Check if device with same IP or MAC already exists
            for existing_device in self.devices:
                if existing_device.get("ip") == ip:
                    self.set_status(f"Device with IP {ip} already exists")
                    return False
                if existing_device.get("mac") == mac:
                    self.set_status(f"Device with MAC {mac} already exists")
                    return False

            # Create the new device
            new_device = {
                "mac": mac,
                "model": model,
                "ip": ip,
                "port": port,
                "manual": True  # Flag to indicate this was manually added
            }

            # Add to device list
            self.devices.append(new_device)

            # Update settings file
            try:
                settings = devices.get_data()
                settings["devices"] = self.devices
                devices.writeJSON(settings)
            except Exception:
                # If get_data fails, create new settings
                settings = {
                    "devices": self.devices,
                    "selectedDevice": self.selected_device_index,
                    "time": __import__('time').time()
                }
                devices.writeJSON(settings)

            self.set_status(f"Manually added device: {model} ({ip})")

            # Call callback if provided
            if callback:
                callback(self.devices)

            return True

        except Exception as e:
            self.set_status(f"Error adding device manually: {str(e)}")
            return False

    def remove_device(
        self,
        index: int,
        callback: Callable[[List[Dict[str, Any]]], None] = None
    ) -> bool:
        """
        Remove a device by index.

        Args:
            index: Index of the device to remove
            callback: Callback function to be called after device is removed

        Returns:
            True if device was removed successfully, False otherwise
        """
        try:
            if not (0 <= index < len(self.devices)):
                self.set_status("Invalid device index")
                return False

            removed_device = self.devices.pop(index)

            # Adjust selected device index if needed
            if self.selected_device_index >= len(self.devices):
                self.selected_device_index = max(0, len(self.devices) - 1)
            elif self.selected_device_index > index:
                self.selected_device_index -= 1

            # Update settings file
            try:
                settings = devices.get_data()
                settings["devices"] = self.devices
                settings["selectedDevice"] = self.selected_device_index
                devices.writeJSON(settings)
            except Exception:
                settings = {
                    "devices": self.devices,
                    "selectedDevice": self.selected_device_index,
                    "time": __import__('time').time()
                }
                devices.writeJSON(settings)

            self.set_status(f"Removed device: {removed_device.get('model', 'Unknown')}")

            # Update sync controller if available
            if self.sync_controller:
                selected_device = self.get_selected_device()
                self.sync_controller.set_device(selected_device)

            # Call callback if provided
            if callback:
                callback(self.devices)

            return True

        except Exception as e:
            self.set_status(f"Error removing device: {str(e)}")
            return False

    def set_sync_controller(self, controller):
        """Set the sync controller reference for coordinating device selection."""
        self.sync_controller = controller
        # If we already have a device selected, update the sync controller
        selected_device = self.get_selected_device()
        if selected_device and self.sync_controller:
            self.sync_controller.set_device(selected_device)

    def _ensure_server(self):
        """Ensure we have an active server connection."""
        try:
            # Try to use the existing socket if it exists
            if self.server is not None:
                # Test if socket is still usable
                try:
                    self.server.getsockname()
                    return  # Socket is still good
                except (socket.error, OSError):
                    # Socket is no longer usable, close it properly
                    try:
                        self.server.close()
                    except:
                        pass
                    self.server = None

            # Create a new socket
            self.server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.server.setsockopt(
                socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
            )  # Add reuse flag

            # Attempt to bind, with retry logic if needed
            max_retries = 3
            retry_delay = 0.5
            for attempt in range(max_retries):
                try:
                    self.server.bind(("", connection.CONNECTION.default.listen_port))
                    self.server.settimeout(connection.CONNECTION.default.timeout)
                    break
                except OSError as e:
                    if attempt < max_retries - 1:
                        self.set_status(
                            f"Socket binding issue, retrying in {retry_delay}s..."
                        )
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                    else:
                        raise e
        except Exception as e:
            self.set_status(f"Error setting up server: {str(e)}")
            raise

    def turn_on_off(self, on: bool = True) -> None:
        """
        Turn the selected device on or off.

        Args:
            on: True to turn on, False to turn off
        """
        try:
            device = self.get_selected_device()
            if device is None:
                self.set_status("No device selected")
                return

            self._ensure_server()
            connection.switch(self.server, device, on)
            self.set_status(f"Device turned {'on' if on else 'off'}")
        except Exception as e:
            self.set_status(f"Error turning device {'on' if on else 'off'}: {str(e)}")

    def set_razer_mode(self, on: bool = True) -> None:
        """
        Set the device to Razer mode.

        Args:
            on: True to enable Razer mode, False to disable
        """
        try:
            device = self.get_selected_device()
            if device is None:
                self.set_status("No device selected")
                return

            self._ensure_server()
            connection.switch_razer(self.server, device, on)
            self.set_status(f"Razer mode {'enabled' if on else 'disabled'}")
        except Exception as e:
            self.set_status(f"Error setting Razer mode: {str(e)}")

    def __del__(self):
        """Clean up resources when the controller is deleted."""
        if self.server:
            try:
                self.server.close()
                self.server = None
            except:
                pass
