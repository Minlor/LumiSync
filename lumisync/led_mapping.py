"""Shared helpers for monitor LED-zone mapping."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_ASPECT_RATIO = 16 / 9
DEFAULT_CAPTURE_DEPTH = 0.28
MIN_CAPTURE_DEPTH = 0.10
MAX_CAPTURE_DEPTH = 0.50
LEGACY_MAPPING_KEY = "sync/led_mapping"
NORMALIZED_MAPPING_KEY = "sync/led_mapping_edge_v1"
CAPTURE_DEPTH_KEY = "sync/led_capture_depth"

LegacyRegion = Tuple[int, int]
NormalizedRect = Dict[str, float]

DEFAULT_LEGACY_MAPPING: List[LegacyRegion] = [
    (0, 3),
    (0, 2),
    (0, 1),
    (0, 0),
    (1, 0),
    (2, 0),
    (2, 1),
    (2, 2),
    (2, 3),
    (1, 3),
]


def clamp_zone_count(zone_count: int) -> int:
    return max(1, min(255, int(zone_count)))


def sanitize_aspect_ratio(aspect_ratio: float | int | None) -> float:
    try:
        ratio = float(aspect_ratio)
    except (TypeError, ValueError):
        return DEFAULT_ASPECT_RATIO
    if ratio <= 0:
        return DEFAULT_ASPECT_RATIO
    return max(0.25, min(8.0, ratio))


def clamp_capture_depth(capture_depth: float | int | None) -> float:
    try:
        depth = float(capture_depth)
    except (TypeError, ValueError):
        return DEFAULT_CAPTURE_DEPTH
    return max(MIN_CAPTURE_DEPTH, min(MAX_CAPTURE_DEPTH, depth))


def capture_depth_from_settings(settings: Any) -> float:
    return clamp_capture_depth(settings.value(CAPTURE_DEPTH_KEY, DEFAULT_CAPTURE_DEPTH))


def normalize_rect(rect: Dict[str, Any]) -> NormalizedRect:
    x = max(0.0, min(0.999, float(rect.get("x", 0.0))))
    y = max(0.0, min(0.999, float(rect.get("y", 0.0))))
    max_w = max(0.001, 1.0 - x)
    max_h = max(0.001, 1.0 - y)
    w = min(max_w, max(0.001, float(rect.get("w", 0.0))))
    h = min(max_h, max(0.001, float(rect.get("h", 0.0))))
    return {"x": x, "y": y, "w": w, "h": h}


def _largest_remainder_counts(zone_count: int, aspect_ratio: float, capture_depth: float) -> List[int]:
    zone_count = clamp_zone_count(zone_count)
    if zone_count == 1:
        return [1, 0, 0, 0]
    if zone_count == 2:
        return [1, 0, 1, 0]
    if zone_count == 3:
        return [1, 1, 1, 0]

    ratio = sanitize_aspect_ratio(aspect_ratio)
    middle_height = max(0.05, 1.0 - 2 * clamp_capture_depth(capture_depth))
    weights = [ratio, middle_height, ratio, middle_height]
    counts = [1, 1, 1, 1]
    remaining = zone_count - 4
    if remaining <= 0:
        return counts

    total_weight = sum(weights)
    raw = [remaining * weight / total_weight for weight in weights]
    extras = [int(value) for value in raw]
    counts = [count + extra for count, extra in zip(counts, extras)]
    leftover = remaining - sum(extras)
    remainders = sorted(
        enumerate(value - int(value) for value in raw),
        key=lambda item: item[1],
        reverse=True,
    )
    for index, _ in remainders[:leftover]:
        counts[index] += 1
    return counts


def generate_screen_mapping(
    zone_count: int,
    aspect_ratio: float | int | None = DEFAULT_ASPECT_RATIO,
    capture_depth: float | int | None = DEFAULT_CAPTURE_DEPTH,
) -> List[NormalizedRect]:
    """Generate one normalized edge-content rectangle per zone.

    The zones prioritize monitor edges. Increasing capture depth expands those
    edge zones inward without creating a dedicated middle-only zone.
    """
    zone_count = clamp_zone_count(zone_count)
    depth = clamp_capture_depth(capture_depth)
    if zone_count == 1:
        return [{"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}]

    top_count, left_count, bottom_count, right_count = _largest_remainder_counts(
        zone_count,
        sanitize_aspect_ratio(aspect_ratio),
        depth,
    )
    middle_h = max(0.001, 1.0 - 2 * depth)
    mapping: List[NormalizedRect] = []

    for index in range(top_count):
        width = 1.0 / top_count
        mapping.append({"x": 1.0 - (index + 1) * width, "y": 0.0, "w": width, "h": depth})

    for index in range(left_count):
        height = middle_h / left_count
        mapping.append({"x": 0.0, "y": depth + index * height, "w": depth, "h": height})

    for index in range(bottom_count):
        width = 1.0 / bottom_count
        mapping.append({"x": index * width, "y": 1.0 - depth, "w": width, "h": depth})

    for index in range(right_count):
        height = middle_h / right_count
        mapping.append(
            {
                "x": 1.0 - depth,
                "y": 1.0 - depth - (index + 1) * height,
                "w": depth,
                "h": height,
            }
        )

    return [normalize_rect(rect) for rect in mapping]


def generate_perimeter_mapping(
    zone_count: int,
    aspect_ratio: float | int | None = DEFAULT_ASPECT_RATIO,
    capture_depth: float | int | None = DEFAULT_CAPTURE_DEPTH,
) -> List[NormalizedRect]:
    """Compatibility alias for older callers."""
    return generate_screen_mapping(zone_count, aspect_ratio, capture_depth)


def legacy_region_to_rect(region: Sequence[Any]) -> NormalizedRect:
    row = int(region[0])
    col = int(region[1])
    return normalize_rect({"x": col / 4, "y": row / 3, "w": 1 / 4, "h": 1 / 3})


def legacy_mapping_to_rects(mapping: Sequence[Sequence[Any]]) -> List[NormalizedRect]:
    return [legacy_region_to_rect(region) for region in mapping]


def parse_normalized_mapping(value: Any) -> Optional[List[NormalizedRect]]:
    if not value:
        return None
    try:
        raw = json.loads(value) if isinstance(value, str) else value
        rects = [normalize_rect(item) for item in raw if isinstance(item, dict)]
    except Exception:
        return None
    return rects if rects else None


def parse_legacy_mapping(value: Any) -> Optional[List[LegacyRegion]]:
    if not value:
        return None
    try:
        raw = json.loads(value) if isinstance(value, str) else value
        mapping = [tuple(item) for item in raw]
        if all(len(item) == 2 for item in mapping):
            return [(int(row), int(col)) for row, col in mapping]
    except Exception:
        return None
    return None


def fit_normalized_mapping_to_count(
    mapping: Sequence[NormalizedRect],
    zone_count: int,
    aspect_ratio: float | int | None = DEFAULT_ASPECT_RATIO,
    capture_depth: float | int | None = DEFAULT_CAPTURE_DEPTH,
) -> List[NormalizedRect]:
    zone_count = clamp_zone_count(zone_count)
    if not mapping:
        return generate_screen_mapping(zone_count, aspect_ratio, capture_depth)
    source = [normalize_rect(rect) for rect in mapping]
    return [
        source[int(index * len(source) / zone_count) % len(source)]
        for index in range(zone_count)
    ]


def load_mapping_from_settings(
    settings: Any,
    zone_count: int,
    aspect_ratio: float | int | None = DEFAULT_ASPECT_RATIO,
    capture_depth: float | int | None = None,
) -> List[NormalizedRect]:
    depth = capture_depth_from_settings(settings) if capture_depth is None else clamp_capture_depth(capture_depth)
    v2_mapping = parse_normalized_mapping(settings.value(NORMALIZED_MAPPING_KEY, None))
    if v2_mapping:
        return fit_normalized_mapping_to_count(v2_mapping, zone_count, aspect_ratio, depth)

    legacy_mapping = parse_legacy_mapping(settings.value(LEGACY_MAPPING_KEY, None))
    if legacy_mapping:
        return generate_screen_mapping(zone_count, aspect_ratio, depth)

    return generate_screen_mapping(zone_count, aspect_ratio, depth)


def serialize_mapping(mapping: Sequence[NormalizedRect]) -> str:
    return json.dumps([normalize_rect(rect) for rect in mapping])
