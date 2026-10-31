"""LSC/Tuya LAN presence listener: who is actually online right now.

LSC Smart Connect devices broadcast their own keep-alive frames to the LAN on
UDP 6667 roughly every ten seconds; the vendor apps use these to show a device
as online with a 20-second grace window. The frames are AES-128-GCM encrypted
and decode without any account or device secret, so LumiSync mirrors the same
behaviour passively: bind to 6667, decode the ``gwId``, remember when each
device was last seen.

Frame layout (reverse engineered from the official desktop app's decoder):

    0..4    header  ``00006699``
    4..10   version/reserved + sequence
    10..14  frame type (``00000013`` = presence announcement)
    14..18  id/length (not authenticated)
    18..30  GCM nonce (12 bytes)
    30..-20 AES-128-GCM ciphertext (JSON ``{"gwId": "..."}``)
    -20..-4 16 byte GCM auth tag
    -4..    tail  ``00009966``

The AES key is a Tuya-embedded constant (byte-exact from the app binary).
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Callable, Dict, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..utils.logging import get_logger

logger = get_logger("lumisync_devices")

PRESENCE_PORT = 6667
PRESENCE_FRESH_AFTER = 25.0  # seconds; mirrors the app's 20 s plus margin
_HEADER = bytes.fromhex("00006699")
_TAIL = bytes.fromhex("00009966")
_FRAME_TYPE = bytes.fromhex("00000013")
_KEY = bytes(
    [108, 30, 200, 226, 187, 155, 181, 154, 181, 11, 13, 175, 100, 155, 65, 10]
)


class PresenceRegistry:
    """Thread-safe record of which Tuya devices broadcast most recently."""

    def __init__(self, fresh_after: float = PRESENCE_FRESH_AFTER):
        self._fresh_after = float(fresh_after)
        self._lock = threading.Lock()
        self._seen: Dict[str, float] = {}

    def note_seen(self, device_id: str, at: Optional[float] = None) -> None:
        with self._lock:
            self._seen[str(device_id)] = float(
                time.time() if at is None else at
            )

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

    def forget_all(self) -> None:
        with self._lock:
            self._seen.clear()


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
            try:
                data, address = self._socket.recvfrom(1500)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                continue
            self.handle_datagram(data, address)

    def handle_datagram(self, data: bytes, address: tuple = ()) -> None:
        """Decode one presence frame; broadcast frames carry no device state."""
        if not isinstance(data, (bytes, bytearray)):
            return
        packet = bytes(data)
        if len(packet) < 50 or not packet.startswith(_HEADER):
            return
        if not packet.endswith(_TAIL):
            return
        if packet[10:14] != _FRAME_TYPE:
            return
        nonce = packet[18:30]
        ciphertext = packet[30:-20]
        tag = packet[-20:-4]
        aad = packet[4:18]
        try:
            plaintext = AESGCM(_KEY).decrypt(nonce, ciphertext + tag, aad)
        except Exception:
            return  # An unparseable frame is simply not a presence beat.
        try:
            payload = json.loads(plaintext.decode("utf-8", "ignore").strip())
        except json.JSONDecodeError:
            return
        device_id = payload.get("gwId") if isinstance(payload, dict) else None
        if not device_id or not isinstance(device_id, str):
            return
        self.registry.note_seen(device_id)
        callback = self._on_seen
        if callback is not None:
            try:
                callback(device_id, address[0] if address else "")
            except Exception:
                pass


# Process-wide singletons: one registry, one listener per process.
registry: PresenceRegistry = PresenceRegistry()
_listener: Optional[TuyaPresenceListener] = None


def start_presence_listener() -> bool:
    """Start the system listener once; safe to call repeatedly."""
    global _listener
    if _listener is not None and _listener.started.is_set():
        return True
    _listener = TuyaPresenceListener(registry)
    return _listener.start()


def stop_presence_listener() -> None:
    global _listener
    if _listener is not None:
        _listener.stop()
        _listener = None


def is_device_online(device_id: str) -> bool:
    """Whether a fresh presence beat for this device has arrived recently."""
    return registry.is_online(device_id)


def presence_age(device_id: str) -> Optional[float]:
    """Seconds since the last beat, or None when never seen."""
    return registry.age(device_id)
