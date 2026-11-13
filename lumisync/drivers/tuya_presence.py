"""LSC/Tuya LAN presence listener: who is actually online right now.

LSC Smart Connect devices broadcast their own keep-alive frames to the LAN on
UDP 6667 roughly every ten seconds; the vendor apps use these to show a device
as online with a 20-second grace window. The frames are AES-128-GCM encrypted
and decode without any account or device secret, so LumiSync mirrors the same
behaviour passively: bind to 6667, decode the ``gwId``, remember when each
device was last seen and which address it announced from. Routers hand these
strips new DHCP leases, so the beat's source address is the freshest record of
where a device lives.

Two frame layouts share the port. Protocol 3.5 devices (e.g. the Battletron
strip) send ``6699`` frames, reverse engineered from the official desktop
app's decoder:

    0..4    header  ``00006699``
    4..10   version/reserved + sequence
    10..14  frame type (``00000013`` = presence announcement)
    14..18  id/length (not authenticated)
    18..30  GCM nonce (12 bytes)
    30..-20 AES-128-GCM ciphertext: 4-byte return code, then JSON
    -20..-4 16 byte GCM auth tag
    -4..    tail  ``00009966``

Protocol 3.3 and 3.4 devices (most LSC plugs and bulbs) send ``55AA`` frames:

    0..4    header  ``000055aa``
    4..8    sequence
    8..12   command (``0x13`` from 3.3, ``0x23`` from 3.4 devices)
    12..16  length of everything after this field
    16..20  return code
    20..-8  AES-128-ECB ciphertext (PKCS#7 padded JSON)
    -8..-4  CRC32
    -4..    tail  ``0000aa55``

Both use the same AES key, a Tuya-embedded constant (byte-exact from the app
binary; it is md5 of a public string). The JSON carries ``gwId``.
"""

from __future__ import annotations

import json
import socket
import struct
import threading
import time
from contextlib import contextmanager
from typing import Callable, Dict, Iterator, List, Optional

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..utils.logging import get_logger

logger = get_logger("lumisync_devices")

PRESENCE_PORT = 6667
PRESENCE_FRESH_AFTER = 25.0  # seconds; mirrors the app's 20 s plus margin
_HEADER = bytes.fromhex("00006699")
_TAIL = bytes.fromhex("00009966")
_FRAME_TYPE = bytes.fromhex("00000013")
_HEADER_55AA = bytes.fromhex("000055aa")
_TAIL_55AA = bytes.fromhex("0000aa55")
# Announcement commands: 0x13 (UDP_NEW, 3.3 firmware), 0x23 (3.4 firmware).
_ANNOUNCE_COMMANDS = (0x13, 0x23)
_KEY = bytes(
    [108, 30, 200, 226, 187, 155, 181, 154, 181, 11, 13, 175, 100, 155, 65, 10]
)


class PresenceRegistry:
    """Thread-safe record of which Tuya devices broadcast most recently."""

    def __init__(self, fresh_after: float = PRESENCE_FRESH_AFTER):
        self._fresh_after = float(fresh_after)
        self._lock = threading.Lock()
        self._seen: Dict[str, float] = {}
        self._addresses: Dict[str, str] = {}

    def note_seen(self, device_id: str, at: Optional[float] = None,
                  address: Optional[str] = None) -> None:
        with self._lock:
            self._seen[str(device_id)] = float(
                time.time() if at is None else at
            )
            if address:
                self._addresses[str(device_id)] = str(address)

    def last_seen(self, device_id: str) -> Optional[float]:
        with self._lock:
            return self._seen.get(str(device_id))

    def age(self, device_id: str, now: Optional[float] = None) -> Optional[float]:
        stamp = self.last_seen(device_id)
        if stamp is None:
            return None
        return max(0.0, float(time.time() if now is None else now) - stamp)

    def is_online(self, device_id: str, now: Optional[float] = None) -> bool:
        age = self.age(device_id, now)
        return age is not None and age <= self._fresh_after

    def address(self, device_id: str, now: Optional[float] = None) -> Optional[str]:
        """Where the device announced itself from, while that beat is fresh."""
        with self._lock:
            address = self._addresses.get(str(device_id))
        return address if address and self.is_online(device_id, now) else None

    def forget_all(self) -> None:
        with self._lock:
            self._seen.clear()
            self._addresses.clear()


class TuyaPresenceListener:
    """Background socket that decodes 6667 broadcasts into ``registry``."""

    def __init__(self,
                 registry: PresenceRegistry,
                 on_seen: Optional[Callable[[str, str], None]] = None,
                 port: int = PRESENCE_PORT):
        self.registry = registry
        self._on_seen = on_seen
        self._port = int(port)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._socket = None
        self.started = threading.Event()

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except OSError:
            return False
        try:
            # Devices broadcast; several apps (LSC desktop, LumiSync) may bind
            # the same port concurrently with SO_REUSEADDR.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("0.0.0.0", self._port))
            listener.settimeout(0.5)
        except OSError as exc:
            logger.warning("Tuya presence listener unavailable: %s", exc)
            listener.close()
            return False
        self._socket = listener
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="lumisync-tuya-presence", daemon=True
        )
        self._thread.start()
        self.started.set()
        logger.info("Tuya presence listener bound to UDP %s", self._port)
        return True

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
            self._socket = None
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            listener = self._socket
            if listener is None:
                break
            try:
                data, address = listener.recvfrom(1500)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                continue
            try:
                self.handle_datagram(data, address)
            except Exception as exc:
                # Anyone on the LAN can send to 6667, and the frame key is a
                # public constant: one hostile or malformed beat must not end
                # the listener while ``started`` still says it runs.
                logger.debug("Ignored an unreadable presence datagram: %s", exc)

    def handle_datagram(self, data: bytes, address: tuple = ()) -> None:
        """Decode one presence frame; broadcast frames carry no device state."""
        if not isinstance(data, (bytes, bytearray)):
            return
        packet = bytes(data)
        if packet.startswith(_HEADER):
            plaintext = _open_gcm_frame(packet)
        elif packet.startswith(_HEADER_55AA):
            plaintext = _open_ecb_frame(packet)
        else:
            return
        if plaintext is None:
            return  # An unparseable frame is simply not a presence beat.
        device_id = _announced_device_id(plaintext)
        if device_id is None:
            return
        self.registry.note_seen(device_id, address=address[0] if address else None)
        callback = self._on_seen
        if callback is not None:
            try:
                callback(device_id, address[0] if address else "")
            except Exception:
                pass


