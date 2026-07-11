"""Toast notifications — transient in-window status messages.

The status bar is hidden by default, so controller feedback (device
actions, sync state, errors) surfaces here instead: small cards stacked
in the bottom-right corner of the main window that fade in, wait, and
fade out.

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
    QPropertyAnimation,
    Qt,
    QTimer,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QWidget,
)

TOAST_WIDTH = 340
MARGIN = 16
SPACING = 8
MAX_TOASTS = 4

KIND_INFO = "info"
KIND_SUCCESS = "success"
KIND_ERROR = "error"


class Toast(QFrame):
    """A single toast card. Managed by ToastManager; don't create directly."""

    def __init__(self, message: str, kind: str, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setProperty("kind", kind)
        self.setFixedWidth(TOAST_WIDTH)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        self.label = QLabel(message)
        self.label.setWordWrap(True)
        self.label.setObjectName("ToastLabel")
        layout.addWidget(self.label)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._life_timer = QTimer(self)
        self._life_timer.setSingleShot(True)
        self._life_timer.timeout.connect(self._fade_out)
        self._closing = False

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

    def mousePressEvent(self, _event) -> None:
        self.dismiss()

    def _fade_out(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._life_timer.stop()
        anim = QPropertyAnimation(self._opacity, b"opacity", self)
        anim.setDuration(220)
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

        while len(self._toasts) > MAX_TOASTS:
            self._toasts[0].dismiss()
            self._toasts.pop(0)

        toast.adjustSize()
        self._restack()
        toast.open(duration)

    def error(self, message: str, duration: int = 6000) -> None:
        self.show(message, KIND_ERROR, duration)

    def success(self, message: str, duration: int = 4000) -> None:
        self.show(message, KIND_SUCCESS, duration)

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _coalesce_key(message: str) -> Optional[str]:
        head, sep, _tail = message.partition(":")
        return head.strip().lower() if sep else None

    def _find_coalescible(self, message: str, kind: str) -> Optional[Toast]:
        key = self._coalesce_key(message)
        if key is None:
            return None
        for toast in reversed(self._toasts):
            if toast.property("kind") != kind or toast._closing:
                continue
            if self._coalesce_key(toast.label.text()) == key:
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
        y = area.bottom() - MARGIN
        for toast in reversed(self._toasts):
            if toast._closing:
                continue
            toast.adjustSize()
            h = toast.height()
            toast.move(area.right() - MARGIN - TOAST_WIDTH, y - h)
            toast.raise_()
            y -= h + SPACING

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._window and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
        ):
            self._restack()
        return super().eventFilter(obj, event)


__all__ = ["ToastManager", "Toast", "KIND_INFO", "KIND_SUCCESS", "KIND_ERROR"]
