"""
Sync controller for the LumiSync GUI.
This module handles synchronization functionality with PySide6 signals.
"""

import platform
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal, QThread, QSettings

if platform.system() == "Windows":
    from pythoncom import CoInitializeEx, CoUninitialize

from ... import connection, led_mapping, utils
from ...config.options import AUDIO, BRIGHTNESS, SYNC
from ...drivers import pool
from ...sync import artwork, audio, monitor, music, processing


DEFAULT_LED_MAPPING = led_mapping.DEFAULT_LEGACY_MAPPING

# QSettings keys for the tunables exposed in the Settings page. Kept here so the
# loader and the UI share one source of truth.
SYNC_SETTINGS_KEYS = {
    "smoothing": "sync/smoothing",
    "saturation": "sync/saturation",
    "gamma_correct": "sync/gamma_correct",
    "monitor_fps": "sync/monitor_fps",
    "music_gain": "sync/music_gain",
    "music_smoothing": "sync/music_smoothing",
    "music_palette": "sync/music_palette",
    "music_reaction": "sync/music_reaction",
    "monitor_brightness": "sync/monitor/brightness",
    "music_brightness": "sync/music/brightness",
}


def _coerce_float(value: Any, fallback: float, low: float, high: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, result))


def _coerce_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return fallback


