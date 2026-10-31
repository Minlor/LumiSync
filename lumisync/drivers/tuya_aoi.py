"""Tuya/LSC AoI "fast channel": UDP 40001 RGBIC streaming, from the vendor app.

The LSC Smart Connect desktop app drives its RGBIC strips over a broadcast
UDP protocol on port 40001 ("AoI") instead of per-write TCP data points,
using a negotiation ("consult") plus AES-128-GCM session keys. The whole
protocol was reconstructed from the app's own JavaScript (the app bundles
unminified SDK sources inside its source maps), so the frames below mirror
it byte for byte:

* frame envelope: ``00006699`` header, 22-byte AAD ("add"), 12-byte GCM
  nonce, one block per device (mate id 4 + length 4 + ciphertext + 16-byte
  tag), ``00009966`` tail.
* AAD = version/reserved(2) + sequence(4) + type(4) + main mate id(4)
  + total length(4) + device count(4), where the total length counts the
  42-byte fixed envelope plus every device block.
* device mate id = crc32(devId padded to 26 bytes + main mate id); the main
  mate id = crc32(this app's uuid padded to 26 bytes). Both identify this
  app instance to the strip.
* consult frame (type 0x05): ``{syncType 4B (0 = negotiate, 1 = cancel),
  token 16B random, randA 16B random}`` encrypted under the fixed consult
  key. The strip answers (type 0x06) with ``randB(16) + hmac(randA)(32) +
  hmac(sessionKey)(32)``; the negotiation key derives from the device's own
  local key and both HMAC echoes must match before the session is accepted.
* business frame (type 0x10): the command value encrypted under the device's
  session key. The strip renders it directly — there is no ACK.

The strip answers exactly one consult per session and then only accepts
business frames for it; replaying an old consult is deduplicated by the
device. LumiSync therefore runs the handshake, keeps the derived session key
in memory, and falls back to the proven TCP paint transport when the
negotiation does not complete.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_module
import os
import socket
import struct
import threading
import time
import uuid
import zlib
from typing import Callable, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..utils.logging import get_logger

logger = get_logger("lumisync_devices")

AOI_FAST_STREAM = False  # frames leave the host at wire speed, but the strip
# has not rendered any business payload yet;
# the vendor-proven TCP paint path stays the default until this is solved.

AOI_PORT = 40001
AOI_HEADER = bytes.fromhex("00006699")
AOI_TAIL = bytes.fromhex("00009966")
CONSULT_REQUEST_TYPE = bytes.fromhex("00000005")
CONSULT_RESPONSE_TYPE = bytes.fromhex("00000006")
BUSINESS_REQUEST_TYPE = bytes.fromhex("00000010")
CONSULT_KEY = bytes.fromhex("6f36045d84b042e01e29b7c819e37cf7")
NEGOTIATION_TIMEOUT = 2.0
NEGOTIATION_ATTEMPTS = 4
ENVELOPE_BYTES = 42  # header 4 + AAD 22 + nonce 12 (the app's "dataLenth")


# ------------------------------------------------------- identity + crypto

def _pad_end(data: bytes, length: int) -> bytes:
    return data[:length].ljust(length, b"\x00")


def _crc32_be(data: bytes) -> bytes:
    return struct.pack(">I", zlib.crc32(data) & 0xFFFFFFFF)


def _mac_hex() -> str:
    return f"{uuid.getnode():012x}"


def main_uuid_for_mac(mac_hex: Optional[str] = None) -> bytes:
    """8-byte identity: SHA256(mac + "mainUuid")[:8], as the app derives it."""
    seed = (str(mac_hex or _mac_hex()) + "mainUuid").encode("ascii")
    return hashlib.sha256(seed).digest()[:8]


def main_mate_id(main_uuid: bytes) -> bytes:
    """crc32(uuid padded to 26 bytes) = the app's "main device" mate id."""
    return _crc32_be(_pad_end(main_uuid, 25) + b"\x00")


