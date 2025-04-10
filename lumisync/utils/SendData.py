import base64
import json
import socket

from . import GetData

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
settings = GetData.get_device_data()
device = settings["devices"][settings["selectedDevice"]]


def convert_colors(colors: list) -> str:
    razer_header = [0xBB, 0x00, 0x0E, 0xB0, 0x01, len(colors)]
    for color in colors:
        razer_header.extend(color)
    checksum = 0
    for byte in razer_header:
        checksum ^= byte
    razer_header.append(checksum)
    base64_header = base64.b64encode(bytearray(razer_header))
    return base64_header.decode("utf-8")


def send_razer_data(data: base64) -> None:
    data = {"msg": {"cmd": "razer", "data": {"pt": data}}}
    send_data(data)
    return


def send_on_off(on_off: bool = None) -> None:
    send_data({"msg": {"cmd": "turn", "data": {"value": 1 if on_off else 0}}})


def send_razer_on_off(on_off: bool = None) -> None:
    send_data(
        {"msg": {"cmd": "razer", "data": {"pt": "uwABsQEK" if on_off else "uwABsgEJ"}}}
    )


def send_data(data) -> json:
    sock.sendto(
        bytes(json.dumps(data), "utf-8"),
        (device.get("Device_IP"), device.get("Device_Port", 4003)),
    )
    return
