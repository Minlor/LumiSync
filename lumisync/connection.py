import select
import base64
import json
import socket
from typing import Any, Dict, List, Tuple

from colorama import Fore

from .config.options import CONNECTION


def listen(server: socket.socket) -> List[str]:
    """Listens for device response."""
    ready = select.select([server], [], [], server.gettimeout())
    if not ready[0]:
        print(f"{Fore.RED}Error: No device found!")
        # Don't exit the program, just return an empty list
        return []

    messages = []
    while True:
        try:
            message, address = server.recvfrom(4096)
            print(f"{Fore.LIGHTGREEN_EX}Received from {address}")
            messages.append(message)
        except (BlockingIOError, socket.timeout):
            break

    return messages


def parse(messages: List[str]) -> List[Dict[str, Any]]:
    """Parses messages from the devices."""
    devices = CONNECTION.devices
    for message in messages:
        message = json.loads(message)
        device = next(
            (x for x in devices if x["mac"] == message["msg"]["data"]["device"]),
            None,
        )
        if device is None:
            devices.append(
                {
                    "mac": message["msg"]["data"]["device"],
                    "model": message["msg"]["data"]["sku"],
                    "ip": message["msg"]["data"]["ip"],
                    "port": CONNECTION.default.port,
                }
            )
        else:
            device["ip"] = message["msg"]["data"]["ip"]
    return devices


def connect() -> Tuple[socket.socket, List[Dict[str, Any]]]:
    """Creates a server listening for devices."""
    print(f"{Fore.LIGHTGREEN_EX}Searching for devices...")
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    # Add socket reuse flag to prevent address already in use errors
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("", CONNECTION.default.listen_port))
    server.settimeout(CONNECTION.default.timeout)
    server.sendto(
        b'{"msg":{"cmd":"scan","data":{"account_topic":"reserve"}}}',
        (CONNECTION.default.multicast, CONNECTION.default.port),
    )
    return server, parse(listen(server))


# TODO: Combine the below functions into one (into a complete "send" function?)
def send(server: socket.socket, device: Dict[str, Any], data: Dict[str, Any]) -> None:
    """Sends some data from the server to a device."""
    server.sendto(
        bytes(json.dumps(data), "utf-8"),
        (device["ip"], device.get("Device_Port", 4003)),
    )


def send_razer_data(
    server: socket.socket, device: Dict[str, Any], data: base64
) -> None:
    send(server, device, {"msg": {"cmd": "razer", "data": {"pt": data}}})


def switch(server: socket.socket, device: Dict[str, Any], on: bool = False) -> None:
    send(server, device, {"msg": {"cmd": "turn", "data": {"value": int(on)}}})


def switch_razer(
    server: socket.socket, device: Dict[str, Any], on: bool = False
) -> None:
    send(
        server,
        device,
        {"msg": {"cmd": "razer", "data": {"pt": "uwABsQEK" if on else "uwABsgEJ"}}},
    )


def set_color(
    server: socket.socket, device: Dict[str, Any], r: int, g: int, b: int
) -> None:
    """Sets the device color."""
    send(
        server,
        device,
        {
            "msg": {
                "cmd": "colorwc",
                "data": {
                    "color": {"r": r, "g": g, "b": b},
                    "colorTemInKelvin": 0
                },
            }
        },
    )
