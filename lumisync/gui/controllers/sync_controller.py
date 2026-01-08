"""
Sync controller for the LumiSync GUI.
This module handles synchronization functionality with PyQt6 signals.
"""

import platform
import socket
import threading
import time
from typing import Any, Dict, Optional

from PyQt6.QtCore import QObject, pyqtSignal, QThread

if platform.system() == "Windows":
    from pythoncom import CoInitializeEx, CoUninitialize

from ... import connection, devices
from ...config.options import AUDIO, BRIGHTNESS, COLORS, GENERAL
from ...sync import monitor, music


class MonitorSyncWorker(QObject):
    """Worker for monitor synchronization in a separate thread."""

    # Signals
    status_updated = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, server, device, stop_event, controller):
        super().__init__()
        self.server = server
        self.device = device
        self.stop_event = stop_event
        self.controller = controller

    def run(self):
        """Run monitor sync loop."""
        try:
            # Enable Razer mode
            connection.switch_razer(self.server, self.device, True)

            # Initialize with black colors
            previous_colors = [(0, 0, 0)] * 10

            while not self.stop_event.is_set():
                try:
                    # Screen capture and color processing
                    colors = []
                    screen = monitor.ScreenGrab().capture()
                    if screen is None:
                        continue

                    width, height = screen.size

                    top, bottom = int(height / 4 * 2), int(height / 4 * 3)

                    for x in range(4):
                        img = screen.crop(
                            (int(width / 4 * x), 0, int(width / 4 * (x + 1)), top)
                        )
                        point = (int(img.size[0] / 2), int(img.size[1] / 2))
                        colors.append(img.getpixel(point))
                    colors.reverse()
                    img = screen.crop((0, top, int(width / 4), bottom))
                    point = (int(img.size[0] / 2), int(img.size[1] / 2))
                    colors.append(img.getpixel(point))
                    for x in range(4):
                        img = screen.crop(
                            (
                                int(width / 4 * x),
                                bottom,
                                int(width / 4 * (x + 1)),
                                height,
                            )
                        )
                        point = (int(img.size[0] / 2), int(img.size[1] / 2))
                        colors.append(img.getpixel(point))
                    img = screen.crop((int((width / 4 * 3)), top, width, bottom))
                    colors.append(img.getpixel(point))

                    # Apply brightness to colors (using current brightness value from controller)
                    colors = monitor.apply_brightness(colors, self.controller.get_monitor_brightness())

                    # Apply smooth transition
                    monitor.smooth_transition(
                        self.server, self.device, previous_colors, colors
                    )

                    # Update previous colors
                    previous_colors = colors

                except Exception as e:
                    self.error_occurred.emit(f"Error in monitor sync: {str(e)}")
                    time.sleep(1)  # Avoid tight loop on error

        except Exception as e:
            self.error_occurred.emit(f"Monitor sync error: {str(e)}")


class MusicSyncWorker(QObject):
    """Worker for music synchronization in a separate thread."""

    # Signals
    status_updated = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, server, device, stop_event, controller):
        super().__init__()
        self.server = server
        self.device = device
        self.stop_event = stop_event
        self.controller = controller

    def run(self):
        """Run music sync loop."""
        try:
            # Initialize COM for this thread (Windows only)
            if platform.system() == "Windows":
                CoInitializeEx(0)

            # Enable Razer mode
            connection.switch_razer(self.server, self.device, True)

            # Initialize current colors
            COLORS.current = [(0, 0, 0)] * GENERAL.nled

            while not self.stop_event.is_set():
                try:
                    # Audio capture and processing
                    with music.sc.get_microphone(
                        id=str(music.sc.default_speaker().name),
                        include_loopback=True,
                    ).recorder(samplerate=AUDIO.sample_rate) as mic:
                        # Try and except due to a soundcard error when no audio is playing
                        try:
                            data = mic.record(
                                numframes=int(AUDIO.duration * AUDIO.sample_rate)
                            )
                        except TypeError:
                            data = None
                        amp = music.get_amplitude(data)

                        # Wave color implementation
                        match amp:
                            case amp if amp < 0.04:
                                COLORS.current.append([int(amp * 255), 0, 0])
                            case amp if 0.04 <= amp < 0.08:
                                COLORS.current.append([0, int(amp * 255), 0])
                            case _:
                                COLORS.current.append([0, 0, int(amp * 255)])

                        COLORS.current.pop(0)

                        # Apply brightness to colors (using current brightness value from controller)
                        adjusted_colors = music.apply_brightness(
                            COLORS.current, self.controller.get_music_brightness()
                        )

                        # Convert colors and send to device
                        from ...utils import convert_colors

                        connection.send_razer_data(
                            self.server, self.device, convert_colors(adjusted_colors)
                        )

                except Exception as e:
                    self.error_occurred.emit(f"Error in music sync: {str(e)}")
                    time.sleep(1)  # Avoid tight loop on error

        except Exception as e:
            self.error_occurred.emit(f"Music sync error: {str(e)}")
        finally:
            # Ensure COM is uninitialized even if an exception occurs
            try:
                if platform.system() == "Windows":
                    CoUninitialize()
            except:
                pass


