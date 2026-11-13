"""Small Fluent building blocks shared by the pages this theme owns.

* ``svg_pixmap`` renders a monochrome SVG from ``resources/icons`` tinted
  with a token colour, at the screen's device-pixel ratio.
* ``FluentIcon`` is a tiny widget that paints such an icon.
* ``paint_severity_glyph`` draws WinUI's filled severity circle (InfoBar,
  toasts) so every notification surface uses the same glyphs.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..resources import ResourceManager
from ..theme.tokens import qcolor

_cache: Dict[Tuple[str, int, int, float], QPixmap] = {}


def _device_pixel_ratio(widget: Optional[QWidget] = None) -> float:
    if widget is not None:
        return max(1.0, float(widget.devicePixelRatioF()))
    app = QGuiApplication.instance()
    screen = QGuiApplication.primaryScreen() if app is not None else None
    return max(1.0, float(screen.devicePixelRatio())) if screen is not None else 1.0


def svg_pixmap(
    filename: str,
    color: QColor | str,
    size: int | QSize = 16,
    *,
    widget: Optional[QWidget] = None,
) -> QPixmap:
    """Return ``filename`` (an icon in resources/icons) tinted with ``color``."""
    qsize = size if isinstance(size, QSize) else QSize(int(size), int(size))
    tint = QColor(color)
    dpr = _device_pixel_ratio(widget)
    key = (filename, qsize.width() * 1000 + qsize.height(), tint.rgba(), dpr)
    cached = _cache.get(key)
    if cached is not None:
        return cached
    pixmap = QPixmap(int(qsize.width() * dpr), int(qsize.height() * dpr))
    pixmap.fill(Qt.GlobalColor.transparent)
    path = ResourceManager.get_icon_path(filename)
    if path is not None:
        renderer = QSvgRenderer(str(path))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter, QRectF(0, 0, pixmap.width(), pixmap.height()))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), tint)
        painter.end()
    pixmap.setDevicePixelRatio(dpr)
    _cache[key] = pixmap
    return pixmap


class FluentIcon(QWidget):
    """A fixed-size monochrome icon painted in a token colour."""

    def __init__(
        self,
        filename: str = "",
        color: str = "fl_text_secondary",
        size: int = 20,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._filename = filename
        self._color = color
        self._size = size
        self.setFixedSize(size, size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def filename(self) -> str:
        return self._filename

    def set_icon(self, filename: str, color: Optional[str] = None) -> None:
        self._filename = filename
        if color is not None:
            self._color = color
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        if not self._filename:
            return
        color = qcolor(self._color) if not self._color.startswith("#") else QColor(self._color)
        if not self.isEnabled():
            color = qcolor("fl_text_disabled")
        painter = QPainter(self)
        painter.drawPixmap(0, 0, svg_pixmap(self._filename, color, self._size, widget=self))


def paint_severity_glyph(painter: QPainter, rect: QRectF, severity: str) -> None:
    """WinUI severity icon: a filled circle in the severity colour with a
    dark glyph (i, ✓, !, ✕)."""
    severity = severity if severity in ("info", "success", "caution", "critical") else "info"
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(qcolor("fl_" + severity))
    painter.drawEllipse(rect)
    glyph = QColor(0, 0, 0, 228)
    unit = rect.width() / 16.0
    pen = QPen(glyph, max(1.4, rect.width() * 0.1), Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    c = rect.center()
    if severity == "success":
        path = QPainterPath(QPointF(c.x() - 3.4 * unit, c.y() + 0.2 * unit))
        path.lineTo(c.x() - 1.0 * unit, c.y() + 2.6 * unit)
        path.lineTo(c.x() + 3.6 * unit, c.y() - 2.4 * unit)
        painter.drawPath(path)
    elif severity == "critical":
        d = 2.8 * unit
        painter.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
        painter.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))
    else:
        top, bottom, dot = (-0.6, 3.6, -3.4) if severity == "info" else (-3.8, 0.9, 3.5)
        painter.drawLine(QPointF(c.x(), c.y() + top * unit), QPointF(c.x(), c.y() + bottom * unit))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glyph)
        painter.drawEllipse(QPointF(c.x(), c.y() + dot * unit), 1.05 * unit, 1.05 * unit)
    painter.restore()


__all__ = ["FluentIcon", "paint_severity_glyph", "svg_pixmap"]
