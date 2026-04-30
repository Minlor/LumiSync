"""
Sync controller for the LumiSync GUI.
This module handles synchronization functionality with PyQt6 signals.
"""

import platform
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal, QThread, QSettings

if platform.system() == "Windows":
    from pythoncom import CoInitializeEx, CoUninitialize

from ... import connection, devices, led_mapping, utils
from ...config.options import AUDIO, BRIGHTNESS, GENERAL
from ...sync import monitor, music


DEFAULT_LED_MAPPING = led_mapping.DEFAULT_LEGACY_MAPPING


def get_led_mapping_from_settings(
    segment_count: int = len(DEFAULT_LED_MAPPING),
    aspect_ratio: float = led_mapping.DEFAULT_ASPECT_RATIO,
) -> List[led_mapping.NormalizedRect]:
    """Load the monitor LED mapping from QSettings."""
    settings = QSettings("Minlor", "LumiSync")
    return led_mapping.load_mapping_from_settings(settings, segment_count, aspect_ratio)


def fit_led_mapping_to_count(
    mapping: List[led_mapping.NormalizedRect],
    segment_count: int,
) -> List[led_mapping.NormalizedRect]:
    """Resize a saved LED mapping to a device's effective segment count."""
    return led_mapping.fit_normalized_mapping_to_count(mapping, segment_count)


