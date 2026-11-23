"""Current track artwork on Linux, read through MPRIS on the D-Bus session bus.

Written from the MPRIS D-Bus Interface Specification
(https://specifications.freedesktop.org/mpris-spec/latest/): every player owns a
bus name starting ``org.mpris.MediaPlayer2.`` and exposes, at the object path
``/org/mpris/MediaPlayer2``, the ``org.mpris.MediaPlayer2.Player`` interface
whose ``PlaybackStatus`` property is ``Playing``, ``Paused`` or ``Stopped`` and
whose ``Metadata`` map may carry ``mpris:artUrl``, a URI to the cover image.

The bus is queried through the ``busctl`` (systemd) or ``gdbus`` (GLib) command
line tools with a short timeout. PySide6's QtDBus can make the calls, but its
Python bindings cannot read the ``a{sv}`` Metadata map back (``QDBusArgument``
has no working read API in PySide6), so it cannot return ``mpris:artUrl``.

Everything here is best effort: a missing bus, tool, player or image returns
``None`` -- the same as Windows with nothing playing -- and nothing raises.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Optional, Protocol, Sequence
from urllib.parse import unquote_to_bytes, urljoin, urlsplit
from urllib.request import url2pathname

from .artwork import MAX_ARTWORK_BYTES


MPRIS_PREFIX = "org.mpris.MediaPlayer2."
MPRIS_PATH = "/org/mpris/MediaPlayer2"
MPRIS_PLAYER = "org.mpris.MediaPlayer2.Player"

DBUS_CALL_TIMEOUT = 1.0
HTTP_TIMEOUT = 3.0
MAX_HTTP_REDIRECTS = 3
MAX_PLAYERS = 8
CACHE_SIZE = 8
FAILURE_RETRY_SECONDS = 30.0

Runner = Callable[[Sequence[str], float], Optional[str]]


def run_command(command: Sequence[str], timeout: float) -> Optional[str]:
    """Run a bus tool and return its stdout, or ``None`` on any failure."""
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


class BusClient(Protocol):
    def list_names(self) -> list[str]: ...

    def get_property(self, destination: str, name: str) -> Any: ...


class BusctlClient:
    """Session-bus calls through ``busctl --user --json=short``."""

    def __init__(self, runner: Runner = run_command, executable: str = "busctl") -> None:
        self._runner = runner
        self._executable = executable

    def _json(self, *arguments: str) -> Any:
        output = self._runner(
            [
                self._executable,
                "--user",
                "--json=short",
                f"--timeout={DBUS_CALL_TIMEOUT:g}",
                *arguments,
            ],
            DBUS_CALL_TIMEOUT + 0.5,
        )
        if not output:
            return None
        try:
            return json.loads(output)
        except ValueError:
            return None

    def list_names(self) -> list[str]:
        reply = self._json(
            "call",
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "ListNames",
        )
        data = reply.get("data") if isinstance(reply, dict) else None
        if isinstance(data, list) and len(data) == 1 and isinstance(data[0], list):
            data = data[0]
        if not isinstance(data, list):
            return []
        return [name for name in data if isinstance(name, str)]

    def get_property(self, destination: str, name: str) -> Any:
        reply = self._json(
            "get-property", destination, MPRIS_PATH, MPRIS_PLAYER, name
        )
        return _plain_busctl_value(reply)


def _plain_busctl_value(value: Any) -> Any:
    """Strip busctl's ``{"type": ..., "data": ...}`` wrappers recursively."""
    if isinstance(value, dict) and set(value) == {"type", "data"}:
        return _plain_busctl_value(value["data"])
    if isinstance(value, dict):
        return {key: _plain_busctl_value(item) for key, item in value.items()}
    return value


_GVARIANT_NAME = re.compile(r"'([^'\\]*)'")
_GVARIANT_STATUS = re.compile(r"^\(<'([A-Za-z]+)'>,\)\s*$")
_GVARIANT_ART_URL = re.compile(
    r"'mpris:artUrl': <(?P<quote>['\"])(?P<body>(?:\\.|(?!(?P=quote)).)*)(?P=quote)>",
    re.DOTALL,
)
_GVARIANT_ESCAPE = re.compile(r"\\(u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}|.)", re.DOTALL)
_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v"}


