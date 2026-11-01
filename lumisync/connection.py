import base64
import json
import platform
import queue
import socket
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from . import sku_catalog
from .config.options import CONNECTION
from .device import DeviceLike, as_device


STATUS_COMMANDS = ("status", "devStatus")
DISCOVERY_METADATA_KEYS = ("Device_Port", "devicePort")
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
    resolved_port = CONNECTION.default.listen_port if port is None else int(port)
    owner = owner or _windows_udp_port_owner(resolved_port)
    message = (
        f"UDP port {resolved_port} is already in use. Close Govee Desktop or another "
        f"LumiSync instance, then try again. {LAN_REQUIREMENTS_MESSAGE}"
    )
    if owner:
        message += f" Windows reports {owner} using the port."
    return message


class _LanSocketCore:
    """One UDP socket with a reader thread that fans replies out to callers.

    Govee devices answer a status query to UDP 4002, not to the querying
    socket. Binding 4002 separately for sync, status polling and each ad-hoc
    adapter meant the kernel delivered each reply to only one of them — often
    the sync socket, which never reads. Every caller now shares this socket and
    receives its own copy of every datagram.
    """

    def __init__(self, port: int, broadcast: bool) -> None:
        self.port = port
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if broadcast:
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        # On Windows SO_REUSEADDR lets Govee Desktop bind the same port first
        # and silently steal replies. Exclusive use turns that into a clear
        # conflict message instead.
        if platform.system() == "Windows" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self._socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1
            )
        try:
            self._socket.bind(("", port))
        except OSError as exc:
            self._socket.close()
            raise OSError(describe_port_conflict(port)) from exc

        self._socket.settimeout(0.25)
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._consumers: List["SharedLanSocket"] = []
        self._closed = False
        self._thread = threading.Thread(
            target=self._read_loop, name="lumisync-lan-reader", daemon=True
        )
        self._thread.start()

    def register(self, consumer: "SharedLanSocket") -> None:
        with self._lock:
            if self._closed:
                raise OSError("LAN socket is closed")
            self._consumers.append(consumer)

    def unregister(self, consumer: "SharedLanSocket") -> None:
        with self._lock:
            if consumer in self._consumers:
                self._consumers.remove(consumer)
            empty = not self._consumers
            if empty:
                self._closed = True
        if not empty:
            return
        with _lan_cores_lock:
            if _lan_cores.get(self.port) is self:
                _lan_cores.pop(self.port, None)
        try:
            self._socket.close()
        except OSError:
            pass

    def sendto(self, data, address) -> None:
        with self._send_lock:
            if self._closed:
                raise OSError("LAN socket is closed")
            self._socket.sendto(data, address)

    def fileno(self) -> int:
        return self._socket.fileno()

    def getsockname(self):
        return self._socket.getsockname()

    def _read_loop(self) -> None:
        while True:
            with self._lock:
                if self._closed:
                    return
            try:
                data, address = self._socket.recvfrom(65535)
            except socket.timeout:
                continue
            except ConnectionResetError:
                # Windows reports an ICMP "port unreachable" from an earlier
                # send (say, to a stale device IP) on the next receive. It says
                # nothing about this socket, so keep reading.
                continue
            except OSError:
                return
            with self._lock:
                consumers = list(self._consumers)
            for consumer in consumers:
                consumer._deliver(data, address)


class SharedLanSocket:
    """A per-caller queue in front of the process-wide :class:`_LanSocketCore`."""

    _MAX_PENDING = 512

    def __init__(self, core: _LanSocketCore, timeout: Optional[float]) -> None:
        self._core = core
        self._timeout = timeout
        self._queue: "queue.Queue[Tuple[bytes, Any]]" = queue.Queue(
            maxsize=self._MAX_PENDING
        )
        self._closed = False

    def _deliver(self, data: bytes, address) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait((data, address))
        except queue.Full:
            # A caller that never reads (the sync socket) must not grow without
            # bound; drop the oldest reply instead.
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait((data, address))
            except queue.Full:
                pass

    def sendto(self, data, address) -> None:
        self._core.sendto(data, address)

    def recvfrom(self, bufsize: int):
        if self._closed:
            raise OSError("LAN socket is closed")
        timeout = self._timeout
        try:
            data, address = self._queue.get(
                timeout=timeout if timeout and timeout > 0 else None
            )
        except queue.Empty:
            raise socket.timeout("timed out") from None
        return data[:bufsize], address

    def settimeout(self, timeout: Optional[float]) -> None:
        self._timeout = timeout

    def gettimeout(self) -> Optional[float]:
        return self._timeout

    def fileno(self) -> int:
        return self._core.fileno()

    def getsockname(self):
        return self._core.getsockname()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._core.unregister(self)

    @property
    def closed(self) -> bool:
        return self._closed


