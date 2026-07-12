import socket
import sys
import time
from typing import Any, Dict, List, Tuple

import numpy as np

# Patch for soundcard compatibility with NumPy 2.0+
# soundcard uses numpy.fromstring binary mode which was removed in NumPy 2.0
if np.lib.NumpyVersion(np.__version__) >= '2.0.0':
    np.fromstring = np.frombuffer

import soundcard as sc

from .. import utils
from ..config.options import AUDIO, BRIGHTNESS, COLORS, SYNC
from ..drivers.registry import create_adapter
from . import audio, processing


def default_loopback_microphone():
    """Return a soundcard microphone that captures the system audio output.

    On Windows this is the default speaker's WASAPI loopback. On Linux
    (PulseAudio / PipeWire) the equivalent is the default sink's *monitor*
    source; the ``include_loopback`` lookup usually resolves it, but if it
    doesn't we fall back to picking a monitor/loopback source directly so
    music sync works out of the box on Linux too.
    """
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
    and mapped to a color, which scrolls along the strip to create motion.
    """
    # Initialize COM on Windows (required for soundcard library in threads)
    if sys.platform == "win32":
        import pythoncom
        pythoncom.CoInitialize()

    try:
        adapter = create_adapter(device, server)
        adapter.begin_stream()
        segment_count = adapter.capabilities.segment_count
        COLORS.current = [(0, 0, 0)] * segment_count
        color_smoother = processing.ColorSmoother(SYNC.music_smoothing, 1)
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

                    raw_color = audio.amplitude_color(
                        data,
                        AUDIO.sample_rate,
                        gain=SYNC.music_gain,
                        palette=SYNC.music_palette,
                    )
                    next_color = color_smoother.update([raw_color])[0]

                    COLORS.current = utils.fit_colors_to_count(
                        COLORS.current, segment_count
                    )
                    COLORS.current.append(next_color)
                    COLORS.current.pop(0)

                    adjusted = processing.apply_brightness(
                        COLORS.current, BRIGHTNESS.music
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