def _unescape_gvariant(body: str) -> str:
    def replace(match: re.Match[str]) -> str:
        escape = match.group(1)
        if escape[0] in "uU" and len(escape) > 1:
            return chr(int(escape[1:], 16))
        return _SIMPLE_ESCAPES.get(escape, escape)

    return _GVARIANT_ESCAPE.sub(replace, body)


class GdbusClient:
    """Session-bus calls through ``gdbus call``, parsing its GVariant text."""

    def __init__(self, runner: Runner = run_command, executable: str = "gdbus") -> None:
        self._runner = runner
        self._executable = executable

    def _call(self, destination: str, path: str, method: str, *arguments: str) -> Optional[str]:
        return self._runner(
            [
                self._executable,
                "call",
                "--session",
                "--timeout",
                str(max(1, int(round(DBUS_CALL_TIMEOUT)))),
                "--dest",
                destination,
                "--object-path",
                path,
                "--method",
                method,
                *arguments,
            ],
            DBUS_CALL_TIMEOUT + 0.5,
        )

    def list_names(self) -> list[str]:
        output = self._call(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus.ListNames",
        )
        return _GVARIANT_NAME.findall(output or "")

    def get_property(self, destination: str, name: str) -> Any:
        output = self._call(
            destination,
            MPRIS_PATH,
            "org.freedesktop.DBus.Properties.Get",
            MPRIS_PLAYER,
            name,
        )
        if not output:
            return None
        if name == "PlaybackStatus":
            match = _GVARIANT_STATUS.match(output.strip())
            return match.group(1) if match else None
        if name == "Metadata":
            match = _GVARIANT_ART_URL.search(output)
            if match is None:
                return {}
            return {"mpris:artUrl": _unescape_gvariant(match.group("body"))}
        return None


def default_bus_client() -> Optional[BusClient]:
    """Pick a bus tool that is installed, preferring busctl's JSON output."""
    busctl = shutil.which("busctl")
    if busctl:
        return BusctlClient(executable=busctl)
    gdbus = shutil.which("gdbus")
    if gdbus:
        return GdbusClient(executable=gdbus)
    return None


def current_art_url(client: Optional[BusClient]) -> Optional[str]:
    """Return ``mpris:artUrl`` of the player most likely being listened to.

    Players that are ``Playing`` win; only when none is playing are ``Paused``
    players considered. A stopped player has no current track to colour by.
    """
    if client is None:
        return None
    try:
        names = sorted(
            name for name in client.list_names() if name.startswith(MPRIS_PREFIX)
        )[:MAX_PLAYERS]
        by_status: dict[str, list[str]] = {"Playing": [], "Paused": []}
        for name in names:
            status = client.get_property(name, "PlaybackStatus")
            if isinstance(status, str) and status in by_status:
                by_status[status].append(name)
        candidates = by_status["Playing"] or by_status["Paused"]
        for name in candidates:
            metadata = client.get_property(name, "Metadata")
            if not isinstance(metadata, dict):
                continue
            url = metadata.get("mpris:artUrl")
            if isinstance(url, str) and url.strip():
                return url.strip()
    except Exception:
        return None
    return None


# ------------------------------------------------------------------ artUrl


def _read_file_url(url: str, limit: int) -> Optional[bytes]:
    parts = urlsplit(url)
    if parts.netloc not in ("", "localhost"):
        return None
    path = url2pathname(parts.path)
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_BINARY", 0)
    try:
        # O_NONBLOCK keeps a FIFO planted at the path from blocking the open;
        # fstat on the opened descriptor then rejects anything but a file.
        descriptor = os.open(path, flags)
    except (OSError, ValueError):
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
            return None
        if info.st_size > limit:
            return None
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            payload = handle.read(limit + 1)
    except OSError:
        return None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not payload or len(payload) > limit:
        return None
    return payload