LanSocket = Union[socket.socket, SharedLanSocket]

_lan_cores: Dict[int, _LanSocketCore] = {}
_lan_cores_lock = threading.Lock()


def create_lan_socket(
    bind_port: Optional[int] = None,
    timeout: Optional[float] = None,
    *,
    broadcast: bool = True,
) -> SharedLanSocket:
    """Return the shared UDP socket used for Govee LAN traffic."""
    port = CONNECTION.default.listen_port if bind_port is None else int(bind_port)
    effective_timeout = CONNECTION.default.timeout if timeout is None else timeout
    for _attempt in range(2):
        with _lan_cores_lock:
            core = _lan_cores.get(port)
            if core is None:
                core = _LanSocketCore(port, broadcast)
                _lan_cores[port] = core
        consumer = SharedLanSocket(core, effective_timeout)
        try:
            core.register(consumer)
        except OSError:
            # The last consumer closed between lookup and registration.
            continue
        return consumer
    raise OSError(describe_port_conflict(port))


def get_device_port(raw_device: DeviceLike) -> int:
    """Return the UDP control port for a device."""
    device = as_device(raw_device)
    for value in (device.get("device_port"), device.port):
        if value is None:
            continue
        try:
            port = int(value)
        except (TypeError, ValueError):
            continue
        if 0 < port <= 65535:
            # Older LumiSync discovery saved the scan port (4001) as the device
            # control port. Keep manual overrides, but repair stale entries.
            if device.manual or port != CONNECTION.default.port:
                return port

    return CONNECTION.default.control_port


def get_segment_count(raw_device: DeviceLike, default: int = 10) -> int:
    """Return the effective zone count for a device.

    Precedence: explicit user override, then the SKU capability catalog
    (bundled table or an imported Govee Desktop cache), then the generic
    default. The catalog lets known models auto-configure their zone count
    without the Govee app.
    """
    device = as_device(raw_device)
    fallback = default if isinstance(default, int) and default > 0 else 10
    if device.segment_count_override is not None:
        return device.segment_count_override

    catalog_count = sku_catalog.segment_count_for(device.catalog_sku)
    if catalog_count and 0 < catalog_count <= 255:
        return catalog_count

    return fallback


def listen(server) -> List[str]:
    """Listen for device responses until the first quiet period."""
    previous_timeout = server.gettimeout()
    try:
        server.settimeout(previous_timeout or CONNECTION.default.timeout)
        try:
            message, address = server.recvfrom(4096)
        except (BlockingIOError, socket.timeout):
            print("Error: No device found!")
            # Don't exit the program, just return an empty list
            return []

        messages = [message]
        print(f"Received from {address}")
        # Drain replies that arrived together without waiting another full
        # discovery timeout.
        server.settimeout(0.2)
        while True:
            try:
                message, address = server.recvfrom(4096)
            except (BlockingIOError, socket.timeout):
                break
            print(f"Received from {address}")
            messages.append(message)
        return messages
    finally:
        try:
            server.settimeout(previous_timeout)
        except OSError:
            pass


