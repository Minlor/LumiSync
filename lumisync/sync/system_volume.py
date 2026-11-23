"""Read the Windows master-volume fader so the music sync can cancel it.

WASAPI loopback captures audio *after* the endpoint (master/speaker) volume is
applied, so turning the system volume down quietens the captured signal and dims
the lights even though the music is unchanged. Rather than reverse-engineer that
attenuation from the signal alone (what :class:`~lumisync.sync.audio.AutoGain`
does, with some lag), this asks Windows what the fader is set to and divides it
straight back out — instant and exact.

Linux behaves the same way for most outputs: a PulseAudio or PipeWire sink whose
volume is applied in software hands its *monitor* source the signal after that
volume (measured on PulseAudio 17 and PipeWire 1.6.2: a sink at 30% gives the
monitor 0.3**3 = 2.7% of the amplitude). There the probe reads the default
sink's volume with ``pactl`` and the same division undoes it. A sink flagged
``HW_VOLUME_CTRL`` applies its volume in the sound card, after the monitor tap,
so the monitor is already independent of the fader; compensating there would
double-correct, and the probe reports "unknown" instead.

The probe degrades gracefully: on other platforms, when ``pycaw`` or ``pactl``
is missing, or on any COM/subprocess error it reports "unknown" and the caller
simply skips compensation.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Callable, Optional, Sequence, Tuple


def compensate(
    bands: Tuple[float, float, float],
    gain: Optional[float],
    max_boost: float = 50.0,
) -> Tuple[float, float, float]:
    """Scale ``bands`` to undo a known master-fader ``gain``.

    ``gain`` is the linear amplitude the fader applied (from
    :meth:`MasterVolumeProbe.linear_gain`); band energy scales with it, so
    multiplying by ``1/gain`` recovers the pre-fader level. A ``None`` gain
    (fader unreadable) or a muted/near-zero gain is a no-op, and the boost is
    capped so a very low fader doesn't amplify the capture noise floor.
    """
    if gain is None or gain <= 1e-6:
        return bands
    boost = min(max_boost, 1.0 / gain)
    return (bands[0] * boost, bands[1] * boost, bands[2] * boost)


class MasterVolumeProbe:
    """Report the linear gain the system master-volume fader is applying.

    ``linear_gain()`` returns a value in ``0..1`` where ``1.0`` is full volume
    and ``0.0`` is muted (a Linux sink raised above 100% reports more than 1),
    or ``None`` when the fader can't be read (in which case
    the caller should not compensate and can fall back to signal-based gain).
    """

    def __init__(self) -> None:
        self._endpoint = None
        self._available = False
        self._last_gain = 1.0
        self._sink: Optional[PulseSinkVolume] = None
        if sys.platform.startswith("linux"):
            pactl = shutil.which("pactl")
            if pactl:
                # Like the Windows probe, read once now so a sink that can't be
                # compensated (no server, hardware volume) is known up front.
                # The probe is rebuilt whenever the recorder reopens, which
                # includes switching the default output.
                sink = PulseSinkVolume(executable=pactl)
                if sink.prime() is not None:
                    self._sink = sink
                    self._available = True
            return
        try:
            from pycaw.pycaw import AudioUtilities

            speakers = AudioUtilities.GetSpeakers()
            # Newer pycaw exposes the activated interface directly; older
            # versions returned a raw IMMDevice that must be activated.
            endpoint = getattr(speakers, "EndpointVolume", None)
            if endpoint is None:
                from ctypes import POINTER, cast

                from comtypes import CLSCTX_ALL
                from pycaw.pycaw import IAudioEndpointVolume

                interface = speakers.Activate(
                    IAudioEndpointVolume._iid_, CLSCTX_ALL, None
                )
                endpoint = cast(interface, POINTER(IAudioEndpointVolume))
            # Probe once so an unusable endpoint fails here, not mid-stream.
            endpoint.GetMasterVolumeLevel()
            self._endpoint = endpoint
            self._available = True
        except Exception:
            # No pycaw, not Windows, or the endpoint could not be activated.
            self._endpoint = None
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def linear_gain(self) -> Optional[float]:
        """Return the master fader's linear amplitude (0..1), or ``None``.

        Uses the dB level (not the audio-tapered scalar) because the audio engine
        attenuates samples by ``10**(dB/20)``, which is exactly what loopback
        captures. The last good reading is returned if a single call fails, so a
        transient COM hiccup doesn't drop compensation for a frame.
        """
        if self._sink is not None:
            return self._sink.linear_gain()
        if not self._available or self._endpoint is None:
            return None
        try:
            if self._endpoint.GetMute():
                self._last_gain = 0.0
                return 0.0
            decibels = float(self._endpoint.GetMasterVolumeLevel())
            gain = 10.0 ** (decibels / 20.0)
            # Clamp to a sane band: >1 shouldn't happen (0 dB is full) but guard
            # against odd endpoints reporting positive dB.
            self._last_gain = max(0.0, min(1.0, gain))
            return self._last_gain
        except Exception:
            return self._last_gain


# ------------------------------------------------------------- Linux (pactl)

PACTL_TIMEOUT = 1.0
PACTL_REFRESH_SECONDS = 1.0
# PulseAudio's PA_VOLUME_NORM: the raw volume that means 100%.
_PA_VOLUME_NORM = 0x10000
# Over-amplified sinks go up to about 150% (3.4x linear); anything far above is
# a parse error, not a real fader.
_MAX_LINEAR_GAIN = 4.0

_RAW_VOLUME = re.compile(r":\s*(\d+)\s*/\s*\d+%")


def _run_pactl(command: Sequence[str], timeout: float) -> Optional[str]:
    env = dict(os.environ)
    # pactl's text output is translated; the parser reads the English labels.
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
    return result.stdout if result.returncode == 0 else None


def parse_default_sink(info_output: str) -> Optional[str]:
    """Return the ``Default Sink:`` name from ``pactl info``."""
    for line in info_output.splitlines():
        label, _, value = line.partition(":")
        if label.strip() == "Default Sink" and value.strip():
            return value.strip()
    return None


def parse_sink_gain(sinks_output: str, sink_name: str) -> Optional[float]:
    """Return the linear gain the named sink applies to its monitor, or ``None``.

    Reads the sink's block in ``pactl list sinks``. A muted sink gives ``0.0``.
    A sink whose volume is in hardware (``HW_VOLUME_CTRL``) gives ``None``: its
    monitor is captured before that volume, so there is nothing to undo. The
    software volume is cubic (PulseAudio's ``pa_sw_volume_to_linear``), so each
    channel's raw value maps to ``(raw / 65536) ** 3`` and the channels are
    averaged as the captured mix averages them.
    """
    block: list[str] = []
    in_block = False
    for line in sinks_output.splitlines():
        if line.startswith("Sink #"):
            if in_block:
                break
            block = []
            continue
        block.append(line)
        if line.strip() == f"Name: {sink_name}":
            in_block = True
    if not in_block:
        return None

    muted = None
    flags: list[str] = []
    raw_volumes: list[int] = []
    for line in block:
        label, _, value = line.strip().partition(":")
        if label == "Mute":
            muted = value.strip().lower() == "yes"
        elif label == "Flags":
            flags = value.split()
        elif label == "Volume":
            raw_volumes = [int(raw) for raw in _RAW_VOLUME.findall(value)]
    if "HW_VOLUME_CTRL" in flags:
        return None
    if muted is None or not raw_volumes:
        return None
    if muted:
        return 0.0
    gains = [(raw / _PA_VOLUME_NORM) ** 3 for raw in raw_volumes]
    return max(0.0, min(_MAX_LINEAR_GAIN, sum(gains) / len(gains)))


class PulseSinkVolume:
    """The default PulseAudio/PipeWire sink's software gain, read via ``pactl``.

    ``linear_gain()`` never waits for ``pactl``: it returns the last reading and,
    at most once per :data:`PACTL_REFRESH_SECONDS`, starts a short-lived worker
    thread to take a new one, so the audio loop is never blocked.
    """

    def __init__(
        self,
        executable: str = "pactl",
        runner: Optional[Callable[[Sequence[str], float], Optional[str]]] = None,
        clock: Callable[[], float] = time.monotonic,
        refresh_seconds: float = PACTL_REFRESH_SECONDS,
    ) -> None:
        self._executable = executable
        self._runner = runner or _run_pactl
        self._clock = clock
        self._refresh_seconds = float(refresh_seconds)
        self._gain: Optional[float] = None
        self._next_refresh = 0.0
        self._worker: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def read_now(self) -> Optional[float]:
        """Query ``pactl`` synchronously and store the result."""
        gain = None
        try:
            info = self._runner([self._executable, "info"], PACTL_TIMEOUT)
            sink = parse_default_sink(info) if info else None
            if sink:
                sinks = self._runner([self._executable, "list", "sinks"], PACTL_TIMEOUT)
                gain = parse_sink_gain(sinks, sink) if sinks else None
        except Exception:
            gain = None
        with self._lock:
            self._gain = gain
        return gain

    def prime(self) -> Optional[float]:
        """Take the first reading synchronously and schedule the next poll."""
        gain = self.read_now()
        with self._lock:
            self._next_refresh = self._clock() + self._refresh_seconds
        return gain

    def linear_gain(self) -> Optional[float]:
        worker = None
        with self._lock:
            now = self._clock()
            if now >= self._next_refresh and (
                self._worker is None or not self._worker.is_alive()
            ):
                self._next_refresh = now + self._refresh_seconds
                worker = threading.Thread(
                    target=self._refresh_worker,
                    name="LumiSyncSinkVolume",
                    daemon=True,
                )
                self._worker = worker
            gain = self._gain
        if worker is not None:
            worker.start()
        return gain

    def _refresh_worker(self) -> None:
        try:
            self.read_now()
        finally:
            with self._lock:
                self._worker = None
