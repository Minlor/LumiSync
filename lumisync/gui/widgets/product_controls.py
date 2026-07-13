"""Shared controls with product-level interaction and visual affordances."""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QWidget,
)

from ..theme.tokens import qcolor


class ToggleSwitch(QAbstractButton):
    """An animated, full-row switch for a binary preference."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(40)
        self.setAccessibleName(text)

        self._position = 0.0
        self._animation = QPropertyAnimation(self, b"position", self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate_state)
        self._update_accessible_state(False)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        text_width = self.fontMetrics().horizontalAdvance(self.text())
        return QSize(max(180, text_width + 76), 40)

    def get_position(self) -> float:
        return self._position

    def set_position(self, value: float) -> None:
        self._position = max(0.0, min(1.0, float(value)))
        self.update()

    position = Property(float, get_position, set_position)

    def _animate_state(self, checked: bool) -> None:
        self._update_accessible_state(checked)
        target = 1.0 if checked else 0.0
        if not self.isVisible():
            self._animation.stop()
            self.set_position(target)
            return
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(target)
        self._animation.start()

    def _update_accessible_state(self, checked: bool) -> None:
        self.setAccessibleDescription("On" if checked else "Off")
        self.setToolTip(f"{self.text()} — {'On' if checked else 'Off'}")

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        enabled = self.isEnabled()
        checked = self.isChecked()
        hovered = self.underMouse() and enabled
        content = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        track_width = 46.0
        track_height = 24.0
        track = QRectF(
            content.right() - track_width,
            content.center().y() - track_height / 2,
            track_width,
            track_height,
        )

        text_color = qcolor("text") if enabled else qcolor("text_disabled")
        painter.setPen(text_color)
        text_rect = content.adjusted(0, 0, -(track_width + 12), 0)
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.text(),
        )

        if not enabled:
            track_fill = qcolor("surface")
            track_border = qcolor("border_dim")
        elif checked:
            track_fill = qcolor("accent_bright") if hovered else qcolor("accent")
            track_border = qcolor("accent_bright")
        else:
            track_fill = qcolor("hover") if hovered else qcolor("surface_alt")
            track_border = qcolor("accent") if hovered else qcolor("border_strong")

        if self.hasFocus() and enabled:
            focus_rect = track.adjusted(-3, -3, 3, 3)
            painter.setPen(QPen(qcolor("accent_bright"), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(focus_rect, 15, 15)

        painter.setPen(QPen(track_border, 1))
        painter.setBrush(track_fill)
        painter.drawRoundedRect(track, track_height / 2, track_height / 2)

        knob_size = 18.0
        knob_left = track.left() + 3 + self._position * (track_width - knob_size - 6)
        knob = QRectF(
            knob_left,
            track.center().y() - knob_size / 2,
            knob_size,
            knob_size,
        )
        knob_fill = qcolor("text") if enabled else qcolor("text_disabled")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob_fill)
        painter.drawEllipse(knob)


class ProductSlider(QSlider):
    """Slider with a generous hit area and direct click-to-position behavior."""

    def __init__(
        self,
        orientation: Qt.Orientation = Qt.Orientation.Horizontal,
        parent: QWidget | None = None,
    ):
        super().__init__(orientation, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(32)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.orientation() == Qt.Orientation.Horizontal
        ):
            option = QStyleOptionSlider()
            self.initStyleOption(option)
            handle = self.style().subControlRect(
                QStyle.ComplexControl.CC_Slider,
                option,
                QStyle.SubControl.SC_SliderHandle,
                self,
            )
            point = event.position().toPoint()
            if not handle.contains(point):
                groove = self.style().subControlRect(
                    QStyle.ComplexControl.CC_Slider,
                    option,
                    QStyle.SubControl.SC_SliderGroove,
                    self,
                )
                slider_min = groove.left()
                slider_max = groove.right() - handle.width() + 1
                click_position = point.x() - handle.width() // 2
                value = QStyle.sliderValueFromPosition(
                    self.minimum(),
                    self.maximum(),
                    click_position - slider_min,
                    max(1, slider_max - slider_min),
                    option.upsideDown,
                )
                self.setValue(value)
                event.accept()
                return
        super().mousePressEvent(event)


class ProductComboBox(QComboBox):
    """Consistently sized dropdown with a clear interactive hit area."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(40)
        self.setMaxVisibleItems(12)


__all__ = ["ProductComboBox", "ProductSlider", "ToggleSwitch"]