def parse(messages: List[str]) -> List[Dict[str, Any]]:
    """Parses discovery messages from the devices.

    The shared LAN socket also receives status replies, so ignore any datagram
    that is not a scan response instead of raising on a missing field.
    """
    devices = CONNECTION.devices
    for message in messages:
        try:
            payload = json.loads(message)
            data = payload["msg"]["data"]
            parsed_device = {
                "mac": data["device"],
                "model": data["sku"],
                "ip": data["ip"],
                "port": CONNECTION.default.control_port,
            }
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            continue
        for key in DISCOVERY_METADATA_KEYS:
            if key in data and data[key] is not None:
                parsed_device[key] = data[key]

        device = next(
            (x for x in devices if x["mac"] == parsed_device["mac"]),
            None,
        )
        if device is None:
            devices.append(parsed_device)
        else:
            device.update(parsed_device)
    return devices


def connect() -> Tuple[LanSocket, List[Dict[str, Any]]]:
    """Creates a server listening for devices.

    Tries both multicast addresses (239.255.255.250 and 255.255.255.255)
    for improved compatibility across different network configurations.
    """
    print("Searching for devices...")
    server = create_lan_socket()

    # Try both multicast addresses for better compatibility
    multicast_addresses = ["239.255.255.250", "255.255.255.255"]
    scan_message = b'{"msg":{"cmd":"scan","data":{"account_topic":"reserve"}}}'

    for addr in multicast_addresses:
        try:
            server.sendto(scan_message, (addr, CONNECTION.default.port))
            print(f"Sent discovery to {addr}")
        except Exception as e:
            print(f"Failed to send to {addr}: {e}")

    return server, parse(listen(server))


def local_ipv4() -> Optional[str]:
    """Best-effort local IPv4 address, without sending any traffic."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()


def subnet_hosts(local_ip: Optional[str]) -> List[str]:
    """Return every host address in ``local_ip``'s /24, excluding self/.0/.255."""
    if not local_ip or local_ip.count(".") != 3:
        return []
    prefix, _, last = local_ip.rpartition(".")
    try:
        own = int(last)
    except ValueError:
        return []
    return [f"{prefix}.{host}" for host in range(1, 255) if host != own]


def sweep_scan(timeout: float = 2.5) -> Tuple[LanSocket, List[Dict[str, Any]]]:
    """Discover devices by unicasting the scan probe to every host in the /24.

    A fallback for networks that filter UDP multicast (guest/segmented Wi-Fi,
    some managed switches), where :func:`connect` finds nothing. Probes each
    host on the scan and control ports, then parses whoever answers as Govee.
    """
    print("Sweeping local subnet for devices...")
    server = create_lan_socket(timeout=timeout)
    scan_message = b'{"msg":{"cmd":"scan","data":{"account_topic":"reserve"}}}'

    targets = subnet_hosts(local_ipv4())
    for addr in targets:
        for port in (CONNECTION.default.port, CONNECTION.default.control_port):
            try:
                server.sendto(scan_message, (addr, port))
            except OSError:
                pass

    # Broadcast too, in case unicast is filtered but broadcast is not.
    try:
        server.sendto(scan_message, ("255.255.255.255", CONNECTION.default.port))
    except OSError:
        pass

    return server, parse(listen(server))


# TODO: Combine the below functions into one (into a complete "send" function?)
def send(server: LanSocket, device: DeviceLike, data: Dict[str, Any]) -> None:
    """Sends some data from the server to a device."""
    typed = as_device(device)
    server.sendto(
        bytes(json.dumps(data), "utf-8"),
        (typed.ip, get_device_port(typed)),
    )


def send_razer_data(
    server: LanSocket, device: DeviceLike, data: str
) -> None:
    send(server, device, {"msg": {"cmd": "razer", "data": {"pt": data}}})


def switch(server: LanSocket, device: DeviceLike, on: bool = False) -> None:
    send(server, device, {"msg": {"cmd": "turn", "data": {"value": int(on)}}})


def switch_razer(
    server: LanSocket, device: DeviceLike, on: bool = False
) -> None:
    frame = bytearray([0xBB, 0x00, 0x01, 0xB1, int(on)])
    checksum = 0
    for byte in frame:
        checksum ^= byte
    frame.append(checksum)
    send(
        server,
        device,
        {
            "msg": {
                "cmd": "razer",
                "data": {"pt": base64.b64encode(frame).decode("ascii")},
            }
        },
    )


