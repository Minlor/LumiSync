"""LumiSync theme package — a single Fluent (WinUI 3) dark theme.

`apply_theme(app)` installs :class:`FluentStyle` (Fusion plus the app-wide
Fluent behaviour QSS cannot express, such as the combo-box popup), a Qt
palette that mirrors the tokens, and `dark.qss` with `{token}` placeholders
substituted from `tokens.TOKENS`.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication

from .fluent_style import FluentStyle, build_palette, fluent_font, upgrade_combo
from .tokens import TOKENS, qcolor


_QSS_PATH = Path(__file__).parent / "dark.qss"


def _build_stylesheet() -> str:
    text = _QSS_PATH.read_text(encoding="utf-8")
    # Substitute {token} occurrences. Longest keys first so "fl_control"
    # never eats the prefix of "fl_control_hover". Manual replacement avoids
    # str.format collisions with stylesheet braces.
    for key in sorted(TOKENS, key=len, reverse=True):
        text = text.replace("{" + key + "}", TOKENS[key])
    # Absolute icon dir for url() references (QSS resolves relative paths
    # against the cwd, which is unreliable).
    from ..resources import ResourceManager

    icon_dir = (ResourceManager.get_resource_dir() / "icons").as_posix()
    return text.replace("{icon_dir}", icon_dir)


def apply_theme(app: QApplication) -> None:
    """Apply the LumiSync Fluent dark theme to the given QApplication."""
    app.setStyle(FluentStyle())
    app.setPalette(build_palette())
    app.setStyleSheet(_build_stylesheet())


__all__ = ["apply_theme", "TOKENS", "qcolor", "fluent_font", "upgrade_combo"]
