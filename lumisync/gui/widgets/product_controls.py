"""Shared Fluent (WinUI 3) controls: ToggleSwitch, Slider and ComboBox.

``ToggleSwitch`` and ``ProductSlider`` paint themselves from the ``fl_*``
tokens so they look identical wherever they are used, independent of the
application stylesheet. ``ProductComboBox`` is a plain QComboBox sized to
the Fluent 32 px control height; its flyout comes from ``FluentStyle``.
"""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QSizePolicy,
    QSlider,
    QStyle,
    QWidget,
)

from ..theme.tokens import qcolor

SWITCH_WIDTH = 40
SWITCH_HEIGHT = 20
SWITCH_TEXT_GAP = 12
CONTROL_HEIGHT = 32


def _keyboard_focus(widget: QWidget) -> bool:
    window = widget.window()
    return widget.hasFocus() and window is not None and window.testAttribute(
        Qt.WidgetAttribute.WA_KeyboardFocusChange
    )


def _alpha(color: QColor, alpha: float) -> QColor:
    result = QColor(color)
    result.setAlphaF(alpha)
    return result


class ToggleSwitch(QAbstractButton):
    """WinUI ToggleSwitch: a 40×20 pill with a sliding knob.

    The optional text sits to the right of the switch and wraps. Settings
    cards use it without text, as the trailing control of a card.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        policy = QSizePolicy(
            QSizePolicy.Policy.Preferred if text else QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Preferred if text else QSizePolicy.Policy.Fixed,
        )
        policy.setHeightForWidth(bool(text))
        self.setSizePolicy(policy)
        self.setMinimumHeight(CONTROL_HEIGHT)
        if text:
            self.setAccessibleName(text)

        self._position = 0.0
        self._hover = 0.0
        self._animation = QPropertyAnimation(self, b"position", self)
        self._animation.setDuration(167)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._hover_animation = QPropertyAnimation(self, b"hoverProgress", self)
        self._hover_animation.setDuration(120)
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate_state)
        self._update_accessible_state(False)

    # ------------------------------------------------------------- geometry

    def _text_width(self) -> int:
        text = self.text()
        return self.fontMetrics().horizontalAdvance(text) if text else 0

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        if not self.text():
            return QSize(SWITCH_WIDTH + 4, CONTROL_HEIGHT)
        return QSize(SWITCH_WIDTH + SWITCH_TEXT_GAP + self._text_width() + 4, CONTROL_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        if not self.text():
            return QSize(SWITCH_WIDTH + 4, CONTROL_HEIGHT)
        return QSize(SWITCH_WIDTH + SWITCH_TEXT_GAP + 80, CONTROL_HEIGHT)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return bool(self.text())

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        if not self.text():
            return CONTROL_HEIGHT
        available = max(1, width - SWITCH_WIDTH - SWITCH_TEXT_GAP - 4)
        bounds = self.fontMetrics().boundingRect(
            QRect(0, 0, available, 10000), int(Qt.TextFlag.TextWordWrap), self.text()
        )
        return max(CONTROL_HEIGHT, bounds.height() + 10)

    def _track_rect(self) -> QRectF:
        return QRectF(2, (self.height() - SWITCH_HEIGHT) / 2, SWITCH_WIDTH, SWITCH_HEIGHT)

    def hitButton(self, pos) -> bool:  # noqa: N802 - Qt API
        return self.rect().contains(pos)

    # ------------------------------------------------------------ animation

    def get_position(self) -> float:
        return self._position

    def set_position(self, value: float) -> None:
        self._position = max(0.0, min(1.0, float(value)))
        self.update()

    position = Property(float, get_position, set_position)

    def get_hover(self) -> float:
        return self._hover

    def set_hover(self, value: float) -> None:
        self._hover = max(0.0, min(1.0, float(value)))
        self.update()

    hoverProgress = Property(float, get_hover, set_hover)

    def _animate_state(self, checked: bool) -> None:
        self._update_accessible_state(checked)
        target = 1.0 if checked else 0.0
        self._animation.stop()
        if not self.isVisible():
            self.set_position(target)
            return
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(target)
        self._animation.start()

    def _animate_hover(self, hovered: bool) -> None:
        self._hover_animation.stop()
        self._hover_animation.setStartValue(self._hover)
        self._hover_animation.setEndValue(1.0 if hovered else 0.0)
        self._hover_animation.start()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._animate_hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._animate_hover(False)
        super().leaveEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        # A switch checked while hidden must not animate on first show.
        if not self._animation.state() == QPropertyAnimation.State.Running:
            self._position = 1.0 if self.isChecked() else 0.0
        super().showEvent(event)

    def _update_accessible_state(self, checked: bool) -> None:
        state = "On" if checked else "Off"
        self.setAccessibleDescription(state)
        if self.text():
            self.setToolTip(f"{self.text()} · {state}")

    # ---------------------------------------------------------------- paint

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        enabled = self.isEnabled()
        pressed = self.isDown()
        track = self._track_rect()
        on = self._position

        # Track: an outlined pill that fills with the accent as it turns on.
        radius = SWITCH_HEIGHT / 2
        if enabled:
            off_border = qcolor("fl_control_strong")
            off_fill = QColor(255, 255, 255, int(10 + 10 * self._hover))
            on_fill = qcolor("fl_accent_hover") if self._hover > 0.5 else qcolor("fl_accent")
            if pressed:
                on_fill = qcolor("fl_accent_pressed")
        else:
            off_border = QColor(255, 255, 255, 41)
            off_fill = QColor(0, 0, 0, 0)
            on_fill = QColor(255, 255, 255, 41)

        if on < 1.0:
            painter.setPen(QPen(off_border, 1))
            painter.setBrush(off_fill)
            painter.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
        if on > 0.0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_alpha(on_fill, on_fill.alphaF() * on))
            painter.drawRoundedRect(track, radius, radius)

        # Knob: 12 px, 14 px on hover; stretches a little while pressed.
        knob_h = 12.0 + 2.0 * self._hover
        if not enabled:
            knob_h = 12.0
        knob_w = knob_h + (3.0 if pressed and enabled else 0.0)
        inset = (SWITCH_HEIGHT - knob_h) / 2
        travel = track.width() - knob_w - 2 * inset
        knob = QRectF(
            track.left() + inset + travel * on,
            track.center().y() - knob_h / 2,
            knob_w,
            knob_h,
        )
        if enabled:
            off_knob = qcolor("fl_text_secondary")
            on_knob = qcolor("fl_accent_on")
        else:
            off_knob = QColor(255, 255, 255, 93)
            on_knob = QColor(255, 255, 255, 135)
        knob_color = QColor(
            int(off_knob.red() + (on_knob.red() - off_knob.red()) * on),
            int(off_knob.green() + (on_knob.green() - off_knob.green()) * on),
            int(off_knob.blue() + (on_knob.blue() - off_knob.blue()) * on),
            int(off_knob.alpha() + (on_knob.alpha() - off_knob.alpha()) * on),
        )
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob_color)
        painter.drawRoundedRect(knob, knob_h / 2, knob_h / 2)

        if enabled and _keyboard_focus(self):
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(qcolor("fl_text"), 2))
            painter.drawRoundedRect(track.adjusted(-2, -2, 2, 2), radius + 2, radius + 2)

        if self.text():
            painter.setPen(qcolor("fl_text") if enabled else qcolor("fl_text_disabled"))
            left = track.right() + SWITCH_TEXT_GAP
            text_rect = QRectF(left, 0, self.width() - left, self.height())
            painter.drawText(
                text_rect,
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap),
                self.text(),
            )


class ProductSlider(QSlider):
    """WinUI Slider: 4 px track, a 20 px thumb with an accent dot that grows
    on hover and shrinks while dragged. Click anywhere to jump there."""

    THUMB_RADIUS = 10.0

    def __init__(
        self,
        orientation: Qt.Orientation = Qt.Orientation.Horizontal,
        parent: QWidget | None = None,
    ):
        super().__init__(orientation, parent)
        self.setMinimumHeight(CONTROL_HEIGHT)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._inner = 6.0
        self._dragging = False
        self._inner_animation = QPropertyAnimation(self, b"innerRadius", self)
        self._inner_animation.setDuration(110)
        self._inner_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    # ------------------------------------------------------------ geometry

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        hint = super().sizeHint()
        if self.orientation() == Qt.Orientation.Horizontal:
            return QSize(max(hint.width(), 160), CONTROL_HEIGHT)
        return hint

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        if self.orientation() == Qt.Orientation.Horizontal:
            return QSize(48, CONTROL_HEIGHT)
        return super().minimumSizeHint()

    def _span(self) -> tuple[float, float]:
        r = self.THUMB_RADIUS
        return r + 1, max(r + 1, self.width() - r - 1)

    def _ratio(self) -> float:
        span = self.maximum() - self.minimum()
        if span <= 0:
            return 0.0
        ratio = (self.sliderPosition() - self.minimum()) / span
        if self.invertedAppearance():
            ratio = 1.0 - ratio
        return max(0.0, min(1.0, ratio))

    def _value_at(self, x: float) -> int:
        left, right = self._span()
        ratio = (x - left) / max(1.0, right - left)
        ratio = max(0.0, min(1.0, ratio))
        if self.invertedAppearance():
            ratio = 1.0 - ratio
        return QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), int(round(ratio * 10000)), 10000
        )

    # --------------------------------------------------------- animation

    def get_inner(self) -> float:
        return self._inner

    def set_inner(self, value: float) -> None:
        self._inner = float(value)
        self.update()

    innerRadius = Property(float, get_inner, set_inner)

    def _animate_inner(self) -> None:
        if self._dragging:
            target = 5.0
        elif self.underMouse() and self.isEnabled():
            target = 7.0
        else:
            target = 6.0
        if abs(target - self._inner) < 0.01:
            return
        self._inner_animation.stop()
        self._inner_animation.setStartValue(self._inner)
        self._inner_animation.setEndValue(target)
        self._inner_animation.start()

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().enterEvent(event)
        self._animate_inner()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().leaveEvent(event)
        self._animate_inner()

    # ------------------------------------------------------------- mouse

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.orientation() == Qt.Orientation.Horizontal
            and self.isEnabled()
        ):
            self._dragging = True
            self.setSliderDown(True)
            self.setSliderPosition(self._value_at(event.position().x()))
            self._animate_inner()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._dragging:
            self.setSliderPosition(self._value_at(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt API
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.setSliderPosition(self._value_at(event.position().x()))
            self.setSliderDown(False)
            self._animate_inner()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------- paint

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt API
        if self.orientation() != Qt.Orientation.Horizontal:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        enabled = self.isEnabled()
        left, right = self._span()
        cy = self.height() / 2
        thumb_x = left + (right - left) * self._ratio()

        track = QRectF(left, cy - 2, right - left, 4)
        rest = qcolor("fl_control_strong") if enabled else QColor(255, 255, 255, 41)
        fill = qcolor("fl_accent") if enabled else QColor(255, 255, 255, 41)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(rest)
        painter.drawRoundedRect(track, 2, 2)
        if thumb_x > left:
            filled = QRectF(left, cy - 2, thumb_x - left, 4)
            painter.setBrush(fill)
            if self.invertedAppearance():
                filled = QRectF(thumb_x, cy - 2, right - thumb_x, 4)
            painter.drawRoundedRect(filled, 2, 2)

        centre = QPointF(thumb_x, cy)
        r = self.THUMB_RADIUS
        if enabled and _keyboard_focus(self):
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(qcolor("fl_text"), 2))
            painter.drawEllipse(centre, r + 2.5, r + 2.5)
        painter.setPen(QPen(qcolor("fl_control_stroke_top"), 1))
        painter.setBrush(qcolor("fl_control_solid"))
        painter.drawEllipse(centre, r - 0.5, r - 0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        if enabled:
            inner = qcolor("fl_accent_pressed") if self._dragging else (
                qcolor("fl_accent_hover") if self._inner > 6.5 else qcolor("fl_accent")
            )
        else:
            inner = QColor(255, 255, 255, 93)
        painter.setBrush(inner)
        painter.drawEllipse(centre, self._inner, self._inner)


class ProductComboBox(QComboBox):
    """A 32 px Fluent ComboBox (the flyout comes from FluentStyle)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(CONTROL_HEIGHT)
        self.setMaxVisibleItems(12)


__all__ = ["ProductComboBox", "ProductSlider", "ToggleSwitch"]
