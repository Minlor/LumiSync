"""Experimental Wayland screen capture for Monitor sync.

Wayland has no screen grab an app may call. The route that works on GNOME,
KDE and wlroots desktops alike is the xdg-desktop-portal ScreenCast API: the
desktop asks the user which screen to share and hands back a PipeWire stream.

* The portal handshake (CreateSession, SelectSources, Start,
  OpenPipeWireRemote) runs over D-Bus with ``jeepney`` on a background thread.
  PySide6's QtDBus cannot send the ``u`` (uint32) option values the portal
  requires, and Qt Multimedia's own capture copies every full-size frame and
  never asks the portal to remember the choice.
* GStreamer's ``pipewiresrc`` reads the stream, drops frames above the sync
  rate, scales what is left to a small RGB image and keeps only the newest
  one. It runs in-process through PyGObject when that is importable, or as a
  ``gst-launch-1.0`` child process otherwise (the AppImage cannot import the
  system's PyGObject).

The portal session is opened once per app run and kept while LumiSync runs,
so the share dialog appears at most once; the ``restore_token`` the portal
returns is stored with the app's settings so later runs skip the dialog when
the desktop supports it. The sync loop only ever reads the newest frame: it
never waits for D-Bus, the dialog or GStreamer.
"""

from __future__ import annotations

import atexit
import importlib
import importlib.util
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Protocol, Tuple

import numpy as np

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST_INTERFACE = "org.freedesktop.portal.ScreenCast"
REQUEST_INTERFACE = "org.freedesktop.portal.Request"
SESSION_INTERFACE = "org.freedesktop.portal.Session"

SOURCE_MONITOR = 1
CURSOR_HIDDEN = 1
PERSIST_UNTIL_REVOKED = 2
RESPONSE_SUCCESS = 0
RESPONSE_CANCELLED = 1

# Frames handed to the sync loop are about this wide; zone averaging needs no
# more, and the width keeps every RGB row 4-byte aligned (no stride padding).
TARGET_WIDTH = 320
RESTORE_TOKEN_KEY = "capture/wayland_restore_token"

EXPERIMENTAL_NOTE = (
    "Wayland screen capture is experimental. Your desktop asks once which "
    "screen to share."
)
DENIED_MESSAGE = (
    "Screen sharing was not allowed, so Monitor sync stopped. Start Monitor "
    "sync again and choose a screen in your desktop's sharing dialog."
)
STOPPED_MESSAGE = (
    "Screen sharing ended, so Monitor sync stopped. Start Monitor sync again "
    "to share the screen."
)
MISSING_DBUS_MESSAGE = (
    "Monitor sync on Wayland could not load its D-Bus module (jeepney). "
    "Reinstall LumiSync, or log in to an X11 session for screen sync."
)
MISSING_PORTAL_MESSAGE = (
    "Monitor sync on Wayland needs your desktop's screen-sharing service: "
    "xdg-desktop-portal with the GNOME, KDE or wlr backend, and PipeWire. "
    "Install them, or log in to an X11 session for screen sync."
)
MISSING_GSTREAMER_MESSAGE = (
    "Monitor sync on Wayland reads the shared screen with GStreamer's PipeWire "
    "plugin. Install it (Debian/Ubuntu: gstreamer1.0-pipewire and "
    "gstreamer1.0-tools; Fedora: pipewire-gstreamer; Arch: gst-plugin-pipewire), "
    "or log in to an X11 session for screen sync."
)
WAITING_NOTE = "Choose the screen to share in your desktop's sharing dialog."


class WaylandCaptureUnavailable(RuntimeError):
    """Something Wayland capture needs is not installed."""


class ScreenSharingDenied(RuntimeError):
    """The user refused the share dialog, or the desktop ended the share."""


class PortalError(RuntimeError):
    """The portal answered a request with an error."""


# --------------------------------------------------------------------- portal