def set_color(
    server: LanSocket, device: DeviceLike, r: int, g: int, b: int
) -> None:
    """Sets the device color."""
    send(
        server,
        device,
        {
            "msg": {
                "cmd": "colorwc",
                "data": {"color": {"r": r, "g": g, "b": b}, "colorTemInKelvin": 0},
            }
        },
    )


def set_color_temp(
    server: LanSocket,
    device: DeviceLike,
    kelvin: int,
    rgb: Optional[Tuple[int, int, int]] = None,
) -> None:
    """Set a tunable-white color temperature via the colorwc command.

    Govee Desktop sends the Kelvin value alongside the RGB that represents it
    (its ``colorTemInKelvincolor``). We mirror that: ``colorTemInKelvin`` drives
    the white channel and ``color`` carries the equivalent RGB for firmware that
    reads it.
    """
    from .utils.colors import kelvin_to_rgb

    r, g, b = rgb if rgb is not None else kelvin_to_rgb(kelvin)
    send(
        server,
        device,
        {
            "msg": {
                "cmd": "colorwc",
                "data": {
                    "color": {"r": int(r), "g": int(g), "b": int(b)},
                    "colorTemInKelvin": int(kelvin),
                },
            }
        },
    )


def set_brightness(
    server: LanSocket, device: DeviceLike, brightness: int
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
                "data": {"value": max(0, min(100, brightness))},
            }
        },
    )


# ---------------------------------------------------------------------------
# H6080 protocol support (Phase 1)
#
# Govee Desktop 2.40.60 references GetCtroSendJsonOfH6080,
# BuildH6080LightSwitchCommand, and IsSeedColor in
# Govee.Application.DeviceService. The H6080 family uses the same JSON-over-UDP
# envelope as standard Govee LAN devices but carries H6080-specific fields
# (lightType, timeStamp) alongside the usual color/brightness/onOff payload.
# ---------------------------------------------------------------------------


def is_h6080_sku(sku: Optional[str]) -> bool:
    """Return True if the SKU belongs to the H6080 device family."""
    return str(sku or "").strip().upper().startswith("H608")


# SKUs that use the 0x0B color command instead of colorwc.
# Govee Desktop 2.40.60 references GetColorSendJsonOf0X0B in
# Govee.Application.DeviceService for certain device families. The specific
# SKU set is not yet confirmed from live traffic — populate this set as
# devices are identified.
_0X0B_SKUS: set[str] = set()


def is_0x0b_sku(sku: Optional[str]) -> bool:
    """Return True if the SKU uses the 0x0B color protocol instead of colorwc."""
    return str(sku or "").strip().upper() in _0X0B_SKUS


def register_0x0b_sku(sku: str) -> None:
    """Register a SKU as using the 0x0B color protocol."""
    _0X0B_SKUS.add(str(sku).strip().upper())


def build_h6080_color_json(
    device: DeviceLike,
    r: int,
    g: int,
    b: int,
    kelvin: int = 0,
) -> Dict[str, Any]:
    """Build an H6080 color-control JSON payload.

    Mirrors the standard ``colorwc`` command but adds H6080-specific fields
    (``lightType``, ``timeStamp``) observed in Govee Desktop's
    ``GetCtroSendJsonOfH6080``.
    """
    return {
        "msg": {
            "cmd": "colorwc",
            "data": {
                "color": {"r": int(r), "g": int(g), "b": int(b)},
                "colorTemInKelvin": int(kelvin),
                "lightType": 1,
                "timeStamp": int(time.time() * 1000),
            },
        }
    }


def build_h6080_switch_json(device: DeviceLike, on: bool) -> Dict[str, Any]:
    """Build an H6080 power-control JSON payload.

    Mirrors the standard ``turn`` command but adds H6080-specific fields
    (``lightType``, ``timeStamp``).
    """
    return {
        "msg": {
            "cmd": "turn",
            "data": {
                "value": int(on),
                "lightType": 1,
                "timeStamp": int(time.time() * 1000),
            },
        }
    }