def child_mate_id(device_id: str, main_mate: bytes) -> bytes:
    """crc32(devId padded to 26 bytes + main mate id) for one strip."""
    padded = _pad_end(device_id.encode("ascii"), 25) + b"\x00"
    return _crc32_be(padded + main_mate)


def device_local_key_bytes(local_key) -> bytes:
    """Exactly 16 key bytes; the app zero-pads or truncates to match."""
    if isinstance(local_key, (bytes, bytearray)):
        raw = bytes(local_key)
    else:
        raw = str(local_key or "").encode("ascii", "ignore")
    return raw[:16].ljust(16, b"\x00")


def negotiate_key_from(device_local_key: bytes, token: bytes) -> bytes:
    """Key derivation: AES-128-GCM-encrypt the token under the local key.

    The app keeps only the ciphertext (16 bytes) — not the ciphertext+tag —
    as the derived key, matching its ``encodeGCM`` [data, tag] pair.
    """
    return AESGCM(device_local_key).encrypt(token[:12], token, None)[:-16]


def session_key_from(negotiate_key: bytes, rand_a: bytes, rand_b: bytes) -> bytes:
    mixed = bytes(a ^ b for a, b in zip(rand_a, rand_b))
    return AESGCM(negotiate_key).encrypt(rand_a[:12], mixed, None)[:-16]


def hmac_sha256(message: bytes, key: bytes) -> bytes:
    return hmac_module.new(key, message, hashlib.sha256).digest()


def aoi_add(sequence: int, frame_type: bytes, main_mate: bytes,
            total_length: int, device_count: int) -> bytes:
    """The 22-byte authenticated additional data shared by every AoI frame."""
    return (
        bytes.fromhex("0000")
        + struct.pack(">I", sequence & 0xFFFFFFFF)
        + frame_type
        + main_mate
        + struct.pack(">I", total_length & 0xFFFFFFFF)
        + struct.pack(">I", device_count & 0xFFFFFFFF)
    )


def _block(device_id: str, main_mate: bytes, nonce: bytes, key: bytes,
           payload: bytes, add: bytes) -> bytes:
    encrypted = AESGCM(key).encrypt(nonce, payload, add)  # ciphertext || tag
    mate = child_mate_id(device_id, main_mate)
    # The length field counts the plaintext (the app does the same), with the
    # auth tag riding on the wire after the ciphertext.
    return mate + struct.pack(">I", len(payload)) + encrypted


# ----------------------------------------------------------------- framing

def consult_payload(token: bytes, rand_a: bytes, sync_type: int = 0) -> bytes:
    """36-byte consult body: syncType(4) + token(16) + randA(16)."""
    return struct.pack(">I", sync_type) + bytes(token) + bytes(rand_a)


def consult_frame(devices: List[str], main_mate: bytes, sequence: int,
                  nonce: Optional[bytes] = None,
                  random_bytes: Optional[Callable[[int], bytes]] = None
                  ) -> Tuple[bytes, bytes, bytes, Dict[str, Tuple[bytes, bytes]]]:
    """Build one consult broadcast; return (frame, nonce, add, consult map).

    The caller keeps ``consult map`` so the eventual type-0x06 reply matches
    its token/randA (the strip only honours one consult per these values).
    """
    random = random_bytes or os.urandom
    nonce = bytes(nonce) if nonce is not None else random(12)
    consults: Dict[str, Tuple[bytes, bytes]] = {}
    payload_by_device: Dict[str, bytes] = {}
    for device_id in devices:
        token = random(16)
        rand_a = random(16)
        consults[device_id] = (token, rand_a)
        payload_by_device[device_id] = consult_payload(token, rand_a)
    blocks_total = len(devices) * (8 + 36 + 16)  # mate + length + tag + payload
    add = aoi_add(sequence, CONSULT_REQUEST_TYPE, main_mate,
                  ENVELOPE_BYTES + blocks_total, len(devices))
    blocks = [
        _block(device_id, main_mate, nonce, CONSULT_KEY,
               payload_by_device[device_id], add)
        for device_id in devices
    ]
    frame = bytes(AOI_HEADER + add + nonce + b"".join(blocks) + AOI_TAIL)
    return frame, nonce, add, consults