def sample_region_color(
    screen,
    width: int,
    height: int,
    rect: led_mapping.NormalizedRect,
) -> Tuple[int, int, int]:
    """Sample color from the center of a normalized screen rectangle."""
    normalized = led_mapping.normalize_rect(rect)
    x1 = int(normalized["x"] * width)
    y1 = int(normalized["y"] * height)
    x2 = int((normalized["x"] + normalized["w"]) * width)
    y2 = int((normalized["y"] + normalized["h"]) * height)
    point = (
        max(0, min(width - 1, x1 + max(1, x2 - x1) // 2)),
        max(0, min(height - 1, y1 + max(1, y2 - y1) // 2)),
    )
    return screen.getpixel(point)


class MonitorSyncWorker(QObject):
    """Worker for monitor synchronization in a separate thread."""

    # Signals
    status_updated = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, server, devices, stop_event, controller):
        super().__init__()
        self.server = server
        self.devices = list(devices)
        self.stop_event = stop_event
        self.controller = controller

    def _send_smooth_frame(
        self,
        device: Dict[str, Any],
        previous_colors: List[Tuple[int, int, int]],
        colors: List[Tuple[int, int, int]],
    ) -> None:
        """Send one smooth transition frame to a participating device."""
        steps = 10
        for step in range(1, steps + 1):
            if self.stop_event.is_set():
                return
            t = step / steps
            interpolated = [
                (
                    int(prev[0] + (cur[0] - prev[0]) * t),
                    int(prev[1] + (cur[1] - prev[1]) * t),
                    int(prev[2] + (cur[2] - prev[2]) * t),
                )
                for prev, cur in zip(previous_colors, colors)
            ]
            payload = utils.convert_colors(interpolated)
            connection.send_razer_data(self.server, device, payload)
            time.sleep(0.01)

    def run(self):
        """Run monitor sync loop."""
        try:
            # Enable Razer mode
            for device in self.devices:
                connection.switch_razer(self.server, device, True)

            # Initialize per-device frame state based on known segment counts.
            previous_by_device = {
                self._device_key(device): [
                    (0, 0, 0)
                ] * connection.get_segment_count(device, default=len(DEFAULT_LED_MAPPING))
                for device in self.devices
            }

            screen_grab = None
            current_display_index = -1
            mapping_check_counter = 0
            empty_capture_count = 0
            cached_mappings: Dict[Tuple[int, int, int], List[led_mapping.NormalizedRect]] = {}

            while not self.stop_event.is_set():
                try:
                    # Periodically refresh LED mapping (every 100 frames)
                    mapping_check_counter += 1
                    if mapping_check_counter >= 100:
                        mapping_check_counter = 0
                        cached_mappings.clear()

                    # Screen capture and color processing
                    desired_display = int(self.controller.get_monitor_display())
                    if screen_grab is None or desired_display != current_display_index:
                        current_display_index = desired_display
                        screen_grab = monitor.ScreenGrab(display_index=current_display_index)

                    screen = screen_grab.capture()
                    if screen is None:
                        empty_capture_count += 1
                        if empty_capture_count in (30, 120):
                            self.status_updated.emit(
                                "Monitor sync is waiting for screen frames. "
                                "Check the display selected in Settings if lights stay dark."
                            )
                        if empty_capture_count >= 120:
                            screen_grab = None
                            current_display_index = -1
                            empty_capture_count = 0
                        time.sleep(0.05)
                        continue

                    empty_capture_count = 0
                    width, height = screen.size
                    aspect_ratio = width / max(1, height)

                    for device in self.devices:
                        key = self._device_key(device)
                        segment_count = connection.get_segment_count(
                            device,
                            default=len(DEFAULT_LED_MAPPING),
                        )
                        mapping_key = (
                            segment_count,
                            int(aspect_ratio * 1000),
                            int(self.controller.get_monitor_display()),
                        )
                        if mapping_key not in cached_mappings:
                            cached_mappings[mapping_key] = get_led_mapping_from_settings(
                                segment_count,
                                aspect_ratio,
                            )
                        device_mapping = cached_mappings[mapping_key]
                        colors = [
                            sample_region_color(screen, width, height, rect)
                            for rect in device_mapping
                        ]
                        colors = monitor.apply_brightness(
                            colors,
                            self.controller.get_monitor_brightness(),
                        )
                        colors = utils.resample_colors_to_count(colors, segment_count)
                        previous_colors = previous_by_device.get(
                            key,
                            [(0, 0, 0)] * segment_count,
                        )
                        previous_colors = utils.fit_colors_to_count(
                            previous_colors,
                            segment_count,
                        )
                        self._send_smooth_frame(device, previous_colors, colors)
                        previous_by_device[key] = colors

                except Exception as e:
                    self.error_occurred.emit(f"Error in monitor sync: {str(e)}")
                    time.sleep(1)  # Avoid tight loop on error

        except Exception as e:
            self.error_occurred.emit(f"Monitor sync error: {str(e)}")

    def _device_key(self, device: Dict[str, Any]) -> str:
        return str(device.get("mac") or device.get("ip") or device.get("model") or id(device))


class MusicSyncWorker(QObject):
    """Worker for music synchronization in a separate thread."""

    # Signals
    status_updated = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, server, devices, stop_event, controller):
        super().__init__()
        self.server = server
        self.devices = list(devices)
        self.stop_event = stop_event
        self.controller = controller

    def run(self):
        """Run music sync loop."""
        try:
            # Initialize COM for this thread (Windows only)
            if platform.system() == "Windows":
                CoInitializeEx(0)

            # Enable Razer mode
            for device in self.devices:
                connection.switch_razer(self.server, device, True)

            current_by_device = {
                self._device_key(device): [
                    (0, 0, 0)
                ] * connection.get_segment_count(device, default=10)
                for device in self.devices
            }

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
                                next_color = (int(amp * 255), 0, 0)
                            case amp if 0.04 <= amp < 0.08:
                                next_color = (0, int(amp * 255), 0)
                            case _:
                                next_color = (0, 0, int(amp * 255))

                        for device in self.devices:
                            key = self._device_key(device)
                            segment_count = connection.get_segment_count(
                                device,
                                default=10,
                            )
                            current = current_by_device.get(
                                key,
                                [(0, 0, 0)] * segment_count,
                            )
                            current = utils.fit_colors_to_count(current, segment_count)
                            current.append(next_color)
                            current.pop(0)
                            current_by_device[key] = current
                            adjusted_colors = music.apply_brightness(
                                current,
                                self.controller.get_music_brightness(),
                            )
                            payload = utils.convert_colors(
                                utils.fit_colors_to_count(adjusted_colors, segment_count)
                            )
                            connection.send_razer_data(self.server, device, payload)

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

    def _device_key(self, device: Dict[str, Any]) -> str:
        return str(device.get("mac") or device.get("ip") or device.get("model") or id(device))


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
        self.selected_devices: List[Dict[str, Any]] = []
        self.active_devices: List[Dict[str, Any]] = []

        # Initialize brightness settings from config
        self.monitor_brightness = BRIGHTNESS.monitor
        self.music_brightness = BRIGHTNESS.music

        # Monitor display selection (0-based)
        self.monitor_display_index = 0
        # Device is set via set_device() signal from DeviceController

    def set_monitor_brightness(self, value: float):
        """Set brightness for monitor sync mode.

        Args:
            value: Brightness value between 0.0 and 1.0
        """
        self.monitor_brightness = float(value)
        BRIGHTNESS.monitor = self.monitor_brightness
        self.brightness_changed.emit("monitor", self.monitor_brightness)

    def set_monitor_display(self, display_index: int) -> None:
        """Select which display to use for monitor sync (0-based)."""
        self.monitor_display_index = max(0, int(display_index))

    def get_monitor_display(self) -> int:
        return int(self.monitor_display_index)

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
        """Compatibility wrapper: set one device for synchronization.

        Args:
            device: Device dictionary
        """
        self.set_devices([device] if device else [])

    def set_devices(self, devices: List[Dict[str, Any]]):
        """Set the devices to use when no explicit mode selection is passed."""
        self.selected_devices = [dict(device) for device in devices if device]
        if self.selected_devices:
            self.status_updated.emit(
                f"Ready to sync with {len(self.selected_devices)} device(s)"
            )

    def get_selected_device(self) -> Optional[Dict[str, Any]]:
        """Get the first selected device for legacy single-device callers.

        Returns:
            Selected device dictionary or None
        """
        devices_for_mode = self.get_selected_devices()
        return devices_for_mode[0] if devices_for_mode else None

    def get_selected_devices(self) -> List[Dict[str, Any]]:
        """Get devices selected for the current or next sync."""
        return [dict(device) for device in (self.active_devices or self.selected_devices)]

    def start_monitor_sync(self, devices: Optional[List[Dict[str, Any]]] = None):
        """Start monitor synchronization for one or more devices."""
        self.start_sync("monitor", devices)

    def start_music_sync(self, devices: Optional[List[Dict[str, Any]]] = None):
        """Start music synchronization for one or more devices."""
        self.start_sync("music", devices)

    def start_sync(self, mode: str, devices: Optional[List[Dict[str, Any]]] = None):
        """Start one active sync mode and fan its output to all devices."""
        source_devices = self.selected_devices if devices is None else devices
        selected = [dict(device) for device in source_devices if device]
        if not selected:
            self.status_updated.emit("No devices selected. Please select at least one device first.")
            return

        if self.sync_thread and self.sync_thread.isRunning():
            self.stop_sync()

        if mode == "monitor":
            BRIGHTNESS.monitor = self.monitor_brightness
            worker_cls = MonitorSyncWorker
            brightness = self.monitor_brightness
            display_mode = "Monitor"
        elif mode == "music":
            BRIGHTNESS.music = self.music_brightness
            worker_cls = MusicSyncWorker
            brightness = self.music_brightness
            display_mode = "Music"
        else:
            self.status_updated.emit(f"Unknown sync mode: {mode}")
            return

        self._ensure_server()
        self.stop_event.clear()
        self.current_sync_mode = mode
        self.active_devices = selected
        self.selected_devices = selected
        self.status_updated.emit(f"Starting {mode} sync...")

        self.sync_thread = QThread()
        self.sync_worker = worker_cls(self.server, selected, self.stop_event, self)
        self.sync_worker.moveToThread(self.sync_thread)

        self.sync_thread.started.connect(self.sync_worker.run)
        self.sync_worker.status_updated.connect(self.status_updated.emit)
        self.sync_worker.error_occurred.connect(self.sync_error.emit)
        self.sync_thread.finished.connect(self.sync_thread.deleteLater)

        self.sync_thread.start()
        self.sync_started.emit(mode)
        self.status_updated.emit(
            f"{display_mode} sync started with {len(selected)} device(s) "
            f"at {int(brightness * 100)}% brightness"
        )

    def stop_sync(self):
        """Stop any active synchronization."""
        if self.sync_thread is None:
            self.close_server()
            return

        try:
            is_running = self.sync_thread.isRunning()
        except RuntimeError:
            # Thread was already deleted
            self.sync_thread = None
            self.sync_worker = None
            self.current_sync_mode = None
            self.active_devices = []
            self.close_server()
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
            self.active_devices = []
            self.sync_stopped.emit()
            self.status_updated.emit("Sync stopped")

        # Clear the reference to allow garbage collection
        self.sync_thread = None
        self.sync_worker = None
        self.close_server()

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
            self.server = connection.create_lan_socket()

    def close_server(self) -> None:
        """Close the shared LAN socket when sync/mapping is idle."""
        if self.server is not None:
            try:
                self.server.close()
            finally:
                self.server = None

    def __del__(self):
        """Clean up resources when the controller is deleted."""
        self.stop_sync()
        self.close_server()
