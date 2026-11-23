"""
Sync controller for the LumiSync GUI.
This module handles synchronization functionality with PySide6 signals.
"""

import sys
import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from PySide6.QtCore import QObject, Signal, QThread, QSettings, QTimer, Qt, Slot

from ... import connection, devices, groups, led_mapping
from ...config.options import BRIGHTNESS, SYNC
from ...config.sync_settings import SYNC_SETTINGS_KEYS, SyncSettings
from ...device import Device, DeviceLike, as_device
from ...sync import audio, monitor
from ...sync.session import SyncSession
from ..app_settings import app_settings


# Capture the real class before tests or integrations monkeypatch QSettings.
_REAL_QSETTINGS = QSettings

DEFAULT_LED_MAPPING = led_mapping.DEFAULT_LEGACY_MAPPING


def _apply_legacy_options(settings: SyncSettings) -> None:
    """Keep the historical option namespaces in sync for external integrations.

    Sync workers no longer read these globals; they receive ``settings``
    directly.  The mirror remains only so older scripts that imported ``SYNC``
    or ``BRIGHTNESS`` keep observing the values the UI persists.
    """
    for name in (
        "monitor_fps",
        "smoothing",
        "gamma_correct",
        "saturation",
        "delta_threshold",
        "music_fps",
        "music_gain",
        "music_auto_gain",
        "music_smoothing",
        "music_palette",
        "music_reaction",
    ):
        setattr(SYNC, name, getattr(settings, name))
    BRIGHTNESS.monitor = settings.monitor_brightness
    BRIGHTNESS.music = settings.music_brightness
    SYNC.monitor_boost = settings.monitor_boost


def load_sync_settings(settings: QSettings, *, shared: bool = False) -> SyncSettings:
    """Read sync tuning into a typed settings object.

    ``settings.json`` is the shared source used by the GUI and headless CLI.
    QSettings remains the fallback for older installs and for tests that inject
    a temporary settings object.
    """
    loaded = None
    if shared:
        try:
            data = devices.get_data(refresh=False)
            saved = data.get("sync")
            if isinstance(saved, dict):
                loaded = SyncSettings.from_mapping(saved)
        except Exception:
            loaded = None
    if loaded is None:
        loaded = SyncSettings.from_qsettings(settings)
    _apply_legacy_options(loaded)
    return loaded


def get_led_mapping_from_settings(
    segment_count: int = len(DEFAULT_LED_MAPPING),
    aspect_ratio: float = led_mapping.DEFAULT_ASPECT_RATIO,
) -> List[led_mapping.NormalizedRect]:
    """Load the monitor LED mapping from QSettings."""
    settings = app_settings()
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


def windows_display_device(screen: Any) -> str:
    """Return the GDI device name (``\\\\.\\DISPLAYn``) showing a QScreen, or "".

    Qt 6 names Windows screens after the monitor model ("Odyssey G5"), while
    dxcam knows outputs only by their GDI device name. Qt keeps a screen's
    top-left corner in native pixels, so the monitor under the screen's
    centre identifies it.
    """
    if sys.platform != "win32":
        return ""
    import ctypes
    from ctypes import wintypes

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    try:
        geometry = screen.geometry()
        ratio = float(screen.devicePixelRatio() or 1.0)
        x = geometry.x() + int(geometry.width() * ratio) // 2
        y = geometry.y() + int(geometry.height() * ratio) // 2
        # A private handle so argtypes never clash with other ctypes users.
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        monitor_handle = user32.MonitorFromPoint(wintypes.POINT(x, y), 0)  # MONITOR_DEFAULTTONULL
        if not monitor_handle:
            return ""
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if not user32.GetMonitorInfoW(monitor_handle, ctypes.byref(info)):
            return ""
        return str(info.szDevice or "")
    except Exception:
        return ""


def screen_name_for_index(display_index: int) -> str:
    """Return the capture name of a Settings display index, or "".

    The stored setting is an index into Qt's screen list (primary first). The
    name (the GDI device name on Windows, else ``QScreen.name()``) is what
    lets capture find the same display among every graphics adapter's
    outputs. GUI thread only.
    """
    try:
        from PySide6.QtGui import QGuiApplication

        if not isinstance(QGuiApplication.instance(), QGuiApplication):
            return ""
        screens = QGuiApplication.screens()
        index = int(display_index)
        if 0 <= index < len(screens):
            screen = screens[index]
            return windows_display_device(screen) or str(screen.name() or "")
    except Exception:
        pass
    return ""


