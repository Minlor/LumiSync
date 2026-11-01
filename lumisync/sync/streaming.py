"""Per-device latest-frame delivery for the sync loops.

The monitor and music workers compute one colour frame per device per tick and
used to send it inline. A frame is cheap for Govee LAN (one UDP datagram) but
expensive for the Tuya RGBIC paint transport (several TCP writes, tens to
hundreds of milliseconds per frame), so sending inline coupled every device to
the slowest one: Govee strips lost their smooth 30-45 fps cadence whenever a
slow transport was in the same sync group.

:class:`LatestFrameSender` gives each device its own sending thread that only
transmits the freshest submitted frame, paced at that device's advertised
update rate. A slow transport simply consumes the newest frame when it can —
the fast transports are no longer held back, and the slow one never builds a
backlog of stale frames.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional, Sequence


class LatestFrameSender:
    """Send the newest frame for one device on a dedicated thread."""

    def __init__(
        self,
        send: Callable[[Sequence], None],
        max_hz: float = 40.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._send = send
        self._interval = 1.0 / max(0.1, float(max_hz))
        self._clock = clock
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._frame = None
        self._closed = False
        self._last_error: Optional[str] = None
        self._sent = 0
        self._next_at = 0.0
        self._thread = threading.Thread(
            target=self._run, name="lumisync-frame-sender", daemon=True
        )
        self._thread.start()

    def submit(self, frame) -> None:
        """Queue a frame, replacing any not-yet-sent one (drop-oldest)."""
        with self._lock:
            if self._closed:
                return
            self._frame = list(frame) if frame is not None else None
            self._wake.notify()

    def last_error(self) -> Optional[str]:
        """Return and clear the most recent send failure, if any."""
        with self._lock:
            error, self._last_error = self._last_error, None
            return error

    def close(self, timeout: float = 2.0) -> None:
        with self._lock:
            self._closed = True
            self._wake.notify_all()
        self._thread.join(timeout)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    @property
    def sent_count(self) -> int:
        """How many frames have been sent successfully so far."""
        with self._lock:
            return self._sent

    # ------------------------------------------------------------------ worker

    def _run(self) -> None:
        while True:
            with self._lock:
                while self._frame is None and not self._closed:
                    self._wake.wait()
                if self._closed:
                    return  # drop anything still pending at shutdown
            delay = self._next_at - self._clock()
            if delay > 0:
                # Pace at the device's rate. Frames submitted during the wait
                # replace one another; only the newest is sent below.
                time.sleep(delay)
                with self._lock:
                    if self._closed:
                        return
            with self._lock:
                frame, self._frame = self._frame, None
            if frame is None:
                continue
            try:
                self._send(frame)
            except Exception as exc:  # keep the stream alive; report if asked
                with self._lock:
                    self._last_error = str(exc)
            else:
                with self._lock:
                    self._sent += 1
            self._next_at = self._clock() + self._interval


__all__ = ["LatestFrameSender"]