def _open_gcm_frame(packet: bytes) -> Optional[bytes]:
    """Plaintext of a ``6699`` (protocol 3.5) announcement, or None."""
    if len(packet) < 50 or not packet.endswith(_TAIL) or packet[10:14] != _FRAME_TYPE:
        return None
    try:
        return AESGCM(_KEY).decrypt(packet[18:30], packet[30:-20] + packet[-20:-4], packet[4:18])
    except Exception:
        return None


def _open_ecb_frame(packet: bytes) -> Optional[bytes]:
    """Plaintext of a ``55AA`` (protocol 3.3/3.4) announcement, or None."""
    if len(packet) < 36 or not packet.endswith(_TAIL_55AA):
        return None
    _sequence, command, length = struct.unpack(">III", packet[4:16])
    if command not in _ANNOUNCE_COMMANDS or length != len(packet) - 16:
        return None
    body = packet[16:-8]  # drop the CRC32 and the tail
    # Device frames lead with a 4-byte return code; old firmware announces in
    # clear text instead of encrypting.
    for start in (0, 4):
        if body[start:start + 1] == b"{":
            return body[start:]
    if len(body) % 16 == 4:
        body = body[4:]
    if not body or len(body) % 16:
        return None
    decryptor = Cipher(algorithms.AES(_KEY), modes.ECB()).decryptor()
    padded = decryptor.update(body) + decryptor.finalize()
    pad = padded[-1]
    if not 1 <= pad <= 16 or padded[-pad:] != bytes([pad]) * pad:
        return None
    return padded[:-pad]


def _announced_device_id(plaintext: bytes) -> Optional[str]:
    """The ``gwId`` an announcement carries, or None for anything else."""
    text = plaintext
    if text[:1] != b"{" and text[4:5] == b"{":
        text = text[4:]  # 3.5 frames put a 4-byte return code before the JSON
    text = text.rstrip(b"\x00")
    try:
        payload = json.loads(text.decode("utf-8", "ignore").strip())
    except (ValueError, RecursionError):
        return None  # not JSON, or nested deep enough to exhaust the parser
    device_id = payload.get("gwId") if isinstance(payload, dict) else None
    if not device_id or not isinstance(device_id, str):
        return None
    return device_id


# Process-wide singletons: one registry, one listener per process.
registry: PresenceRegistry = PresenceRegistry()
_listener: Optional[TuyaPresenceListener] = None
_subscribers: List[Callable[[str, str], None]] = []
_subscribers_lock = threading.Lock()


def subscribe(callback: Callable[[str, str], None]) -> None:
    """Call ``callback(device_id, address)`` for every beat (listener thread)."""
    with _subscribers_lock:
        if callback not in _subscribers:
            _subscribers.append(callback)


def unsubscribe(callback: Callable[[str, str], None]) -> None:
    with _subscribers_lock:
        if callback in _subscribers:
            _subscribers.remove(callback)


def _notify(device_id: str, address: str) -> None:
    with _subscribers_lock:
        callbacks = list(_subscribers)
    for callback in callbacks:
        try:
            callback(device_id, address)
        except Exception:
            pass


def start_presence_listener() -> bool:
    """Start the system listener once; safe to call repeatedly."""
    global _listener
    if _listener is not None and _listener.started.is_set():
        return True
    _listener = TuyaPresenceListener(registry, on_seen=_notify)
    return _listener.start()


def stop_presence_listener() -> None:
    global _listener
    if _listener is not None:
        _listener.stop()
        _listener = None


@contextmanager
def released_port() -> Iterator[None]:
    """Free UDP 6667 for a tinytuya scan, then resume listening.

    tinytuya binds the port without SO_REUSEADDR, which Windows refuses while
    this listener holds it (WinError 10048): every Tuya LAN search failed.
    """
    running = _listener is not None and _listener.started.is_set()
    if running:
        stop_presence_listener()
    try:
        yield
    finally:
        if running:
            start_presence_listener()


def is_device_online(device_id: str) -> bool:
    """Whether a fresh presence beat for this device has arrived recently."""
    return registry.is_online(device_id)


def presence_age(device_id: str) -> Optional[float]:
    """Seconds since the last beat, or None when never seen."""
    return registry.age(device_id)


def presence_address(device_id: str) -> Optional[str]:
    """The address of a fresh beat, or None when unseen or stale."""
    return registry.address(device_id)
