import base64
from typing import Any, Dict, List, Tuple

import json
import webcolors
from matplotlib.colors import CSS4_COLORS
from colorama import Fore


# TODO: This could perhaps be replaced by numpy functions -> faster
def lerp(start: float, end: float, step: float) -> float:
    """Performs a linear interpolation."""
    return start + (end - start) * step


def get_color(name: str, format: str = "rgb") -> Tuple[int, int, int] | str:
    """Gets the a color by name and returns it in the specified format.

    Parameters
    ----------
    name : str
        The color's name.
    format : str, optional
        The color's return format. Either hexadecimal or rgb.
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


# TODO: Add documentation
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


def writeJSON(settings: Dict[str, Any]) -> None:
    """Writes a .json file."""
    with open("settings.json", "w") as f:
        json.dump(settings, f)
    print(f"{Fore.LIGHTGREEN_EX}Data written to Settings.json")


# TODO: This needs to be reimplemented for a more permanent configuration
# def parseJSON() -> Dict[str, Any]:
#     """Gets a devices's data."""
#     try:
#         with open("Settings.json", "r") as f:
#             data = json.load(f)
#
#         if time.time() - data.get("time", 0) > 86400:
#             print("Device data is older than 24 hours, requesting new data...")
#             # TODO: Replace this
#             data = devices.start()
#         if len(data["devices"]) > 1:
#             print(
#                 f"{colorama.Fore.LIGHTYELLOW_EX}Please select a device:\n{colorama.Fore.YELLOW}"
#                 + "\n".join(
#                     [
#                         f"{i + 1}) {device['Device_IP']} ({device['Model']})"
#                         for i, device in enumerate(data["devices"])
#                     ]
#                 )
#             )
#             selectedDevice = input("")
#             data["selectedDevice"] = int(selectedDevice) - 1
#             utils.writeJSON(data)
#         return data
#
#     except FileNotFoundError:
#         print("Settings.json not found, requesting new data...")
#         # TODO: Replace this
#         data = devices.start()
#         return data