def _worker_settings(settings, controller=None) -> SyncSettings:
    """Accept a typed settings object, or the legacy controller test double."""
    if isinstance(settings, SyncSettings):
        return settings
    resolved = SyncSettings.from_options()
    if controller is not None:
        getter = getattr(controller, "get_monitor_brightness", None)
        if getter is not None:
            resolved.monitor_brightness = max(0.1, min(1.0, float(getter())))
        getter = getattr(controller, "get_monitor_boost", None)
        if getter is not None:
            resolved.monitor_boost = max(1.0, min(1.5, float(getter())))
        getter = getattr(controller, "get_music_brightness", None)
        if getter is not None:
            resolved.music_brightness = max(0.1, min(1.0, float(getter())))
        getter = getattr(controller, "get_monitor_display", None)
        if getter is not None:
            resolved.monitor_display = max(0, int(getter()))
    return resolved


class MonitorSyncWorker(QObject):
    """Qt adapter around the shared :class:`SyncSession` monitor engine."""

    status_updated = Signal(str)
    error_occurred = Signal(str)
    streaming_started = Signal(int)  # devices that opened their stream
    finished = Signal()

    def __init__(self, server, devices, stop_event, settings, controller=None):
        super().__init__()
        self.server = server
        self.devices = list(devices)
        self.stop_event = stop_event
        self.settings = _worker_settings(settings, controller)

    def run(self):
        session = SyncSession(
            "monitor",
            self.devices,
            self.settings,
            server=self.server,
            stop_event=self.stop_event,
            mapping_provider=get_led_mapping_from_settings,
            on_status=self.status_updated.emit,
            on_error=self.error_occurred.emit,
            on_started=self.streaming_started.emit,
        )
        try:
            session.run()
        finally:
            self.finished.emit()


class MusicSyncWorker(QObject):
    """Qt adapter around the shared :class:`SyncSession` music engine."""

    status_updated = Signal(str)
    error_occurred = Signal(str)
    auto_state_changed = Signal(str, object)  # reaction key, representative RGB
    streaming_started = Signal(int)  # devices that opened their stream
    finished = Signal()

    def __init__(self, server, devices, stop_event, settings, controller=None):
        super().__init__()
        self.server = server
        self.devices = list(devices)
        self.stop_event = stop_event
        self.settings = _worker_settings(settings, controller)

    def run(self):
        session = SyncSession(
            "music",
            self.devices,
            self.settings,
            server=self.server,
            stop_event=self.stop_event,
            on_status=self.status_updated.emit,
            on_error=self.error_occurred.emit,
            on_music_auto_state=self.auto_state_changed.emit,
            on_started=self.streaming_started.emit,
        )
        try:
            session.run()
        finally:
            self.finished.emit()