def _decode_data_url(url: str, limit: int) -> Optional[bytes]:
    header, separator, data = url[len("data:"):].partition(",")
    if not separator:
        return None
    parameters = [part.strip().lower() for part in header.split(";")]
    media_type = parameters[0] if parameters else ""
    if not media_type.startswith("image/"):
        return None
    if "base64" in parameters[1:]:
        compact = "".join(unquote_to_bytes(data).decode("ascii", "ignore").split())
        if len(compact) * 3 // 4 > limit + 3:
            return None
        try:
            payload = base64.b64decode(compact, validate=True)
        except (binascii.Error, ValueError):
            return None
    else:
        if len(data) > limit * 3:
            return None
        payload = unquote_to_bytes(data)
    if not payload or len(payload) > limit:
        return None
    return payload


def _fetch_http_url(url: str, limit: int, timeout: float) -> Optional[bytes]:
    import requests

    deadline = time.monotonic() + timeout
    current = url
    for _redirect in range(MAX_HTTP_REDIRECTS + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            with requests.get(
                current,
                stream=True,
                timeout=(remaining, remaining),
                allow_redirects=False,
                headers={"Accept": "image/*"},
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("Location", "")
                    current = urljoin(current, location)
                    if urlsplit(current).scheme.lower() not in ("http", "https"):
                        return None
                    continue
                if response.status_code != 200:
                    return None
                content_type = (
                    response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                )
                if not content_type.startswith("image/"):
                    return None
                declared = response.headers.get("Content-Length", "")
                if declared.isdigit() and int(declared) > limit:
                    return None
                payload = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        payload.extend(chunk)
                    if len(payload) > limit or time.monotonic() > deadline:
                        return None
                return bytes(payload) or None
        except (requests.RequestException, OSError, ValueError):
            return None
    return None


def load_art_url(
    url: str,
    limit: int = MAX_ARTWORK_BYTES,
    http_timeout: float = HTTP_TIMEOUT,
) -> Optional[bytes]:
    """Return the image bytes an ``mpris:artUrl`` points at, or ``None``."""
    scheme = urlsplit(url).scheme.lower()
    try:
        if scheme == "file":
            return _read_file_url(url, limit)
        if scheme == "data":
            return _decode_data_url(url, limit)
        if scheme in ("http", "https"):
            return _fetch_http_url(url, limit, http_timeout)
    except Exception:
        return None
    return None


class ArtworkCache:
    """Remember the artwork of recent URLs so a track is loaded once.

    Failures are remembered too, but only for :data:`FAILURE_RETRY_SECONDS`, so
    a cover that is still being written or a brief network hiccup recovers.
    """

    def __init__(
        self,
        loader: Callable[[str], Optional[bytes]] = load_art_url,
        size: int = CACHE_SIZE,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._loader = loader
        self._size = max(1, int(size))
        self._clock = clock
        self._entries: OrderedDict[str, tuple[Optional[bytes], float]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, url: str) -> Optional[bytes]:
        with self._lock:
            entry = self._entries.get(url)
            if entry is not None:
                payload, stored_at = entry
                if payload is not None or self._clock() - stored_at < FAILURE_RETRY_SECONDS:
                    self._entries.move_to_end(url)
                    return payload
        payload = self._loader(url)
        with self._lock:
            self._entries[url] = (payload, self._clock())
            self._entries.move_to_end(url)
            while len(self._entries) > self._size:
                self._entries.popitem(last=False)
        return payload


_CACHE = ArtworkCache()


def read_artwork_bytes(
    client_factory: Callable[[], Optional[BusClient]] = default_bus_client,
    cache: Optional[ArtworkCache] = None,
) -> Optional[bytes]:
    """Return the current MPRIS player's cover image bytes, or ``None``."""
    try:
        url = current_art_url(client_factory())
        if url is None:
            return None
        return (cache or _CACHE).get(url)
    except Exception:
        return None