def parse_response_frame(frame: bytes
                         ) -> Optional[Tuple[bytes, bytes, bytes, bytes, bytes]]:
    """Split an AoI reply into (mate, nonce, ciphertext, tag, aad)."""
    if not isinstance(frame, (bytes, bytearray)) or len(frame) < ENVELOPE_BYTES + 8:
        return None
    packet = bytes(frame)
    if not packet.startswith(AOI_HEADER) or not packet.endswith(AOI_TAIL):
        return None
    add = packet[4:26]
    nonce = packet[26:38]
    mate = packet[38:42]
    if len(packet) < 50:
        return None
    length = struct.unpack(">I", packet[42:46])[0]
    if length <= 0 or len(packet) < 46 + length + 16:
        return None
    ciphertext = packet[46:46 + length]
    tag = packet[46 + length:46 + length + 16]
    return mate, nonce, ciphertext, tag, add


def business_frame(device_id: str, main_mate: bytes, sequence: int,
                   session_key: bytes, value: bytes,
                   nonce: Optional[bytes] = None) -> bytes:
    """One-device business broadcast carrying a DP-style value."""
    value = bytes(value)
    add = aoi_add(sequence, BUSINESS_REQUEST_TYPE, main_mate,
                  ENVELOPE_BYTES + 8 + len(value) + 16, 1)
    nonce = os.urandom(12) if nonce is None else bytes(nonce)
    block = _block(device_id, main_mate, nonce, session_key, value, add)
    return bytes(AOI_HEADER + add + nonce + block + AOI_TAIL)


def respond_consult(local_key, token: bytes, rand_a: bytes,
                    nonce: bytes, add: bytes, main_mate: bytes,
                    sequence: int, device_id: str,
                    random_bytes: Optional[Callable[[int], bytes]] = None) -> bytes:
    """Device-side reply (only used by tests to simulate a strip)."""
    random = random_bytes or os.urandom
    raw = bytes(local_key) if isinstance(local_key, (bytes, bytearray)) \
        else str(local_key).encode("ascii", "ignore")
    negotiate = negotiate_key_from(raw[:16].ljust(16, b"\x00"), token)
    rand_b = bytes(random(16))
    session = session_key_from(negotiate, rand_a, rand_b)
    plaintext = rand_b + hmac_sha256(rand_a, negotiate) + hmac_sha256(session, negotiate)
    encrypted = AESGCM(negotiate).encrypt(nonce, plaintext, add)
    mate = child_mate_id(device_id, main_mate)
    # Same length semantics as the app: the payload length, then ciphertext+tag.
    block = mate + struct.pack(">I", len(plaintext)) + encrypted
    return bytes(AOI_HEADER + add + nonce + block + AOI_TAIL)


# ------------------------------------------------------------------ client

