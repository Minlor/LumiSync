"""Toast notifications — transient in-window status messages.

The status bar is hidden by default, so controller feedback (device
actions, sync state, errors) surfaces here instead: Fluent InfoBar-style
cards stacked in the bottom-right corner of the main window that fade in,
wait, and fade out. At most three are visible; the oldest goes first.

Each toast paints its own surface: an 8 px rounded card tinted by severity
(opaque flyout grey for information, WinUI's success / caution / critical
backgrounds otherwise), a hairline stroke, a soft shadow, a filled severity
glyph, the message, and a small ✕ close glyph.

Rapid-fire updates from the same source (e.g. a brightness slider drag
emitting one status per tick) coalesce into a single toast whose text and
lifetime refresh in place, keyed by the message prefix before ":".
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..theme.tokens import qcolor
from .fluent import paint_severity_glyph

TOAST_WIDTH = 360          # The visible card; the widget adds shadow room.
SHADOW = 12                # Transparent margin holding the soft shadow.
MARGIN = 16 - SHADOW       # The card sits 16 px from the window edges.
SPACING = 8 - 2 * SHADOW   # Shadows overlap; the cards stay 8 px apart.
MAX_TOASTS = 3

KIND_INFO = "info"
KIND_SUCCESS = "success"
KIND_WARNING = "warning"
KIND_ERROR = "error"

_SEVERITY = {
    KIND_INFO: "info",
    KIND_SUCCESS: "success",
    KIND_WARNING: "caution",
    KIND_ERROR: "critical",
}
_BACKGROUND = {
    "info": "fl_flyout",
    "success": "fl_success_bg",
    "caution": "fl_caution_bg",
    "critical": "fl_critical_bg",
}


class _SeverityIcon(QWidget):
    def __init__(self, severity: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._severity = severity
        self.setFixedSize(16, 16)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        paint_severity_glyph(painter, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self._severity)


class _CloseGlyph(QAbstractButton):
    """A subtle ✕ (no boxed button), as on WinUI's InfoBar."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(32, 32)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAccessibleName("Dismiss notification")
        self.setToolTip("Dismiss")

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
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self.isDown() or self.underMouse():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor("fl_control_pressed" if self.isDown() else "fl_subtle_hover"))
            painter.drawRoundedRect(rect, 4, 4)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(qcolor("fl_text"), 1.5))
            painter.drawRoundedRect(rect.adjusted(0.75, 0.75, -0.75, -0.75), 4, 4)
        painter.setPen(QPen(qcolor("fl_text_secondary"), 1.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        c = rect.center()
        d = 4.0
        painter.drawLine(QPointF(c.x() - d, c.y() - d), QPointF(c.x() + d, c.y() + d))
        painter.drawLine(QPointF(c.x() - d, c.y() + d), QPointF(c.x() + d, c.y() - d))


class Toast(QFrame):
    """A single toast card. Managed by ToastManager; don't create directly."""

    def __init__(self, message: str, kind: str, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setProperty("kind", kind)
        self._severity = _SEVERITY.get(kind, "info")
        self.setFixedWidth(TOAST_WIDTH + 2 * SHADOW)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        self.setAccessibleName(
            {"info": "Notification", "success": "Success", "caution": "Warning",
             "critical": "Error"}[self._severity]
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SHADOW + 16, SHADOW + 6, SHADOW + 6, SHADOW + 6)
        layout.setSpacing(12)

        icon_column = QVBoxLayout()
        icon_column.setContentsMargins(0, 10, 0, 0)
        icon_column.addWidget(_SeverityIcon(self._severity))
        icon_column.addStretch(1)
        layout.addLayout(icon_column)

        self.label = QLabel(message)
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setObjectName("ToastLabel")
        self.label.setContentsMargins(0, 7, 0, 7)
        self.label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.label, 1)

        close_column = QVBoxLayout()
        close_column.setContentsMargins(0, 0, 0, 0)
        self.close_button = _CloseGlyph()
        self.close_button.clicked.connect(self.dismiss)
        close_column.addWidget(self.close_button)
        close_column.addStretch(1)
        layout.addLayout(close_column)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._life_timer = QTimer(self)
        self._life_timer.setSingleShot(True)
        self._life_timer.timeout.connect(self._fade_out)
        self._closing = False

    @property
    def severity(self) -> str:
        return self._severity

    def card_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        card = self.card_rect()
        # Soft shadow: stacked, slightly offset rounded rects fading outwards.
        painter.setPen(Qt.PenStyle.NoPen)
        steps = SHADOW - 2
        for step in range(steps, 0, -1):
            alpha = int(30 * (1 - step / steps) ** 2) + 2
            painter.setBrush(QColor(0, 0, 0, alpha))
            spread = float(step)
            painter.drawRoundedRect(
                card.adjusted(-spread, -spread + 3, spread, spread + 3),
                8 + spread,
                8 + spread,
            )
        path = QPainterPath()
        path.addRoundedRect(card, 8, 8)
        background = qcolor(_BACKGROUND[self._severity])
        background.setAlpha(255)
        painter.fillPath(path, background)
        painter.setPen(QPen(qcolor("fl_card_stroke"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(card.adjusted(0.5, 0.5, -0.5, -0.5), 7.5, 7.5)

    def set_message(self, message: str) -> None:
        self.label.setText(message)
        self.adjustSize()

    def open(self, duration: int) -> None:
        self.show()
        self.raise_()
        anim = QPropertyAnimation(self._opacity, b"opacity", self)
        anim.setDuration(160)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        self._life_timer.start(duration)

    def restart(self, duration: int) -> None:
        if self._closing:
            return
        self._life_timer.start(duration)

    def dismiss(self) -> None:
        self._fade_out()

    def mousePressEvent(self, _event) -> None:  # noqa: N802 - Qt API
        self.dismiss()

    def _fade_out(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._life_timer.stop()
        anim = QPropertyAnimation(self._opacity, b"opacity", self)
        anim.setDuration(200)
        anim.setStartValue(self._opacity.opacity())
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(self.deleteLater)
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)


class ToastManager(QObject):
    """Owns the stack of active toasts inside a host window."""

    def __init__(self, window: QWidget):
        super().__init__(window)
        self._window = window
        self._toasts: List[Toast] = []
        window.installEventFilter(self)

    # ------------------------------------------------------------------ api

    def show(self, message: str, kind: str = KIND_INFO, duration: int = 4500) -> None:
        message = (message or "").strip()
        if not message:
            return

        if message.lower().startswith("error"):
            kind = KIND_ERROR

        existing = self._find_coalescible(message, kind)
        if existing is not None:
            existing.set_message(message)
            existing.restart(duration)
            self._restack()
            return

        toast = Toast(message, kind, self._window)
        toast.destroyed.connect(lambda *_: self._on_toast_destroyed(toast))
        self._toasts.append(toast)

        while len([t for t in self._toasts if not t._closing]) > MAX_TOASTS:
            oldest = next(t for t in self._toasts if not t._closing)
            oldest.dismiss()
            self._toasts.remove(oldest)

        toast.adjustSize()
        self._restack()
        toast.open(duration)

    def error(self, message: str, duration: int = 6000) -> None:
        self.show(message, KIND_ERROR, duration)

    def success(self, message: str, duration: int = 4000) -> None:
        self.show(message, KIND_SUCCESS, duration)

    def warning(self, message: str, duration: int = 6000) -> None:
        self.show(message, KIND_WARNING, duration)

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _coalesce_key(message: str) -> Optional[str]:
        head, sep, _tail = message.partition(":")
        return head.strip().lower() if sep else None

    def _find_coalescible(self, message: str, kind: str) -> Optional[Toast]:
        key = self._coalesce_key(message)
        for toast in reversed(self._toasts):
            if toast.property("kind") != kind or toast._closing:
                continue
            text = toast.label.text()
            # Identical messages and same-prefix updates refresh in place.
            if text == message or (key is not None and self._coalesce_key(text) == key):
                return toast
        return None

    def _on_toast_destroyed(self, toast: Toast) -> None:
        self._toasts = [t for t in self._toasts if t is not toast]
        try:
            self._restack()
        except RuntimeError:
            # Window already deleted during app teardown.
            self._toasts.clear()

    def _restack(self) -> None:
        area = self._window.contentsRect()
        status = getattr(self._window, "statusBar", None)
        bottom = area.bottom()
        try:
            bar = status() if callable(status) else None
        except RuntimeError:
            bar = None
        if bar is not None and bar.isVisible():
            bottom = bar.geometry().top() - 1
        y = bottom - MARGIN + 1
        for toast in reversed(self._toasts):
            if toast._closing:
                continue
            toast.adjustSize()
            h = toast.height()
            toast.move(area.right() - MARGIN - toast.width() + 1, y - h)
            toast.raise_()
            y -= h + SPACING

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        if obj is self._window and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
        ):
            self._restack()
        return super().eventFilter(obj, event)


__all__ = [
    "ToastManager",
    "Toast",
    "KIND_INFO",
    "KIND_SUCCESS",
    "KIND_WARNING",
    "KIND_ERROR",
]