def send_h6080_color(
    server: LanSocket,
    device: DeviceLike,
    r: int,
    g: int,
    b: int,
    kelvin: int = 0,
) -> None:
    """Send an H6080 color command over LAN UDP."""
    send(server, device, build_h6080_color_json(device, r, g, b, kelvin))


def send_h6080_switch(server: LanSocket, device: DeviceLike, on: bool) -> None:
    """Send an H6080 power command over LAN UDP."""
    send(server, device, build_h6080_switch_json(device, on))


# ---------------------------------------------------------------------------
# 0x0B protocol support (Phase 1)
#
# Govee Desktop 2.40.60 references GetColorSendJsonOf0X0B in
# Govee.Application.DeviceService. The 0x0B protocol uses a distinct command
# identifier for color control on certain device families.
# ---------------------------------------------------------------------------


def build_0x0b_color_json(
    device: DeviceLike,
    r: int,
    g: int,
    b: int,
) -> Dict[str, Any]:
    """Build a 0x0B color-control JSON payload."""
    return {
        "msg": {
            "cmd": "0x0B",
            "data": {
                "color": {"r": int(r), "g": int(g), "b": int(b)},
                "timeStamp": int(time.time() * 1000),
            },
        }
    }


def send_0x0b_color(
    server: LanSocket, device: DeviceLike, r: int, g: int, b: int
) -> None:
    """Send a 0x0B color command over LAN UDP."""
    send(server, device, build_0x0b_color_json(device, r, g, b))


# ---------------------------------------------------------------------------
# Advanced segment control (Phase 2)
#
# Govee Desktop 2.40.60 references ControlColorBarByIot,
# NewControlColorBarByIot, GetSegmentInfoByBase64, and
# GetSegmentInfoByBase64New. These use per-segment color data carried either
# as JSON (skuID + segments) or as a binary base64 frame (pt parameter).
# The binary frame format mirrors the existing Razer switch frame
# (0xBB 0x00 0x01 0xB1 <payload...> <xor-checksum>) but carries per-segment
# RGB values instead of a single on/off byte.
# ---------------------------------------------------------------------------


def _xor_checksum(frame: bytes | bytearray) -> int:
    checksum = 0
    for byte in frame:
        checksum ^= byte
    return checksum


def build_segment_frame(
    colors: List[Tuple[int, int, int]],
    sku_id: Optional[str] = None,
) -> bytes:
    """Build a binary segment-control frame for advanced per-zone control.

    Frame layout (Govee proprietary binary protocol):
      0xBB 0x00 0x01 0xB1 <segment_count:1 byte> <R G B per segment...> <xor-checksum>

    The checksum is the XOR of all preceding bytes.
    """
    frame = bytearray([0xBB, 0x00, 0x01, 0xB1])
    frame.append(len(colors) & 0xFF)
    for r, g, b in colors:
        frame.append(max(0, min(255, int(r))))
        frame.append(max(0, min(255, int(g))))
        frame.append(max(0, min(255, int(b))))
    checksum = 0
    for byte in frame:
        checksum ^= byte
    frame.append(checksum)
    return bytes(frame)


def send_segment_frame(
    server: LanSocket,
    device: DeviceLike,
    colors: List[Tuple[int, int, int]],
) -> None:
    """Send a binary segment-control frame over LAN UDP (razer transport).

    Encodes the binary frame as base64 in the ``pt`` field, matching the
    existing Razer switch frame transport.
    """
    frame = build_segment_frame(colors)
    send(
        server,
        device,
        {
            "msg": {
                "cmd": "razer",
                "data": {"pt": base64.b64encode(frame).decode("ascii")},
            }
        },
    )


