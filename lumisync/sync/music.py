import socket
import sys
from typing import Any, Dict, List, Tuple

import numpy as np

# Patch for soundcard compatibility with NumPy 2.0+
# soundcard uses numpy.fromstring binary mode which was removed in NumPy 2.0
if np.lib.NumpyVersion(np.__version__) >= '2.0.0':
    np.fromstring = np.frombuffer

import soundcard as sc

from .. import connection, utils
from ..config.options import GENERAL, AUDIO, COLORS, BRIGHTNESS

def start(server: socket.socket, device: Dict[str, Any]) -> None:
    # Initialize COM on Windows (required for soundcard library in threads)
    if sys.platform == "win32":
        import pythoncom
        pythoncom.CoInitialize()

    try:
        connection.switch_razer(server, device, True)
        COLORS.current = [(0, 0, 0)] * GENERAL.nled
        while True:
            with sc.get_microphone(
                id=str(sc.default_speaker().name), include_loopback=True
            ).recorder(samplerate=AUDIO.sample_rate) as mic:

                # NOTE: Try and except due to a soundcard error when no audio is playing
                try:
                    data = mic.record(numframes=int(AUDIO.duration * AUDIO.sample_rate))
                except TypeError:
                    data = None
                amp = get_amplitude(data)
                wave_color(server, device, amp)
    finally:
        if sys.platform == "win32":
            pythoncom.CoUninitialize()


def get_amplitude(mic_data=None) -> float:
    """Gets the audio amplitude."""
    if mic_data is None:
        return 0

    amplitude = np.max(np.abs(mic_data))
    if amplitude > 1:
        amplitude = 1
    return amplitude


def apply_brightness(colors: List[Tuple[int, int, int]], brightness_factor: float) -> List[Tuple[int, int, int]]:
    """Apply brightness factor to a list of colors.

    Args:
        colors: List of RGB color tuples
        brightness_factor: Brightness factor (0.0 to 1.0)

    Returns:
        List of adjusted RGB color tuples
    """
    return [(
        int(r * brightness_factor),
        int(g * brightness_factor),
        int(b * brightness_factor)
    ) for r, g, b in colors]


# TODO: Is this a valid approach for multiple devices?
def wave_color(server: socket.socket, device: Dict[str, Any], amplitude: float) -> None:
    """Determines the wave color from the amplitude."""
    match amplitude:
        case amplitude if amplitude < 0.04:
            COLORS.current.append([int(amplitude * 255), 0, 0])
        case amplitude if 0.04 <= amplitude < 0.08:
            COLORS.current.append([0, int(amplitude * 255), 0])
        case _:
            COLORS.current.append([0, 0, int(amplitude * 255)])

    COLORS.current.pop(0)

    # Apply brightness to current colors
    adjusted_colors = apply_brightness(COLORS.current, BRIGHTNESS.music)

    connection.send_razer_data(server, device, utils.convert_colors(adjusted_colors))
