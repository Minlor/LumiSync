"""Design tokens for the LumiSync dark theme.

Single source of truth for colors, radii, spacing. The QSS file references
these via {token} placeholders; Python painters import TOKENS / qcolor()
to stay in sync.
"""

from __future__ import annotations

from PySide6.QtGui import QColor


TOKENS: dict[str, str] = {
    # Surfaces: neutral WinUI 3 dark greys. The solid fallback matches
    # fl_base; cards and controls layer translucent whites over it.
    "bg":             "#1C1C1C",
    "surface":        "#272727",
    "surface_alt":    "#2D2D2D",
    "hover":          "#353535",
    "pressed":        "#292929",
    # Windows 11 system-backdrop layers. These are only selected after DWM
    # accepts the request; all other platforms keep the solid colors. The
    # rail and the content share one layer, as in WinUI's NavigationView.
    "mica_bg":         "rgba(28, 28, 28, 0.62)",
    "mica_sidebar":    "rgba(28, 28, 28, 0.62)",
    "mica_surface":    "rgba(255, 255, 255, 0.051)",
    "mica_control":    "rgba(255, 255, 255, 0.061)",
    "mica_border":     "rgba(255, 255, 255, 0.070)",
    "mica_device_surface": "rgba(255, 255, 255, 0.051)",
    "mica_inspector_surface": "rgba(40, 40, 40, 0.92)",
    # Desktop Acrylic shows the desktop through a blur, so its tint stays
    # dense enough to keep text and focus legible over bright wallpapers.
    "acrylic_bg":      "rgba(28, 28, 28, 0.84)",
    "acrylic_sidebar": "rgba(28, 28, 28, 0.84)",
    "acrylic_surface": "rgba(255, 255, 255, 0.051)",
    "acrylic_control": "rgba(255, 255, 255, 0.061)",
    "acrylic_border":  "rgba(255, 255, 255, 0.070)",
    "device_surface": "#262626",
    "device_hover": "#2C2C2C",
    "acrylic_device_surface": "rgba(255, 255, 255, 0.051)",
    "acrylic_inspector_surface": "rgba(40, 40, 40, 0.92)",

    # Navigation uses neutral selection glass; the accent belongs to the
    # slim location marker, keeping the rail quiet like Fluent apps.
    "nav_selected":    "rgba(255, 255, 255, 0.090)",
    "nav_hover":       "rgba(255, 255, 255, 0.060)",

    # Borders (opaque approximations of the Fluent strokes over fl_base, so
    # painters that call QColor.name() keep working).
    "border":         "#333333",
    "border_dim":     "#2A2A2A",
    "border_strong":  "#5C5C5C",

    # Text
    "text":           "#FFFFFF",
    "text_dim":       "#C5C5C5",
    "text_disabled":  "#9A9A9A",

    # Accent (the Fluent dark-theme accent: a light blue fill with dark text)
    "accent":          "#6BA6FF",
    "accent_bright":   "#8DBBFF",
    "accent_dim":      "rgba(107, 166, 255, 0.20)",
    "accent_glow":     "rgba(107, 166, 255, 0.12)",
    "accent_surface":  "rgba(107, 166, 255, 0.38)",
    "accent_hover":    "rgba(125, 178, 255, 0.50)",
    "accent_pressed":  "rgba(91, 147, 230, 0.30)",
    "accent_border":   "rgba(141, 187, 255, 0.52)",

    # Semantic
    "success":        "#6CCB5F",
    "success_dim":    "rgba(108, 203, 95, 0.18)",
    "warning":        "#F2C94C",
    "danger":         "#FF99A4",
    "danger_bright":  "#FFB3BB",
    "danger_dim":     "rgba(255, 153, 164, 0.18)",

    # Geometry: controls 4px, cards and flyouts 8px.
    "radius_sm":      "4px",
    "radius_md":      "4px",
    "radius_lg":      "8px",

    # Fluent (Windows 11 / WinUI 3 dark) layer system, modelled on the
    # GalaxyBudsClient reference. New UI uses these; values are final so
    # painted widgets and the stylesheet stay in step.
    "fl_base":             "#1C1C1C",   # solid window fallback
    "fl_layer":            "rgba(58, 58, 58, 0.30)",   # content layer
    "fl_card":             "rgba(255, 255, 255, 0.051)",  # CardBackgroundFillColorDefault
    "fl_card_secondary":   "rgba(255, 255, 255, 0.031)",
    "fl_card_hover":       "rgba(255, 255, 255, 0.082)",
    "fl_card_stroke":      "rgba(255, 255, 255, 0.060)",
    "fl_control":          "rgba(255, 255, 255, 0.061)",  # ControlFillColorDefault
    "fl_control_hover":    "rgba(255, 255, 255, 0.084)",
    "fl_control_pressed":  "rgba(255, 255, 255, 0.033)",
    "fl_control_disabled": "rgba(255, 255, 255, 0.043)",
    "fl_control_stroke":   "rgba(255, 255, 255, 0.070)",
    "fl_control_stroke_top": "rgba(255, 255, 255, 0.094)",
    "fl_control_strong":   "rgba(255, 255, 255, 0.545)",  # slider track, toggle-off ring
    "fl_control_solid":    "#454545",   # slider thumb ring
    "fl_divider":          "rgba(255, 255, 255, 0.083)",
    "fl_subtle_hover":     "rgba(255, 255, 255, 0.060)",  # list/nav hover
    "fl_subtle_selected":  "rgba(255, 255, 255, 0.090)",  # list/nav selection
    "fl_flyout":           "rgba(44, 44, 44, 0.98)",
    "fl_flyout_stroke":    "rgba(255, 255, 255, 0.080)",
    "fl_text":             "#FFFFFF",
    "fl_text_secondary":   "rgba(255, 255, 255, 0.773)",
    "fl_text_tertiary":    "rgba(255, 255, 255, 0.529)",
    "fl_text_disabled":    "rgba(255, 255, 255, 0.365)",
    # Accent fill in dark Fluent is a light tone with dark foreground.
    "fl_accent":           "#6BA6FF",
    "fl_accent_hover":     "#7DB2FF",
    "fl_accent_pressed":   "#5B93E6",
    "fl_accent_on":        "#08101F",
    "fl_accent_text":      "#8DBBFF",   # accent-coloured text / links
    "fl_info":             "#6BA6FF",
    "fl_success":          "#6CCB5F",
    "fl_caution":          "#FCE100",
    "fl_critical":         "#FF99A4",
    # InfoBar / notification backgrounds per severity (WinUI dark values).
    "fl_info_bg":          "rgba(255, 255, 255, 0.031)",
    "fl_success_bg":       "#393D1B",
    "fl_caution_bg":       "#433519",
    "fl_critical_bg":      "#442726",
    "fl_radius_control":   "4px",
    "fl_radius_card":      "8px",

    # Typography (Fluent type ramp: Title 28, Body 14, Caption 12)
    "font_title":     "28px",
    "font_body":      "14px",
    "font_small":     "12px",
    "font_caption":   "12px",
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
