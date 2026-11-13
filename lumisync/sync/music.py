import platform
import socket
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

# Patch for soundcard compatibility with NumPy 2.0+
# soundcard uses numpy.fromstring binary mode which was removed in NumPy 2.0
if np.lib.NumpyVersion(np.__version__) >= '2.0.0':
    np.fromstring = np.frombuffer

from ..config.sync_settings import SyncSettings
from . import processing

# Frames per recorder block. Without it soundcard's PulseAudio backend asks
# the server for its maximum fragment size (~2 s), which reads as seconds of
# lag and stutter on Linux. 512 frames is ~11 ms at 48 kHz on every backend.
RECORD_BLOCKSIZE = 512


def _soundcard_backend():
    """Load the audio backend only when music capture is requested.

    SoundCard connects to PulseAudio while importing on Linux. Deferring that
    import keeps LumiSync importable on headless systems and lets the existing
    capture error handling report unavailable audio when the feature is used.
    """

    import soundcard

    return soundcard


def default_output_id():
    """Return a stable identifier for the current default speaker, if known."""
    try:
        sc = _soundcard_backend()
        speaker = sc.default_speaker()
    except Exception:
        return None
    identity = getattr(speaker, "id", None) or getattr(speaker, "name", None)
    return str(identity) if identity else None


def _platform_system() -> str:
    return platform.system()


def _is_loopback(microphone: Any) -> bool:
    try:
        return bool(getattr(microphone, "isloopback", False))
    except Exception:
        return False


def _device_name(device: Any) -> str:
    try:
        return str(getattr(device, "name", "") or "")
    except Exception:
        return ""


def _device_id(device: Any) -> str:
    try:
        return str(getattr(device, "id", "") or "")
    except Exception:
        return ""


def _loopback_by_id(sc: Any, wanted_id: str) -> Any:
    """Return the loopback source with exactly this id, or None.

    soundcard falls back to substring and fuzzy *name* matching when the id is
    unknown, so the result must be checked: on PipeWire the sink's input
    source often shares its description and would otherwise win.
    """
    try:
        microphone = sc.get_microphone(id=wanted_id, include_loopback=True)
    except Exception:
        return None
    if _device_id(microphone) == wanted_id and _is_loopback(microphone):
        return microphone
    return None


def default_loopback_microphone():
    """Return a soundcard microphone that captures the system audio output.

    On Windows this is the default speaker's WASAPI loopback, found by the
    speaker's endpoint id. On Linux (PulseAudio / PipeWire) it is the default
    sink's ``<sink>.monitor`` source. Name matching is only a fallback among
    monitor/loopback sources: a real microphone is never returned, because
    music sync would then silently react to the room instead of the music.
    """
    sc = _soundcard_backend()
    speaker = sc.default_speaker()
    speaker_id = _device_id(speaker)
    speaker_name = _device_name(speaker)

    system = _platform_system()
    if speaker_id:
        if system == "Windows":
            microphone = _loopback_by_id(sc, speaker_id)
        elif system == "Darwin":
            microphone = None
        else:
            microphone = _loopback_by_id(sc, f"{speaker_id}.monitor")
        if microphone is not None:
            return microphone

    try:
        candidates = [
            mic for mic in sc.all_microphones(include_loopback=True) if _is_loopback(mic)
        ]
    except Exception:
        candidates = []
    if speaker_name:
        wanted = speaker_name.lower()
        for microphone in candidates:
            if wanted in _device_name(microphone).lower():
                return microphone

    available = ", ".join(_device_name(mic) or _device_id(mic) for mic in candidates)
    output = speaker_name or "the default output"
    if available:
        raise RuntimeError(
            f"No monitor/loopback source found for {output}. Available "
            f"monitor/loopback sources: {available}. Choose one of them as the "
            "default output and try again."
        )
    if system == "Darwin":
        raise RuntimeError(
            "macOS cannot record system audio directly. Install a loopback "
            "driver (for example BlackHole), route the output through it and "
            "try again."
        )
    raise RuntimeError(
        f"No monitor/loopback source found for {output}. On Linux, make sure "
        "PulseAudio or PipeWire is running and the output has a monitor source."
    )


def _initialize_com() -> bool:
    """Initialise COM for a recording thread on Windows (as the sync thread does)."""
    if _platform_system() != "Windows":
        return False
    try:
        from pythoncom import CoInitialize

        CoInitialize()
        return True
    except Exception:
        return False


def _uninitialize_com(initialized: bool) -> None:
    if not initialized:
        return
    try:
        from pythoncom import CoUninitialize

        CoUninitialize()
    except Exception:
        pass


def _open_recorder(microphone: Any, samplerate: int, blocksize: int) -> Any:
    try:
        return microphone.recorder(samplerate=samplerate, blocksize=blocksize)
    except TypeError:
        return microphone.recorder(samplerate=samplerate)