class SyncController(QObject):
    """Controller for managing synchronization with PySide6 signals."""

    # Signals
    status_updated = Signal(str)
    sync_started = Signal(str)  # mode: "monitor" or "music"
    sync_stopped = Signal()
    brightness_changed = Signal(str, float)  # mode, value
    device_sync_brightness_changed = Signal(str, float)  # device key, 0.1-1.0 level
    monitor_boost_changed = Signal(float)  # 1.0-1.5 tone-map gain
    sync_error = Signal(str)
    music_auto_state_changed = Signal(str, object)

    def __init__(self):
        """Initialize the sync controller."""
        super().__init__()

        self.sync_thread: Optional[QThread] = None
        self.sync_worker: Optional[QObject] = None
        self.stop_event = threading.Event()
        self.current_sync_mode: Optional[str] = None
        self.server: Optional[connection.LanSocket] = None
        self.selected_devices: List[Device] = []
        self.active_devices: List[Device] = []
        self._pending_start: Optional[Tuple[str, List[Device]]] = None
        self._stop_announce = True
        self._stop_poll_active = False
        self._closing = False

        # Keep mode-specific brightness stable across launches. The dataclass is
        # the one object workers receive; the legacy option namespaces are only
        # mirrored for external integrations.
        self._settings = app_settings()
        self.sync_settings = load_sync_settings(self._settings, shared=True)
        self.monitor_brightness = self.sync_settings.monitor_brightness
        self.monitor_boost = self.sync_settings.monitor_boost
        self.music_brightness = self.sync_settings.music_brightness
        self.monitor_display_index = self.sync_settings.monitor_display

    def _schedule_sync_persist(self) -> None:
        """Apply live edits immediately; the shared JSON copy is flushed on start/exit."""
        self._apply_settings_to_options()

    def _apply_settings_to_options(self) -> None:
        self.monitor_brightness = self.sync_settings.monitor_brightness
        self.monitor_boost = self.sync_settings.monitor_boost
        self.music_brightness = self.sync_settings.music_brightness
        self.monitor_display_index = self.sync_settings.monitor_display
        self._resolve_monitor_display_name()
        _apply_legacy_options(self.sync_settings)

    def _resolve_monitor_display_name(self) -> None:
        """Pair the stored display index with that screen's name for capture."""
        self.sync_settings.monitor_display_name = screen_name_for_index(
            self.sync_settings.monitor_display
        )

    def monitor_capture_supported(self) -> Tuple[bool, str]:
        """Whether this session can capture the screen, and a message.

        The GUI uses this to disable the Monitor start button up front when
        capture is impossible (for example Wayland without the screen-sharing
        portal or GStreamer). A supported Wayland session still returns a
        note, since capture there is experimental.
        """
        try:
            return monitor.capture_supported()
        except Exception as exc:
            return False, f"Screen capture is unavailable: {exc}"

    def flush_sync_settings(self) -> None:
        """Write the live settings to QSettings and the shared JSON file."""
        self.sync_settings.to_qsettings(self._settings)
        flush = getattr(self._settings, "sync", None)
        if callable(flush):
            flush()
        # Only mirror to settings.json for a real platform settings object.
        # Test doubles must not overwrite the developer's saved configuration.
        if isinstance(self._settings, _REAL_QSETTINGS):
            try:
                devices.update_settings({"sync": self.sync_settings.to_dict()})
            except Exception:
                # Settings persistence must never break an active light stream.
                pass

    def apply_sync_settings(self, settings: SyncSettings) -> None:
        """Merge settings loaded by the Settings page into the live object."""
        self.sync_settings.update_from(settings)
        self._apply_settings_to_options()
        self._schedule_sync_persist()

    def set_monitor_brightness(self, value: float):
        """Set brightness for monitor sync mode."""
        self.sync_settings.set_brightness("monitor", value)
        self._apply_settings_to_options()
        self._settings.setValue(
            SYNC_SETTINGS_KEYS["monitor_brightness"],
            self.sync_settings.monitor_brightness,
        )
        self._schedule_sync_persist()
        self.brightness_changed.emit("monitor", self.sync_settings.monitor_brightness)

    def set_monitor_boost(self, value: float):
        """Set the hue-preserving tone-map boost for monitor sync (1.0-1.5)."""
        self.sync_settings.monitor_boost = max(1.0, min(1.5, float(value)))
        self._apply_settings_to_options()
        self._settings.setValue(
            SYNC_SETTINGS_KEYS["monitor_boost"],
            self.sync_settings.monitor_boost,
        )
        self._schedule_sync_persist()
        self.monitor_boost_changed.emit(self.sync_settings.monitor_boost)

    def set_monitor_display(self, display_index: int) -> None:
        """Select which display to use for monitor sync (0-based)."""
        self.sync_settings.monitor_display = max(0, int(display_index))
        self._apply_settings_to_options()
        self._settings.setValue(
            SYNC_SETTINGS_KEYS["monitor_display"], self.sync_settings.monitor_display
        )
        self._schedule_sync_persist()

    def get_monitor_display(self) -> int:
        return int(self.sync_settings.monitor_display)

    def set_music_brightness(self, value: float):
        """Set brightness for music sync mode."""
        self.sync_settings.set_brightness("music", value)
        self._apply_settings_to_options()
        self._settings.setValue(
            SYNC_SETTINGS_KEYS["music_brightness"],
            self.sync_settings.music_brightness,
        )
        self._schedule_sync_persist()
        self.brightness_changed.emit("music", self.sync_settings.music_brightness)

    def get_monitor_brightness(self) -> float:
        """Get brightness for monitor sync mode."""
        return self.sync_settings.monitor_brightness

    def get_monitor_boost(self) -> float:
        """Get the hue-preserving tone-map boost for monitor sync."""
        return self.sync_settings.monitor_boost

    def get_music_brightness(self) -> float:
        """Get brightness for music sync mode."""
        return self.sync_settings.music_brightness

    # ------------------------------------------------------ per-light levels

    @staticmethod
    def _device_sync_key(device: Union[str, DeviceLike]) -> str:
        """The stable key a light's level is stored under (``groups.device_key``)."""
        if isinstance(device, str):
            return device
        try:
            return groups.device_key(device)
        except Exception:
            return ""

    def get_device_sync_brightness(self, device: Union[str, DeviceLike]) -> float:
        """A light's sync brightness level (0.1-1.0) for Monitor and Music Sync."""
        return self.sync_settings.device_level(self._device_sync_key(device))

    def set_device_sync_brightness(self, device: Union[str, DeviceLike], value: float) -> None:
        """Set and persist a light's level; a running sync uses it on its next frame.

        The session reads the level from the same live ``SyncSettings`` object
        every frame, exactly like the mode brightness, so nothing restarts.
        """
        key = self._device_sync_key(device)
        if not key:
            return
        before = self.sync_settings.device_level(key)
        level = self.sync_settings.set_device_level(key, value)
        self._persist_device_sync_brightness()
        if level != before:
            self.device_sync_brightness_changed.emit(key, level)

    def reset_device_sync_brightness(
        self, devices: Optional[Iterable[Union[str, DeviceLike]]] = None
    ) -> None:
        """Put the given lights (default: every light) back to 100%."""
        if devices is None:
            keys = list(self.sync_settings.device_brightness)
        else:
            keys = [key for key in (self._device_sync_key(device) for device in devices) if key]
        changed = [key for key in keys if self.sync_settings.device_level(key) != 1.0]
        for key in changed:
            self.sync_settings.set_device_level(key, 1.0)
        self._persist_device_sync_brightness()
        for key in changed:
            self.device_sync_brightness_changed.emit(key, 1.0)

    def _persist_device_sync_brightness(self) -> None:
        self._settings.setValue(
            SYNC_SETTINGS_KEYS["device_brightness"],
            self.sync_settings.device_brightness_json(),
        )
        self._schedule_sync_persist()

    def set_music_auto_gain(self, enabled: bool) -> None:
        """Apply and persist whether loudness is normalized against master volume."""
        self.sync_settings.music_auto_gain = bool(enabled)
        self._settings.setValue(
            SYNC_SETTINGS_KEYS["music_auto_gain"],
            self.sync_settings.music_auto_gain,
        )
        self._schedule_sync_persist()

    def get_music_auto_gain(self) -> bool:
        return bool(self.sync_settings.music_auto_gain)

    def set_music_palette(self, palette: str) -> None:
        """Apply and persist the live music color palette."""
        palette = str(palette)
        if palette not in audio.PALETTES:
            return
        self.sync_settings.music_palette = palette
        self._settings.setValue(SYNC_SETTINGS_KEYS["music_palette"], palette)
        self._schedule_sync_persist()

    def get_music_palette(self) -> str:
        return str(self.sync_settings.music_palette)

    def set_music_reaction(self, reaction: str) -> None:
        """Apply and persist how music colors move across LED zones."""
        reaction = str(reaction)
        if reaction not in audio.REACTIONS:
            return
        self.sync_settings.music_reaction = reaction
        self._settings.setValue(SYNC_SETTINGS_KEYS["music_reaction"], reaction)
        self._schedule_sync_persist()

    def get_music_reaction(self) -> str:
        return str(self.sync_settings.music_reaction)

    def set_device(self, device: Device | Dict[str, Any]):
        """Compatibility wrapper: set one device for synchronization."""
        self.set_devices([device] if device else [])

    def set_devices(self, devices: List[Device | Dict[str, Any]]):
        """Set the devices to use when no explicit mode selection is passed."""
        self.selected_devices = [as_device(device).copy() for device in devices if device]

    def get_selected_device(self) -> Optional[Device]:
        """Get the first selected device for legacy single-device callers."""
        devices_for_mode = self.get_selected_devices()
        return devices_for_mode[0] if devices_for_mode else None

    def get_selected_devices(self) -> List[Device]:
        """Get devices selected for the current or next sync."""
        return [
            as_device(device).copy()
            for device in (self.active_devices or self.selected_devices)
        ]

    def start_monitor_sync(self, devices: Optional[List[Device | Dict[str, Any]]] = None):
        """Start monitor synchronization for one or more devices."""
        self.start_sync("monitor", devices)

    def start_music_sync(self, devices: Optional[List[Device | Dict[str, Any]]] = None):
        """Start music synchronization for one or more devices."""
        self.start_sync("music", devices)

    def start_sync(
        self,
        mode: str,
        devices: Optional[Sequence[Device | Dict[str, Any]]] = None,
    ):
        """Start one active sync mode and fan its output to all devices."""
        if mode not in ("monitor", "music"):
            self.status_updated.emit(f"Unknown sync mode: {mode}")
            return
        if self._closing:
            return

        source_devices = self.selected_devices if devices is None else devices
        selected = [as_device(device).copy() for device in source_devices if device]
        from ...drivers.registry import capabilities_for_device
        if any(not capabilities_for_device(device).supports_streaming for device in selected):
            self.status_updated.emit("Screen and music sync require local LAN or Bluetooth control. Choose a local connection in Devices.")
            return
        if not selected:
            self.status_updated.emit("No devices selected. Please select at least one device first.")
            return
        if mode == "monitor":
            # Refuse before any device is switched into its stream mode.
            supported, reason = self.monitor_capture_supported()
            if not supported:
                self.status_updated.emit(reason)
                return

        if self.sync_thread is not None:
            # Never block the GUI waiting for a Bluetooth/Tuya connect to time
            # out. Remember what the user asked for and start it as soon as the
            # previous worker has really exited.
            self._pending_start = (mode, selected)
            self.status_updated.emit(
                "Stopping the current sync before starting the new one…"
            )
            self.stop_sync(announce=False)
            return

        worker_cls = MonitorSyncWorker if mode == "monitor" else MusicSyncWorker
        if mode == "monitor":
            # Screens may have been plugged in or out since the last change.
            self._resolve_monitor_display_name()

        # The CLI reads settings.json, so publish the current live values
        # before a session starts. Tests with a temporary QSettings file are
        # still isolated by the explicit shutdown flush below.
        self.flush_sync_settings()
        if any(device.transport in ("", "lan") for device in selected):
            try:
                self._ensure_server()
            except OSError as exc:
                # A port conflict must read as a normal sync error, not a
                # top-level "Unexpected Error" dialog from an uncaught slot.
                self.sync_error.emit(str(exc))
                self.status_updated.emit(str(exc))
                return
        self.stop_event.clear()
        self.current_sync_mode = mode
        self.active_devices = selected
        self.selected_devices = selected

        self.sync_thread = QThread(self)
        self.sync_worker = worker_cls(
            self.server, selected, self.stop_event, self.sync_settings
        )
        self.sync_worker.moveToThread(self.sync_thread)

        self.sync_thread.started.connect(self.sync_worker.run)
        self.sync_worker.status_updated.connect(self.status_updated.emit)
        self.sync_worker.error_occurred.connect(self.sync_error.emit)
        streaming_started = getattr(self.sync_worker, "streaming_started", None)
        if streaming_started is not None:
            streaming_started.connect(self._on_streaming_started)
        if hasattr(self.sync_worker, "finished"):
            self.sync_worker.finished.connect(self.sync_thread.quit)
        if isinstance(self.sync_worker, MusicSyncWorker):
            self.sync_worker.auto_state_changed.connect(
                self.music_auto_state_changed.emit
            )
        self.sync_thread.finished.connect(self.sync_worker.deleteLater)
        self.sync_thread.finished.connect(self._on_sync_thread_finished, Qt.ConnectionType.QueuedConnection)

        self.sync_thread.start()
        self.sync_started.emit(mode)
        # "Started" is reported by _on_streaming_started once devices opened.

    @Slot(int)
    def _on_streaming_started(self, opened: int) -> None:
        """Report a started sync only after its devices accepted the stream."""
        worker = self.sender()
        mode = self.current_sync_mode
        if mode is None or (worker is not None and worker is not self.sync_worker):
            return
        selected = list(self.active_devices)
        opened = max(0, int(opened))
        brightness = self.sync_settings.brightness_for(mode)
        if 0 < opened < len(selected):
            devices_text = f"{opened} of {len(selected)} devices"
        else:
            devices_text = f"{opened} {'device' if opened == 1 else 'devices'}"
        self.status_updated.emit(
            f"{mode.capitalize()} sync started with {devices_text} "
            f"at {int(brightness * 100)}% brightness"
        )
        zone_hint = self._zone_hint_for(selected)
        if zone_hint:
            self.status_updated.emit(zone_hint)

    def _zone_hint_for(self, devices: Sequence[DeviceLike]) -> Optional[str]:
        """Return a tip when a LAN device has no known zone count.

        The Govee LAN segment stream carries its zone count in every frame,
        and devices silently drop frames whose count they reject — which reads
        as "sync started but nothing happens" (issue #49). Devices missing
        from the SKU catalog fall back to the generic 10-zone default, so
        point users at the per-device zone-count control instead of staying
        silent.
        """
        from ... import sku_catalog

        typed = [as_device(device) for device in devices]
        unknown = sorted(
            {
                device.catalog_sku or "Unknown"
                for device in typed
                if device.transport in ("", "lan")
                and sku_catalog.capabilities_for(device.catalog_sku) is None
                and device.segment_count_override is None
            }
        )
        if not unknown:
            return None
        names = ", ".join(unknown)
        return (
            f"No zone count is known for {names}. If the lights stay solid "
            "during sync, set the correct zone count with Adjust Zones."
        )

    def _finalize_sync_thread(self, thread: QThread, *, announce: bool) -> None:
        """Drop refs after the native thread has actually joined."""
        if thread is not self.sync_thread:
            return
        had_active_mode = self.current_sync_mode is not None
        self.sync_thread = None
        self.sync_worker = None
        try:
            thread.deleteLater()
        except RuntimeError:
            pass
        self.current_sync_mode = None
        self.active_devices = []
        self._stop_poll_active = False
        self.close_server()
        if had_active_mode:
            self.sync_stopped.emit()
            if announce:
                self.status_updated.emit("Sync stopped")

        pending = self._pending_start
        self._pending_start = None
        if pending is not None and not self._closing:
            mode, devices = pending
            QTimer.singleShot(0, lambda: self.start_sync(mode, devices))

    def _poll_stop_thread(self) -> None:
        """Wait for a stopping worker without blocking the GUI thread."""
        thread = self.sync_thread
        if thread is None:
            self._stop_poll_active = False
            return
        try:
            done = thread.wait(0) is True
        except RuntimeError:
            done = True
        if not done:
            QTimer.singleShot(25, self._poll_stop_thread)
            return
        self._finalize_sync_thread(thread, announce=self._stop_announce)

    def stop_sync(self, announce: bool = True):
        """Request a stop and return immediately.

        The worker keeps owning its QThread and sockets until it really exits;
        a timer finalizes them so a slow Bluetooth/Tuya connect can no longer
        freeze the window for two seconds.
        """
        if self.sync_thread is None:
            self.close_server()
            return

        thread = self.sync_thread
        self.stop_event.set()
        try:
            thread.quit()
        except (AttributeError, RuntimeError):
            pass

        try:
            done = thread.wait(0) is True
        except (AttributeError, RuntimeError):
            done = True
        if done:
            self._finalize_sync_thread(thread, announce=announce)
            return

        self._stop_announce = announce
        if not self._stop_poll_active:
            self._stop_poll_active = True
            QTimer.singleShot(25, self._poll_stop_thread)

    @Slot()
    def _on_sync_thread_finished(self) -> None:
        """Clear controller state when a worker exits without an explicit stop."""
        thread = self.sync_thread
        sender = self.sender()
        if thread is None or (isinstance(sender, QThread) and sender is not thread):
            return
        try:
            done = thread.wait(0) is True
        except RuntimeError:
            done = True
        if not done:
            QTimer.singleShot(1, self._on_sync_thread_finished)
            return
        self._finalize_sync_thread(thread, announce=False)

    def shutdown(self) -> None:
        """Stop sync for application shutdown without a blocking wait."""
        self._closing = True
        self._pending_start = None
        self.stop_sync(announce=False)
        self.flush_sync_settings()

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
            self.sync_thread.isRunning()
            return True  # Ownership continues until the joined-thread cleanup.
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
