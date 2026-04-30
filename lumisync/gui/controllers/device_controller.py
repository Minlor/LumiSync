"""
Device controller for the LumiSync GUI.
This module handles device discovery and management with PyQt6 signals.
"""

import socket
import time
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal, QThread, QTimer

from ... import connection, devices


class DeviceDiscoveryWorker(QObject):
    """Worker object for device discovery in a separate thread."""

    # Signals
    finished = pyqtSignal(list, int, int)  # devices, selected_index, LAN replies
    error = pyqtSignal(str)  # error message

    def run(self):
        """Run device discovery in background thread."""
        try:
            settings = devices.discover_lan_devices(preserve_existing=True)

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

    state_updated = pyqtSignal(int, dict)
    finished = pyqtSignal()
    error = pyqtSignal(str)

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
                                "last_error": "No status reply",
                            },
                        )
                    else:
                        status.update(
                            {
                                "online": True,
                                "stale": False,
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


class DeviceController(QObject):
    """Controller for device management with PyQt6 signals."""

    # Signals
    status_updated = pyqtSignal(str)  # Status message
    devices_discovered = pyqtSignal(list)  # List of devices
    device_selected = pyqtSignal(dict)  # Selected device info
    device_added = pyqtSignal(dict)  # Newly added device
    device_removed = pyqtSignal(int)  # Index of removed device
    device_updated = pyqtSignal(int, dict)  # Index, updated device
    device_state_updated = pyqtSignal(int, dict)  # Index, cached state
    discovery_started = pyqtSignal()  # Discovery process started
    discovery_finished = pyqtSignal()  # Discovery process finished

    def __init__(self):
        """Initialize the device controller."""
        super().__init__()

        self.devices: List[Dict[str, Any]] = []
        self.selected_device_index: int = 0
        self.discovery_thread: Optional[QThread] = None
        self.discovery_worker: Optional[DeviceDiscoveryWorker] = None
        self.status_thread: Optional[QThread] = None
        self.status_worker: Optional[DeviceStatusWorker] = None
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

    def discover_devices(self):
        """Start device discovery in background thread."""
        if self._discovery_running():
            self.status_updated.emit("Discovery already in progress...")
            return

        self.discovery_started.emit()
        self.status_updated.emit("Discovering devices...")

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
        self.devices = discovered_devices
        self.selected_device_index = selected_index

        if last_discovery_count == 0 and self.devices:
            self.status_updated.emit(
                f"No new LAN devices found; kept {len(self.devices)} saved device(s)"
            )
        else:
            self.status_updated.emit(f"Found {len(self.devices)} device(s)")
        self.devices_discovered.emit(self.devices)
        self.discovery_finished.emit()
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

    def _device_key(self, device: Dict[str, Any]) -> str:
        return str(device.get("mac") or device.get("ip") or device.get("model") or "?")

    def _default_state(self, device: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "power_on": None,
            "brightness": None,
            "color": None,
            "color_temp": None,
            "online": False,
            "stale": True,
            "last_seen": None,
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

    def refresh_device_states(self) -> None:
        """Refresh current state for all known devices."""
        indexes = list(range(len(self.devices)))
        self._start_status_refresh(indexes)

    def refresh_device_state_at(self, index: int) -> None:
        """Refresh current state for one device."""
        self._start_status_refresh([index])

    def _schedule_status_refresh(self, index: int) -> None:
        """Confirm an optimistic command with a few short LAN status retries."""
        for delay_ms in (250, 700, 1300):
            QTimer.singleShot(
                delay_ms,
                lambda idx=index: self.refresh_device_state_at(idx),
            )

    def _start_status_refresh(self, indexes: List[int]) -> None:
        if self._status_running():
            return

        indexed_devices = [
            (idx, self.devices[idx])
            for idx in indexes
            if 0 <= idx < len(self.devices)
        ]
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

    def _send_turn(self, device: Dict[str, Any], on: bool) -> str:
        if not self._can_try_lan(device):
            raise RuntimeError(
                "Device has no LAN IP. Discover it on the same network or add it manually."
            )
        self._ensure_server()
        try:
            connection.switch(self.server, device, on)
        finally:
            self._close_server()
        return "LAN"

    def _send_brightness(self, device: Dict[str, Any], brightness: int) -> str:
        if not self._can_try_lan(device):
            raise RuntimeError(
                "Device has no LAN IP. Discover it on the same network or add it manually."
            )
        self._ensure_server()
        try:
            connection.set_brightness(self.server, device, brightness)
        finally:
            self._close_server()
        return "LAN"

    def _send_color(self, device: Dict[str, Any], r: int, g: int, b: int) -> str:
        if not self._can_try_lan(device):
            raise RuntimeError(
                "Device has no LAN IP. Discover it on the same network or add it manually."
            )
        self._ensure_server()
        try:
            connection.set_color(self.server, device, r, g, b)
        finally:
            self._close_server()
        return "LAN"

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
            self.refresh_device_state_at(len(self.devices) - 1)

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
            self._merge_device_state(
                self.selected_device_index,
                {
                    "power_on": on,
                    "online": True,
                    "stale": True,
                    "last_error": None,
                },
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
            self._merge_device_state(
                self.selected_device_index,
                {
                    "online": True,
                    "stale": True,
                    "last_error": None,
                },
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
            self._merge_device_state(
                self.selected_device_index,
                {
                    "color": (r, g, b),
                    "online": True,
                    "stale": True,
                    "last_error": None,
                },
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
            self._merge_device_state(
                self.selected_device_index,
                {
                    "brightness": bounded,
                    "online": True,
                    "stale": True,
                    "last_error": None,
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
            self._merge_device_state(
                index,
                {
                    "power_on": on,
                    "online": True,
                    "stale": True,
                    "last_error": None,
                },
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
            self._merge_device_state(
                index,
                {
                    "color": (r, g, b),
                    "online": True,
                    "stale": True,
                    "last_error": None,
                },
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
            self._merge_device_state(
                index,
                {
                    "brightness": bounded,
                    "online": True,
                    "stale": True,
                    "last_error": None,
                },
            )
            self.status_updated.emit(
                f"{device.get('model', 'Device')}: brightness {bounded}% via {transport}"
            )
            self._schedule_status_refresh(index)
        except Exception as e:
            self.status_updated.emit(f"Error: {str(e)}")

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
        for thread_attr in ("status_thread", "discovery_thread"):
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
            except:
                pass
