"""Frequency-domain audio analysis for the music sync engine.

The original music sync keyed color off peak amplitude alone: quiet -> red,
louder -> green, loudest -> blue. That reacts to volume, not to the music.

This module runs a real FFT on each captured audio window and splits the
spectrum into bass / mid / treble energy. Those bands drive color directly
(bass -> red, mid -> green, treble -> blue), so a bass drop lights the strip
red and a bright hi-hat pattern pushes it blue, much closer to how a music
visualizer behaves.
"""

from __future__ import annotations

import colorsys
from typing import Tuple

import numpy as np

# Band edges in Hz. Treble is capped below typical loopback Nyquist so it stays
# meaningful even when the effective sample rate is lower than requested.
BASS_RANGE = (20.0, 250.0)
MID_RANGE = (250.0, 4000.0)
TREBLE_RANGE = (4000.0, 16000.0)

RGB = Tuple[int, int, int]

# Color palettes the music sync can render. "rgb" is the direct band->channel
# mapping; the others derive a hue from the spectral balance (bass=low pitch,
# treble=high pitch) so the strip sweeps through a themed gradient.
PALETTE_RGB = "rgb"
PALETTES = ("rgb", "spectrum", "warm", "cool", "mono")
PALETTE_LABELS = {
    "rgb": "Classic (bass red · treble blue)",
    "spectrum": "Spectrum (hue follows pitch)",
    "warm": "Warm (red → amber)",
    "cool": "Cool (cyan → blue)",
    "mono": "Mono pulse (brightness only)",
}


def _to_mono(samples) -> np.ndarray:
    if samples is None:
        return np.empty(0, dtype=np.float32)
    data = np.asarray(samples, dtype=np.float32)
    if data.size == 0:
        return np.empty(0, dtype=np.float32)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return np.ascontiguousarray(data.reshape(-1))


def _band_energy(
    spectrum: np.ndarray,
    freqs: np.ndarray,
    low: float,
    high: float,
) -> float:
    mask = (freqs >= low) & (freqs < high)
    if not np.any(mask):
        return 0.0
    # RMS of the magnitude within the band keeps loud narrow peaks from
    # dominating a wide band's average.
    return float(np.sqrt(np.mean(spectrum[mask] ** 2)))


def spectral_bands(samples, sample_rate: int) -> Tuple[float, float, float]:
    """Return raw ``(bass, mid, treble)`` magnitude energy for one window.

    Values are unnormalized; :func:`bands_to_color` handles scaling. Returns
    zeros when there is no signal (e.g. silence or a dropped capture).
    """
    mono = _to_mono(samples)
    if mono.size < 2:
        return (0.0, 0.0, 0.0)

    windowed = mono * np.hanning(mono.size).astype(np.float32)
    spectrum = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(mono.size, d=1.0 / max(1, sample_rate))

    nyquist = sample_rate / 2.0
    treble_high = min(TREBLE_RANGE[1], nyquist)
    return (
        _band_energy(spectrum, freqs, *BASS_RANGE),
        _band_energy(spectrum, freqs, *MID_RANGE),
        _band_energy(spectrum, freqs, TREBLE_RANGE[0], treble_high),
    )


def _hsv(hue: float, sat: float, val: float) -> RGB:
    r, g, b = colorsys.hsv_to_rgb(hue % 1.0, sat, val)
    return (
        int(max(0, min(255, round(r * 255)))),
        int(max(0, min(255, round(g * 255)))),
        int(max(0, min(255, round(b * 255)))),
    )


def bands_to_color(
    bass: float,
    mid: float,
    treble: float,
    gain: float = 1.7,
    palette: str = PALETTE_RGB,
) -> RGB:
    """Map band energies to an RGB color under the chosen palette.

    The bands are normalized against their combined level so the *balance* of
    the spectrum sets the hue, while overall loudness sets the intensity.

    Palettes:
        rgb: bass->red, mid->green, treble->blue channels directly.
        spectrum/warm/cool: hue derived from the spectral centroid (0 = all
            bass, 1 = all treble) across a themed hue range.
        mono: a white pulse whose brightness tracks loudness.
    """
    bands = np.array([bass, mid, treble], dtype=np.float32)
    total = float(bands.sum())
    if total <= 1e-9:
        return (0, 0, 0)

    # Perceived loudness compresses over a huge dynamic range; a soft knee via
    # sqrt keeps quiet passages visible without blowing out loud ones.
    level = float(min(1.0, np.sqrt(total) * gain))

    if palette == "mono":
        value = int(round(level * 255))
        return (value, value, value)

    if palette in ("spectrum", "warm", "cool"):
        # Spectral centroid in [0, 1]: weight mid at 0.5 and treble at 1.0.
        centroid = float((bands[1] * 0.5 + bands[2] * 1.0) / total)
        if palette == "spectrum":
            hue = 0.66 * centroid          # red (bass) -> blue (treble)
        elif palette == "warm":
            hue = 0.02 + 0.12 * centroid   # deep red -> amber
        else:  # cool
            hue = 0.5 + 0.16 * centroid    # cyan -> blue
        return _hsv(hue, 1.0, level)

    # Default: direct band-to-channel mapping.
    channels = bands / total * level * 255.0
    rgb = np.clip(np.rint(channels), 0, 255).astype(int)
    return (int(rgb[0]), int(rgb[1]), int(rgb[2]))


def amplitude_color(
    samples,
    sample_rate: int,
    gain: float = 1.7,
    palette: str = PALETTE_RGB,
) -> RGB:
    """Convenience wrapper: window -> bands -> color in one call."""
    return bands_to_color(
        *spectral_bands(samples, sample_rate), gain=gain, palette=palette
    )
