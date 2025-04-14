import time

import colour
import dxcam
from PIL import Image

from ..utils import SendData

ss = dxcam.create()


def lerp(start_value, end, t):
    return start_value + t * (end - start_value)


def start():
    SendData.send_razer_on_off(True)
    previous_colors = [(0, 0, 0)] * 10  # Initialize with black colors
    while True:
        colors = []
        try:
            screen = ss.grab()
            if screen is None:
                continue

            screen = Image.fromarray(screen)
            width, height = screen.size
        except OSError:
            print("Warning: Screenshot failed, trying again...")
            continue

        top, bottom = int(height / 4 * 2), int(height / 4 * 3)

        for x in range(4):
            img = screen.crop((int(width / 4 * x), 0, int(width / 4 * (x + 1)), top))
            point = (int(img.size[0] / 2), int(img.size[1] / 2))
            colors.append(img.getpixel(point))
        colors.reverse()
        img = screen.crop((0, top, int(width / 4), bottom))
        point = (int(img.size[0] / 2), int(img.size[1] / 2))
        colors.append(img.getpixel(point))
        for x in range(4):
            img = screen.crop(
                (int(width / 4 * x), bottom, int(width / 4 * (x + 1)), height)
            )
            point = (int(img.size[0] / 2), int(img.size[1] / 2))
            colors.append(img.getpixel(point))
        img = screen.crop((int((width / 4 * 3)), top, width, bottom))
        colors.append(img.getpixel(point))

        smooth_transition(previous_colors, colors)
        previous_colors = colors


def smooth_transition(previous_colors, colors, steps=10, delay=0.01):
    prev_colors = [
        colour.Color(rgb=(c[0] / 255, c[1] / 255, c[2] / 255)) for c in previous_colors
    ]
    next_colors = [
        colour.Color(rgb=(c[0] / 255, c[1] / 255, c[2] / 255)) for c in colors
    ]

    for step in range(steps):
        interpolated_colors = []
        for i in range(len(colors)):
            r = lerp(prev_colors[i].red, next_colors[i].red, step / steps)
            g = lerp(prev_colors[i].green, next_colors[i].green, step / steps)
            b = lerp(prev_colors[i].blue, next_colors[i].blue, step / steps)
            interpolated_colors.append((int(r * 255), int(g * 255), int(b * 255)))

        SendData.send_razer_data(SendData.convert_colors(interpolated_colors))
        time.sleep(delay)

