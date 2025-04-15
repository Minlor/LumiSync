import base64
import json
import socket
import time
from typing import Any, Dict

import colorama

from . import devices


def get_data() -> Dict[str, Any]:
    """Gets a devices's data."""
    try:
        with open("Settings.json", "r") as f:
            data = json.load(f)

        if time.time() - data.get("time", 0) > 86400:
            print("Device data is older than 24 hours, requesting new data...")
            data = devices.start()
        if len(data["devices"]) > 1:
            print(
                f"{colorama.Fore.LIGHTYELLOW_EX}Please select a device:\n{colorama.Fore.YELLOW}"
                + "\n".join(
                    [
                        f"{i + 1}) {device['Device_IP']} ({device['Model']})"
                        for i, device in enumerate(data["devices"])
                    ]
                )
            )
            selectedDevice = input("")
            data["selectedDevice"] = int(selectedDevice) - 1
            devices.writeJSON(data)
        return data

    except FileNotFoundError:
        print("Settings.json not found, requesting new data...")
        data = devices.start()
        return data


sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
settings = get_data()
device = settings["devices"][settings["selectedDevice"]]


# TODO: Combine the below functions into one
def send_data(data: Dict[str, Any]) -> None:
    """Sends data to a device."""
    sock.sendto(
        bytes(json.dumps(data), "utf-8"),
        (device.get("Device_IP"), device.get("Device_Port", 4003)),
    )


def send_razer_data(data: base64) -> None:
    """Sends data to a device (razer mode)."""
    send_data({"msg": {"cmd": "razer", "data": {"pt": data}}})


def switch(on: bool = False) -> None:
    """Switches the device on or off."""
    send_data({"msg": {"cmd": "turn", "data": {"value": int(on)}}})


def switch_razer(on: bool = False) -> None:
    """Switches the device on or off (razer mode)."""
    send_data(
        {"msg": {"cmd": "razer", "data": {"pt": "uwABsQEK" if on else "uwABsgEJ"}}}
    )
