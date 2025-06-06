"""
Color utilities for LumiSync.
This module provides functions for color manipulation and conversion.
"""

import base64
from typing import List, Tuple

import webcolors
from matplotlib.colors import CSS4_COLORS


def lerp(start: float, end: float, step: float) -> float:
    """Performs a linear interpolation.

    Parameters
    ----------
    start : float
        The start value.
    end : float
        The end value.
    step : float
        The interpolation factor (0.0 to 1.0).

    Returns
    -------
    float
        The interpolated value.
    """
    return start + (end - start) * step


def get_color(name: str, format: str = "rgb") -> Tuple[int, int, int] | str:
    """Gets the color by name and returns it in the specified format.

    Parameters
    ----------
    name : str
        The color's name.
    format : str, optional
        The color's return format. Either "hexadecimal" or "rgb".
        Default is "rgb".

    Returns
    -------
    color : str or tuple of int
        Either a string if "hexadecimal" or a tuple of ints if "rgb".
    """
    color = CSS4_COLORS.get(name.lower())
    if format == "rgb":
        color = webcolors.hex_to_rgb(color)
    return color


def convert_colors(colors: List[Tuple[int, int, int]]) -> str:
    """Converts a list of RGB colors to the Razer protocol format.

    Parameters
    ----------
    colors : List[Tuple[int, int, int]]
        A list of RGB color tuples.

    Returns
    -------
    str
        Base64 encoded string for the Razer protocol.
    """
    razer_header = [0xBB, 0x00, 0x0E, 0xB0, 0x01, len(colors)]
    for color in colors:
        razer_header.extend(color)

    checksum = 0
    for byte in razer_header:
        checksum ^= byte

    razer_header.append(checksum)
    base64_header = base64.b64encode(bytearray(razer_header))
    return base64_header.decode("utf-8")