@dataclass(frozen=True)
class PortalStream:
    node_id: int
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class PortalGrant:
    """A started ScreenCast session: what to read and how to restore it."""

    stream: PortalStream
    restore_token: Optional[str]


class Portal(Protocol):
    """The part of the ScreenCast portal capture needs (faked in tests)."""

    def start(self, restore_token: Optional[str], cancelled: Callable[[], bool]) -> PortalGrant: ...

    def open_remote(self) -> int: ...

    def close(self) -> None: ...


def _variant(value: Any) -> Any:
    """Unwrap a jeepney ``(signature, value)`` variant."""
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str):
        return value[1]
    return value


def parse_streams(raw: Any) -> List[PortalStream]:
    """Turn the portal's ``a(ua{sv})`` streams into :class:`PortalStream`."""
    streams: List[PortalStream] = []
    for entry in _variant(raw) or []:
        try:
            node_id, props = entry
            width, height = _variant((props or {}).get("size", ("(ii)", (0, 0))))
            streams.append(PortalStream(int(node_id), int(width), int(height)))
        except (TypeError, ValueError):
            continue
    return streams


class JeepneyPortal:
    """The ScreenCast portal over a private D-Bus connection.

    The portal ties the session to this connection, so it stays open until
    :meth:`close`. Calls are serialized; none runs on the sync loop's thread.
    """

    def __init__(self, connection_factory: Optional[Callable[[], Any]] = None) -> None:
        self._jeepney = importlib.import_module("jeepney")
        self._blocking = importlib.import_module("jeepney.io.blocking")
        self._factory = connection_factory or (
            lambda: self._blocking.open_dbus_connection(bus="SESSION", enable_fds=True)
        )
        self._lock = threading.RLock()
        self._conn: Any = None
        self._session: Optional[str] = None
        self._portal = self._jeepney.DBusAddress(
            PORTAL_PATH, bus_name=PORTAL_BUS, interface=SCREENCAST_INTERFACE
        )

    # -- helpers

    def _connection(self) -> Any:
        if self._conn is None:
            self._conn = self._factory()
        return self._conn

    def _property(self, name: str) -> Any:
        conn = self._connection()
        message = self._jeepney.Properties(self._portal).get(name)
        reply = conn.send_and_get_reply(message, timeout=5)
        return _variant(reply.body[0])

    def _request(
        self,
        method: str,
        signature: str,
        args: Tuple[Any, ...],
        options: Dict[str, Tuple[str, Any]],
        cancelled: Callable[[], bool],
    ) -> Dict[str, Any]:
        """Call a portal method that answers through a Request ``Response``."""
        conn = self._connection()
        token = "lumisync_" + secrets.token_hex(6)
        sender = str(conn.unique_name).lstrip(":").replace(".", "_")
        request_path = f"{PORTAL_PATH}/request/{sender}/{token}"
        rule = self._jeepney.MatchRule(
            type="signal", interface=REQUEST_INTERFACE, member="Response", path=request_path
        )
        bus = self._blocking.Proxy(self._jeepney.message_bus, conn, timeout=5)
        bus.AddMatch(rule)
        try:
            with conn.filter(rule, bufsize=4) as queue:
                options = dict(options, handle_token=("s", token))
                message = self._jeepney.new_method_call(
                    self._portal, method, signature, args + (options,)
                )
                reply = conn.send_and_get_reply(message, timeout=10)
                if reply.header.message_type == self._jeepney.MessageType.error:
                    raise PortalError(f"{method}: {reply.body[0] if reply.body else 'error'}")
                while True:
                    if cancelled():
                        raise ScreenSharingDenied(DENIED_MESSAGE)
                    try:
                        signal = conn.recv_until_filtered(queue, timeout=0.5)
                    except TimeoutError:
                        continue
                    code, results = signal.body
                    if int(code) == RESPONSE_CANCELLED:
                        raise ScreenSharingDenied(DENIED_MESSAGE)
                    if int(code) != RESPONSE_SUCCESS:
                        raise PortalError(f"{method} was ended by the desktop (code {code}).")
                    return {key: _variant(value) for key, value in dict(results).items()}
        finally:
            try:
                bus.RemoveMatch(rule)
            except Exception:
                pass

    # -- Portal

    def start(self, restore_token: Optional[str], cancelled: Callable[[], bool]) -> PortalGrant:
        with self._lock:
            self.close_session()
            version = int(self._property("version") or 1)
            source_types = int(self._property("AvailableSourceTypes") or 0)
            if source_types and not source_types & SOURCE_MONITOR:
                raise PortalError("The desktop cannot share whole screens.")
            created = self._request(
                "CreateSession", "a{sv}", (),
                {"session_handle_token": ("s", "lumisync_" + secrets.token_hex(6))},
                cancelled,
            )
            self._session = str(created["session_handle"])
            options: Dict[str, Tuple[str, Any]] = {
                "types": ("u", SOURCE_MONITOR),
                "multiple": ("b", False),
            }
            if version >= 2:
                cursor_modes = int(self._property("AvailableCursorModes") or 0)
                if cursor_modes & CURSOR_HIDDEN:
                    options["cursor_mode"] = ("u", CURSOR_HIDDEN)
            if version >= 4:
                options["persist_mode"] = ("u", PERSIST_UNTIL_REVOKED)
                if restore_token:
                    options["restore_token"] = ("s", restore_token)
            self._request("SelectSources", "oa{sv}", (self._session,), options, cancelled)
            started = self._request("Start", "osa{sv}", (self._session, ""), {}, cancelled)
            streams = parse_streams(started.get("streams"))
            if not streams:
                raise PortalError("The desktop shared no screen.")
            token = started.get("restore_token")
            return PortalGrant(streams[0], str(token) if token else None)

    def open_remote(self) -> int:
        with self._lock:
            if self._session is None:
                raise PortalError("No screen-sharing session is open.")
            message = self._jeepney.new_method_call(
                self._portal, "OpenPipeWireRemote", "oa{sv}", (self._session, {})
            )
            reply = self._connection().send_and_get_reply(message, timeout=10)
            if reply.header.message_type == self._jeepney.MessageType.error:
                raise PortalError(f"OpenPipeWireRemote: {reply.body[0] if reply.body else 'error'}")
            fd: Any = reply.body[0]
            to_raw: Any = getattr(fd, "to_raw_fd", None)
            return int(to_raw() if to_raw is not None else fd)

    def close_session(self) -> None:
        with self._lock:
            session, self._session = self._session, None
            if session is None or self._conn is None:
                return
            try:
                address = self._jeepney.DBusAddress(
                    session, bus_name=PORTAL_BUS, interface=SESSION_INTERFACE
                )
                self._conn.send(self._jeepney.new_method_call(address, "Close"))
            except Exception:
                pass

    def close(self) -> None:
        with self._lock:
            self.close_session()
            conn, self._conn = self._conn, None
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def portal_available(connection_factory: Optional[Callable[[], Any]] = None) -> bool:
    """True when the session bus offers ScreenCast with whole-screen sources."""
    portal: Optional[JeepneyPortal] = None
    try:
        portal = JeepneyPortal(connection_factory)
        types = int(portal._property("AvailableSourceTypes") or 0)
        return bool(types & SOURCE_MONITOR)
    except Exception:
        return False
    finally:
        if portal is not None:
            portal.close()


