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
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

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

    # --------------------------------------------------------------- public API

    def run(self) -> None:
        """Block until the stop event is set, then release every resource."""
        com_initialized = False
        try:
            if self.mode == "music" and platform.system() == "Windows":
                from pythoncom import CoInitialize

                CoInitialize()
                com_initialized = True
            self._open_devices()
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

    def _open_devices(self) -> None:
        for index, device in enumerate(self.devices):
            key = f"{device.identity or 'device'}:{index}"
            adapter = pool.acquire(device, self.server)
            self.adapters[key] = adapter
            self._keys.append((device, key))
            adapter.begin_stream()

        for _device, key in self._keys:
            adapter = self.adapters[key]

            def send_frame(colors: Sequence[RGB], _adapter=adapter) -> None:
                _adapter.set_segments(list(colors))

            self.senders[key] = self._sender_factory(
                send_frame,
                max_hz=float(getattr(adapter.capabilities, "max_update_hz", 40.0)),
            )

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

    # ---------------------------------------------------------------- monitor

    def _run_monitor(self) -> None:
        screen_grab = None
        current_display = -1
        empty_capture_count = 0
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
            try:
                # Periodically drop the mapping cache so edits made in the LED
                # Mapping tab are picked up mid-sync.
                mapping_check_counter += 1
                if mapping_check_counter >= 100:
                    mapping_check_counter = 0
                    cached_mappings.clear()

                desired_display = max(0, int(self.settings.monitor_display))
                if screen_grab is None or desired_display != current_display:
                    current_display = desired_display
                    screen_grab = monitor.ScreenGrab(display_index=current_display)
                    cached_mappings.clear()

                frame = screen_grab.capture_array()
                if frame is None:
                    empty_capture_count += 1
                    if empty_capture_count in (30, 120):
                        self._status(
                            "Monitor sync is waiting for screen frames. "
                            "Check the display selected in Settings if lights stay dark."
                        )
                    if empty_capture_count >= 120:
                        screen_grab = None
                        current_display = -1
                        empty_capture_count = 0
                    self._wait(0.05)
                    continue

                empty_capture_count = 0
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

            except (monitor.WaylandUnsupportedError, monitor.ScreenCaptureDependencyError):
                raise
            except Exception as exc:
                self._error(f"Error in monitor sync: {exc}")
                self._wait(1.0)
                continue

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
                # Open the loopback recorder once, then stream frames from it.
                with _loopback_microphone().recorder(
                    samplerate=AUDIO.sample_rate
                ) as mic:
                    while not self.stop_event.is_set():
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