class TuyaAoiClient:
    """Keeps UDP 40001 open, runs the consult handshake and streams frames."""

    def __init__(self, main_uuid: Optional[bytes] = None,
                 port: int = AOI_PORT,
                 random_bytes: Optional[Callable[[int], bytes]] = None,
                 now: Optional[Callable[[], float]] = None):
        self.main_uuid = bytes(main_uuid) if main_uuid is not None else main_uuid_for_mac()
        self.main_mate = main_mate_id(self.main_uuid)
        self._port = int(port)
        self._random = random_bytes or os.urandom
        self._now = now or time.monotonic
        self._sequence = 0
        self._lock = threading.RLock()
        self._sessions: Dict[str, Tuple[bytes, float]] = {}
        self._keys: Dict[str, bytes] = {}
        self._ips: Dict[str, str] = {}
        self._pending: Dict[str, Tuple[bytes, bytes, threading.Event]] = {}
        self._socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._targets: List[Tuple[str, int]] = []
        self.started = threading.Event()

    # ---- transport ------------------------------------------------------

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return True
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except OSError:
            return False
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            listener.bind(("0.0.0.0", self._port))
            listener.settimeout(0.2)
        except OSError as exc:
            logger.warning("AoI client unavailable: %s", exc)
            listener.close()
            return False
        self._socket = listener
        self._targets = [("255.255.255.255", self._port)]
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._pump, name="lumisync-tuya-aoi", daemon=True
        )
        self._thread.start()
        self.started.set()
        logger.info("AoI client bound to UDP %s", self._port)
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
        self.started.clear()

    def _pump(self) -> None:
        while not self._stop.is_set():
            socket_ = self._socket
            if socket_ is None:
                break
            try:
                data, _address = socket_.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    break
                continue
            self.handle_datagram(data)

    def learn_local_key(self, device_id: str, local_key: str, ip: Optional[str] = None) -> None:
        """Record the local key (and optionally IP) needed for this strip."""
        with self._lock:
            self._keys[str(device_id)] = device_local_key_bytes(local_key)
            if ip:
                self._ips[str(device_id)] = str(ip)

    def forget(self, device_id: str) -> None:
        with self._lock:
            self._sessions.pop(str(device_id), None)
            self._pending.pop(str(device_id), None)

    def has_session(self, device_id: str) -> bool:
        with self._lock:
            return str(device_id) in self._sessions

    def session_key(self, device_id: str) -> Optional[bytes]:
        with self._lock:
            session = self._sessions.get(str(device_id))
        return session[0] if session else None

    def session_age(self, device_id: str) -> Optional[float]:
        with self._lock:
            session = self._sessions.get(str(device_id))
        if session is None:
            return None
        return max(0.0, self._now() - session[1])

    def next_sequence(self) -> int:
        with self._lock:
            self._sequence = (self._sequence % 0xFFFFFFFF) + 1
            return self._sequence

    def handle_datagram(self, data: bytes) -> None:
        """Accept only consult replies (type 0x06).

        Our own broadcasts can echo back on the sending socket (and other
        apps on the LAN negotiate too), so anything that is not a reply is
        ignored before it can clobber a pending negotiation.
        """
        if not isinstance(data, (bytes, bytearray)) or len(data) < 50:
            return
        if bytes(data[10:14]) != CONSULT_RESPONSE_TYPE:
            return
        parsed = parse_response_frame(data)
        if parsed is None:
            return
        _mate, nonce, ciphertext, tag, add = parsed
        # The reply block carries the strip's own mate id; the strip echoes
        # the *requested device's* mate id in the reply AAD, which is what
        # matches a pending consult.
        aad_mate = bytes(add[10:14])
        with self._lock:
            pending = [
                (identity, state)
                for identity, state in self._pending.items()
                if child_mate_id(identity, self.main_mate) == aad_mate
            ]
        for identity, (token, rand_a, done) in pending:
            try:
                with self._lock:
                    local_key = self._keys.get(identity)
                if local_key is None:
                    done.set()
                    return
                negotiate = negotiate_key_from(local_key, token)
                plaintext = AESGCM(negotiate).decrypt(nonce, ciphertext + tag, add)
                if len(plaintext) < 80:
                    done.set()
                    return
                rand_b = plaintext[:16]
                if hmac_sha256(rand_a, negotiate) != plaintext[16:48]:
                    done.set()
                    return
                session = session_key_from(negotiate, rand_a, rand_b)
                if hmac_sha256(session, negotiate) != plaintext[48:80]:
                    done.set()
                    return
                with self._lock:
                    self._sessions[identity] = (session, self._now())
                    self._pending.pop(identity, None)
                logger.info("AoI session established with %s", identity)
            except Exception as exc:
                logger.info("AoI negotiation failed for %s: %s", identity, exc)
            done.set()

    def ensure_session(self, device_id: str) -> bool:
        """Negotiate (up to ``NEGOTIATION_ATTEMPTS`` tries) and stay bounded."""
        device_id = str(device_id)
        if self.has_session(device_id):
            return True
        with self._lock:
            local_key = self._keys.get(device_id)
        if local_key is None:
            return False
        for _attempt in range(NEGOTIATION_ATTEMPTS):
            if self.has_session(device_id):
                return True
            frame, _nonce, _add, consults = consult_frame(
                [device_id], self.main_mate, self.next_sequence(),
                random_bytes=self._random,
            )
            token, rand_a = consults[device_id]
            done = threading.Event()
            with self._lock:
                self._pending[device_id] = (token, rand_a, done)
            for target in self._targets_for(device_id):
                self.broadcast(frame, target)
            # Wait out the grace window and keep polling: only a real
            # type-0x06 reply (typed filter above) can finish this attempt.
            deadline = self._now() + NEGOTIATION_TIMEOUT
            while self._now() < deadline:
                if self.has_session(device_id):
                    return True
                done.wait(0.25)
            with self._lock:
                self._pending.pop(device_id, None)
        return self.has_session(device_id)

    def broadcast(self, frame: bytes, target: Optional[Tuple[str, int]] = None) -> None:
        """Send one frame: to a specific endpoint, or everywhere."""
        if target is not None:
            socket_ = self._socket
            if socket_ is None:
                return
            try:
                socket_.sendto(frame, target)
            except OSError as exc:
                logger.debug("AoI send failed via %s: %s", target, exc)
            return
        for target in list(self._targets):
            socket_ = self._socket
            if socket_ is None:
                return
            try:
                socket_.sendto(frame, target)
            except OSError as exc:
                logger.debug("AoI send failed via %s: %s", target, exc)

    def _targets_for(self, device_id: str) -> List[Tuple[str, int]]:
        """The strip's own address first, then the LAN broadcast."""
        ip = self._ips.get(device_id)
        targets = []
        if ip:
            targets.append((ip, self._port))
        targets.append(("255.255.255.255", self._port))
        return targets

    def send_business(self, device_id: str, value: bytes,
                      session_key: Optional[bytes] = None) -> bool:
        key = session_key if session_key is not None else self.session_key(device_id)
        if key is None:
            return False
        frame = business_frame(device_id, self.main_mate, self.next_sequence(), key, value)
        for target in self._targets_for(device_id):
            self.broadcast(frame, target)
        return True


