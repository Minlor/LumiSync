import base64
from typing import List, Tuple

import webcolors
from matplotlib.colors import CSS4_COLORS


def get_color(name: str, format: str = "rgb"):
    """Gets the a color by name and returns it in the specified format."""
    color = CSS4_COLORS.get(name.lower())
    if format == "rgb":
        color = webcolors.hex_to_rgb(color)
    return color


# TODO: Rewrite this for individual colors?
def convert_colors(colors: List[Tuple[int, int, int]]) -> str:
    razer_header = [0xBB, 0x00, 0x0E, 0xB0, 0x01, len(colors)]
    for color in colors:
        razer_header.extend(color)

    checksum = 0
    for byte in razer_header:
        checksum ^= byte

    razer_header.append(checksum)
    base64_header = base64.b64encode(bytearray(razer_header))
    return base64_header.decode("utf-8")
