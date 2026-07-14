import socket
import sys
import time
from typing import Any, Dict, List, Tuple

import numpy as np

# Patch for soundcard compatibility with NumPy 2.0+
# soundcard uses numpy.fromstring binary mode which was removed in NumPy 2.0
if np.lib.NumpyVersion(np.__version__) >= '2.0.0':
    np.fromstring = np.frombuffer

from ..config.options import AUDIO, BRIGHTNESS, SYNC
from ..drivers.registry import create_adapter
from . import artwork, audio, processing


def _soundcard_backend():
    """Load the audio backend only when music capture is requested.

    SoundCard connects to PulseAudio while importing on Linux. Deferring that
    import keeps LumiSync importable on headless systems and lets the existing
    capture error handling report unavailable audio when the feature is used.
    """

    import soundcard

    return soundcard


def default_loopback_microphone():
    """Return a soundcard microphone that captures the system audio output.

    On Windows this is the default speaker's WASAPI loopback. On Linux
    (PulseAudio / PipeWire) the equivalent is the default sink's *monitor*
    source; the ``include_loopback`` lookup usually resolves it, but if it
    doesn't we fall back to picking a monitor/loopback source directly so
    music sync works out of the box on Linux too.
    """
    sc = _soundcard_backend()
    speaker_name = None
    try:
        speaker_name = str(sc.default_speaker().name)
    except Exception:
        pass

    if speaker_name:
        try:
            return sc.get_microphone(id=speaker_name, include_loopback=True)
        except Exception:
            pass

    mics = sc.all_microphones(include_loopback=True)
    loopbacks = [m for m in mics if getattr(m, "isloopback", False)] or mics
    if speaker_name:
        for mic in loopbacks:
            if speaker_name.lower() in str(mic.name).lower():
                return mic
    if loopbacks:
        return loopbacks[0]

    raise RuntimeError(
        "No audio loopback device found. On Linux, make sure PulseAudio or "
        "PipeWire is running and a monitor source is available."
    )


def start(server: socket.socket, device: Dict[str, Any]) -> None:
    """Run the CLI music-sync loop for a single device.

    Each captured audio window is split into bass/mid/treble energy by an FFT
    and rendered using the selected palette and spatial reaction style.
    """
    # Initialize COM on Windows (required for soundcard library in threads)
    if sys.platform == "win32":
        import pythoncom
        pythoncom.CoInitialize()

    try:
        adapter = create_adapter(device, server)
        adapter.begin_stream()
        segment_count = adapter.capabilities.segment_count
        renderer = audio.MusicPatternRenderer(segment_count, SYNC.music_fps)
        artwork_provider = artwork.ArtworkPaletteProvider()
        smoother = processing.ColorSmoother(
            SYNC.music_smoothing, segment_count
        )
        active_reaction = SYNC.music_reaction
        numframes = int(max(AUDIO.duration, AUDIO.music_window) * AUDIO.sample_rate)
        frame_interval = 1.0 / max(1, SYNC.music_fps)

        while True:
            with default_loopback_microphone().recorder(
                samplerate=AUDIO.sample_rate
            ) as mic:
                while True:
                    frame_start = time.monotonic()
                    # NOTE: Try/except due to a soundcard error when no audio plays.
                    try:
                        data = mic.record(numframes=numframes)
                    except TypeError:
                        data = None

                    if active_reaction != SYNC.music_reaction:
                        active_reaction = SYNC.music_reaction
                        renderer = audio.MusicPatternRenderer(
                            segment_count, SYNC.music_fps
                        )
                        smoother = processing.ColorSmoother(
                            SYNC.music_smoothing, segment_count
                        )

                    bands = audio.spectral_bands(data, AUDIO.sample_rate)
                    palette_colors = (
                        artwork_provider.get_colors()
                        if SYNC.music_palette == audio.PALETTE_ALBUM_ART
                        else None
                    )
                    frame = renderer.render(
                        bands,
                        reaction=SYNC.music_reaction,
                        gain=SYNC.music_gain,
                        palette=SYNC.music_palette,
                        palette_colors=palette_colors,
                    )
                    smoother.alpha = SYNC.music_smoothing
                    frame = smoother.update(frame)

                    adjusted = processing.apply_brightness(
                        frame, BRIGHTNESS.music
                    )
                    adapter.set_segments(adjusted)

                    elapsed = time.monotonic() - frame_start
                    if elapsed < frame_interval:
                        time.sleep(frame_interval - elapsed)
    finally:
        if sys.platform == "win32":
            pythoncom.CoUninitialize()


def get_amplitude(mic_data=None) -> float:
    """Gets the peak audio amplitude (0..1). Retained for compatibility."""
    if mic_data is None:
        return 0

    amplitude = np.max(np.abs(mic_data))
    if amplitude > 1:
        amplitude = 1
    return amplitude


def apply_brightness(colors: List[Tuple[int, int, int]], brightness_factor: float) -> List[Tuple[int, int, int]]:
    """Apply a 0..1 brightness factor to a list of RGB colors."""
    return processing.apply_brightness(colors, brightness_factor)
