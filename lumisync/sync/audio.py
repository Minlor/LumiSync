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
from typing import Sequence, Tuple

import numpy as np

# Band edges in Hz. Treble is capped below typical loopback Nyquist so it stays
# meaningful even when the effective sample rate is lower than requested.
BASS_RANGE = (20.0, 250.0)
MID_RANGE = (250.0, 4000.0)
TREBLE_RANGE = (4000.0, 16000.0)

RGB = Tuple[int, int, int]

# Color palettes the music sync can render. A palette controls color only;
# reaction styles below independently control where those colors appear.
PALETTE_RGB = "rgb"
PALETTE_AUTO = "auto"
PALETTE_ALBUM_ART = "album_art"
PALETTES = (
    "rgb",
    "auto",
    "album_art",
    "rainbow",
    "aurora",
    "sunset",
    "ocean",
    "neon",
    "jewel",
    "fire",
    "spectrum",
    "warm",
    "cool",
    "mono",
)
ROTATING_PALETTES = ("rainbow", PALETTE_AUTO, PALETTE_ALBUM_ART)
PALETTE_LABELS = {
    "rgb": "Classic RGB",
    "auto": "Auto Mix",
    "album_art": "Current Artwork",
    "rainbow": "Rainbow Cycle",
    "aurora": "Aurora",
    "sunset": "Electric Sunset",
    "ocean": "Deep Ocean",
    "neon": "Neon Nights",
    "jewel": "Jewel Tones",
    "fire": "Firelight",
    "spectrum": "Pitch Spectrum",
    "warm": "Warm Glow",
    "cool": "Cool Glow",
    "mono": "Mono Pulse",
}
PALETTE_DESCRIPTIONS = {
    "rgb": "Bass, mids, and treble map directly to red, green, and blue.",
    "auto": "Chooses and rotates rich color families from the live spectrum.",
    "album_art": "Uses current track artwork when available, then falls back to Auto Mix.",
    "rainbow": "Continuously rotates through the spectrum; louder audio moves faster.",
    "aurora": "Emerald, cyan, violet, and rose move with the music's pitch.",
    "sunset": "Deep magenta rises through orange into warm gold.",
    "ocean": "A layered mix of deep blue, cyan, teal, and violet.",
    "neon": "Electric pink, cyan, violet, and orange with high saturation.",
    "jewel": "Emerald, sapphire, amethyst, and ruby tones.",
    "fire": "Crimson, orange, and gold respond strongly to bass and energy.",
    "spectrum": "Pitch sweeps from red through the full visible color spectrum.",
    "warm": "A focused red-to-amber gradient for a calmer room.",
    "cool": "A focused cyan-to-blue gradient for a cooler atmosphere.",
    "mono": "Music controls brightness while the color stays neutral white.",
}

PALETTE_STOPS: dict[str, tuple[RGB, ...]] = {
    "aurora": ((16, 185, 129), (34, 211, 238), (139, 92, 246), (244, 114, 182)),
    "sunset": ((126, 34, 104), (236, 72, 153), (249, 115, 22), (253, 224, 71)),
    "ocean": ((7, 72, 116), (14, 165, 233), (45, 212, 191), (99, 102, 241)),
    "neon": ((236, 72, 153), (34, 211, 238), (168, 85, 247), (249, 115, 22)),
    "jewel": ((5, 150, 105), (37, 99, 235), (147, 51, 234), (225, 29, 72)),
    "fire": ((153, 27, 27), (239, 68, 68), (249, 115, 22), (250, 204, 21)),
}

