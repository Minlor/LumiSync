import socket
import threading
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Patch for soundcard compatibility with NumPy 2.0+
# soundcard uses numpy.fromstring binary mode which was removed in NumPy 2.0
if np.lib.NumpyVersion(np.__version__) >= '2.0.0':
    np.fromstring = np.frombuffer

from ..config.sync_settings import SyncSettings
from . import processing


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


def start(
    server: socket.socket,
    device: Dict[str, Any],
    stop_event=None,
    settings: Optional[SyncSettings] = None,
) -> None:
    """Run music sync through the shared :class:`SyncSession` engine."""
    from .session import SyncSession

    session = SyncSession(
        "music",
        [device],
        settings or SyncSettings.from_options(),
        server=server,
        stop_event=stop_event or threading.Event(),
        on_error=print,
    )
    session.run()


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