# --------------------------------------------------------------- frame readers


class FrameReader(Protocol):
    """Reads the PipeWire stream; the newest small RGB frame wins."""

    def start(self) -> None: ...

    def latest(self) -> Optional[np.ndarray]: ...

    def error(self) -> Optional[str]: ...

    def stop(self) -> None: ...


def frame_size(stream: PortalStream, width: int = TARGET_WIDTH) -> Tuple[int, int]:
    """Output size for a stream: ``width`` wide (a multiple of 4), same aspect."""
    width = max(4, width - width % 4)
    if stream.width > 0 and stream.height > 0:
        height = int(round(width * stream.height / stream.width))
    else:
        height = int(round(width * 9 / 16))
    return width, max(2, height + height % 2)


def pipeline_description(fd: int, stream: PortalStream, fps: int, sink: str) -> str:
    """The GStreamer pipeline: drop to the sync rate, then scale and convert.

    ``videorate`` drops surplus frames before any pixel is touched, and
    scaling happens before the colour conversion so only the small image is
    converted.
    """
    width, height = frame_size(stream)
    fps = max(1, min(144, int(fps)))
    return (
        # pipewiresrc timestamps its buffers itself.
        f"pipewiresrc fd={int(fd)} path={int(stream.node_id)} "
        f"! videorate drop-only=true max-rate={fps} "
        "! videoscale ! videoconvert "
        f"! video/x-raw,format=RGB,width={width},height={height},pixel-aspect-ratio=1/1 "
        f"! {sink}"
    )


