"""Design tokens for the LumiSync dark theme.

Single source of truth for colors, radii, spacing. The QSS file references
these via {token} placeholders; Python painters import TOKENS / qcolor()
to stay in sync.
"""

from __future__ import annotations

from PySide6.QtGui import QColor


TOKENS: dict[str, str] = {
    # Surfaces (neutral charcoal with a restrained indigo cast). The window
    # material supplies color; controls remain legible over any wallpaper.
    "bg":             "#11121A",
    "surface":        "#1B1C27",
    "surface_alt":    "#252735",
    "hover":          "#303242",
    "pressed":        "#393C4E",
    # Windows 11 system-backdrop layers. These are only selected after DWM
    # accepts the request; all other platforms keep the solid colors.
    "mica_bg":         "rgba(17, 17, 26, 0.74)",
    "mica_sidebar":    "rgba(9, 10, 17, 0.62)",
    "mica_surface":    "rgba(30, 30, 43, 0.78)",
    "mica_control":    "rgba(40, 41, 55, 0.86)",
    "mica_border":     "rgba(222, 226, 255, 0.11)",
    "mica_device_surface": "rgba(15, 17, 25, 0.90)",
    "mica_inspector_surface": "rgba(21, 22, 33, 0.94)",
    # Desktop Acrylic exposes more of the blurred desktop than Mica. Separate
    # foundation, panel, and control opacities preserve visual hierarchy.
    "acrylic_bg":      "rgba(15, 15, 24, 0.70)",
    "acrylic_sidebar": "rgba(8, 9, 15, 0.58)",
    "acrylic_surface": "rgba(29, 30, 42, 0.76)",
    "acrylic_control": "rgba(40, 42, 56, 0.84)",
    "acrylic_border":  "rgba(222, 226, 255, 0.11)",
    "device_surface": "#151721",
    "device_hover": "#1C1F2C",
    "acrylic_device_surface": "rgba(14, 16, 24, 0.89)",
    "acrylic_inspector_surface": "rgba(20, 21, 31, 0.94)",

    # Navigation uses neutral selection glass; the accent belongs to the icon
    # and slim location marker, keeping the rail quiet like Fluent apps.
    "nav_selected":    "rgba(244, 247, 255, 0.10)",
    "nav_hover":       "rgba(244, 247, 255, 0.06)",

    # Borders
    "border":         "#343644",
    "border_dim":     "#252633",
    "border_strong":  "#55586D",

    # Text
    "text":           "#F3F7FF",
    "text_dim":       "#B0B2C0",
    "text_disabled":  "#717383",

    # Accent (accessible Fluent-inspired blue)
    "accent":          "#4A8BF5",
    "accent_bright":   "#68A3FF",
    "accent_dim":      "rgba(74, 139, 245, 0.20)",
    "accent_glow":     "rgba(74, 139, 245, 0.12)",
    "accent_surface":  "rgba(89, 151, 255, 0.38)",
    "accent_hover":    "rgba(105, 164, 255, 0.50)",
    "accent_pressed":  "rgba(70, 128, 224, 0.30)",
    "accent_border":   "rgba(125, 177, 255, 0.52)",

    # Semantic
    "success":        "#34D399",
    "success_dim":    "rgba(52, 211, 153, 0.18)",
    "warning":        "#FBBF24",
    "danger":         "#F87171",
    "danger_bright":  "#EF4444",
    "danger_dim":     "rgba(248, 113, 113, 0.18)",

    # Geometry
    "radius_sm":      "8px",
    "radius_md":      "10px",
    "radius_lg":      "14px",

    # Typography
    "font_title":     "20pt",
    "font_body":      "10pt",
    "font_small":     "9pt",
    "font_caption":   "8pt",
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