class AudioCapture:
    """Record system audio on its own thread into a small ring buffer.

    soundcard's ``record()`` blocks until data arrives; on PulseAudio a stream
    that stops delivering blocks forever, and on Windows the backend can hand
    back hours of padded silence after the PC wakes from sleep. Reading on a
    dedicated thread keeps the sync loop responsive to Stop, lets it notice a
    stall (:meth:`seconds_since_data`) and only ever exposes the newest audio.
    """

    def __init__(
        self,
        *,
        samplerate: int,
        buffer_frames: int,
        blocksize: int = RECORD_BLOCKSIZE,
        microphone_factory: Optional[Callable[[], Any]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._samplerate = int(samplerate)
        self._blocksize = int(blocksize)
        self._capacity = max(1, int(buffer_frames))
        self._factory = microphone_factory or default_loopback_microphone
        self._clock = clock
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._buffer: Optional[np.ndarray] = None
        self._filled = 0
        self._total = 0
        self._read_total = 0
        self._ready = False
        self._closed = False
        self._error: Optional[BaseException] = None
        self._name = ""
        self._last_data_at: Optional[float] = None
        self._thread = threading.Thread(
            target=self._run, name="lumisync-audio-capture", daemon=True
        )

    # ----------------------------------------------------------------- control

    def start(self) -> None:
        self._thread.start()

    def close(self, timeout: float = 0.5) -> None:
        """Stop recording. A thread stuck inside the backend is left behind."""
        with self._changed:
            self._closed = True
            self._changed.notify_all()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    @property
    def device_name(self) -> str:
        with self._lock:
            return self._name or "the default output"

    # ----------------------------------------------------------------- reading

    def wait_until_ready(self, timeout: float) -> bool:
        """Return True once recording; raise if the device could not be opened."""
        with self._changed:
            if not (self._ready or self._error is not None or self._closed):
                self._changed.wait(max(0.0, timeout))
            if self._error is not None:
                raise self._error
            return self._ready

    def read(self, numframes: int, timeout: float) -> Optional[np.ndarray]:
        """Return the newest ``numframes`` frames once that many new ones arrived.

        Returns None if they did not arrive within ``timeout`` seconds (or the
        capture was closed), and raises the recording thread's error.
        """
        numframes = max(1, int(numframes))
        deadline = time.monotonic() + max(0.0, timeout)
        with self._changed:
            while True:
                if self._error is not None:
                    raise self._error
                if self._closed:
                    return None
                if self._buffer is not None and self._total - self._read_total >= numframes:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._changed.wait(remaining)
            self._read_total = self._total
            count = min(numframes, self._filled)
            return self._buffer[-count:].copy()

    def seconds_since_data(self) -> float:
        """Seconds since the backend last delivered audio (silence included)."""
        with self._lock:
            last = self._last_data_at
        return 0.0 if last is None else max(0.0, self._clock() - last)

    # ------------------------------------------------------------------ worker

    def _push(self, data: Any) -> None:
        try:
            block = np.asarray(data, dtype=np.float32)
        except (TypeError, ValueError):
            return
        if block.ndim == 1:
            block = block.reshape(-1, 1)
        if block.ndim != 2 or block.shape[0] == 0 or block.shape[1] == 0:
            return
        now = self._clock()
        with self._changed:
            capacity = self._capacity
            if self._buffer is None or self._buffer.shape[1] != block.shape[1]:
                self._buffer = np.zeros((capacity, block.shape[1]), dtype=np.float32)
                self._filled = 0
            count = block.shape[0]
            if count >= capacity:
                # Includes the padded silence soundcard returns after a long
                # suspend: only the newest window is ever kept.
                self._buffer[:] = block[-capacity:]
            else:
                self._buffer[:-count] = self._buffer[count:]
                self._buffer[-count:] = block
            self._filled = min(capacity, self._filled + count)
            self._total += count
            self._last_data_at = now
            self._changed.notify_all()

    def _run(self) -> None:
        com_initialized = _initialize_com()
        try:
            microphone = self._factory()
            with self._lock:
                self._name = _device_name(microphone)
            with _open_recorder(microphone, self._samplerate, self._blocksize) as recorder:
                with self._changed:
                    self._ready = True
                    self._last_data_at = self._clock()
                    self._changed.notify_all()
                while True:
                    with self._lock:
                        if self._closed:
                            break
                    try:
                        data = recorder.record(numframes=None)
                    except TypeError:
                        # soundcard's PulseAudio backend can return no chunk.
                        time.sleep(0.005)
                        continue
                    self._push(data)
        except BaseException as exc:
            with self._changed:
                if not self._closed:
                    self._error = exc
                self._changed.notify_all()
        finally:
            with self._changed:
                self._changed.notify_all()
            _uninitialize_com(com_initialized)


def start(
    server: socket.socket,
    device: Dict[str, Any],
    stop_event=None,
    settings: Optional[SyncSettings] = None,
) -> None:
    """Run music sync through the shared :class:`SyncSession` engine."""
    from .session import SyncSession

    session = SyncSession(
        "music",
        [device],
        settings or SyncSettings.from_options(),
        server=server,
        stop_event=stop_event or threading.Event(),
        on_status=print,
        on_error=print,
    )
    session.run()


def get_amplitude(mic_data=None) -> float:
    """Gets the peak audio amplitude (0..1). Retained for compatibility."""
    if mic_data is None:
        return 0

    amplitude = np.max(np.abs(mic_data))
    if amplitude > 1:
        amplitude = 1
    return amplitude


def apply_brightness(colors: List[Tuple[int, int, int]], brightness_factor: float) -> List[Tuple[int, int, int]]:
    """Apply a 0..1 brightness factor to a list of RGB colors."""
    return processing.apply_brightness(colors, brightness_factor)
