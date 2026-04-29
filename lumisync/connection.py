import select
import base64
import json
import platform
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

from colorama import Fore

from .config.options import CONNECTION


STATUS_COMMANDS = ("status", "devStatus")
LAN_REQUIREMENTS_MESSAGE = (
    "Make sure the device is on the same network and LAN Control is enabled "
    "for it in Govee Home."
)
PORT_IN_USE_MESSAGE = (
    f"UDP port {CONNECTION.default.listen_port} is already in use. "
    "Close Govee Desktop or another LumiSync instance, then try again. "
    f"{LAN_REQUIREMENTS_MESSAGE}"
)


def _windows_udp_port_owner(port: int) -> Optional[str]:
    """Return the PID using a UDP port on Windows, based on read-only netstat."""
    if platform.system() != "Windows":
        return None

    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "udp"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    needle = f":{int(port)}"
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[0].upper() == "UDP" and needle in parts[1]:
            return f"PID {parts[-1]}"
    return None


def describe_port_conflict(
    port: Optional[int] = None,
    owner: Optional[str] = None,
) -> str:
    """Build a clear, user-facing LAN socket conflict message."""
    port = CONNECTION.default.listen_port if port is None else int(port)
    owner = owner or _windows_udp_port_owner(port)
    message = (
        f"UDP port {port} is already in use. Close Govee Desktop or another "
        f"LumiSync instance, then try again. {LAN_REQUIREMENTS_MESSAGE}"
    )
    if owner:
        message += f" Windows reports {owner} using the port."
    return message


def create_lan_socket(
    bind_port: Optional[int] = None,
    timeout: Optional[float] = None,
    *,
    broadcast: bool = True,
) -> socket.socket:
    """Create the UDP socket LumiSync uses for Govee LAN traffic."""
    port = CONNECTION.default.listen_port if bind_port is None else int(bind_port)
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if broadcast:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("", port))
    except OSError as exc:
        server.close()
        raise OSError(describe_port_conflict(port)) from exc
    server.settimeout(CONNECTION.default.timeout if timeout is None else timeout)
    return server


def get_segment_count(device: Dict[str, Any], default: int = 10) -> int:
    """Return the best known LED/segment count for a LAN device."""
    fallback = default if isinstance(default, int) and default > 0 else 10
    for key in (
        "SegmentNums",
        "segmentNums",
        "SegmentNum",
        "segmentNum",
        "segments",
        "nled",
        "led_count",
        "ledCount",
    ):
        value = device.get(key)
        if isinstance(value, (list, tuple)):
            value = len(value)
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        if 0 < count <= 255:
            return count
    return fallback


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
    """Creates a server listening for devices.

    Tries both multicast addresses (239.255.255.250 and 255.255.255.255)
    for improved compatibility across different network configurations.
    """
    print(f"{Fore.LIGHTGREEN_EX}Searching for devices...")
    server = create_lan_socket()

    # Try both multicast addresses for better compatibility
    multicast_addresses = ["239.255.255.250", "255.255.255.255"]
    scan_message = b'{"msg":{"cmd":"scan","data":{"account_topic":"reserve"}}}'

    for addr in multicast_addresses:
        try:
            server.sendto(scan_message, (addr, CONNECTION.default.port))
            print(f"{Fore.LIGHTGREEN_EX}Sent discovery to {addr}")
        except Exception as e:
            print(f"{Fore.YELLOW}Failed to send to {addr}: {e}")

    return server, parse(listen(server))


# TODO: Combine the below functions into one (into a complete "send" function?)
def send(server: socket.socket, device: Dict[str, Any], data: Dict[str, Any]) -> None:
    """Sends some data from the server to a device."""
    server.sendto(
        bytes(json.dumps(data), "utf-8"),
        (
            device["ip"],
            device.get("port", device.get("Device_Port", CONNECTION.default.port)),
        ),
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


def set_brightness(
    server: socket.socket, device: Dict[str, Any], brightness: int
) -> None:
    """Sets the device brightness.

    Args:
        server: Socket server
        device: Device dictionary
        brightness: Brightness value (0-100)
    """
    send(
        server,
        device,
        {
            "msg": {
                "cmd": "brightness",
                "data": {
                    "value": max(0, min(100, brightness))
                },
            }
        },
    )


def parse_status_payload(payload: Any) -> Optional[Dict[str, Any]]:
    """Parse a Govee LAN status response payload into UI-friendly state."""
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="replace")
        if isinstance(payload, str):
            payload = json.loads(payload)

        msg = payload.get("msg", {})
        if msg.get("cmd") not in STATUS_COMMANDS:
            return None

        data = msg.get("data", {}) or {}
        color = data.get("color") or {}
        parsed_color = None
        if all(k in color for k in ("r", "g", "b")):
            parsed_color = (
                int(color.get("r", 0)),
                int(color.get("g", 0)),
                int(color.get("b", 0)),
            )

        on_off = data.get("onOff")
        return {
            "power_on": None if on_off is None else bool(int(on_off)),
            "brightness": None if data.get("brightness") is None else int(data["brightness"]),
            "color": parsed_color,
            "color_temp": data.get("colorTemInKelvin"),
            "raw": data,
        }
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def query_status(
    server: socket.socket,
    device: Dict[str, Any],
    timeout: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Query a device for its current LAN status.

    Recent Govee Desktop builds use `status`, while some integrations and
    devices have used `devStatus`. Try both for firmware compatibility.
    """
    previous_timeout = server.gettimeout()
    effective_timeout = CONNECTION.default.timeout if timeout is None else timeout
    deadline = time.monotonic() + max(0.05, effective_timeout)

    try:
        server.settimeout(max(0.05, effective_timeout))
        command_index = 0
        send(
            server,
            device,
            {"msg": {"cmd": STATUS_COMMANDS[command_index], "data": {}}},
        )

        while time.monotonic() < deadline:
            try:
                packet, address = server.recvfrom(4096)
            except socket.timeout:
                command_index += 1
                if command_index >= len(STATUS_COMMANDS):
                    return None

                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None

                server.settimeout(max(0.05, remaining))
                send(
                    server,
                    device,
                    {"msg": {"cmd": STATUS_COMMANDS[command_index], "data": {}}},
                )
                continue

            # Ignore unrelated traffic. Broadcast sockets can receive stale
            # discovery or status packets from other devices.
            if device.get("ip") and address[0] != device.get("ip"):
                continue

            status = parse_status_payload(packet)
            if status is not None:
                return status

        return None
    finally:
        server.settimeout(previous_timeout)
