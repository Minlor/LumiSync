"""One sync engine shared by the GUI and the command line.

The monitor and music loops used to exist twice: once in ``monitor.py`` /
``music.py`` for the CLI, and once in the GUI controller.  The GUI copy learned
multi-device fan-out, shared connections, per-device frame pacing and live LED
mapping edits; the CLI copy did not.  :class:`SyncSession` owns that behaviour
once so both front ends run the same code.
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

import numpy as np

from .. import led_mapping, utils
from ..config.options import AUDIO, GENERAL
from ..config.sync_settings import SyncSettings
from ..device import Device, DeviceLike, as_device
from ..drivers import pool
from ..drivers.base import TransportAdapter
from . import artwork, audio, monitor, processing, streaming, system_volume

RGB = Tuple[int, int, int]
MappingProvider = Callable[[int, float], List[led_mapping.NormalizedRect]]
StatusCallback = Callable[[str], None]
AutoStateCallback = Callable[[str, Tuple[int, int, int]], None]
StartedCallback = Callable[[int], None]

# A device that recovers and then fails the same way again is reported at most
# this often, so a flaky link cannot flood the error toasts.
REPEAT_SEND_ERROR_AFTER = 30.0
_SENDER_STOPPED = "the frame sender stopped."

# Stream modes such as Govee's Razer/DreamView are enabled by one unacknowledged
# packet and time out on the device. Re-arm them on a schedule, and before the
# first frame after a pause (capture backoff, lock screen, PC sleep).
STREAM_KEEPALIVE_INTERVAL = 10.0
STREAM_RESUME_GAP = 2.0

# Consecutive screen-capture failures before the displays are re-enumerated.
CAPTURE_REBUILD_AFTER = 2
# A still-black capture this long on Linux/macOS usually means no permission.
BLACK_FRAME_HINT_AFTER = 10.0

# Music: a loop gap this long means the PC slept or the loop froze; reopen the
# recorder so audio that piled up meanwhile is dropped.
AUDIO_REOPEN_GAP = 2.0
# No audio at all (not even silence) from the backend for this long is a stall.
AUDIO_STALL_SECONDS = 3.0
AUDIO_OPEN_TIMEOUT = 5.0
AUDIO_READ_TIMEOUT = 0.25
# Recording threads stuck inside the audio backend are left behind; stop
# opening new ones while this many are still stuck.
MAX_STUCK_AUDIO_CAPTURES = 2
SILENCE_PEAK = 1e-5
SILENCE_HINT_AFTER = 8.0

_FULL_SCREEN: led_mapping.NormalizedRect = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}

# Capture errors that end Monitor sync with their own plain message instead
# of a retry: a missing backend, or a screen share the user refused or the
# desktop ended (retrying would only bring the share dialog back).
_FATAL_CAPTURE_ERRORS = (
    monitor.WaylandUnsupportedError,
    monitor.ScreenCaptureDependencyError,
    monitor.ScreenSharingDeniedError,
)


class _SendFailure(NamedTuple):
    """The last send error reported for one device."""

    message: str
    reported_at: float
    sent_at_failure: Optional[int]


def _default_mapping(segment_count: int, aspect_ratio: float) -> List[led_mapping.NormalizedRect]:
    return led_mapping.generate_screen_mapping(segment_count, aspect_ratio)


def _max_update_hz(capabilities: Any) -> float:
    try:
        return max(0.1, float(getattr(capabilities, "max_update_hz", 40.0)))
    except (TypeError, ValueError):
        return 40.0


def _streams_one_colour(capabilities: Any) -> bool:
    """Whether a device shows a frame as one averaged colour (matrix panels)."""
    return bool(getattr(capabilities, "is_matrix", False)) or not bool(
        getattr(capabilities, "supports_segments", True)
    )


def _frame_is_black(frame: np.ndarray) -> bool:
    height, width = frame.shape[:2]
    sample = frame[:: max(1, height // 64), :: max(1, width // 64), :3]
    return not bool(sample.any())


class SyncSession:
    """Run monitor or music sync for a set of devices.

    ``settings`` is intentionally a live object: the GUI mutates it from the
    settings page while the worker runs.  It is not a module-level global, so
    the CLI and tests can run independent sessions without interfering.
    """

    def __init__(
        self,
        mode: str,
        devices: Sequence[DeviceLike],
        settings: SyncSettings,
        *,
        server=None,
        stop_event: Optional[threading.Event] = None,
        mapping_provider: Optional[MappingProvider] = None,
        on_status: Optional[StatusCallback] = None,
        on_error: Optional[StatusCallback] = None,
        on_music_auto_state: Optional[AutoStateCallback] = None,
        on_started: Optional[StartedCallback] = None,
        sender_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        if mode not in ("monitor", "music"):
            raise ValueError(f"Unknown sync mode: {mode}")
        self.mode = mode
        self.devices = [as_device(device) for device in devices]
        self.settings = settings
        self.server = server
        self.stop_event = stop_event or threading.Event()
        self.mapping_provider = mapping_provider or _default_mapping
        self._on_status = on_status
        self._on_error = on_error
        self._on_music_auto_state = on_music_auto_state
        self._on_started = on_started
        self._sender_factory = sender_factory or streaming.LatestFrameSender

        self.adapters: Dict[str, TransportAdapter] = {}
        self.senders: Dict[str, Any] = {}
        self._keys: List[Tuple[Device, str]] = []
        self._sender_errors: Dict[str, _SendFailure] = {}
        self._last_submit_at: Dict[str, float] = {}
        self._last_keepalive_at: Dict[str, float] = {}
        self._screen_grab: Any = None
        self._stuck_captures: List[Any] = []

    # --------------------------------------------------------------- public API

    def run(self) -> None:
        """Block until the stop event is set, then release every resource."""
        com_initialized = False
        try:
            # sys.platform (not platform.system()) so type checkers on other
            # platforms skip the pywin32-only imports.
            if sys.platform == "win32":
                if self.mode == "music":
                    from pythoncom import CoInitialize

                    CoInitialize()
                    com_initialized = True
            if not self._open_devices() or self.stop_event.is_set():
                return
            self._notify_started()
            if self.mode == "monitor":
                self._run_monitor()
            else:
                self._run_music()
        except monitor.WaylandUnsupportedError as exc:
            self._error(str(exc))
        except monitor.ScreenCaptureDependencyError as exc:
            self._error(str(exc))
        except monitor.ScreenSharingDeniedError as exc:
            self._error(str(exc))
        except Exception as exc:
            self._error(f"{self.mode.capitalize()} sync error: {exc}")
        finally:
            self._close_devices()
            if sys.platform == "win32" and com_initialized:
                try:
                    from pythoncom import CoUninitialize

                    CoUninitialize()
                except Exception:
                    pass

    # ------------------------------------------------------------------ status

    def _status(self, message: str) -> None:
        if self._on_status is not None:
            self._on_status(message)

    def _error(self, message: str) -> None:
        if self._on_error is not None:
            self._on_error(message)

    def _notify_started(self) -> None:
        """Tell the front end how many devices are streaming, once they are."""
        if self._on_started is None:
            return
        try:
            self._on_started(len(self._keys))
        except Exception:
            pass

    def _wait(self, seconds: float) -> None:
        if seconds > 0:
            self.stop_event.wait(seconds)

    # ------------------------------------------------------------- device setup

    @staticmethod
    def _device_label(device: Device) -> str:
        return str(
            device.get("name")
            or device.get("model")
            or device.identity
            or "Device"
        )

    def _discard_adapter(self, device: Device, adapter: TransportAdapter) -> None:
        """End a stream and release a device that could not be opened."""
        try:
            adapter.end_stream()
        except Exception:
            pass
        if pool.is_pooled(device):
            pool.close(device)
            return
        close = getattr(adapter, "close", None)
        if close is not None:
            try:
                close()
            except Exception:
                pass

    def _open_devices(self) -> bool:
        """Open every device independently so one failure cannot stop the rest."""
        failures: List[str] = []
        for index, device in enumerate(self.devices):
            key = f"{device.identity or 'device'}:{index}"
            adapter = None
            try:
                adapter = pool.acquire(device, self.server)
                adapter.begin_stream()
            except Exception as exc:
                failures.append(f"{self._device_label(device)}: {exc}")
                if adapter is not None:
                    self.adapters.pop(key, None)
                    self._discard_adapter(device, adapter)
                if self.stop_event.is_set():
                    break
                continue
            self.adapters[key] = adapter
            self._keys.append((device, key))
            # begin_stream() just armed the stream mode.
            self._last_keepalive_at[key] = time.monotonic()
            # Stop was requested while connecting: do not open the rest, but
            # still let the caller tear down the device we just opened.
            if self.stop_event.is_set():
                break

        if self.stop_event.is_set():
            return bool(self._keys)

        if not self._keys:
            if failures:
                self._error(
                    f"Could not start {len(failures)} device(s): "
                    + "; ".join(failures)
                )
            return False

        for device, key in list(self._keys):
            adapter = self.adapters[key]

            def send_frame(colors: Sequence[RGB], _adapter=adapter) -> None:
                _adapter.set_segments(list(colors))

            try:
                self.senders[key] = self._sender_factory(
                    send_frame,
                    max_hz=_max_update_hz(adapter.capabilities),
                )
            except Exception as exc:
                failures.append(f"{self._device_label(device)}: {exc}")
                self.adapters.pop(key, None)
                self._keys.remove((device, key))
                self._discard_adapter(device, adapter)

        if failures:
            self._error(
                f"Could not start {len(failures)} device(s): " + "; ".join(failures)
            )
        return bool(self._keys and self.senders)

    def _close_devices(self) -> None:
        for sender in self.senders.values():
            try:
                sender.close()
            except Exception:
                pass
        for adapter in self.adapters.values():
            try:
                adapter.end_stream()
            except Exception:
                pass
            if not pool.is_pooled(getattr(adapter, "device", {})):
                close = getattr(adapter, "close", None)
                if close is not None:
                    try:
                        close()
                    except Exception:
                        self._status("Could not close a device connection after sync.")
        self.senders.clear()
        self.adapters.clear()
        self._keys.clear()
        self._sender_errors.clear()
        self._last_submit_at.clear()
        self._last_keepalive_at.clear()

    def _device_rate_cap(self) -> Optional[float]:
        """The fastest update rate any open device accepts, if known."""
        rates = [
            _max_update_hz(self.adapters[key].capabilities)
            for _device, key in self._keys
            if key in self.adapters
        ]
        return max(rates) if rates else None

    def _frame_interval(self, fps: float, rate_cap: Optional[float]) -> float:
        """Seconds per loop tick: the user's fps, but never above every device's rate."""
        rate = max(1.0, float(fps))
        if rate_cap is not None:
            rate = min(rate, rate_cap)
        return 1.0 / max(1.0, rate)

    # -------------------------------------------------------- stream keepalive

    def _keep_alive(self, key: str, now: float) -> None:
        self._last_keepalive_at[key] = now
        adapter = self.adapters.get(key)
        keep_alive = getattr(adapter, "keep_stream_alive", None)
        if not callable(keep_alive):
            return
        try:
            keep_alive()
        except Exception:
            # The next scheduled keepalive retries; frames keep flowing.
            pass

    def _keep_streams_alive(self, now: float) -> None:
        """Re-arm every device's stream mode about every ten seconds."""
        for _device, key in self._keys:
            last = self._last_keepalive_at.get(key)
            if last is None:
                self._last_keepalive_at[key] = now
            elif now - last >= STREAM_KEEPALIVE_INTERVAL:
                self._keep_alive(key, now)

    def _submit(self, key: str, frame: Sequence[RGB], now: float) -> None:
        """Queue a frame, re-arming the stream first if the device went quiet."""
        last = self._last_submit_at.get(key)
        if last is not None and now - last > STREAM_RESUME_GAP:
            self._keep_alive(key, now)
        self.senders[key].submit(frame)
        self._last_submit_at[key] = now

    def _drain_sender_errors(self, now: Optional[float] = None) -> None:
        """Surface each device's send failures once per failure episode.

        ``last_error()`` clears on read, so an empty read only means nothing
        failed since the last check, not that the device recovered. An episode
        ends once a frame is sent after the failure (``sent_count`` moved on);
        the same error is then reported again at most every
        ``REPEAT_SEND_ERROR_AFTER`` seconds.
        """
        now = time.monotonic() if now is None else now
        for device, key in self._keys:
            sender = self.senders.get(key)
            if sender is None:
                continue
            label = self._device_label(device)
            reported = self._sender_errors.get(key)

            if not getattr(sender, "alive", True):
                if reported is None or reported.message != _SENDER_STOPPED:
                    self._error(f"{label}: {_SENDER_STOPPED}")
                    self._sender_errors[key] = _SendFailure(_SENDER_STOPPED, now, None)
                continue

            last_error = getattr(sender, "last_error", None)
            if not callable(last_error):
                continue
            try:
                error = last_error()
            except Exception:
                continue
            if not error:
                continue

            message = str(error)
            sent = getattr(sender, "sent_count", None)
            recovered = (
                reported is not None
                and sent is not None
                and sent != reported.sent_at_failure
            )
            if (
                reported is None
                or message != reported.message
                or (recovered and now - reported.reported_at >= REPEAT_SEND_ERROR_AFTER)
            ):
                self._error(f"{label}: {message}")
                self._sender_errors[key] = _SendFailure(message, now, sent)
            else:
                self._sender_errors[key] = reported._replace(sent_at_failure=sent)

    # ---------------------------------------------------------------- monitor

    def _release_screen_grab(self, screen_grab) -> None:
        closed = False
        try:
            close = getattr(screen_grab, "close", None)
            if callable(close):
                close()
                closed = True
        except Exception:
            pass
        if closed:
            try:
                monitor.ScreenGrab.reset_cache()
            except Exception:
                pass

    def _drop_screen_grab(self) -> None:
        screen_grab, self._screen_grab = self._screen_grab, None
        if screen_grab is not None:
            self._release_screen_grab(screen_grab)

    @staticmethod
    def _after_capture_failure(failures: int) -> None:
        """Re-enumerate displays once a capture keeps failing (hot-plug, GPU reset)."""
        if failures < CAPTURE_REBUILD_AFTER:
            return
        try:
            monitor.rebuild_capture_backend()
        except Exception:
            pass

    @staticmethod
    def _capture_screen_frame(screen_grab):
        """Read one frame, tolerating older/fake captures with capture_array only."""
        capture_frame = getattr(screen_grab, "capture_frame", None)
        if callable(capture_frame):
            result = capture_frame()
            if isinstance(result, tuple):
                frame, channel_order = result[0], result[1]
                return frame, str(channel_order or "RGB")
        return screen_grab.capture_array(), "RGB"

    def _run_monitor(self) -> None:
        try:
            self._monitor_loop()
        finally:
            # Release the dxcam camera / mss handle however the loop ended, so
            # the next session (or another app) can open the display.
            self._drop_screen_grab()

    def _monitor_loop(self) -> None:
        current_target: Optional[Tuple[int, str]] = None
        last_frame = None
        last_channel_order = "RGB"
        frame_missing_since: Optional[float] = None
        missing_warned = False
        capture_backoff = 1.0
        capture_failures = 0
        last_capture_error: Optional[str] = None
        last_processing_error: Optional[str] = None
        last_capture_note: Optional[str] = None
        mapping_check_counter = 0
        cached_mappings: Dict[Tuple[Any, ...], List[led_mapping.NormalizedRect]] = {}
        # Per-frame zone colours, shared by every device with the same mapping
        # and reused while the screen is still (same frame object).
        zone_colors: Dict[Tuple[Any, ...], List[RGB]] = {}
        colors_frame = None
        check_black = GENERAL.platform != "Windows"
        frame_black = False
        black_since: Optional[float] = None
        black_hinted = False
        smoothers: Dict[str, processing.ColorSmoother] = {}
        last_sent: Dict[str, Optional[List[RGB]]] = {}
        last_sent_at: Dict[str, Optional[float]] = {}
        last_update_at: Dict[str, Optional[float]] = {}
        rate_cap = self._device_rate_cap()

        for _device, key in self._keys:
            capabilities = self.adapters[key].capabilities
            count = 1 if _streams_one_colour(capabilities) else capabilities.segment_count
            smoothers[key] = processing.ColorSmoother(self.settings.smoothing, count)
            last_sent[key] = None
            last_sent_at[key] = None
            last_update_at[key] = None

        while not self.stop_event.is_set():
            frame_start = time.monotonic()
            self._keep_streams_alive(frame_start)
            # Periodically drop the mapping cache so edits made in the LED
            # Mapping tab are picked up mid-sync.
            mapping_check_counter += 1
            if mapping_check_counter >= 100:
                mapping_check_counter = 0
                cached_mappings.clear()
                zone_colors.clear()

            target = (
                max(0, int(self.settings.monitor_display)),
                str(getattr(self.settings, "monitor_display_name", "") or ""),
            )
            if self._screen_grab is None or target != current_target:
                self._drop_screen_grab()
                current_target = target
                try:
                    self._screen_grab = monitor.ScreenGrab(
                        display_index=target[0],
                        display_name=target[1] or None,
                        stop_event=self.stop_event,
                    )
                except _FATAL_CAPTURE_ERRORS:
                    raise
                except Exception as exc:
                    message = f"Error in monitor sync: {exc}"
                    if message != last_capture_error:
                        self._error(message)
                        last_capture_error = message
                    self._screen_grab = None
                    current_target = None
                    try:
                        monitor.ScreenGrab.reset_cache()
                    except Exception:
                        pass
                    capture_failures += 1
                    self._after_capture_failure(capture_failures)
                    self._wait(capture_backoff)
                    capture_backoff = min(10.0, capture_backoff * 2)
                    continue
                note = getattr(self._screen_grab, "fallback_note", None)
                if isinstance(note, str) and note and note != last_capture_note:
                    self._status(note)
                    last_capture_note = note
                cached_mappings.clear()
                zone_colors.clear()
                colors_frame = None
                last_frame = None
                frame_missing_since = None
                missing_warned = False

            try:
                frame, channel_order = self._capture_screen_frame(self._screen_grab)
            except _FATAL_CAPTURE_ERRORS:
                raise
            except Exception as exc:
                # A broken capture (dxcam's "NoneType has no AcquireNextFrame",
                # or a display recovery that timed out) usually never recovers
                # by itself. Release it, drop the cached camera and rebuild
                # with backoff. Report each distinct failure once.
                message = f"Error in monitor sync: {exc}"
                if message != last_capture_error:
                    self._error(message)
                    last_capture_error = message
                self._drop_screen_grab()
                current_target = None
                last_frame = None
                colors_frame = None
                zone_colors.clear()
                frame_missing_since = None
                missing_warned = False
                capture_failures += 1
                self._after_capture_failure(capture_failures)
                self._wait(capture_backoff)
                capture_backoff = min(10.0, capture_backoff * 2)
                continue

            if frame is None:
                now = time.monotonic()
                if frame_missing_since is None:
                    frame_missing_since = now
                if last_frame is None:
                    # dxcam returns None while the screen has not changed. That
                    # is normal after the first frame, but not before one has
                    # ever arrived.
                    if not missing_warned and now - frame_missing_since >= 2.0:
                        self._status(
                            "Monitor sync is waiting for screen frames. "
                            "Check the display selected in Settings if lights stay dark."
                        )
                        missing_warned = True
                    if now - frame_missing_since >= 10.0:
                        self._drop_screen_grab()
                        current_target = None
                        frame_missing_since = now
                        missing_warned = False
                        capture_failures += 1
                        self._after_capture_failure(capture_failures)
                        self._wait(capture_backoff)
                        capture_backoff = min(10.0, capture_backoff * 2)
                        continue
                    self._wait(0.05)
                    continue
                # A still screen: reuse the last frame instead of treating the
                # missing update as a capture failure.
                frame = last_frame
                channel_order = last_channel_order
            else:
                if frame_missing_since is not None:
                    frame_missing_since = None
                    if missing_warned:
                        self._status("Monitor sync screen capture recovered.")
                    missing_warned = False
                if last_capture_error is not None:
                    last_capture_error = None
                    self._status("Monitor sync screen capture recovered.")
                last_frame = frame
                last_channel_order = channel_order
                capture_backoff = 1.0
                capture_failures = 0

            try:
                height, width = frame.shape[:2]
                aspect_ratio = width / max(1, height)
                aspect_key = int(aspect_ratio * 1000)
                now = time.monotonic()
                if frame is not colors_frame:
                    zone_colors.clear()
                    colors_frame = frame
                    if check_black and not black_hinted:
                        frame_black = _frame_is_black(frame)

                if check_black and not black_hinted:
                    if not frame_black:
                        black_since = None
                    elif black_since is None:
                        black_since = now
                    elif now - black_since >= BLACK_FRAME_HINT_AFTER:
                        self._status(
                            "Screen capture only returns black frames. On macOS, "
                            "allow LumiSync under System Settings > Privacy & "
                            "Security > Screen Recording; on Linux, check that "
                            "the X11 session allows screen capture. Then restart sync."
                        )
                        black_hinted = True

                settings = self.settings
                for device, key in self._keys:
                    adapter = self.adapters[key]
                    capabilities = adapter.capabilities
                    one_colour = _streams_one_colour(capabilities)
                    segment_count = 1 if one_colour else int(capabilities.segment_count)
                    mapping_key = (one_colour, segment_count, aspect_key, target[0])
                    colors_key = (
                        mapping_key,
                        bool(settings.gamma_correct),
                        channel_order,
                        float(settings.saturation),
                        float(settings.monitor_boost),
                    )
                    colors = zone_colors.get(colors_key)
                    if colors is None:
                        device_mapping = cached_mappings.get(mapping_key)
                        if device_mapping is None:
                            # A panel that shows one averaged colour samples the
                            # whole screen once instead of hundreds of zones.
                            device_mapping = (
                                [dict(_FULL_SCREEN)]
                                if one_colour
                                else self.mapping_provider(segment_count, aspect_ratio)
                            )
                            cached_mappings[mapping_key] = device_mapping
                        colors = processing.average_zone_colors(
                            frame,
                            device_mapping,
                            gamma_correct=settings.gamma_correct,
                            channel_order=channel_order,
                        )
                        colors = processing.apply_saturation(colors, settings.saturation)
                        # Boost before brightness: the brightness slider is the
                        # ceiling and must not be pushed back up to 255.
                        colors = processing.apply_brightness_boost(
                            colors, settings.monitor_boost
                        )
                        zone_colors[colors_key] = colors
                    colors = utils.resample_colors_to_count(colors, segment_count)
                    # The mode's brightness times this light's own level, read
                    # every frame so a slider move applies without a restart.
                    colors = processing.apply_brightness(
                        colors, settings.frame_brightness("monitor", device.identity)
                    )

                    smoother = smoothers.get(key)
                    if smoother is None:
                        smoother = processing.ColorSmoother(
                            settings.smoothing, segment_count
                        )
                        smoothers[key] = smoother
                    else:
                        smoother.alpha = settings.smoothing

                    # Fast transports keep the fixed per-frame EMA; slow ones
                    # (Tuya paint streams) time-scale the fade by the time since
                    # the previous update, not the previous (skipped) send.
                    previous_update = last_update_at.get(key)
                    dt = (now - previous_update) if previous_update is not None else None
                    last_update_at[key] = now
                    max_hz = _max_update_hz(capabilities)
                    smoothed = smoother.update(colors, dt if max_hz < 30.0 else None)

                    if processing.frame_needs_send(
                        last_sent.get(key),
                        smoothed,
                        settings.delta_threshold,
                        last_sent_at=last_sent_at.get(key),
                        now=now,
                    ):
                        self._submit(key, smoothed, now)
                        last_sent[key] = smoothed
                        last_sent_at[key] = now
                last_processing_error = None
            except _FATAL_CAPTURE_ERRORS:
                raise
            except Exception as exc:
                message = f"Error in monitor sync: {exc}"
                if message != last_processing_error:
                    self._error(message)
                    last_processing_error = message
                self._wait(1.0)
                continue

            self._drain_sender_errors()
            elapsed = time.monotonic() - frame_start
            self._wait(self._frame_interval(self.settings.monitor_fps, rate_cap) - elapsed)

    # ------------------------------------------------------------------ music

    def _release_audio_capture(self, capture: Any) -> None:
        capture.close()
        if getattr(capture, "alive", False):
            self._stuck_captures.append(capture)

    def _open_audio_capture(self, numframes: int) -> Any:
        """Start recording and wait (stop-responsively) until it is running."""
        self._stuck_captures = [
            stuck for stuck in self._stuck_captures if getattr(stuck, "alive", False)
        ]
        if len(self._stuck_captures) >= MAX_STUCK_AUDIO_CAPTURES:
            raise RuntimeError(
                "The audio system is not responding. Restart the audio service "
                "(PulseAudio/PipeWire) or the computer."
            )
        capture = _audio_capture(numframes)
        capture.start()
        deadline = time.monotonic() + AUDIO_OPEN_TIMEOUT
        try:
            while not self.stop_event.is_set():
                if capture.wait_until_ready(0.1):
                    return capture
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"{capture.device_name} did not start recording within "
                        f"{int(AUDIO_OPEN_TIMEOUT)} seconds."
                    )
        except BaseException:
            self._release_audio_capture(capture)
            raise
        return capture

    def _run_music(self) -> None:
        renderers: Dict[str, audio.MusicPatternRenderer] = {}
        smoothers: Dict[str, processing.ColorSmoother] = {}
        last_output_at: Dict[str, Optional[float]] = {}
        active_reaction = None
        normalizer = audio.AutoGain(self.settings.music_fps)
        artwork_provider = artwork.ArtworkPaletteProvider()
        last_auto_reaction = ""
        last_auto_color = (0, 0, 0)
        last_auto_emit = 0.0
        numframes = int(max(AUDIO.duration, AUDIO.music_window) * AUDIO.sample_rate)
        last_audio_error = None
        audio_backoff = 1.0
        listening_to: Optional[str] = None
        silent_since: Optional[float] = None
        silence_hinted = False
        rate_cap = self._device_rate_cap()

        for _device, key in self._keys:
            last_output_at[key] = None

        while not self.stop_event.is_set():
            capture = None
            try:
                # Follow the default output so switching to headphones does not
                # leave music sync recording the old speaker forever.
                output_id = _audio_output_id()
                capture = self._open_audio_capture(numframes)
                if self.stop_event.is_set():
                    break
                # The volume endpoint can change with the recorder; read the
                # master volume from a fresh probe each time it reopens.
                volume_probe = system_volume.MasterVolumeProbe()
                source = capture.device_name
                if source != listening_to:
                    self._status(f"Listening to {source}")
                    listening_to = source

                next_output_check = time.monotonic() + 2.0
                last_iteration = time.monotonic()
                while not self.stop_event.is_set():
                    frame_start = time.monotonic()
                    if frame_start - last_iteration > AUDIO_REOPEN_GAP:
                        # The PC slept or the loop froze. Reopen the recorder so
                        # the silence soundcard queued meanwhile is dropped.
                        break
                    last_iteration = frame_start
                    self._keep_streams_alive(frame_start)
                    if frame_start >= next_output_check:
                        next_output_check = frame_start + 2.0
                        current_output = _audio_output_id()
                        if (
                            output_id is not None
                            and current_output is not None
                            and current_output != output_id
                        ):
                            self._status(
                                "Music sync audio output changed; switching "
                                "to the new playback device."
                            )
                            break

                    data = capture.read(numframes, timeout=AUDIO_READ_TIMEOUT)
                    if data is None:
                        if capture.seconds_since_data() > AUDIO_STALL_SECONDS:
                            raise RuntimeError(
                                f"No audio data from {source} for "
                                f"{int(AUDIO_STALL_SECONDS)} seconds."
                            )
                        self._drain_sender_errors()
                        continue

                    peak = float(np.max(np.abs(data))) if np.size(data) else 0.0
                    if peak >= SILENCE_PEAK:
                        silent_since = None
                        silence_hinted = False
                    elif silent_since is None:
                        silent_since = frame_start
                    elif (
                        not silence_hinted
                        and frame_start - silent_since >= SILENCE_HINT_AFTER
                    ):
                        self._status(
                            f"No sound is reaching LumiSync from {source}. Play "
                            "some audio, or make sure it is the output you are "
                            "listening on."
                        )
                        silence_hinted = True

                    bands = audio.spectral_bands(data, AUDIO.sample_rate)
                    if self.settings.music_auto_gain:
                        bands = system_volume.compensate(
                            bands, volume_probe.linear_gain()
                        )
                        bands = normalizer.process(bands)
                    palette_colors = (
                        artwork_provider.get_colors()
                        if self.settings.music_palette == audio.PALETTE_ALBUM_ART
                        else None
                    )

                    if active_reaction != self.settings.music_reaction:
                        active_reaction = self.settings.music_reaction
                        renderers.clear()
                        smoothers.clear()

                    auto_state = None
                    for device_index, (device, key) in enumerate(self._keys):
                        adapter = self.adapters[key]
                        segment_count = adapter.capabilities.segment_count
                        renderer = renderers.get(key)
                        if renderer is None:
                            renderer = audio.MusicPatternRenderer(
                                segment_count, self.settings.music_fps
                            )
                            renderers[key] = renderer
                        frame = renderer.render(
                            bands,
                            reaction=self.settings.music_reaction,
                            gain=self.settings.music_gain,
                            palette=self.settings.music_palette,
                            palette_colors=palette_colors,
                        )

                        smoother = smoothers.get(key)
                        if smoother is None:
                            smoother = processing.ColorSmoother(
                                self.settings.music_smoothing, segment_count
                            )
                            smoothers[key] = smoother
                        else:
                            smoother.alpha = self.settings.music_smoothing

                        now = time.monotonic()
                        last_out = last_output_at.get(key)
                        max_hz = _max_update_hz(adapter.capabilities)
                        frame = smoother.update(
                            frame,
                            (now - last_out)
                            if (last_out is not None and max_hz < 30.0)
                            else None,
                        )
                        # The mode's brightness times this light's own level.
                        adjusted = processing.apply_brightness(
                            frame,
                            self.settings.frame_brightness("music", device.identity),
                        )
                        self._submit(key, adjusted, now)
                        last_output_at[key] = now

                        if (
                            device_index == 0
                            and active_reaction == audio.REACTION_AUTO
                            and sum(bands) > 1e-9
                        ):
                            representative = max(
                                adjusted, key=sum, default=(0, 0, 0)
                            )
                            auto_state = (
                                renderer.active_reaction,
                                (
                                    int(representative[0]),
                                    int(representative[1]),
                                    int(representative[2]),
                                ),
                            )

                    if auto_state is not None and self._on_music_auto_state is not None:
                        reaction_key, color = auto_state
                        now = time.monotonic()
                        color_delta = max(
                            abs(first - second)
                            for first, second in zip(color, last_auto_color)
                        )
                        if (
                            reaction_key != last_auto_reaction
                            or (
                                now - last_auto_emit >= 0.10
                                and color_delta >= 4
                            )
                        ):
                            self._on_music_auto_state(
                                reaction_key,
                                (int(color[0]), int(color[1]), int(color[2])),
                            )
                            last_auto_reaction = reaction_key
                            last_auto_color = (
                                int(color[0]),
                                int(color[1]),
                                int(color[2]),
                            )
                            last_auto_emit = now

                    # Only a whole frame rendered and queued counts as recovery.
                    if last_audio_error is not None:
                        last_audio_error = None
                        self._status("Music sync audio input recovered")
                    audio_backoff = 1.0

                    self._drain_sender_errors()
                    elapsed = time.monotonic() - frame_start
                    self._wait(
                        self._frame_interval(self.settings.music_fps, rate_cap) - elapsed
                    )

            except Exception as exc:
                # Audio-device failures can repeat every frame. Report once,
                # then retry with backoff so sync quietly recovers.
                message = f"Error in music sync: {exc}"
                if message != last_audio_error:
                    self._error(message)
                    last_audio_error = message
                self._wait(audio_backoff)
                audio_backoff = min(10.0, audio_backoff * 2)
            finally:
                if capture is not None:
                    self._release_audio_capture(capture)


def _audio_capture(numframes: int) -> Any:
    # Imported lazily so constructing a session in tests does not touch audio.
    from .music import AudioCapture

    return AudioCapture(
        samplerate=AUDIO.sample_rate,
        buffer_frames=max(4096, int(numframes) * 2),
    )


def _audio_output_id():
    # Imported lazily so constructing a session in tests does not touch audio.
    try:
        from .music import default_output_id

        return default_output_id()
    except Exception:
        return None