def build_segment_frame_multi_pack(
    colors: List[Tuple[int, int, int]],
) -> bytes:
    """Build a multi-pack (run-length encoded) segment-control frame.

    Groups consecutive segments with identical colors into runs to reduce
    frame size. Govee Desktop's ``MultiPackControlColorBarByIot`` uses this
    packing for devices that support it.

    Frame layout:
      0xBB 0x00 0x01 0xB2 <num_runs>
      <run>...                                        (repeated per run)
      <xor-checksum>

    Each run is:
      type 0 (color run):   0x00 <run_count> <R> <G> <B>
      type 1 (brightness):  0x01 <run_count> <brightness 0-100>

    The checksum is the XOR of all preceding bytes.
    """
    if not colors:
        frame = bytearray([0xBB, 0x00, 0x01, 0xB2, 0x00])
        frame.append(_xor_checksum(frame))
        return bytes(frame)

    # Normalize and cluster consecutive identical colors into runs.
    normalized = [
        (max(0, min(255, int(r))), max(0, min(255, int(g))), max(0, min(255, int(b))))
        for r, g, b in colors
    ]

    runs: List[Tuple[int, int, int, int, int]] = []  # (type, count, r, g, b)
    cur_color = normalized[0]
    cur_count = 1
    for color in normalized[1:]:
        if color == cur_color:
            cur_count += 1
        else:
            runs.append((0, cur_count, *cur_color))
            cur_color = color
            cur_count = 1
    runs.append((0, cur_count, *cur_color))

    frame = bytearray([0xBB, 0x00, 0x01, 0xB2])
    frame.append(len(runs) & 0xFF)
    for run_type, count, r, g, b in runs:
        frame.append(run_type & 0xFF)
        frame.append(min(count, 255) & 0xFF)
        frame.append(r)
        frame.append(g)
        frame.append(b)
    checksum = 0
    for byte in frame:
        checksum ^= byte
    frame.append(checksum)
    return bytes(frame)


def build_segment_frame_gradient(
    colors: List[Tuple[int, int, int]],
) -> bytes:
    """Build a gradient segment-control frame.

    Sends a start color, end color, and segment count; the device interpolates
    intermediate colors. Govee Desktop's ``ControlColorGradientGeneratorByIot``
    uses this for gradient segments.

    Frame layout:
      0xBB 0x00 0x01 0xB3 <segment_count> <start_R> <start_G> <start_B>
                          <end_R> <end_G> <end_B> <xor-checksum>

    The checksum is the XOR of all preceding bytes.
    """
    if not colors:
        frame = bytearray([0xBB, 0x00, 0x01, 0xB3, 0x00])
        frame.extend(b"\x00" * 6)
        frame.append(_xor_checksum(frame))
        return bytes(frame)

    start = (
        max(0, min(255, int(colors[0][0]))),
        max(0, min(255, int(colors[0][1]))),
        max(0, min(255, int(colors[0][2]))),
    )
    end = (
        max(0, min(255, int(colors[-1][0]))),
        max(0, min(255, int(colors[-1][1]))),
        max(0, min(255, int(colors[-1][2]))),
    )

    frame = bytearray([0xBB, 0x00, 0x01, 0xB3])
    frame.append(len(colors) & 0xFF)
    frame.extend(start)
    frame.extend(end)
    checksum = 0
    for byte in frame:
        checksum ^= byte
    frame.append(checksum)
    return bytes(frame)


def send_segment_frame_multi_pack(
    server: LanSocket,
    device: DeviceLike,
    colors: List[Tuple[int, int, int]],
) -> None:
    """Send a multi-pack segment-control frame over LAN UDP (razer transport)."""
    frame = build_segment_frame_multi_pack(colors)
    send(
        server,
        device,
        {
            "msg": {
                "cmd": "razer",
                "data": {"pt": base64.b64encode(frame).decode("ascii")},
            }
        },
    )


def send_segment_frame_gradient(
    server: LanSocket,
    device: DeviceLike,
    colors: List[Tuple[int, int, int]],
) -> None:
    """Send a gradient segment-control frame over LAN UDP (razer transport)."""
    frame = build_segment_frame_gradient(colors)
    send(
        server,
        device,
        {
            "msg": {
                "cmd": "razer",
                "data": {"pt": base64.b64encode(frame).decode("ascii")},
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
            "brightness": None
            if data.get("brightness") is None
            else int(data["brightness"]),
            "color": parsed_color,
            "color_temp": data.get("colorTemInKelvin"),
            "raw": data,
        }
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def query_status(
    server: LanSocket,
    device: DeviceLike,
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