class LatestFrame:
    """A one-slot mailbox: writers replace, the reader takes (newest wins)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None

    def put(self, frame: np.ndarray) -> None:
        with self._lock:
            self._frame = frame

    def take(self) -> Optional[np.ndarray]:
        with self._lock:
            frame, self._frame = self._frame, None
        return frame


def _child_environment() -> Dict[str, str]:
    """Environment for host tools: without the frozen bundle's libraries.

    The AppImage puts its own lib directories on ``LD_LIBRARY_PATH``; a host
    ``gst-launch-1.0`` must load the host's GLib and GStreamer, not those.
    """
    env = dict(os.environ)
    if not getattr(sys, "frozen", False):
        return env
    original = env.pop("LD_LIBRARY_PATH_ORIG", None)
    bundle = os.path.dirname(os.path.abspath(sys.executable))
    roots = {bundle, os.path.abspath(getattr(sys, "_MEIPASS", bundle))}
    appdir = os.environ.get("APPDIR")
    if appdir:
        roots.add(os.path.abspath(appdir))
    kept = [
        entry for entry in (original or env.get("LD_LIBRARY_PATH", "")).split(os.pathsep)
        if entry and not any(os.path.abspath(entry).startswith(root) for root in roots)
    ]
    if kept:
        env["LD_LIBRARY_PATH"] = os.pathsep.join(kept)
    else:
        env.pop("LD_LIBRARY_PATH", None)
    for name in ("GST_PLUGIN_PATH", "GST_PLUGIN_SYSTEM_PATH", "GST_PLUGIN_SCANNER", "GI_TYPELIB_PATH"):
        env.pop(name, None)
    return env


class GstLaunchReader:
    """``gst-launch-1.0`` child writing raw frames to a pipe."""

    def __init__(self, fd: int, stream: PortalStream, fps: int, executable: str = "gst-launch-1.0") -> None:
        self._fd = fd
        self._stream = stream
        self._fps = fps
        self._executable = executable
        self._size = frame_size(stream)
        self._slot = LatestFrame()
        self._process: Optional[subprocess.Popen] = None
        self._errors: Deque[str] = deque(maxlen=8)
        self._ended = threading.Event()
        self._stopping = False

    def start(self) -> None:
        description = pipeline_description(self._fd, self._stream, self._fps, "fdsink fd=1 sync=false")
        try:
            self._process = subprocess.Popen(
                [self._executable, "-q", *description.split()],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=(self._fd,),
                env=_child_environment(),
            )
        finally:
            # The child holds its own copy of the PipeWire remote.
            try:
                os.close(self._fd)
            except OSError:
                pass
        threading.Thread(target=self._read_frames, name="wayland-capture-frames", daemon=True).start()
        threading.Thread(target=self._read_errors, name="wayland-capture-log", daemon=True).start()

    def _read_frames(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        width, height = self._size
        size = width * height * 3
        stdout: Any = process.stdout  # a BufferedReader (bufsize=-1)
        try:
            while True:
                buffer = bytearray(size)
                view = memoryview(buffer)
                filled = 0
                while filled < size:
                    count = stdout.readinto(view[filled:])
                    if not count:
                        return
                    filled += count
                self._slot.put(np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 3))
        except (OSError, ValueError):
            return
        finally:
            self._ended.set()

    def _read_errors(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            text = line.decode("utf-8", "replace").strip()
            if text:
                self._errors.append(text)

    def latest(self) -> Optional[np.ndarray]:
        return self._slot.take()

    def error(self) -> Optional[str]:
        if self._stopping or not self._ended.is_set():
            return None
        return self._errors[-1] if self._errors else "the screen stream ended"

    def stop(self) -> None:
        self._stopping = True
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for pipe in (process.stdout, process.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except Exception:
                pass


class GstAppSinkReader:
    """In-process GStreamer through PyGObject; ``appsink`` holds one frame."""

    def __init__(self, fd: int, stream: PortalStream, fps: int) -> None:
        gi = importlib.import_module("gi")
        gi.require_version("Gst", "1.0")
        self._gst = importlib.import_module("gi.repository.Gst")
        self._gst.init(None)
        self._fd = fd
        self._description = pipeline_description(
            fd, stream, fps, "appsink name=sink max-buffers=1 drop=true sync=false"
        )
        self._pipeline: Any = None
        self._sink: Any = None
        self._error: Optional[str] = None

    def start(self) -> None:
        self._pipeline = self._gst.parse_launch(self._description)
        self._sink = self._pipeline.get_by_name("sink")
        self._pipeline.set_state(self._gst.State.PLAYING)

    def _poll_bus(self) -> None:
        if self._pipeline is None or self._error is not None:
            return
        message_type = self._gst.MessageType
        message = self._pipeline.get_bus().pop_filtered(message_type.ERROR | message_type.EOS)
        if message is None:
            return
        if message.type == message_type.ERROR:
            error, _debug = message.parse_error()
            self._error = str(getattr(error, "message", error))
        else:
            self._error = "the screen stream ended"

    def latest(self) -> Optional[np.ndarray]:
        self._poll_bus()
        if self._sink is None or self._error is not None:
            return None
        sample = self._sink.emit("try-pull-sample", 0)
        if sample is None:
            return None
        structure = sample.get_caps().get_structure(0)
        width = int(structure.get_value("width"))
        height = int(structure.get_value("height"))
        buffer = sample.get_buffer()
        ok, info = buffer.map(self._gst.MapFlags.READ)
        if not ok:
            return None
        try:
            stride = len(info.data) // max(1, height)
            rows = np.frombuffer(info.data, dtype=np.uint8, count=stride * height)
            return rows.reshape(height, stride)[:, : width * 3].reshape(height, width, 3).copy()
        finally:
            buffer.unmap(info)

    def error(self) -> Optional[str]:
        self._poll_bus()
        return self._error

    def stop(self) -> None:
        pipeline, self._pipeline = self._pipeline, None
        self._sink = None
        if pipeline is not None:
            try:
                pipeline.set_state(self._gst.State.NULL)
            except Exception:
                pass
        # pipewiresrc dups the fd it was given; ours is no longer needed.
        try:
            os.close(self._fd)
        except OSError:
            pass


ReaderFactory = Callable[[int, PortalStream, int], FrameReader]


def _gi_gstreamer_available() -> bool:
    if importlib.util.find_spec("gi") is None:
        return False
    try:
        gi = importlib.import_module("gi")
        gi.require_version("Gst", "1.0")
        gst = importlib.import_module("gi.repository.Gst")
        gst.init(None)
        return all(gst.ElementFactory.find(name) is not None for name in ("pipewiresrc", "appsink", "videoscale", "videoconvert", "videorate"))
    except Exception:
        return False


def _gst_launch_available() -> bool:
    launch = shutil.which("gst-launch-1.0")
    inspect = shutil.which("gst-inspect-1.0")
    if not launch or not inspect:
        return False
    try:
        result = subprocess.run(
            [inspect, "--exists", "pipewiresrc"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_child_environment(),
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def default_reader_factory() -> Optional[ReaderFactory]:
    """In-process GStreamer when PyGObject works, else ``gst-launch-1.0``."""
    if _gi_gstreamer_available():
        return GstAppSinkReader
    if _gst_launch_available():
        return GstLaunchReader
    return None


# -------------------------------------------------------------- token storage


class TokenStore(Protocol):
    def load(self) -> Optional[str]: ...

    def save(self, token: Optional[str]) -> None: ...


class SettingsTokenStore:
    """Keeps the portal's restore token with the app's other settings."""

    def __init__(self, key: str = RESTORE_TOKEN_KEY) -> None:
        self._key = key

    def _settings(self) -> Any:
        from ..gui.app_settings import app_settings

        return app_settings()

    def load(self) -> Optional[str]:
        try:
            value = self._settings().value(self._key, "")
        except Exception:
            return None
        return str(value) if value else None

    def save(self, token: Optional[str]) -> None:
        try:
            settings = self._settings()
            if token:
                settings.setValue(self._key, token)
            else:
                settings.remove(self._key)
            settings.sync()
        except Exception:
            pass


# -------------------------------------------------------------------- service


IDLE = "idle"
REQUESTING = "requesting"
RUNNING = "running"
DENIED = "denied"
FAILED = "failed"


def _default_fps() -> int:
    try:
        from ..config.options import SYNC

        return int(SYNC.monitor_fps)
    except Exception:
        return 45


class WaylandScreenCast:
    """One portal share per app run, shared by every Monitor sync session.

    ``acquire`` (any thread) returns at once and starts the handshake in the
    background when no share is open. Sync sessions read frames through the
    returned :class:`CaptureHandle`; releasing the last handle stops the
    GStreamer reader but keeps the portal session, so the next session only
    reopens the PipeWire remote and the dialog does not come back.
    """

    def __init__(
        self,
        *,
        portal_factory: Optional[Callable[[], Portal]] = None,
        reader_factory: Optional[ReaderFactory] = None,
        token_store: Optional[TokenStore] = None,
        fps: Callable[[], int] = _default_fps,
        thread_factory: Callable[..., Any] = threading.Thread,
    ) -> None:
        self._portal_factory = portal_factory or JeepneyPortal
        self._reader_factory = reader_factory
        self._token_store = token_store if token_store is not None else SettingsTokenStore()
        self._fps = fps
        self._thread_factory = thread_factory
        self._lock = threading.RLock()
        self._state = IDLE
        self._message = ""
        self._portal: Optional[Portal] = None
        self._grant: Optional[PortalGrant] = None
        self._reader: Optional[FrameReader] = None
        self._handles = 0
        self._generation = 0
        self._shutdown = False
        self._worker: Any = None

    # -- state

    @property
    def state(self) -> str:
        return self._state

    @property
    def message(self) -> str:
        return self._message

    # -- public API

    def acquire(self) -> "CaptureHandle":
        with self._lock:
            if self._shutdown:
                raise ScreenSharingDenied(STOPPED_MESSAGE)
            self._handles += 1
            if self._state in (IDLE, DENIED, FAILED):
                # A new Start after a refusal asks again; nothing else does.
                self._begin(self._request_share)
            elif self._state == RUNNING and self._reader is None:
                self._begin(self._reopen)
            return CaptureHandle(self)

    def release(self) -> None:
        with self._lock:
            self._handles = max(0, self._handles - 1)
            if self._handles or self._reader is None:
                return
            reader, self._reader = self._reader, None
        reader.stop()

    def latest(self) -> Optional[np.ndarray]:
        """Newest frame since the last call, ``None`` if none; raises when ended."""
        with self._lock:
            state, reader = self._state, self._reader
        if state in (DENIED, FAILED):
            raise ScreenSharingDenied(self._message or STOPPED_MESSAGE)
        if reader is None:
            return None
        frame = reader.latest()
        if frame is not None:
            return frame
        problem = reader.error()
        if problem:
            self._fail(STOPPED_MESSAGE, reader)
            raise ScreenSharingDenied(STOPPED_MESSAGE)
        return None

    def waiting_for_user(self) -> bool:
        return self._state == REQUESTING

    def shutdown(self) -> None:
        """Stop the reader and close the portal session (app quit)."""
        with self._lock:
            self._shutdown = True
            self._generation += 1
            reader, self._reader = self._reader, None
            portal, self._portal = self._portal, None
            self._grant = None
            self._state = IDLE
        if reader is not None:
            reader.stop()
        if portal is not None:
            try:
                portal.close()
            except Exception:
                pass

    # -- background work

    def _begin(self, target: Callable[[int], None]) -> None:
        self._generation += 1
        self._state = REQUESTING if target == self._request_share else self._state
        generation = self._generation
        worker = self._thread_factory(
            target=target, args=(generation,), name="wayland-capture-portal", daemon=True
        )
        self._worker = worker
        worker.start()

    def _current(self, generation: int) -> bool:
        return generation == self._generation and not self._shutdown

    def _request_share(self, generation: int) -> None:
        portal: Optional[Portal] = None
        try:
            reader_factory = self._reader_factory or default_reader_factory()
            if reader_factory is None:
                raise WaylandCaptureUnavailable(MISSING_GSTREAMER_MESSAGE)
            with self._lock:
                previous, self._portal = self._portal, None
            if previous is not None:
                previous.close()
            portal = self._portal_factory()
            token = self._token_store.load()
            cancelled = lambda: not self._current(generation)  # noqa: E731
            try:
                grant = portal.start(token, cancelled)
            except PortalError:
                if not token:
                    raise
                # A stale or foreign token: forget it and ask normally.
                self._token_store.save(None)
                grant = portal.start(None, cancelled)
            self._token_store.save(grant.restore_token)
            fd = portal.open_remote()
            reader = reader_factory(fd, grant.stream, self._fps())
            reader.start()
        except ScreenSharingDenied as exc:
            self._token_store.save(None)
            self._finish_failed(generation, portal, DENIED, str(exc) or DENIED_MESSAGE)
            return
        except WaylandCaptureUnavailable as exc:
            self._finish_failed(generation, portal, FAILED, str(exc))
            return
        except Exception as exc:
            self._finish_failed(
                generation, portal, FAILED,
                f"Screen sharing could not start ({exc}). Start Monitor sync again to retry.",
            )
            return
        with self._lock:
            if not self._current(generation):
                stale = True
            else:
                stale = False
                self._portal, self._grant, self._reader_factory = portal, grant, reader_factory
                self._state = RUNNING
                self._message = ""
                if self._handles:
                    self._reader = reader
        if stale:
            reader.stop()
            portal.close()
        elif self._reader is not reader:
            reader.stop()

    def _reopen(self, generation: int) -> None:
        """Start a new reader on the existing share (no dialog)."""
        with self._lock:
            portal, grant, factory = self._portal, self._grant, self._reader_factory
        try:
            if portal is None or grant is None or factory is None:
                raise PortalError("No screen-sharing session is open.")
            fd = portal.open_remote()
            reader = factory(fd, grant.stream, self._fps())
            reader.start()
        except Exception:
            self._fail(STOPPED_MESSAGE, None)
            return
        with self._lock:
            keep = self._current(generation) and self._handles > 0 and self._reader is None
            if keep:
                self._reader = reader
        if not keep:
            reader.stop()

    def _finish_failed(self, generation: int, portal: Optional[Portal], state: str, message: str) -> None:
        if portal is not None:
            try:
                portal.close()
            except Exception:
                pass
        with self._lock:
            if self._current(generation):
                self._state = state
                self._message = message

    def _fail(self, message: str, reader: Optional[FrameReader]) -> None:
        with self._lock:
            if reader is not None and self._reader is not reader:
                return
            self._generation += 1
            current, self._reader = self._reader, None
            portal, self._portal = self._portal, None
            self._grant = None
            self._state = FAILED
            self._message = message
        if current is not None:
            current.stop()
        if portal is not None:
            try:
                portal.close()
            except Exception:
                pass


class CaptureHandle:
    """What ``ScreenGrab`` holds: frames from the shared share, no ownership."""

    def __init__(self, service: WaylandScreenCast) -> None:
        self._service: Optional[WaylandScreenCast] = service

    def grab(self) -> Optional[np.ndarray]:
        service = self._service
        if service is None:
            return None
        return service.latest()

    def waiting_for_user(self) -> bool:
        service = self._service
        return bool(service is not None and service.waiting_for_user())

    def close(self) -> None:
        service, self._service = self._service, None
        if service is not None:
            service.release()


# ------------------------------------------------------------ module plumbing


_service_lock = threading.Lock()
_service: Optional[WaylandScreenCast] = None
_availability: Optional[Tuple[float, bool, str]] = None
AVAILABILITY_RETRY_AFTER = 30.0


def service() -> WaylandScreenCast:
    """The process-wide share, created on first use and stopped at exit."""
    global _service
    with _service_lock:
        if _service is None:
            _service = WaylandScreenCast()
            _hook_shutdown(_service)
        return _service


def _hook_shutdown(instance: WaylandScreenCast) -> None:
    atexit.register(instance.shutdown)
    try:
        from PySide6.QtCore import QCoreApplication

        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(instance.shutdown)
    except Exception:
        pass


def set_service(instance: Optional[WaylandScreenCast]) -> None:
    """Replace the process-wide share (tests)."""
    global _service
    with _service_lock:
        _service = instance


def _checks() -> Iterable[Tuple[Callable[[], bool], str]]:
    yield (lambda: importlib.util.find_spec("jeepney") is not None), MISSING_DBUS_MESSAGE
    yield (lambda: default_reader_factory() is not None), MISSING_GSTREAMER_MESSAGE
    yield portal_available, MISSING_PORTAL_MESSAGE


def availability(
    checks: Optional[Iterable[Tuple[Callable[[], bool], str]]] = None,
    clock: Callable[[], float] = time.monotonic,
) -> Tuple[bool, str]:
    """``(True, EXPERIMENTAL_NOTE)`` or ``(False, what to install)``.

    A success is remembered for the app run; a failure is checked again after
    a short while so installing the missing piece does not need a restart.
    """
    global _availability
    cached = _availability
    if checks is None and cached is not None:
        checked_at, ok, message = cached
        if ok or clock() - checked_at < AVAILABILITY_RETRY_AFTER:
            return ok, message
    result: Tuple[bool, str] = (True, EXPERIMENTAL_NOTE)
    for check, message in checks if checks is not None else _checks():
        try:
            passed = bool(check())
        except Exception:
            passed = False
        if not passed:
            result = (False, message)
            break
    if checks is None:
        _availability = (clock(), result[0], result[1])
    return result


def reset_availability() -> None:
    global _availability
    _availability = None


__all__ = [
    "CaptureHandle",
    "DENIED_MESSAGE",
    "EXPERIMENTAL_NOTE",
    "GstAppSinkReader",
    "GstLaunchReader",
    "JeepneyPortal",
    "LatestFrame",
    "MISSING_GSTREAMER_MESSAGE",
    "MISSING_PORTAL_MESSAGE",
    "PortalError",
    "PortalGrant",
    "PortalStream",
    "STOPPED_MESSAGE",
    "ScreenSharingDenied",
    "WAITING_NOTE",
    "WaylandCaptureUnavailable",
    "WaylandScreenCast",
    "availability",
    "frame_size",
    "parse_streams",
    "pipeline_description",
    "service",
]
