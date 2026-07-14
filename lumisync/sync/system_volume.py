"""Read the Windows master-volume fader so the music sync can cancel it.

WASAPI loopback captures audio *after* the endpoint (master/speaker) volume is
applied, so turning the system volume down quietens the captured signal and dims
the lights even though the music is unchanged. Rather than reverse-engineer that
attenuation from the signal alone (what :class:`~lumisync.sync.audio.AutoGain`
does, with some lag), this asks Windows what the fader is set to and divides it
straight back out — instant and exact.

The probe degrades gracefully: on non-Windows, when ``pycaw`` is missing, or on
any COM error it reports "unknown" and the caller simply skips compensation.
"""

from __future__ import annotations

from typing import Optional, Tuple


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
    and ``0.0`` is muted, or ``None`` when the fader can't be read (in which case
    the caller should not compensate and can fall back to signal-based gain).
    """

    def __init__(self) -> None:
        self._endpoint = None
        self._available = False
        self._last_gain = 1.0
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
