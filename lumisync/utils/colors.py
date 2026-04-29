"""
Color utilities for LumiSync.
This module provides functions for color manipulation and conversion.
"""

import base64
from typing import Iterable, List, Tuple

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


def clamp_channel(value: int) -> int:
    """Clamp a color channel to the byte range expected by Govee payloads."""
    try:
        channel = int(value)
    except (TypeError, ValueError):
        channel = 0
    return max(0, min(255, channel))


def normalize_rgb(color: Iterable[int]) -> Tuple[int, int, int]:
    """Normalize any RGB-like iterable into a safe 3-byte tuple."""
    values = list(color)[:3]
    values.extend([0] * (3 - len(values)))
    return (
        clamp_channel(values[0]),
        clamp_channel(values[1]),
        clamp_channel(values[2]),
    )


def fit_colors_to_count(
    colors: List[Tuple[int, int, int]],
    count: int,
) -> List[Tuple[int, int, int]]:
    """Resize a color list to a device's segment count by cycling samples."""
    count = max(0, int(count))
    if count == 0:
        return []
    if not colors:
        return [(0, 0, 0)] * count
    normalized = [normalize_rgb(color) for color in colors]
    return [normalized[index % len(normalized)] for index in range(count)]


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
    if len(colors) > 255:
        raise ValueError("Razer payload supports at most 255 RGB segments.")

    normalized_colors = [normalize_rgb(color) for color in colors]
    razer_header = [0xBB, 0x00, 0x0E, 0xB0, 0x01, len(normalized_colors)]
    for color in normalized_colors:
        razer_header.extend(color)

    checksum = 0
    for byte in razer_header:
        checksum ^= byte

    razer_header.append(checksum)
    base64_header = base64.b64encode(bytearray(razer_header))
    return base64_header.decode("utf-8")

