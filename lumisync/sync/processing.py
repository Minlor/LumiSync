"""Real-time color processing for the LumiSync sync engines.

This module centralises the frame -> per-zone color -> smoothed output pipeline
so the CLI (`sync/monitor.py`) and the GUI worker share one implementation.

Design goals:

* Sample whole screen regions, not a single center pixel, using vectorized
  NumPy so it stays cheap even for large edge zones.
* Average in linear light (gamma-correct) so mixed regions do not turn muddy.
* Smooth transitions with a per-frame temporal EMA instead of transmitting ten
  interpolated packets per frame. That removes ~100 ms of built-in latency and
  cuts UDP traffic to the strip by an order of magnitude.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

import numpy as np

from .. import led_mapping

RGB = Tuple[int, int, int]

# Cap how many pixels per axis we actually read from a zone before averaging.
# Zones can cover a large slice of a 4K frame; striding down to at most this
# many samples per axis keeps the gamma conversion fast without hurting the
# average in any perceptible way.
_MAX_SAMPLES_PER_AXIS = 24

# Precomputed sRGB<->linear lookup tables keyed by 8-bit input. Building the
# table once avoids a float `pow` over every sampled pixel each frame.
_SRGB_TO_LINEAR = np.where(
    np.arange(256) / 255.0 <= 0.04045,
    (np.arange(256) / 255.0) / 12.92,
    ((np.arange(256) / 255.0 + 0.055) / 1.055) ** 2.4,
).astype(np.float32)


def _linear_to_srgb(values: np.ndarray) -> np.ndarray:
    """Convert linear-light floats (0..1) back to sRGB floats (0..1)."""
    values = np.clip(values, 0.0, 1.0)
    return np.where(
        values <= 0.0031308,
        values * 12.92,
        1.055 * np.power(values, 1.0 / 2.4) - 0.055,
    )


def _zone_bounds(rect: dict, width: int, height: int) -> Tuple[int, int, int, int]:
    r = led_mapping.normalize_rect(rect)
    x1 = min(width - 1, max(0, int(r["x"] * width)))
    y1 = min(height - 1, max(0, int(r["y"] * height)))
    x2 = min(width, max(x1 + 1, int((r["x"] + r["w"]) * width)))
    y2 = min(height, max(y1 + 1, int((r["y"] + r["h"]) * height)))
    return x1, y1, x2, y2


def average_zone_colors(
    frame: np.ndarray,
    mapping: Sequence[dict],
    *,
    gamma_correct: bool = True,
) -> List[RGB]:
    """Average the pixels inside each mapped zone of an RGB frame.

    Args:
        frame: ``(H, W, 3)`` uint8 array in RGB order (as produced by
            :meth:`ScreenGrab.capture_array`).
        mapping: normalized edge rectangles, one per LED zone.
        gamma_correct: average in linear light for perceptually accurate color.

    Returns:
        One ``(r, g, b)`` tuple per zone.
    """
    if frame is None or frame.size == 0:
        return [(0, 0, 0) for _ in mapping]

    height, width = frame.shape[:2]
    colors: List[RGB] = []
    for rect in mapping:
        x1, y1, x2, y2 = _zone_bounds(rect, width, height)
        sub = frame[y1:y2, x1:x2, :3]

        step_y = max(1, sub.shape[0] // _MAX_SAMPLES_PER_AXIS)
        step_x = max(1, sub.shape[1] // _MAX_SAMPLES_PER_AXIS)
        sub = sub[::step_y, ::step_x].reshape(-1, 3)

        if gamma_correct:
            linear = _SRGB_TO_LINEAR[sub]
            mean_linear = linear.mean(axis=0)
            srgb = _linear_to_srgb(mean_linear) * 255.0
        else:
            srgb = sub.mean(axis=0)

        rgb = np.clip(np.rint(srgb), 0, 255).astype(int)
        colors.append((int(rgb[0]), int(rgb[1]), int(rgb[2])))
    return colors


def apply_saturation(colors: Sequence[RGB], factor: float) -> List[RGB]:
    """Scale each color's saturation around its own luma.

    A factor of 1.0 is a no-op. Values above 1.0 push colors away from grey,
    which makes screen-synced ambient light read as more vivid, closer to how
    Govee's DreamView presents it.
    """
    if factor == 1.0:
        return [tuple(int(c) for c in color) for color in colors]

    result: List[RGB] = []
    for color in colors:
        r, g, b = (float(color[0]), float(color[1]), float(color[2]))
        luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
        result.append(
            (
                int(max(0, min(255, round(luma + (r - luma) * factor)))),
                int(max(0, min(255, round(luma + (g - luma) * factor)))),
                int(max(0, min(255, round(luma + (b - luma) * factor)))),
            )
        )
    return result


def apply_brightness(colors: Sequence[RGB], factor: float) -> List[RGB]:
    """Scale every channel by a 0..1 brightness factor."""
    return [
        (
            int(color[0] * factor),
            int(color[1] * factor),
            int(color[2] * factor),
        )
        for color in colors
    ]


def colors_changed(
    previous: Sequence[RGB],
    current: Sequence[RGB],
    threshold: int,
) -> bool:
    """Return True if any channel moved more than ``threshold`` since last send.

    Used to skip redundant UDP frames while the screen is static.
    """
    if previous is None or len(previous) != len(current):
        return True
    if threshold <= 0:
        return True
    for prev, cur in zip(previous, current):
        if (
            abs(prev[0] - cur[0]) > threshold
            or abs(prev[1] - cur[1]) > threshold
            or abs(prev[2] - cur[2]) > threshold
        ):
            return True
    return False


class ColorSmoother:
    """Per-zone temporal exponential moving average.

    Each :meth:`update` nudges the held color toward the new target by ``alpha``.
    One call == one frame == one packet, so smoothing happens on the color value
    rather than by flooding the strip with interpolated packets.
    """

    def __init__(self, alpha: float = 0.55, count: int = 0) -> None:
        self.alpha = float(max(0.0, min(1.0, alpha)))
        self._state: np.ndarray | None = (
            np.zeros((count, 3), dtype=np.float32) if count else None
        )

    def reset(self, count: int = 0) -> None:
        self._state = np.zeros((count, 3), dtype=np.float32) if count else None

    def update(self, target: Sequence[RGB]) -> List[RGB]:
        target_arr = np.asarray(target, dtype=np.float32).reshape(-1, 3)
        if self._state is None or self._state.shape[0] != target_arr.shape[0]:
            # Count changed (or first frame): snap to the target so we don't fade
            # in from black on start or on a segment-count change.
            self._state = target_arr.copy()
        else:
            self._state += (target_arr - self._state) * self.alpha

        rounded = np.clip(np.rint(self._state), 0, 255).astype(int)
        return [(int(row[0]), int(row[1]), int(row[2])) for row in rounded]
