"""One sync engine shared by the GUI and the command line.

The monitor and music loops used to exist twice: once in ``monitor.py`` /
``music.py`` for the CLI, and once in the GUI controller.  The GUI copy learned
multi-device fan-out, shared connections, per-device frame pacing and live LED
mapping edits; the CLI copy did not.  :class:`SyncSession` owns that behaviour
once so both front ends run the same code.
"""

from __future__ import annotations

import platform
import threading
import time
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from .. import led_mapping, utils
from ..config.options import AUDIO
from ..config.sync_settings import SyncSettings
from ..device import Device, DeviceLike, as_device
from ..drivers import pool
from ..drivers.base import TransportAdapter
from . import artwork, audio, monitor, processing, streaming, system_volume

RGB = Tuple[int, int, int]
MappingProvider = Callable[[int, float], List[led_mapping.NormalizedRect]]
StatusCallback = Callable[[str], None]
AutoStateCallback = Callable[[str, Tuple[int, int, int]], None]

# A device that recovers and then fails the same way again is reported at most
# this often, so a flaky link cannot flood the error toasts.
REPEAT_SEND_ERROR_AFTER = 30.0
_SENDER_STOPPED = "the frame sender stopped."


class _SendFailure(NamedTuple):
    """The last send error reported for one device."""

    message: str
    reported_at: float
    sent_at_failure: Optional[int]