def load_sync_settings(settings: QSettings) -> None:
    """Apply persisted sync tuning from QSettings onto the live SYNC config.

    QSettings round-trips values as strings on some platforms, so every field
    is coerced and clamped to a safe range before it reaches the sync loops.
    """
    keys = SYNC_SETTINGS_KEYS
    SYNC.smoothing = _coerce_float(settings.value(keys["smoothing"], SYNC.smoothing), SYNC.smoothing, 0.05, 1.0)
    SYNC.saturation = _coerce_float(settings.value(keys["saturation"], SYNC.saturation), SYNC.saturation, 1.0, 2.0)
    SYNC.gamma_correct = _coerce_bool(settings.value(keys["gamma_correct"], SYNC.gamma_correct), SYNC.gamma_correct)
    SYNC.monitor_fps = int(_coerce_float(settings.value(keys["monitor_fps"], SYNC.monitor_fps), SYNC.monitor_fps, 10, 144))
    SYNC.music_gain = _coerce_float(settings.value(keys["music_gain"], SYNC.music_gain), SYNC.music_gain, 0.5, 4.0)
    SYNC.music_smoothing = _coerce_float(settings.value(keys["music_smoothing"], SYNC.music_smoothing), SYNC.music_smoothing, 0.05, 1.0)
    palette = str(settings.value(keys["music_palette"], SYNC.music_palette))
    if palette in audio.PALETTES:
        SYNC.music_palette = palette
    reaction = str(
        settings.value(keys["music_reaction"], SYNC.music_reaction)
    )
    if reaction in audio.REACTIONS:
        SYNC.music_reaction = reaction


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
    status_updated = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, server, devices, stop_event, controller):
        super().__init__()
        self.server = server
        self.devices = list(devices)
        self.stop_event = stop_event
        self.controller = controller

    def run(self):
        """Run monitor sync loop.

        One captured frame produces one smoothed packet per device. Temporal
        smoothing lives in a per-device EMA (:class:`processing.ColorSmoother`),
        so we no longer flood the strip with ten interpolated packets per frame.
        Frame pacing and a delta check keep CPU and UDP traffic bounded.
        """
        try:
            # One driver per device; enable its segment/streaming mode once.
            adapters = {}
            for device in self.devices:
                adapter = pool.acquire(device, self.server)
                adapter.begin_stream()
                adapters[self._device_key(device)] = adapter

            # Per-device smoothing state and last transmitted frame.
            smoothers: Dict[str, processing.ColorSmoother] = {}
            last_sent: Dict[str, Optional[List[Tuple[int, int, int]]]] = {}
            last_sent_at: Dict[str, Optional[float]] = {}
            for device in self.devices:
                key = self._device_key(device)
                count = adapters[key].capabilities.segment_count
                smoothers[key] = processing.ColorSmoother(SYNC.smoothing, count)
                last_sent[key] = None
                last_sent_at[key] = None

            screen_grab = None
            current_display_index = -1
            mapping_check_counter = 0
            empty_capture_count = 0
            cached_mappings: Dict[Tuple[int, int, int], List[led_mapping.NormalizedRect]] = {}
            frame_interval = 1.0 / max(1, SYNC.monitor_fps)

            while not self.stop_event.is_set():
                frame_start = time.monotonic()
                try:
                    # Periodically drop the mapping cache so edits made in the
                    # LED Mapping tab are picked up mid-sync.
                    mapping_check_counter += 1
                    if mapping_check_counter >= 100:
                        mapping_check_counter = 0
                        cached_mappings.clear()

                    # Screen capture and color processing
                    desired_display = int(self.controller.get_monitor_display())
                    if screen_grab is None or desired_display != current_display_index:
                        current_display_index = desired_display
                        screen_grab = monitor.ScreenGrab(display_index=current_display_index)
                        cached_mappings.clear()

                    frame = screen_grab.capture_array()
                    if frame is None:
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
                        self.stop_event.wait(0.05)
                        continue

                    empty_capture_count = 0
                    height, width = frame.shape[:2]
                    aspect_ratio = width / max(1, height)
                    brightness = self.controller.get_monitor_brightness()

                    for device in self.devices:
                        key = self._device_key(device)
                        adapter = adapters[key]
                        segment_count = adapter.capabilities.segment_count
                        mapping_key = (
                            segment_count,
                            int(aspect_ratio * 1000),
                            current_display_index,
                        )
                        if mapping_key not in cached_mappings:
                            cached_mappings[mapping_key] = get_led_mapping_from_settings(
                                segment_count,
                                aspect_ratio,
                            )
                        device_mapping = cached_mappings[mapping_key]

                        colors = processing.average_zone_colors(
                            frame, device_mapping, gamma_correct=SYNC.gamma_correct
                        )
                        colors = processing.apply_saturation(colors, SYNC.saturation)
                        colors = utils.resample_colors_to_count(colors, segment_count)
                        colors = processing.apply_brightness(colors, brightness)

                        smoother = smoothers.get(key)
                        if smoother is None:
                            smoother = processing.ColorSmoother(SYNC.smoothing, segment_count)
                            smoothers[key] = smoother
                        else:
                            smoother.alpha = SYNC.smoothing  # live-apply UI changes
                        smoothed = smoother.update(colors)

                        now = time.monotonic()
                        if processing.frame_needs_send(
                            last_sent.get(key),
                            smoothed,
                            SYNC.delta_threshold,
                            last_sent_at=last_sent_at.get(key),
                            now=now,
                        ):
                            adapter.set_segments(smoothed)
                            last_sent[key] = smoothed
                            last_sent_at[key] = now

                except monitor.WaylandUnsupportedError as e:
                    # Permanent on this session; retrying would spam. Report once
                    # and stop the monitor sync cleanly.
                    self.error_occurred.emit(str(e))
                    return
                except monitor.ScreenCaptureDependencyError as e:
                    self.error_occurred.emit(str(e))
                    return
                except Exception as e:
                    self.error_occurred.emit(f"Error in monitor sync: {str(e)}")
                    time.sleep(1)  # Avoid tight loop on error
                    continue

                elapsed = time.monotonic() - frame_start
                if elapsed < frame_interval:
                    self.stop_event.wait(frame_interval - elapsed)

        except Exception as e:
            self.error_occurred.emit(f"Monitor sync error: {str(e)}")

    def _device_key(self, device: Dict[str, Any]) -> str:
        return str(device.get("mac") or device.get("ip") or device.get("model") or id(device))


