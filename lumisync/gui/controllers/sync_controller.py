"""
Sync controller for the LumiSync GUI.
This module handles synchronization functionality (monitor sync and music sync).
"""

import platform
import socket
import threading
import time
from typing import Any, Callable, Dict, Optional

if platform.system() == "Windows":
    from pythoncom import CoInitializeEx, CoUninitialize

from ... import connection, devices
from ...config.options import AUDIO, BRIGHTNESS, COLORS, GENERAL
from ...sync import monitor, music


class SyncController:
    """Controller for managing synchronization."""

    def __init__(self, status_callback: Callable[[str], None] | None = None):
        """
        Initialize the sync controller.

        Args:
            status_callback: Callback function to update status messages
        """
        self.status_callback = status_callback
        self.sync_thread = None
        self.stop_event = threading.Event()
        self.current_sync_mode = None
        self.server = None
        self.selected_device = None

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
                self.set_status(
                    f"Selected device: {self.selected_device.get('model', 'Unknown')}"
                )
        except Exception as e:
            self.set_status(f"Error initializing device: {str(e)}")

    def set_status(self, message: str) -> None:
        """Set the status message."""
        if self.status_callback:
            # Don't log brightness updates to avoid recursion
            if "brightness set to" not in message:
                self.status_callback(message)
            # For brightness updates, handle without callback to avoid recursion
            else:
                # Directly set the status without logging
                pass

    def set_monitor_brightness(self, value: float) -> None:
        """Set brightness for monitor sync mode.

        Args:
            value: Brightness value between 0.0 and 1.0
        """
        self.monitor_brightness = float(value)
        BRIGHTNESS.monitor = self.monitor_brightness
        # Don't call set_status to avoid recursion
        # self.set_status(f"Monitor sync brightness set to {int(self.monitor_brightness * 100)}%")

    def set_music_brightness(self, value: float) -> None:
        """Set brightness for music sync mode.

        Args:
            value: Brightness value between 0.0 and 1.0
        """
        self.music_brightness = float(value)
        BRIGHTNESS.music = self.music_brightness
        # Don't call set_status to avoid recursion
        # self.set_status(f"Music sync brightness set to {int(self.music_brightness * 100)}%")

    def get_monitor_brightness(self) -> float:
        """Get brightness for monitor sync mode."""
        return self.monitor_brightness

    def get_music_brightness(self) -> float:
        """Get brightness for music sync mode."""
        return self.music_brightness

    def _ensure_server(self):
        """Ensure we have an active server connection."""
        if self.server is None:
            self.server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.server.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self.server.setsockopt(
                socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
            )  # Add reuse flag
            self.server.bind(("", connection.CONNECTION.default.listen_port))
            self.server.settimeout(connection.CONNECTION.default.timeout)

    def set_device(self, device: Dict[str, Any]) -> None:
        """Set the device to use for synchronization."""
        self.selected_device = device
        if device:
            self.set_status(f"Ready to sync with {device.get('model', 'Unknown')}")

    def get_selected_device(self) -> Dict[str, Any]:
        """Get the currently selected device.

        If no device is explicitly selected, try to get one from settings.
        """
        if self.selected_device is None:
            self._init_device()
        return self.selected_device

    def start_monitor_sync(self) -> None:
        """Start monitor synchronization."""
        device = self.get_selected_device()
        if device is None:
            self.set_status("No device selected. Please select a device first.")
            return

        if self.sync_thread and self.sync_thread.is_alive():
            self.stop_sync()

        # Set the brightness in the config before starting sync
        BRIGHTNESS.monitor = self.monitor_brightness

        self._ensure_server()
        self.stop_event.clear()
        self.current_sync_mode = "monitor"
        self.set_status("Starting monitor sync...")

        def sync_task():
            try:
                # Enable Razer mode
                connection.switch_razer(self.server, device, True)

                # Initialize with black colors
                previous_colors = [(0, 0, 0)] * 10

                while not self.stop_event.is_set():
                    try:
                        # This code is adapted from monitor.py's start() function
                        colors = []
                        screen = monitor.ss.grab()
                        if screen is None:
                            continue

                        screen = monitor.Image.fromarray(screen)
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

                        # Apply brightness to colors
                        colors = monitor.apply_brightness(colors, BRIGHTNESS.monitor)

                        # Apply smooth transition
                        monitor.smooth_transition(
                            self.server, device, previous_colors, colors
                        )

                        # Update previous colors
                        previous_colors = colors
                    except Exception as e:
                        self.set_status(f"Error in monitor sync: {str(e)}")
                        time.sleep(1)  # Avoid tight loop on error
            except Exception as e:
                self.set_status(f"Monitor sync error: {str(e)}")
            finally:
                self.current_sync_mode = None
                self.set_status("Monitor sync stopped")

        self.sync_thread = threading.Thread(target=sync_task)
        self.sync_thread.daemon = True
        self.sync_thread.start()
        self.set_status(
            f"Monitor sync started with {device.get('model', 'Unknown')} at {int(self.monitor_brightness * 100)}% brightness"
        )

    def start_music_sync(self) -> None:
        """Start music synchronization."""
        device = self.get_selected_device()
        if device is None:
            self.set_status("No device selected. Please select a device first.")
            return

        if self.sync_thread and self.sync_thread.is_alive():
            self.stop_sync()

        # Set the brightness in the config before starting sync
        BRIGHTNESS.music = self.music_brightness

        self._ensure_server()
        self.stop_event.clear()
        self.current_sync_mode = "music"
        self.set_status("Starting music sync...")

        def sync_task():
            try:
                # Initialize COM for this thread
                if platform.system() == "Windows":
                    CoInitializeEx(0)

                # Enable Razer mode
                connection.switch_razer(self.server, device, True)

                # Initialize current colors
                COLORS.current = [(0, 0, 0)] * GENERAL.nled

                while not self.stop_event.is_set():
                    try:
                        # This code is adapted from music.py's start() function
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

                            # Custom wave_color implementation since we need to pass server and device
                            match amp:
                                case amp if amp < 0.04:
                                    COLORS.current.append([int(amp * 255), 0, 0])
                                case amp if 0.04 <= amp < 0.08:
                                    COLORS.current.append([0, int(amp * 255), 0])
                                case _:
                                    COLORS.current.append([0, 0, int(amp * 255)])

                            COLORS.current.pop(0)

                            # Apply brightness to colors
                            adjusted_colors = music.apply_brightness(
                                COLORS.current, BRIGHTNESS.music
                            )

                            # Convert colors and send to device
                            from ...utils import convert_colors

                            connection.send_razer_data(
                                self.server, device, convert_colors(adjusted_colors)
                            )

                    except Exception as e:
                        self.set_status(f"Error in music sync: {str(e)}")
                        time.sleep(1)  # Avoid tight loop on error
            except Exception as e:
                self.set_status(f"Music sync error: {str(e)}")
            finally:
                # Ensure COM is uninitialized even if an exception occurs
                try:
                    if platform.system() == "Windows":
                        CoUninitialize()
                except:
                    pass
                self.current_sync_mode = None
                self.set_status("Music sync stopped")

        self.sync_thread = threading.Thread(target=sync_task)
        self.sync_thread.daemon = True
        self.sync_thread.start()
        self.set_status(
            f"Music sync started with {device.get('model', 'Unknown')} at {int(self.music_brightness * 100)}% brightness"
        )

    def stop_sync(self) -> None:
        """Stop any active synchronization."""
        if self.sync_thread and self.sync_thread.is_alive():
            self.stop_event.set()
            self.set_status(f"Stopping {self.current_sync_mode} sync...")
            self.sync_thread.join(timeout=2)  # Wait for thread to finish

            # If thread is still alive after timeout, we can't do much more in Python
            if self.sync_thread.is_alive():
                self.set_status(
                    f"Warning: {self.current_sync_mode} sync thread did not stop cleanly"
                )

            self.current_sync_mode = None
            self.set_status("Sync stopped")

    def get_current_sync_mode(self) -> Optional[str]:
        """Get the current synchronization mode."""
        return self.current_sync_mode

    def is_syncing(self) -> bool:
        """Check if synchronization is active."""
        return self.sync_thread is not None and self.sync_thread.is_alive()

    def __del__(self):
        """Clean up resources when the controller is deleted."""
        self.stop_sync()
        if self.server:
            try:
                self.server.close()
            except:
                pass
