import numpy as np
import soundcard as sc

from ..utils import SendData

# Set the audio parameters
duration = 0.01  # seconds
sample_rate = 48000  # You can adjust this based on your requirements

LED_COUNT = 20
colors = [[0, 0, 0]] * LED_COUNT


def start():
    SendData.send_razer_on_off(True)
    while True:
        with sc.get_microphone(
            id=str(sc.default_speaker().name), include_loopback=True
        ).recorder(samplerate=sample_rate) as mic:
            # Try and except due to a soundcard error when no audio is playing
            try:
                data = mic.record(numframes=duration * sample_rate)
            except TypeError:
                data = None
            amp = get_amplitude(data)
            wave_color(amp)


def get_amplitude(mic_data=None):
    if mic_data is None:
        return 0
    amplitude = np.max(np.abs(mic_data))
    if amplitude > 1:
        amplitude = 1
    return amplitude


def wave_color(amplitude):
    match amplitude:
        case amplitude if amplitude < 0.04:
            colors.append([int(amplitude * 255), 0, 0])
        case amplitude if amplitude > 0.04 and amplitude < 0.08:
            colors.append([0, int(amplitude * 255), 0])
        case _:
            colors.append([0, 0, int(amplitude * 255)])
    colors.pop(0)
    SendData.send_razer_data(SendData.convert_colors(colors))