# ------------------------------------------------- RGBIC business payload

def rgb_to_hsv_tuya(color) -> Tuple[int, int, int]:
    """Tuya HSV units from the app's `rgbToHsv`: H 0-360, S/V 0-1000."""
    import colorsys as _c

    r, g, b = (max(0, min(255, int(channel))) / 255 for channel in color)
    h, s, v = _c.rgb_to_hsv(r, g, b)
    return round(h * 360), round(s * 1000), round(v * 1000)


def rgbic_business_value(bands: List[Tuple[int, int, int]],
                         zone_bands: List[int],
                         zone_count: int,
                         angle: int = 0) -> bytes:
    """Vendor AoI payload for one RGBIC frame.

    Layout from the app: ``0004`` + up to four band colours as ``HHHHSSSSVVVV``
    (Tuya units) + the zone count as four hex digits + one band byte per zone.
    The app reverses the byte order when the layout angle is ±180°, which only
    matters for the vendor's drag-and-drop editor, so LumiSync keeps the zone
    order from the sync frame.
    """
    bands = list(bands)[:4]
    part = "".join(
        "%04x%04x%04x" % rgb_to_hsv_tuya(band) for band in bands
    )
    indexes = [max(1, min(4, int(band))) for band in zone_bands]
    if abs(int(angle) % 360) == 180:
        indexes.reverse()
    zone_hex = "".join(f"{index:02x}" for index in indexes)
    header = "0004"
    count = f"{max(1, int(zone_count)):04x}"
    return bytes.fromhex(header + part + count + zone_hex)


# -------------------------------------------------------------- integration

aoi_client: Optional[TuyaAoiClient] = None


def aoi() -> TuyaAoiClient:
    """Process-wide AoI client (one UDP port per process)."""
    global aoi_client
    if aoi_client is None:
        aoi_client = TuyaAoiClient()
    return aoi_client


def send_business_value(device_id: str, local_key: str, value: bytes) -> bool:
    """Convenience: ensure a session then push one business frame."""
    client = aoi()
    client.learn_local_key(device_id, local_key)
    if not client.ensure_session(device_id):
        return False
    return client.send_business(device_id, value)
