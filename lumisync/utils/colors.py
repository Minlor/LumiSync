"""
Color utilities for LumiSync.
This module provides functions for color manipulation and conversion.
"""

import base64
import math
from typing import Iterable, List, Tuple

import webcolors

# webcolors ships the CSS3 named-color table; CSS4 added exactly one name.
_CSS4_EXTRAS = {"rebeccapurple": "#663399"}


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
    key = name.lower()
    hex_value = _CSS4_EXTRAS.get(key) or webcolors.name_to_hex(key)
    if format == "rgb":
        return tuple(webcolors.hex_to_rgb(hex_value))
    return hex_value


def kelvin_to_rgb(kelvin: int) -> Tuple[int, int, int]:
    """Approximate an sRGB color for a white color temperature in Kelvin.

    Based on Tanner Helland's widely used piecewise fit, clamped to the range
    Govee strips accept (~2000-9000 K). Used for tunable-white control and as a
    fallback where a device has no native white channel.
    """
    temp = max(1000, min(40000, int(kelvin))) / 100.0

    if temp <= 66:
        red = 255.0
    else:
        red = 329.698727446 * ((temp - 60) ** -0.1332047592)

    if temp <= 66:
        green = 99.4708025861 * math.log(temp) - 161.1195681661 if temp > 0 else 0.0
    else:
        green = 288.1221695283 * ((temp - 60) ** -0.0755148492)

    if temp >= 66:
        blue = 255.0
    elif temp <= 19:
        blue = 0.0
    else:
        blue = 138.5177312231 * math.log(temp - 10) - 305.0447927307

    return (
        int(max(0, min(255, round(red)))),
        int(max(0, min(255, round(green)))),
        int(max(0, min(255, round(blue)))),
    )


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


def resample_colors_to_count(
    colors: List[Tuple[int, int, int]],
    count: int,
) -> List[Tuple[int, int, int]]:
    """Resize perimeter colors so samples are spread over one physical loop."""
    count = max(0, int(count))
    if count == 0:
        return []
    if not colors:
        return [(0, 0, 0)] * count

    normalized = [normalize_rgb(color) for color in colors]
    source_count = len(normalized)
    if source_count == count:
        return list(normalized)

    resampled = []
    for index in range(count):
        position = index * source_count / count
        left = int(position) % source_count
        right = (left + 1) % source_count
        fraction = position - int(position)
        resampled.append(
            (
                int(lerp(normalized[left][0], normalized[right][0], fraction)),
                int(lerp(normalized[left][1], normalized[right][1], fraction)),
                int(lerp(normalized[left][2], normalized[right][2], fraction)),
            )
        )
    return resampled


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

