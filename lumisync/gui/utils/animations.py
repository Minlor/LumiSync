"""Small animation helpers used across the GUI.

Keep these minimal — short durations, easing curves only. No bouncing.
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPropertyAnimation,
    QObject,
    QTimer,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QStackedWidget, QWidget


def animate_height(
    widget: QWidget,
    end_height: int,
    *,
    duration: int = 220,
    easing: QEasingCurve.Type = QEasingCurve.Type.OutCubic,
    on_finished: Optional[Callable[[], None]] = None,
) -> QPropertyAnimation:
    """Animate `maximumHeight` from current to `end_height`."""
    anim = QPropertyAnimation(widget, b"maximumHeight", widget)
    anim.setDuration(duration)
    anim.setStartValue(widget.maximumHeight())
    anim.setEndValue(end_height)
    anim.setEasingCurve(easing)
    if on_finished is not None:
        anim.finished.connect(on_finished)
    anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return anim


def fade_swap_stack(stack: QStackedWidget, target_index: int, *, duration: int = 140) -> None:
    """Cross-fade transition between pages of a QStackedWidget."""
    if target_index == stack.currentIndex() or target_index < 0:
        return

    outgoing = stack.currentWidget()
    incoming = stack.widget(target_index)
    if outgoing is None or incoming is None:
        stack.setCurrentIndex(target_index)
        return

    # Fade out current
    out_eff = QGraphicsOpacityEffect(outgoing)
    out_eff.setOpacity(1.0)
    outgoing.setGraphicsEffect(out_eff)
    out_anim = QPropertyAnimation(out_eff, b"opacity", outgoing)
    out_anim.setDuration(duration)
    out_anim.setStartValue(1.0)
    out_anim.setEndValue(0.0)
    out_anim.setEasingCurve(QEasingCurve.Type.InCubic)

    def _swap() -> None:
        # Clean up old effect, swap, fade in
        outgoing.setGraphicsEffect(None)
        stack.setCurrentIndex(target_index)
        in_eff = QGraphicsOpacityEffect(incoming)
        in_eff.setOpacity(0.0)
        incoming.setGraphicsEffect(in_eff)
        in_anim = QPropertyAnimation(in_eff, b"opacity", incoming)
        in_anim.setDuration(duration)
        in_anim.setStartValue(0.0)
        in_anim.setEndValue(1.0)
        in_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        in_anim.finished.connect(lambda: incoming.setGraphicsEffect(None))
        in_anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    out_anim.finished.connect(_swap)
    out_anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)


class PulseDot(QWidget):
    """A small status dot that pulses when active."""

    def __init__(self, color: QColor, parent: Optional[QWidget] = None, *, size: int = 10):
        super().__init__(parent)
        self._color = QColor(color)
        self._size = size
        self._pulse = 1.0
        self._active = False
        self.setFixedSize(size + 4, size + 4)

        self._anim = QPropertyAnimation(self, b"pulse", self)
        self._anim.setDuration(900)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.35)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)

    def set_color(self, color: QColor) -> None:
        self._color = QColor(color)
        self.update()

    def set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        if active:
            self._anim.start()
        else:
            self._anim.stop()
            self._pulse = 1.0
            self.update()

    def _get_pulse(self) -> float:
        return self._pulse

    def _set_pulse(self, value: float) -> None:
        self._pulse = float(value)
        self.update()

    pulse = pyqtProperty(float, _get_pulse, _set_pulse)

    def paintEvent(self, _event) -> None:
        from PyQt6.QtGui import QPainter, QBrush

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)

        # Outer glow ring (only when active)
        if self._active:
            ring = QColor(self._color)
            ring.setAlphaF(0.25 * self._pulse)
            painter.setBrush(QBrush(ring))
            r = self.rect()
            painter.drawEllipse(r)

        # Main dot
        c = QColor(self._color)
        if not self._active:
            c.setAlphaF(0.55)
        painter.setBrush(QBrush(c))
        cx = self.width() // 2
        cy = self.height() // 2
        painter.drawEllipse(cx - self._size // 2, cy - self._size // 2, self._size, self._size)


__all__ = ["animate_height", "fade_swap_stack", "PulseDot"]