class MusicSyncWorker(QObject):
    """Worker for music synchronization in a separate thread."""

    # Signals
    status_updated = Signal(str)
    error_occurred = Signal(str)
    auto_state_changed = Signal(str, object)  # reaction key, representative RGB

    def __init__(self, server, devices, stop_event, controller):
        super().__init__()
        self.server = server
        self.devices = list(devices)
        self.stop_event = stop_event
        self.controller = controller

    def run(self):
        """Run music sync loop.

        Each audio window is split into bass/mid/treble energy by an FFT. The
        selected reaction renderer then turns those bands into a spatial LED
        frame, independently from the selected color palette.
        """
        try:
            # Initialize COM for this thread (Windows only)
            if platform.system() == "Windows":
                CoInitializeEx(0)

            # One driver per device; enable its segment/streaming mode once.
            adapters = {}
            for device in self.devices:
                adapter = pool.acquire(device, self.server)
                adapter.begin_stream()
                adapters[self._device_key(device)] = adapter

            renderers = {}
            smoothers = {}
            active_reaction = None
            artwork_provider = artwork.ArtworkPaletteProvider()
            last_auto_reaction = ""
            last_auto_color = (0, 0, 0)
            last_auto_emit = 0.0
            numframes = int(max(AUDIO.duration, AUDIO.music_window) * AUDIO.sample_rate)
            frame_interval = 1.0 / max(1, SYNC.music_fps)

            while not self.stop_event.is_set():
                try:
                    # Open the loopback recorder once, then stream frames from it.
                    with music.default_loopback_microphone().recorder(
                        samplerate=AUDIO.sample_rate
                    ) as mic:
                        while not self.stop_event.is_set():
                            frame_start = time.monotonic()
                            # Try/except due to a soundcard error when no audio plays.
                            try:
                                data = mic.record(numframes=numframes)
                            except TypeError:
                                data = None

                            bands = audio.spectral_bands(
                                data, AUDIO.sample_rate
                            )
                            palette_colors = (
                                artwork_provider.get_colors()
                                if SYNC.music_palette
                                == audio.PALETTE_ALBUM_ART
                                else None
                            )
                            brightness = self.controller.get_music_brightness()

                            # A live style change starts from a clean frame so
                            # the previous motion does not smear into the new
                            # one. Palette, gain, and smoothing remain live.
                            if active_reaction != SYNC.music_reaction:
                                active_reaction = SYNC.music_reaction
                                renderers.clear()
                                smoothers.clear()

                            auto_state = None
                            for device_index, device in enumerate(self.devices):
                                key = self._device_key(device)
                                adapter = adapters[key]
                                segment_count = adapter.capabilities.segment_count
                                renderer = renderers.get(key)
                                if renderer is None:
                                    renderer = audio.MusicPatternRenderer(
                                        segment_count, SYNC.music_fps
                                    )
                                    renderers[key] = renderer
                                frame = renderer.render(
                                    bands,
                                    reaction=SYNC.music_reaction,
                                    gain=SYNC.music_gain,
                                    palette=SYNC.music_palette,
                                    palette_colors=palette_colors,
                                )

                                smoother = smoothers.get(key)
                                if smoother is None:
                                    smoother = processing.ColorSmoother(
                                        SYNC.music_smoothing, segment_count
                                    )
                                    smoothers[key] = smoother
                                else:
                                    smoother.alpha = SYNC.music_smoothing
                                frame = smoother.update(frame)
                                adjusted_colors = processing.apply_brightness(
                                    frame, brightness
                                )
                                adapter.set_segments(adjusted_colors)

                                if (
                                    device_index == 0
                                    and active_reaction == audio.REACTION_AUTO
                                    and sum(bands) > 1e-9
                                ):
                                    representative = max(
                                        adjusted_colors,
                                        key=sum,
                                        default=(0, 0, 0),
                                    )
                                    auto_state = (
                                        renderer.active_reaction,
                                        representative,
                                    )

                            if auto_state is not None:
                                reaction_key, color = auto_state
                                now = time.monotonic()
                                color_delta = max(
                                    abs(first - second)
                                    for first, second in zip(
                                        color, last_auto_color
                                    )
                                )
                                if (
                                    reaction_key != last_auto_reaction
                                    or (
                                        now - last_auto_emit >= 0.10
                                        and color_delta >= 4
                                    )
                                ):
                                    self.auto_state_changed.emit(
                                        reaction_key,
                                        tuple(color),
                                    )
                                    last_auto_reaction = reaction_key
                                    last_auto_color = tuple(color)
                                    last_auto_emit = now

                            elapsed = time.monotonic() - frame_start
                            if elapsed < frame_interval:
                                self.stop_event.wait(frame_interval - elapsed)

                except Exception as e:
                    self.error_occurred.emit(f"Error in music sync: {str(e)}")
                    self.stop_event.wait(1)  # Avoid tight loop on error

        except Exception as e:
            self.error_occurred.emit(f"Music sync error: {str(e)}")
        finally:
            # Ensure COM is uninitialized even if an exception occurs
            try:
                if platform.system() == "Windows":
                    CoUninitialize()
            except Exception:
                pass

    def _device_key(self, device: Dict[str, Any]) -> str:
        return str(device.get("mac") or device.get("ip") or device.get("model") or id(device))