# Spatial reactions are deliberately separate from palettes: the palette
# decides which colors the spectrum produces, while the reaction decides how
# those colors occupy and move through the device's LED zones.
REACTION_FLOW = "flow"
REACTION_AUTO = "auto"
REACTIONS = (
    "auto",
    "flow",
    "pulse",
    "energy_fill",
    "band_split",
    "center_burst",
    "wave",
    "bass_bounce",
    "chase",
    "twinkle",
)
REACTION_LABELS = {
    "auto": "Auto Director",
    "flow": "Color Flow",
    "pulse": "Beat Pulse",
    "energy_fill": "Energy Fill",
    "band_split": "Band Split",
    "center_burst": "Center Burst",
    "wave": "Spectrum Wave",
    "bass_bounce": "Bass Bounce",
    "chase": "Color Chase",
    "twinkle": "Twinkle",
}
REACTION_DESCRIPTIONS = {
    "auto": "Chooses and holds a fitting reaction from loudness, transients, and spectral balance.",
    "flow": "Pitch sets each new color; loudness sets its brightness as it travels.",
    "pulse": "Loudness and sudden musical hits make the whole light breathe and flash.",
    "energy_fill": "Loudness and frequency activity fill the light farther from start to end.",
    "band_split": "Measured bass, mids, and treble drive separate parts of the light.",
    "center_burst": "Bass and musical transients launch brighter ripples from the center.",
    "wave": "Loudness controls wave height and speed while pitch controls its color.",
    "bass_bounce": "Measured bass energy controls how far the light fills toward the center.",
    "chase": "Loudness controls chase speed and brightness; pitch controls its color.",
    "twinkle": "Treble controls sparkle density while overall loudness controls brightness.",
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

    window = np.hanning(mono.size).astype(np.float32)
    windowed = mono * window
    # Normalize by the Hann window's coherent gain. Without this, FFT
    # magnitudes grow with the capture-window length and ordinary audio clips
    # the reaction level to 100%, making quiet and loud passages look alike.
    spectrum = np.abs(np.fft.rfft(windowed))
    spectrum *= 2.0 / max(1e-9, float(window.sum()))
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


def _mix_rgb(first: RGB, second: RGB, amount: float) -> RGB:
    amount = max(0.0, min(1.0, float(amount)))
    return tuple(
        int(round(a + (b - a) * amount)) for a, b in zip(first, second)
    )


def _gradient_color(
    stops: Sequence[RGB],
    position: float,
    level: float,
) -> RGB:
    if not stops or level <= 0.0:
        return (0, 0, 0)
    if len(stops) == 1:
        color = stops[0]
    else:
        position = max(0.0, min(1.0, float(position)))
        scaled = position * (len(stops) - 1)
        index = min(len(stops) - 2, int(scaled))
        color = _mix_rgb(stops[index], stops[index + 1], scaled - index)
    return tuple(
        int(round(max(0, min(255, channel)) * max(0.0, min(1.0, level))))
        for channel in color
    )


def _spectral_complexity(bands: np.ndarray, total: float) -> float:
    """Return normalized spectral entropy for three frequency bands."""
    if total <= 1e-9:
        return 0.0
    shares = bands / total
    active = shares[shares > 1e-9]
    if active.size <= 1:
        return 0.0
    return float(-np.sum(active * np.log(active)) / np.log(3.0))


def bands_to_color(
    bass: float,
    mid: float,
    treble: float,
    gain: float = 1.7,
    palette: str = PALETTE_RGB,
    hue_offset: float = 0.0,
    palette_colors: Sequence[RGB] | None = None,
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

    # Spectral centroid in [0, 1]: weight mid at 0.5 and treble at 1.0.
    centroid = float((bands[1] * 0.5 + bands[2] * 1.0) / total)

    if palette in ("spectrum", "warm", "cool"):
        if palette == "spectrum":
            hue = 0.66 * centroid          # red (bass) -> blue (treble)
        elif palette == "warm":
            hue = 0.02 + 0.12 * centroid   # deep red -> amber
        else:  # cool
            hue = 0.5 + 0.16 * centroid    # cyan -> blue
        return _hsv(hue, 1.0, level)

    if palette in PALETTE_STOPS:
        return _gradient_color(PALETTE_STOPS[palette], centroid, level)

    if palette == "rainbow":
        return _hsv(hue_offset + centroid * 0.42, 0.96, level)

    if palette == PALETTE_ALBUM_ART and palette_colors:
        # The rotating offset lets several colors from one cover appear over
        # time while the spectral centroid keeps every frame audio-reactive.
        position = (centroid * 0.62 + hue_offset) % 1.0
        return _gradient_color(palette_colors, position, level)

    if palette in (PALETTE_AUTO, PALETTE_ALBUM_ART):
        complexity = _spectral_complexity(bands, total)
        bass_share, mid_share, treble_share = (float(value) for value in bands / total)
        if complexity > 0.82:
            stops = PALETTE_STOPS["neon"]
        elif bass_share > 0.52:
            stops = PALETTE_STOPS["fire"]
        elif treble_share > 0.44:
            stops = PALETTE_STOPS["ocean"]
        elif mid_share > 0.50:
            stops = PALETTE_STOPS["jewel"]
        else:
            stops = PALETTE_STOPS["aurora"]
        return _gradient_color(
            stops,
            (centroid * 0.62 + hue_offset) % 1.0,
            level,
        )

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


class MusicPatternRenderer:
    """Turn one spectrum reading into a spatial frame of LED-zone colors.

    The renderer keeps the small amount of history needed by moving patterns.
    Switching reaction style resets that history, preventing colors from the
    previous style leaking into the new one.
    """

    def __init__(self, segment_count: int, fps: int = 60):
        self.segment_count = max(1, int(segment_count))
        self._fps = max(1, int(fps))
        self._frame_dt = 1.0 / self._fps
        self._frame: list[RGB] = [(0, 0, 0)] * self.segment_count
        self._reaction = ""
        self._phase = 0.0
        self._chase_position = 0.0
        self._chase_direction = 1.0
        self._twinkles: list[RGB] = [(0, 0, 0)] * self.segment_count
        self._previous_level = 0.0
        self._fill_level = 0.0
        self._fill_color: RGB = (0, 0, 0)
        self._palette_phase = 0.0
        self._auto_renderer: MusicPatternRenderer | None = None
        self._auto_reaction = REACTION_FLOW
        # Hold is tracked in seconds so a phrase lasts the same wall-clock time
        # regardless of the configured frame rate.
        self._auto_hold_seconds = 0.0
        # One rotation index per decision category so each category cycles
        # through its own members instead of sharing a global counter.
        self._auto_cycle: dict[str, int] = {}
        # Smoothed spectral features feed the category decision so one noisy
        # window can't pin the next phrase; transients use a decaying peak.
        self._auto_features_ready = False
        self._auto_level = 0.0
        self._auto_bass_share = 0.0
        self._auto_treble_share = 0.0
        self._auto_complexity = 0.0
        self._auto_transient_peak = 0.0
        # Crossfade state used to blend the previous frame into a freshly
        # chosen reaction so switches don't blink to black.
        self._auto_fade = 0.0
        self._auto_fade_from: list[RGB] = []

    @property
    def active_reaction(self) -> str:
        """Return the concrete reaction currently producing the frame."""
        if self._reaction == REACTION_AUTO:
            return self._auto_reaction
        return self._reaction or REACTION_FLOW

    def render(
        self,
        bands: Tuple[float, float, float],
        *,
        reaction: str = REACTION_FLOW,
        gain: float = 1.7,
        palette: str = PALETTE_RGB,
        palette_colors: Sequence[RGB] | None = None,
    ) -> list[RGB]:
        reaction = reaction if reaction in REACTIONS else REACTION_FLOW
        if reaction != self._reaction:
            self._reset(reaction)

        bass, mid, treble = (
            max(0.0, float(bands[0])),
            max(0.0, float(bands[1])),
            max(0.0, float(bands[2])),
        )
        total = bass + mid + treble
        level = self._level(total, gain)
        level_rise = max(0.0, level - self._previous_level)
        transient = min(1.0, level_rise * 4.0)

        # Auto Director delegates to a nested renderer; the palette rotation and
        # beat-strength math below only apply to concrete reactions, so return
        # before computing them.
        if reaction == REACTION_AUTO:
            self._frame = self._auto_frame(
                (bass, mid, treble),
                level=level,
                transient=transient,
                gain=gain,
                palette=palette,
                palette_colors=palette_colors,
            )
            self._previous_level = level
            return list(self._frame)

        bass_share = bass / total if total > 1e-9 else 0.0
        beat_strength = min(
            1.0,
            transient + level * (0.35 + 0.65 * bass_share),
        )
        if total > 1e-9 and palette in ROTATING_PALETTES:
            # Rotation remains tied to live sound: silence pauses the palette.
            self._palette_phase = (
                self._palette_phase + 0.0015 + 0.0040 * level
            ) % 1.0

        source = bands_to_color(
            bass,
            mid,
            treble,
            gain=gain,
            palette=palette,
            hue_offset=self._palette_phase,
            palette_colors=palette_colors,
        )

        if reaction == "pulse":
            pulse = self._scale(source, 0.48 + 0.52 * beat_strength)
            self._frame = [pulse] * self.segment_count
        elif reaction == "energy_fill":
            self._frame = self._energy_fill_frame(
                bass,
                mid,
                treble,
                source=source,
                level=level,
                transient=transient,
            )
        elif reaction == "band_split":
            self._frame = self._band_split_frame(
                bass,
                mid,
                treble,
                source=source,
                gain=gain,
                palette=palette,
                palette_colors=palette_colors,
            )
        elif reaction == "center_burst":
            burst = self._scale(source, 0.22 + 0.78 * beat_strength)
            self._frame = self._center_burst_frame(burst)
        elif reaction == "wave":
            self._frame = self._wave_frame(source, level)
        elif reaction == "bass_bounce":
            self._frame = self._bass_bounce_frame(
                bass,
                mid,
                treble,
                source=source,
                level=level,
            )
        elif reaction == "chase":
            self._frame = self._chase_frame(source, level)
        elif reaction == "twinkle":
            self._frame = self._twinkle_frame(
                bass,
                mid,
                treble,
                source=source,
                gain=gain,
                palette=palette,
                palette_colors=palette_colors,
                level=level,
                transient=transient,
            )
        else:  # flow
            self._frame = self._frame[1:] + [source]

        self._previous_level = level
        return list(self._frame)

    def _auto_frame(
        self,
        bands: Tuple[float, float, float],
        *,
        level: float,
        transient: float,
        gain: float,
        palette: str,
        palette_colors: Sequence[RGB] | None,
    ) -> list[RGB]:
        total = sum(bands)
        if total > 1e-9:
            self._update_auto_features(bands, total, level, transient)

        if total > 1e-9 and self._auto_hold_seconds <= 0.0:
            previous = self._auto_reaction
            self._auto_reaction = self._pick_auto_reaction()
            # Hold each choice for a musical phrase. Louder passages hold at
            # least as long as calm ones so busy sections don't churn styles.
            self._auto_hold_seconds = 3.0 + 2.0 * self._auto_level
            if self._auto_reaction != previous and any(
                color != (0, 0, 0) for color in self._frame
            ):
                # Blend out of the frame we were showing so the new reaction,
                # which rebuilds its motion from an empty strip, doesn't blink.
                # Skip when the strip is already dark: there's nothing to fade.
                self._auto_fade_from = list(self._frame)
                self._auto_fade = 1.0

        if total > 1e-9:
            self._auto_hold_seconds = max(
                0.0, self._auto_hold_seconds - self._frame_dt
            )
        if self._auto_renderer is None:
            self._auto_renderer = MusicPatternRenderer(self.segment_count, self._fps)
        frame = self._auto_renderer.render(
            bands,
            reaction=self._auto_reaction,
            gain=gain,
            palette=palette,
            palette_colors=palette_colors,
        )

        if self._auto_fade > 0.0 and len(self._auto_fade_from) == len(frame):
            frame = [
                _mix_rgb(fresh, previous_color, self._auto_fade)
                for fresh, previous_color in zip(frame, self._auto_fade_from)
            ]
            # ~0.12s crossfade regardless of frame rate.
            self._auto_fade = max(0.0, self._auto_fade - self._frame_dt / 0.12)
        return frame

    def _update_auto_features(
        self,
        bands: Tuple[float, float, float],
        total: float,
        level: float,
        transient: float,
    ) -> None:
        """Smooth the features the category decision reads.

        Band shares and loudness use an EMA so a single noisy window can't
        decide the next phrase; the transient keeps a decaying peak so a real
        hit is still felt at the next decision point.
        """
        values = np.asarray(bands, dtype=np.float32)
        bass_share, _mid_share, treble_share = (float(v) for v in values / total)
        complexity = _spectral_complexity(values, total)
        if not self._auto_features_ready:
            self._auto_level = level
            self._auto_bass_share = bass_share
            self._auto_treble_share = treble_share
            self._auto_complexity = complexity
            self._auto_features_ready = True
        else:
            alpha = 0.25
            self._auto_level += (level - self._auto_level) * alpha
            self._auto_bass_share += (bass_share - self._auto_bass_share) * alpha
            self._auto_treble_share += (
                treble_share - self._auto_treble_share
            ) * alpha
            self._auto_complexity += (complexity - self._auto_complexity) * alpha
        self._auto_transient_peak = max(transient, self._auto_transient_peak * 0.85)

    def _pick_auto_reaction(self) -> str:
        """Choose the next concrete reaction from the smoothed features."""
        if self._auto_transient_peak > 0.55:
            key, candidates = "transient", ("pulse", "center_burst")
        elif self._auto_bass_share > 0.55:
            key = "bass"
            candidates = ("bass_bounce", "energy_fill", "center_burst")
        elif self._auto_treble_share > 0.45:
            key, candidates = "treble", ("twinkle", "wave", "chase")
        elif self._auto_complexity > 0.78:
            key, candidates = "complex", ("band_split", "wave", "energy_fill")
        elif self._auto_level > 0.65:
            key, candidates = "loud", ("chase", "pulse", "energy_fill")
        else:
            key, candidates = "calm", ("flow", "wave", "energy_fill")
        index = self._auto_cycle.get(key, 0)
        self._auto_cycle[key] = index + 1
        return candidates[index % len(candidates)]

    def _band_split_frame(
        self,
        bass: float,
        mid: float,
        treble: float,
        *,
        source: RGB,
        gain: float,
        palette: str,
        palette_colors: Sequence[RGB] | None,
    ) -> list[RGB]:
        if self.segment_count == 1:
            return [source]

        band_colors = (
            bands_to_color(
                bass, 0.0, 0.0, gain=gain, palette=palette,
                hue_offset=self._palette_phase, palette_colors=palette_colors,
            ),
            bands_to_color(
                0.0, mid, 0.0, gain=gain, palette=palette,
                hue_offset=self._palette_phase, palette_colors=palette_colors,
            ),
            bands_to_color(
                0.0, 0.0, treble, gain=gain, palette=palette,
                hue_offset=self._palette_phase, palette_colors=palette_colors,
            ),
        )
        if self.segment_count == 2:
            return [band_colors[0], band_colors[2]]
        return [
            band_colors[min(2, (index * 3) // self.segment_count)]
            for index in range(self.segment_count)
        ]

    def _center_burst_frame(self, source: RGB) -> list[RGB]:
        left_center = (self.segment_count - 1) // 2
        right_center = self.segment_count // 2
        previous = self._frame
        frame = [(0, 0, 0)] * self.segment_count

        for index in range(left_center):
            frame[index] = previous[index + 1]
        for index in range(right_center + 1, self.segment_count):
            frame[index] = previous[index - 1]

        frame[left_center] = source
        frame[right_center] = source
        return frame

    def _reset(self, reaction: str) -> None:
        self._frame = [(0, 0, 0)] * self.segment_count
        self._twinkles = [(0, 0, 0)] * self.segment_count
        self._reaction = reaction
        self._phase = 0.0
        self._chase_position = 0.0
        self._chase_direction = 1.0
        # Deliberately keep _previous_level: zeroing it makes the first frame
        # after a switch read as a full-scale transient (a phantom flash).
        self._fill_level = 0.0
        self._fill_color = (0, 0, 0)
        self._auto_renderer = None
        self._auto_reaction = REACTION_FLOW
        self._auto_hold_seconds = 0.0
        self._auto_cycle = {}
        self._auto_features_ready = False
        self._auto_level = 0.0
        self._auto_bass_share = 0.0
        self._auto_treble_share = 0.0
        self._auto_complexity = 0.0
        self._auto_transient_peak = 0.0
        self._auto_fade = 0.0
        self._auto_fade_from = []

    @staticmethod
    def _level(energy: float, gain: float) -> float:
        if energy <= 1e-9:
            return 0.0
        return float(min(1.0, np.sqrt(energy) * gain))

    @staticmethod
    def _scale(color: RGB, amount: float) -> RGB:
        amount = max(0.0, min(1.0, float(amount)))
        return tuple(int(round(channel * amount)) for channel in color)

    @staticmethod
    def _brightest(first: RGB, second: RGB) -> RGB:
        return tuple(max(a, b) for a, b in zip(first, second))

    def _wave_frame(self, source: RGB, level: float) -> list[RGB]:
        if source == (0, 0, 0):
            return [(0, 0, 0)] * self.segment_count

        frame = []
        for index in range(self.segment_count):
            position = index / max(1, self.segment_count)
            wave = 0.5 + 0.5 * np.sin(position * 2.0 * np.pi - self._phase)
            frame.append(self._scale(source, 0.16 + 0.84 * float(wave)))
        self._phase = (self._phase + 0.18 + 0.22 * level) % (2.0 * np.pi)
        return frame

    def _energy_fill_frame(
        self,
        bass: float,
        mid: float,
        treble: float,
        *,
        source: RGB,
        level: float,
        transient: float,
    ) -> list[RGB]:
        total = bass + mid + treble
        complexity = _spectral_complexity(
            np.array([bass, mid, treble], dtype=np.float32), total
        )

        # Loud audio fills the meter, while richer audio that is active across
        # several bands earns the final part of the strip. Transients add a
        # short kick without allowing silence or a timer to create movement.
        activity = min(
            1.0,
            level * (0.62 + 0.38 * complexity) + transient * 0.10,
        )
        target_fill = activity * self.segment_count
        response = 0.68 if target_fill > self._fill_level else 0.22
        self._fill_level += (target_fill - self._fill_level) * response
        if target_fill <= 1e-9 and self._fill_level < 0.02:
            self._fill_level = 0.0

        if source != (0, 0, 0):
            self._fill_color = source
        else:
            self._fill_color = self._scale(self._fill_color, 0.72)

        full_segments = min(self.segment_count, int(self._fill_level))
        partial = self._fill_level - full_segments
        frame = [(0, 0, 0)] * self.segment_count
        for index in range(full_segments):
            frame[index] = self._fill_color
        if full_segments < self.segment_count and partial > 1e-9:
            frame[full_segments] = self._scale(self._fill_color, partial)
        return frame

    def _bass_bounce_frame(
        self,
        bass: float,
        mid: float,
        treble: float,
        *,
        source: RGB,
        level: float,
    ) -> list[RGB]:
        total = bass + mid + treble
        if total <= 1e-9 or source == (0, 0, 0):
            return [(0, 0, 0)] * self.segment_count

        bass_share = bass / total
        fill = level * float(np.sqrt(bass_share))
        half = (self.segment_count + 1) // 2
        active_from_each_end = min(half, int(np.ceil(fill * half)))
        return [
            source
            if index < active_from_each_end
            or index >= self.segment_count - active_from_each_end
            else (0, 0, 0)
            for index in range(self.segment_count)
        ]

    def _chase_frame(self, source: RGB, level: float) -> list[RGB]:
        faded = [self._scale(color, 0.58) for color in self._frame]
        if source == (0, 0, 0):
            return faded

        head = max(0, min(self.segment_count - 1, int(round(self._chase_position))))
        faded[head] = self._brightest(faded[head], source)

        if self.segment_count > 1:
            step = 0.55 + 0.45 * level
            next_position = self._chase_position + self._chase_direction * step
            if next_position >= self.segment_count - 1:
                next_position = float(self.segment_count - 1)
                self._chase_direction = -1.0
            elif next_position <= 0.0:
                next_position = 0.0
                self._chase_direction = 1.0
            self._chase_position = next_position
        return faded

    def _twinkle_frame(
        self,
        bass: float,
        mid: float,
        treble: float,
        *,
        source: RGB,
        gain: float,
        palette: str,
        palette_colors: Sequence[RGB] | None,
        level: float,
        transient: float,
    ) -> list[RGB]:
        total = bass + mid + treble
        self._twinkles = [self._scale(color, 0.62) for color in self._twinkles]
        if total <= 1e-9 or source == (0, 0, 0):
            return list(self._twinkles)

        treble_share = treble / total
        sparkle_color = bands_to_color(
            bass * 0.15,
            mid * 0.45,
            treble * 1.25,
            gain=gain,
            palette=palette,
            hue_offset=self._palette_phase,
            palette_colors=palette_colors,
        )
        sparkle_count = max(
            1,
            int(
                round(
                    min(1.0, level + transient)
                    * (1.0 + treble_share * max(1, self.segment_count // 3))
                )
            ),
        )
        step = int(self._phase)
        for offset in range(min(self.segment_count, sparkle_count)):
            index = (step * 7 + offset * 3) % self.segment_count
            self._twinkles[index] = self._brightest(
                self._twinkles[index], sparkle_color
            )
        self._phase += 1.0

        ambient = self._scale(source, 0.12)
        return [self._brightest(ambient, color) for color in self._twinkles]