def _default_mapping(segment_count: int, aspect_ratio: float) -> List[led_mapping.NormalizedRect]:
    return led_mapping.generate_screen_mapping(segment_count, aspect_ratio)


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
        self._sender_factory = sender_factory or streaming.LatestFrameSender

        self.adapters: Dict[str, TransportAdapter] = {}
        self.senders: Dict[str, Any] = {}
        self._keys: List[Tuple[Device, str]] = []
        self._sender_errors: Dict[str, _SendFailure] = {}

    # --------------------------------------------------------------- public API

    def run(self) -> None:
        """Block until the stop event is set, then release every resource."""
        com_initialized = False
        try:
            if self.mode == "music" and platform.system() == "Windows":
                from pythoncom import CoInitialize

                CoInitialize()
                com_initialized = True
            if not self._open_devices() or self.stop_event.is_set():
                return
            if self.mode == "monitor":
                self._run_monitor()
            else:
                self._run_music()
        except monitor.WaylandUnsupportedError as exc:
            self._error(str(exc))
        except monitor.ScreenCaptureDependencyError as exc:
            self._error(str(exc))
        except Exception as exc:
            self._error(f"{self.mode.capitalize()} sync error: {exc}")
        finally:
            self._close_devices()
            if com_initialized:
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
                    max_hz=float(getattr(adapter.capabilities, "max_update_hz", 40.0)),
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
        screen_grab = None
        current_display = -1
        last_frame = None
        last_channel_order = "RGB"
        frame_missing_since: Optional[float] = None
        missing_warned = False
        capture_backoff = 1.0
        last_capture_error: Optional[str] = None
        last_processing_error: Optional[str] = None
        mapping_check_counter = 0
        cached_mappings: Dict[Tuple[int, int, int], List[led_mapping.NormalizedRect]] = {}
        smoothers: Dict[str, processing.ColorSmoother] = {}
        last_sent: Dict[str, Optional[List[RGB]]] = {}
        last_sent_at: Dict[str, Optional[float]] = {}

        for _device, key in self._keys:
            adapter = self.adapters[key]
            count = adapter.capabilities.segment_count
            smoothers[key] = processing.ColorSmoother(self.settings.smoothing, count)
            last_sent[key] = None
            last_sent_at[key] = None

        while not self.stop_event.is_set():
            frame_start = time.monotonic()
            # Periodically drop the mapping cache so edits made in the LED
            # Mapping tab are picked up mid-sync.
            mapping_check_counter += 1
            if mapping_check_counter >= 100:
                mapping_check_counter = 0
                cached_mappings.clear()

            desired_display = max(0, int(self.settings.monitor_display))
            if screen_grab is None or desired_display != current_display:
                if screen_grab is not None:
                    self._release_screen_grab(screen_grab)
                current_display = desired_display
                try:
                    screen_grab = monitor.ScreenGrab(display_index=current_display)
                except (monitor.WaylandUnsupportedError, monitor.ScreenCaptureDependencyError):
                    raise
                except Exception as exc:
                    message = f"Error in monitor sync: {exc}"
                    if message != last_capture_error:
                        self._error(message)
                        last_capture_error = message
                    screen_grab = None
                    current_display = -1
                    try:
                        monitor.ScreenGrab.reset_cache()
                    except Exception:
                        pass
                    self._wait(capture_backoff)
                    capture_backoff = min(10.0, capture_backoff * 2)
                    continue
                cached_mappings.clear()
                last_frame = None
                frame_missing_since = None
                missing_warned = False

            try:
                frame, channel_order = self._capture_screen_frame(screen_grab)
            except (monitor.WaylandUnsupportedError, monitor.ScreenCaptureDependencyError):
                raise
            except Exception as exc:
                # A broken capture (dxcam's "NoneType has no AcquireNextFrame")
                # usually never recovers by itself. Release it, drop the cached
                # camera and rebuild with backoff. Report each distinct failure
                # once instead of once per tick.
                message = f"Error in monitor sync: {exc}"
                if message != last_capture_error:
                    self._error(message)
                    last_capture_error = message
                self._release_screen_grab(screen_grab)
                screen_grab = None
                current_display = -1
                last_frame = None
                frame_missing_since = None
                missing_warned = False
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
                        self._release_screen_grab(screen_grab)
                        screen_grab = None
                        current_display = -1
                        frame_missing_since = now
                        missing_warned = False
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

            try:
                height, width = frame.shape[:2]
                aspect_ratio = width / max(1, height)

                for _device, key in self._keys:
                    adapter = self.adapters[key]
                    segment_count = adapter.capabilities.segment_count
                    mapping_key = (
                        segment_count,
                        int(aspect_ratio * 1000),
                        current_display,
                    )
                    if mapping_key not in cached_mappings:
                        cached_mappings[mapping_key] = self.mapping_provider(
                            segment_count, aspect_ratio
                        )
                    device_mapping = cached_mappings[mapping_key]

                    colors = processing.average_zone_colors(
                        frame,
                        device_mapping,
                        gamma_correct=self.settings.gamma_correct,
                        channel_order=channel_order,
                    )
                    colors = processing.apply_saturation(
                        colors, self.settings.saturation
                    )
                    colors = utils.resample_colors_to_count(colors, segment_count)
                    colors = processing.apply_brightness(
                        colors, self.settings.monitor_brightness
                    )

                    smoother = smoothers.get(key)
                    if smoother is None:
                        smoother = processing.ColorSmoother(
                            self.settings.smoothing, segment_count
                        )
                        smoothers[key] = smoother
                    else:
                        smoother.alpha = self.settings.smoothing

                    now = time.monotonic()
                    previous_time = last_sent_at.get(key)
                    dt = (now - previous_time) if previous_time is not None else None
                    # Fast transports keep the fixed per-frame EMA; slow ones
                    # (Tuya paint streams) time-scale the fade.
                    max_hz = float(
                        getattr(adapter.capabilities, "max_update_hz", 40.0)
                    )
                    smoothed = smoother.update(colors, dt if max_hz < 30.0 else None)

                    if processing.frame_needs_send(
                        last_sent.get(key),
                        smoothed,
                        self.settings.delta_threshold,
                        last_sent_at=last_sent_at.get(key),
                        now=now,
                    ):
                        self.senders[key].submit(smoothed)
                        last_sent[key] = smoothed
                        last_sent_at[key] = now
                last_processing_error = None
            except (monitor.WaylandUnsupportedError, monitor.ScreenCaptureDependencyError):
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
            self._wait(1.0 / max(1, self.settings.monitor_fps) - elapsed)

    # ------------------------------------------------------------------ music

    def _run_music(self) -> None:
        renderers: Dict[str, audio.MusicPatternRenderer] = {}
        smoothers: Dict[str, processing.ColorSmoother] = {}
        last_output_at: Dict[str, Optional[float]] = {}
        active_reaction = None
        normalizer = audio.AutoGain(self.settings.music_fps)
        volume_probe = system_volume.MasterVolumeProbe()
        artwork_provider = artwork.ArtworkPaletteProvider()
        last_auto_reaction = ""
        last_auto_color = (0, 0, 0)
        last_auto_emit = 0.0
        numframes = int(max(AUDIO.duration, AUDIO.music_window) * AUDIO.sample_rate)
        last_audio_error = None
        audio_backoff = 1.0

        for _device, key in self._keys:
            last_output_at[key] = None

        while not self.stop_event.is_set():
            try:
                # Follow the default output so switching to headphones does not
                # leave music sync recording the old speaker forever.
                output_id = _audio_output_id()
                with _loopback_microphone().recorder(
                    samplerate=AUDIO.sample_rate
                ) as mic:
                    audio_backoff = 1.0
                    next_output_check = time.monotonic() + 2.0
                    while not self.stop_event.is_set():
                        if time.monotonic() >= next_output_check:
                            next_output_check = time.monotonic() + 2.0
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
                        frame_start = time.monotonic()
                        try:
                            data = mic.record(numframes=numframes)
                        except TypeError:
                            data = None

                        if data is not None and last_audio_error is not None:
                            last_audio_error = None
                            audio_backoff = 1.0
                            self._status("Music sync audio input recovered")

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
                        for device_index, (_device, key) in enumerate(self._keys):
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
                            max_hz = float(
                                getattr(adapter.capabilities, "max_update_hz", 40.0)
                            )
                            frame = smoother.update(
                                frame,
                                (now - last_out)
                                if (last_out is not None and max_hz < 30.0)
                                else None,
                            )
                            adjusted = processing.apply_brightness(
                                frame, self.settings.music_brightness
                            )
                            self.senders[key].submit(adjusted)
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

                        self._drain_sender_errors()
                        elapsed = time.monotonic() - frame_start
                        self._wait(1.0 / max(1, self.settings.music_fps) - elapsed)

            except Exception as exc:
                # Audio-device failures can repeat every frame. Report once,
                # then retry with backoff so sync quietly recovers.
                message = f"Error in music sync: {exc}"
                if message != last_audio_error:
                    self._error(message)
                    last_audio_error = message
                self._wait(audio_backoff)
                audio_backoff = min(10.0, audio_backoff * 2)


def _loopback_microphone():
    # Imported lazily so constructing a session in tests does not touch audio.
    from .music import default_loopback_microphone

    return default_loopback_microphone()


def _audio_output_id():
    # Imported lazily so constructing a session in tests does not touch audio.
    try:
        from .music import default_output_id

        return default_output_id()
    except Exception:
        return None
