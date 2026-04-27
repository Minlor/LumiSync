"""LumiSync theme package — single dark theme.

`apply_theme(app)` loads `dark.qss`, substitutes `{token}` placeholders from
`tokens.TOKENS`, sets a Qt palette that mirrors the same colors, and applies
the resulting stylesheet to the QApplication.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

from .tokens import TOKENS, qcolor


_QSS_PATH = Path(__file__).parent / "dark.qss"


def _build_stylesheet() -> str:
    text = _QSS_PATH.read_text(encoding="utf-8")
    # Substitute {token} occurrences. Manual loop avoids str.format collisions
    # with stylesheet syntax (e.g. percentages or braces).
    for key, value in TOKENS.items():
        text = text.replace("{" + key + "}", value)
    return text


def _build_palette() -> QPalette:
    p = QPalette()
    bg = qcolor("bg")
    surface = qcolor("surface")
    surface_alt = qcolor("surface_alt")
    text = qcolor("text")
    text_dim = qcolor("text_dim")
    accent = qcolor("accent")

    p.setColor(QPalette.ColorRole.Window, bg)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, surface)
    p.setColor(QPalette.ColorRole.AlternateBase, surface_alt)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.PlaceholderText, text_dim)
    p.setColor(QPalette.ColorRole.Button, surface_alt)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.Highlight, accent)
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.ToolTipBase, surface_alt)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    return p


def apply_theme(app: QApplication) -> None:
    """Apply the LumiSync dark theme to the given QApplication."""
    app.setStyle("Fusion")
    app.setPalette(_build_palette())
    app.setStyleSheet(_build_stylesheet())


__all__ = ["apply_theme", "TOKENS", "qcolor"]
