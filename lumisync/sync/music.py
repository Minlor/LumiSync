import socket
from typing import Any, Dict

import numpy as np
import soundcard as sc

from .. import connection, utils
from ..config.options import GENERAL, AUDIO, COLORS

def start(server: socket.socket, device: Dict[str, Any]) -> None:
    connection.switch_razer(server, device, True)
    COLORS.current = [[0, 0, 0]] * GENERAL.nled
    while True:
        with sc.get_microphone(
            id=str(sc.default_speaker().name), include_loopback=True
        ).recorder(samplerate=AUDIO.sample_rate) as mic:

            # NOTE: Try and except due to a soundcard error when no audio is playing
            try:
                data = mic.record(numframes=AUDIO.duration * AUDIO.sample_rate)
            except TypeError:
                data = None
            amp = get_amplitude(data)
            wave_color(server, device, amp)


def get_amplitude(mic_data=None) -> float:
    """Gets the audio amplitude."""
    if mic_data is None:
        return 0

    amplitude = np.max(np.abs(mic_data))
    if amplitude > 1:
        amplitude = 1
    return amplitude


# TODO: Is this a valid approach for multiple devices?
def wave_color(server: socket.socket, device: Dict[str, Any], amplitude: float) -> None:
    """Determines the wave color from the amplitude."""
    match amplitude:
        case amplitude if amplitude < 0.04:
            COLORS.current.append([int(amplitude * 255), 0, 0])
        case amplitude if amplitude > 0.04 and amplitude < 0.08:
            COLORS.current.append([0, int(amplitude * 255), 0])
        case _:
            COLORS.current.append([0, 0, int(amplitude * 255)])

    COLORS.current.pop(0)
    connection.send_razer_data(server, device, utils.convert_colors(COLORS.current))