class SyncController(QObject):
    """Controller for managing synchronization with PySide6 signals."""

    # Signals
    status_updated = Signal(str)
    sync_started = Signal(str)  # mode: "monitor" or "music"
    sync_stopped = Signal()
    brightness_changed = Signal(str, float)  # mode, value
    sync_error = Signal(str)
    music_auto_state_changed = Signal(str, object)

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

        # Keep mode-specific brightness stable across launches.
        self._settings = QSettings("Minlor", "LumiSync")
        self.monitor_brightness = _coerce_float(
            self._settings.value(
                SYNC_SETTINGS_KEYS["monitor_brightness"], BRIGHTNESS.monitor
            ),
            BRIGHTNESS.monitor,
            0.1,
            1.0,
        )
        self.music_brightness = _coerce_float(
            self._settings.value(
                SYNC_SETTINGS_KEYS["music_brightness"], BRIGHTNESS.music
            ),
            BRIGHTNESS.music,
            0.1,
            1.0,
        )
        BRIGHTNESS.monitor = self.monitor_brightness
        BRIGHTNESS.music = self.music_brightness

        # Monitor display selection (0-based)
        self.monitor_display_index = 0
        # Device is set via set_device() signal from DeviceController

    def set_monitor_brightness(self, value: float):
        """Set brightness for monitor sync mode.

        Args:
            value: Brightness value between 0.0 and 1.0
        """
        self.monitor_brightness = max(0.1, min(1.0, float(value)))
        BRIGHTNESS.monitor = self.monitor_brightness
        self._settings.setValue(
            SYNC_SETTINGS_KEYS["monitor_brightness"], self.monitor_brightness
        )
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
        self.music_brightness = max(0.1, min(1.0, float(value)))
        BRIGHTNESS.music = self.music_brightness
        self._settings.setValue(
            SYNC_SETTINGS_KEYS["music_brightness"], self.music_brightness
        )
        self.brightness_changed.emit("music", self.music_brightness)

    def get_monitor_brightness(self) -> float:
        """Get brightness for monitor sync mode."""
        return self.monitor_brightness

    def get_music_brightness(self) -> float:
        """Get brightness for music sync mode."""
        return self.music_brightness

    def set_music_palette(self, palette: str) -> None:
        """Apply and persist the live music color palette."""
        palette = str(palette)
        if palette not in audio.PALETTES:
            return
        SYNC.music_palette = palette
        self._settings.setValue(
            SYNC_SETTINGS_KEYS["music_palette"], palette
        )

    def get_music_palette(self) -> str:
        return str(SYNC.music_palette)

    def set_music_reaction(self, reaction: str) -> None:
        """Apply and persist how music colors move across LED zones."""
        reaction = str(reaction)
        if reaction not in audio.REACTIONS:
            return
        SYNC.music_reaction = reaction
        self._settings.setValue(
            SYNC_SETTINGS_KEYS["music_reaction"], reaction
        )

    def get_music_reaction(self) -> str:
        return str(SYNC.music_reaction)

    def set_device(self, device: Dict[str, Any]):
        """Compatibility wrapper: set one device for synchronization.

        Args:
            device: Device dictionary
        """
        self.set_devices([device] if device else [])

    def set_devices(self, devices: List[Dict[str, Any]]):
        """Set the devices to use when no explicit mode selection is passed."""
        self.selected_devices = [dict(device) for device in devices if device]

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
            self.stop_sync(announce=False)

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

        self.sync_thread = QThread()
        self.sync_worker = worker_cls(self.server, selected, self.stop_event, self)
        self.sync_worker.moveToThread(self.sync_thread)

        self.sync_thread.started.connect(self.sync_worker.run)
        self.sync_worker.status_updated.connect(self.status_updated.emit)
        self.sync_worker.error_occurred.connect(self.sync_error.emit)
        if isinstance(self.sync_worker, MusicSyncWorker):
            self.sync_worker.auto_state_changed.connect(
                self.music_auto_state_changed.emit
            )
        self.sync_thread.finished.connect(self.sync_thread.deleteLater)

        self.sync_thread.start()
        self.sync_started.emit(mode)
        device_word = "device" if len(selected) == 1 else "devices"
        self.status_updated.emit(
            f"{display_mode} sync started with {len(selected)} {device_word} "
            f"at {int(brightness * 100)}% brightness"
        )

    def stop_sync(self, announce: bool = True):
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
            if announce:
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