class SyncController(QObject):
    """Controller for managing synchronization with PyQt6 signals."""

    # Signals
    status_updated = pyqtSignal(str)
    sync_started = pyqtSignal(str)  # mode: "monitor" or "music"
    sync_stopped = pyqtSignal()
    brightness_changed = pyqtSignal(str, float)  # mode, value
    sync_error = pyqtSignal(str)

    def __init__(self):
        """Initialize the sync controller."""
        super().__init__()

        self.sync_thread: Optional[QThread] = None
        self.sync_worker: Optional[QObject] = None
        self.stop_event = threading.Event()
        self.current_sync_mode: Optional[str] = None
        self.server: Optional[socket.socket] = None
        self.selected_device: Optional[Dict[str, Any]] = None

        # Initialize brightness settings from config
        self.monitor_brightness = BRIGHTNESS.monitor
        self.music_brightness = BRIGHTNESS.music

        # Initialize with available device if any
        self._init_device()

    def _init_device(self):
        """Initialize with available device if any."""
        try:
            settings = devices.get_data()
            if settings["devices"] and len(settings["devices"]) > 0:
                self.selected_device = settings["devices"][settings["selectedDevice"]]
                self.status_updated.emit(
                    f"Selected device: {self.selected_device.get('model', 'Unknown')}"
                )
        except Exception as e:
            self.status_updated.emit(f"Error initializing device: {str(e)}")

    def set_monitor_brightness(self, value: float):
        """Set brightness for monitor sync mode.

        Args:
            value: Brightness value between 0.0 and 1.0
        """
        self.monitor_brightness = float(value)
        BRIGHTNESS.monitor = self.monitor_brightness
        self.brightness_changed.emit("monitor", self.monitor_brightness)

    def set_music_brightness(self, value: float):
        """Set brightness for music sync mode.

        Args:
            value: Brightness value between 0.0 and 1.0
        """
        self.music_brightness = float(value)
        BRIGHTNESS.music = self.music_brightness
        self.brightness_changed.emit("music", self.music_brightness)

    def get_monitor_brightness(self) -> float:
        """Get brightness for monitor sync mode."""
        return self.monitor_brightness

    def get_music_brightness(self) -> float:
        """Get brightness for music sync mode."""
        return self.music_brightness

    def set_device(self, device: Dict[str, Any]):
        """Set the device to use for synchronization.

        Args:
            device: Device dictionary
        """
        self.selected_device = device
        if device:
            self.status_updated.emit(
                f"Ready to sync with {device.get('model', 'Unknown')}"
            )

    def get_selected_device(self) -> Optional[Dict[str, Any]]:
        """Get the currently selected device.

        Returns:
            Selected device dictionary or None
        """
        if self.selected_device is None:
            self._init_device()
        return self.selected_device

    def start_monitor_sync(self):
        """Start monitor synchronization."""
        device = self.get_selected_device()
        if device is None:
            self.status_updated.emit("No device selected. Please select a device first.")
            return

        if self.sync_thread and self.sync_thread.isRunning():
            self.stop_sync()

        # Set the brightness in the config before starting sync
        BRIGHTNESS.monitor = self.monitor_brightness

        self._ensure_server()
        self.stop_event.clear()
        self.current_sync_mode = "monitor"
        self.status_updated.emit("Starting monitor sync...")

        # Create thread and worker
        self.sync_thread = QThread()
        self.sync_worker = MonitorSyncWorker(
            self.server, device, self.stop_event, self
        )
        self.sync_worker.moveToThread(self.sync_thread)

        # Connect signals
        self.sync_thread.started.connect(self.sync_worker.run)
        self.sync_worker.status_updated.connect(self.status_updated.emit)
        self.sync_worker.error_occurred.connect(self.sync_error.emit)
        self.sync_thread.finished.connect(self.sync_thread.deleteLater)

        # Start thread
        self.sync_thread.start()
        self.sync_started.emit("monitor")
        self.status_updated.emit(
            f"Monitor sync started with {device.get('model', 'Unknown')} "
            f"at {int(self.monitor_brightness * 100)}% brightness"
        )

    def start_music_sync(self):
        """Start music synchronization."""
        device = self.get_selected_device()
        if device is None:
            self.status_updated.emit("No device selected. Please select a device first.")
            return

        if self.sync_thread and self.sync_thread.isRunning():
            self.stop_sync()

        # Set the brightness in the config before starting sync
        BRIGHTNESS.music = self.music_brightness

        self._ensure_server()
        self.stop_event.clear()
        self.current_sync_mode = "music"
        self.status_updated.emit("Starting music sync...")

        # Create thread and worker
        self.sync_thread = QThread()
        self.sync_worker = MusicSyncWorker(
            self.server, device, self.stop_event, self
        )
        self.sync_worker.moveToThread(self.sync_thread)

        # Connect signals
        self.sync_thread.started.connect(self.sync_worker.run)
        self.sync_worker.status_updated.connect(self.status_updated.emit)
        self.sync_worker.error_occurred.connect(self.sync_error.emit)
        self.sync_thread.finished.connect(self.sync_thread.deleteLater)

        # Start thread
        self.sync_thread.start()
        self.sync_started.emit("music")
        self.status_updated.emit(
            f"Music sync started with {device.get('model', 'Unknown')} "
            f"at {int(self.music_brightness * 100)}% brightness"
        )

    def stop_sync(self):
        """Stop any active synchronization."""
        if self.sync_thread is None:
            return

        try:
            is_running = self.sync_thread.isRunning()
        except RuntimeError:
            # Thread was already deleted
            self.sync_thread = None
            return

        if is_running:
            self.stop_event.set()
            mode = self.current_sync_mode or "sync"
            self.status_updated.emit(f"Stopping {mode}...")

            # Wait for thread to finish
            self.sync_thread.quit()
            self.sync_thread.wait(2000)  # Wait up to 2 seconds

            # If thread is still running after timeout
            try:
                if self.sync_thread.isRunning():
                    self.status_updated.emit(
                        f"Warning: {mode} thread did not stop cleanly"
                    )
            except RuntimeError:
                pass

            self.current_sync_mode = None
            self.sync_stopped.emit()
            self.status_updated.emit("Sync stopped")

        # Clear the reference to allow garbage collection
        self.sync_thread = None

    def get_current_sync_mode(self) -> Optional[str]:
        """Get the current synchronization mode.

        Returns:
            Current sync mode string or None
        """
        return self.current_sync_mode

    def is_syncing(self) -> bool:
        """Check if synchronization is active.

        Returns:
            True if sync is running, False otherwise
        """
        if self.sync_thread is None:
            return False

        try:
            return self.sync_thread.isRunning()
        except RuntimeError:
            # Thread was deleted (C/C++ object has been deleted)
            return False

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
        self.stop_sync()
        if self.server:
            try:
                self.server.close()
            except:
                pass
