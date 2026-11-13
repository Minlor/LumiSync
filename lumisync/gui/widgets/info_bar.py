"""Fluent (WinUI 3) InfoBar: an inline, persistent message about page state.

Unlike a toast, an InfoBar describes something that stays true until it is
fixed (an account that needs signing in again, no network) and sits in the
page it concerns. The widget paints its own surface from the ``fl_*`` tokens
so it looks the same whatever the application stylesheet does.
"""

from __future__ import annotations

import html
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..theme import TOKENS, qcolor

SEVERITY_INFO = "info"
SEVERITY_SUCCESS = "success"
SEVERITY_CAUTION = "caution"
SEVERITY_CRITICAL = "critical"
SEVERITIES = (SEVERITY_INFO, SEVERITY_SUCCESS, SEVERITY_CAUTION, SEVERITY_CRITICAL)


def _severity(severity: str) -> str:
    return severity if severity in SEVERITIES else SEVERITY_INFO


def paint_severity_icon(painter: QPainter, rect: QRectF, severity: str) -> None:
    """Draw WinUI's filled severity circle with a dark glyph inside *rect*."""
    severity = _severity(severity)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(qcolor("fl_" + severity))
    painter.drawEllipse(rect)
    glyph = QColor(0, 0, 0, 228)
    side = rect.width()
    pen = QPen(glyph, max(1.4, side * 0.1), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
               Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    c = rect.center()
    unit = side / 16.0
    if severity == SEVERITY_SUCCESS:
        path = QPainterPath(QPointF(c.x() - 3.4 * unit, c.y() + 0.2 * unit))
        path.lineTo(c.x() - 1.0 * unit, c.y() + 2.6 * unit)
        path.lineTo(c.x() + 3.6 * unit, c.y() - 2.4 * unit)
        painter.drawPath(path)
    elif severity == SEVERITY_CRITICAL:
        d = 2.8 * unit
        painter.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
        painter.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))
    else:
        # "i" for information, "!" for caution: a stem and a dot.
        stem_top, stem_bottom, dot = (
            (-0.6, 3.6, -3.4) if severity == SEVERITY_INFO else (-3.8, 0.9, 3.5)
        )
        painter.drawLine(QPointF(c.x(), c.y() + stem_top * unit), QPointF(c.x(), c.y() + stem_bottom * unit))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glyph)
        radius = 1.05 * unit
        painter.drawEllipse(QPointF(c.x(), c.y() + dot * unit), radius, radius)
    painter.restore()


class SeverityIcon(QWidget):
    """A 16 px filled severity glyph (used by InfoBars and account rows)."""

    def __init__(self, severity: str = SEVERITY_INFO, parent: Optional[QWidget] = None, *, size: int = 16):
        super().__init__(parent)
        self._severity = _severity(severity)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def severity(self) -> str:
        return self._severity

    def set_severity(self, severity: str) -> None:
        self._severity = _severity(severity)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        paint_severity_icon(painter, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self._severity)


class _CloseButton(QAbstractButton):
    """Subtle 32 px ✕ button drawn like WinUI's InfoBar close button."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Close")
        self.setToolTip("Close")

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return QSize(32, 32)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self.isDown():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor("fl_control_pressed"))
            painter.drawRoundedRect(rect, 4, 4)
        elif self.underMouse():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor("fl_subtle_hover"))
            painter.drawRoundedRect(rect, 4, 4)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(qcolor("fl_text"), 1.5))
            painter.drawRoundedRect(rect.adjusted(0.75, 0.75, -0.75, -0.75), 4, 4)
        painter.setPen(QPen(qcolor("fl_text_secondary"), 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        c = rect.center()
        d = 4.5
        painter.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
        painter.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))


class InfoBar(QWidget):
    """Severity icon, bold title and message on one line, optional action, ✕.

    The title and message flow as one paragraph, so they share a line when
    there is room and wrap underneath when there is not, as in WinUI.
    """

    closed = Signal()
    action_triggered = Signal()

    def __init__(
        self,
        severity: str = SEVERITY_INFO,
        title: str = "",
        message: str = "",
        action_text: Optional[str] = None,
        parent: Optional[QWidget] = None,
        *,
        closable: bool = True,
    ):
        super().__init__(parent)
        self.setObjectName("InfoBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.setMinimumHeight(48)
        self._severity = _severity(severity)
        self._title = ""
        self._message = ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 8, 8, 8)
        layout.setSpacing(12)
        # The icon lines up with the first line of text, as in WinUI, so a
        # wrapped message does not leave it floating in the middle. The text
        # has 6 px padding and a ~20 px line: an 8 px drop centres the icon.
        self.icon = SeverityIcon(self._severity, self)
        icon_box = QWidget(self)
        icon_box.setFixedWidth(16)
        icon_layout = QVBoxLayout(icon_box)
        icon_layout.setContentsMargins(0, 8, 0, 0)
        icon_layout.setSpacing(0)
        icon_layout.addWidget(self.icon)
        icon_layout.addStretch(1)
        layout.addWidget(icon_box)

        self.text_label = QLabel(self)
        self.text_label.setObjectName("InfoBarText")
        self.text_label.setTextFormat(Qt.TextFormat.RichText)
        self.text_label.setWordWrap(True)
        self.text_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.text_label.setStyleSheet(
            f"QLabel#InfoBarText {{ color: {TOKENS['fl_text']}; background: transparent; padding: 6px 0; }}"
        )
        layout.addWidget(self.text_label, 1)

        self.action_button = QPushButton(self)
        self.action_button.setObjectName("InfoBarAction")
        self.action_button.setAutoDefault(False)
        self.action_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_button.clicked.connect(self.action_triggered)
        layout.addWidget(self.action_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.close_button = _CloseButton(self)
        self.close_button.clicked.connect(self._close)
        self.close_button.setVisible(closable)
        layout.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignVCenter)

        self.set_content(severity, title, message, action_text)

    # ------------------------------------------------------------------ api

    def severity(self) -> str:
        return self._severity

    def title(self) -> str:
        return self._title

    def message(self) -> str:
        return self._message

    def set_content(
        self,
        severity: str,
        title: str,
        message: str = "",
        action_text: Optional[str] = None,
    ) -> None:
        self._severity = _severity(severity)
        self._title = str(title or "")
        self._message = str(message or "")
        self.icon.set_severity(self._severity)
        parts = []
        if self._title:
            parts.append(f"<b>{html.escape(self._title)}</b>")
        if self._message:
            parts.append(html.escape(self._message))
        self.text_label.setText("&nbsp;&nbsp;&nbsp;".join(parts))
        self.action_button.setText(action_text or "")
        self.action_button.setVisible(bool(action_text))
        self.setAccessibleName(self._title)
        self.setAccessibleDescription(self._message)
        self.update()

    # ------------------------------------------------------------------ internals

    def _close(self) -> None:
        self.hide()
        self.closed.emit()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(qcolor("fl_card_stroke"), 1))
        painter.setBrush(qcolor(f"fl_{self._severity}_bg"))
        painter.drawRoundedRect(rect, 8, 8)


__all__ = [
    "InfoBar",
    "SeverityIcon",
    "paint_severity_icon",
    "SEVERITY_INFO",
    "SEVERITY_SUCCESS",
    "SEVERITY_CAUTION",
    "SEVERITY_CRITICAL",
]
