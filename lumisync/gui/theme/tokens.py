"""Design tokens for the LumiSync dark theme.

Single source of truth for colors, radii, spacing. The QSS file references
these via {token} placeholders; Python painters import TOKENS / qcolor()
to stay in sync.
"""

from __future__ import annotations

from PyQt6.QtGui import QColor


TOKENS: dict[str, str] = {
    # Surfaces
    "bg":             "#0E1116",
    "surface":        "#161A21",
    "surface_alt":    "#1F242D",
    "hover":          "#252B36",
    "pressed":        "#2D3340",

    # Borders
    "border":         "#2A2F3A",
    "border_dim":     "#1F242D",
    "border_strong":  "#3A4150",

    # Text
    "text":           "#E6E8EE",
    "text_dim":       "#8A93A6",
    "text_disabled":  "#4A5263",

    # Accent (RGB-evocative violet)
    "accent":         "#7C5CFF",
    "accent_bright":  "#9077FF",
    "accent_dim":     "rgba(124, 92, 255, 0.32)",
    "accent_glow":    "rgba(124, 92, 255, 0.18)",

    # Semantic
    "success":        "#34D399",
    "success_dim":    "rgba(52, 211, 153, 0.18)",
    "warning":        "#FBBF24",
    "danger":         "#F87171",
    "danger_bright":  "#EF4444",
    "danger_dim":     "rgba(248, 113, 113, 0.18)",

    # Geometry
    "radius_sm":      "6px",
    "radius_md":      "8px",
    "radius_lg":      "12px",
}


def qcolor(name: str) -> QColor:
    """Return a QColor for a token. Handles 'rgba(...)' and '#hex'."""
    raw = TOKENS[name].strip()
    if raw.startswith("rgba"):
        inner = raw[raw.index("(") + 1 : raw.rindex(")")]
        parts = [p.strip() for p in inner.split(",")]
        r, g, b = (int(parts[0]), int(parts[1]), int(parts[2]))
        a = int(round(float(parts[3]) * 255)) if len(parts) > 3 else 255
        return QColor(r, g, b, a)
    return QColor(raw)


__all__ = ["TOKENS", "qcolor"]
