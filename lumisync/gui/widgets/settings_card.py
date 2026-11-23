"""Windows 11 Settings-style building blocks.

* ``SettingsGroup`` — a 14 px semibold section header over a stack of cards
  (4 px apart), with optional header actions on the right.
* ``SettingsCard`` — 64 px minimum, 16 px padding, 8 px radius: an optional
  20 px icon, a title with a wrapping description, and a trailing control.
* ``SettingsList`` — several rows inside one card, separated by hairlines
  (the reference app's list style; used for short lists such as lights).
* ``SettingsExpander`` — a card whose chevron reveals extra content inside
  the same card, animated.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..theme.tokens import qcolor
from .fluent import FluentIcon

CARD_MIN_HEIGHT = 64
CARD_PADDING = 16


def _label(text: str, role: str, *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", role)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(wrap)
    return label


class _CardContent(QWidget):
    """Icon + title/description + trailing control, shared by every card."""

    def __init__(
        self,
        icon: Optional[str],
        title: str,
        description: Optional[str],
        control: Optional[QWidget],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(CARD_PADDING, 10, CARD_PADDING, 10)
        layout.setSpacing(16)
        self._layout = layout

        # Widgets are parented before any visibility change: setVisible(True)
        # on a parentless widget makes it a top-level window, which flashes
        # on screen for a frame before the layout adopts it.
        self.icon = FluentIcon(icon or "", "fl_text_secondary", 20)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self.icon.setVisible(bool(icon))

        text_host = QWidget(self)
        text = QVBoxLayout(text_host)
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(1)
        self.title_label = _label(title, "settingsTitle", wrap=True)
        self.description_label = _label(description or "", "settingsDescription", wrap=True)
        text.addWidget(self.title_label)
        text.addWidget(self.description_label)
        self.description_label.setVisible(bool(description))
        text_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        text_host.setMinimumWidth(120)
        self.text_host = text_host
        layout.addWidget(text_host, 1, Qt.AlignmentFlag.AlignVCenter)

        self.trailing = QHBoxLayout()
        self.trailing.setContentsMargins(0, 0, 0, 0)
        self.trailing.setSpacing(8)
        layout.addLayout(self.trailing)
        self._control: Optional[QWidget] = None
        if control is not None:
            self.set_control(control)
        self.setMinimumHeight(CARD_MIN_HEIGHT - 2)

    def set_control(self, control: Optional[QWidget]) -> None:
        if self._control is not None:
            self.trailing.removeWidget(self._control)
            self._control.setParent(None)
        self._control = control
        if control is not None:
            self.trailing.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
            if not control.accessibleName():
                control.setAccessibleName(self.title_label.text())

    def control(self) -> Optional[QWidget]:
        return self._control


class SettingsCard(QFrame):
    """A Fluent settings card (or, inside a ``SettingsList``, one row)."""

    def __init__(
        self,
        icon: Optional[str] = None,
        title: str = "",
        description: Optional[str] = None,
        control: Optional[QWidget] = None,
        parent: Optional[QWidget] = None,
        *,
        in_list: bool = False,
    ):
        super().__init__(parent)
        self.setObjectName("SettingsRow" if in_list else "SettingsCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self._content = _CardContent(icon, title, description, control, self)
        layout.addWidget(self._content)
        self.setMinimumHeight(CARD_MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    # Convenience accessors -------------------------------------------------
    @property
    def title_label(self) -> QLabel:
        return self._content.title_label

    @property
    def description_label(self) -> QLabel:
        return self._content.description_label

    @property
    def icon(self) -> FluentIcon:
        return self._content.icon

    def set_title(self, text: str) -> None:
        self._content.title_label.setText(text)

    def set_description(self, text: Optional[str]) -> None:
        self._content.description_label.setText(text or "")
        self._content.description_label.setVisible(bool(text))

    def set_control(self, control: Optional[QWidget]) -> None:
        self._content.set_control(control)

    def control(self) -> Optional[QWidget]:
        return self._content.control()

    def add_trailing(self, widget: QWidget) -> None:
        """Add another trailing widget before the control (e.g. a value)."""
        self._content.trailing.insertWidget(0, widget, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_title_role(self, role: str) -> None:
        self._content.title_label.setProperty("role", role)
        self._content.title_label.style().unpolish(self._content.title_label)
        self._content.title_label.style().polish(self._content.title_label)


class SettingsList(QFrame):
    """One card holding several rows separated by hairline dividers."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("SettingsList")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(1, 1, 1, 1)
        self._layout.setSpacing(0)
        self._rows: List[QWidget] = []
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    def add_row(self, row: QWidget) -> QWidget:
        if self._rows:
            divider = QFrame()
            divider.setObjectName("SettingsDivider")
            divider.setFixedHeight(1)
            self._layout.addWidget(divider)
        self._layout.addWidget(row)
        self._rows.append(row)
        self._update_positions()
        return row

    def _update_positions(self) -> None:
        """Tell rows where they sit so hover fills follow the card corners."""
        count = len(self._rows)
        for index, row in enumerate(self._rows):
            position = (
                "only" if count == 1
                else "first" if index == 0
                else "last" if index == count - 1
                else "middle"
            )
            if row.property("position") != position:
                row.setProperty("position", position)
                row.style().unpolish(row)
                row.style().polish(row)

    def rows(self) -> List[QWidget]:
        return list(self._rows)

    def clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        self._rows = []


class SettingsGroup(QWidget):
    """A titled stack of settings cards."""

    def __init__(self, title: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("SettingsGroup")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        self._root = root

        header = QHBoxLayout()
        header.setContentsMargins(1, 0, 0, 4)
        header.setSpacing(8)
        self.header_label = _label(title, "groupHeader")
        header.addWidget(self.header_label, 0, Qt.AlignmentFlag.AlignBottom)
        header.addStretch(1)
        self._header = header
        root.addLayout(header)
        # Only now is the label parented; see _CardContent on why it matters.
        self.header_label.setVisible(bool(title))

    def set_header_visible(self, visible: bool) -> None:
        """Show or hide the title row without removing it.

        A hidden row takes no space: the first card starts where the title
        would have been.
        """
        visible = bool(visible) and bool(self.header_label.text())
        self.header_label.setVisible(visible)
        self._header.setContentsMargins(1, 0, 0, 4 if visible else 0)

    def header_visible(self) -> bool:
        return not self.header_label.isHidden()

    def add_header_action(self, widget: QWidget) -> None:
        self._header.addWidget(widget, 0, Qt.AlignmentFlag.AlignBottom)

    def add_card(self, card: QWidget) -> QWidget:
        self._root.addWidget(card)
        return card

    def add_widget(self, widget: QWidget) -> QWidget:
        return self.add_card(widget)

    def insert_widget(self, index: int, widget: QWidget) -> QWidget:
        # Index 0 is the header row.
        self._root.insertWidget(index + 1, widget)
        return widget


class _ExpanderHeader(QAbstractButton):
    """The always-visible part of an expander: a card row plus a chevron."""

    def __init__(self, content: _CardContent, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 40, 0)
        layout.setSpacing(0)
        layout.addWidget(content)
        self._content = content
        self.setMinimumHeight(CARD_MIN_HEIGHT - 2)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.rounded_bottom = True

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        hint = self._content.sizeHint()
        return QSize(hint.width() + 40, max(CARD_MIN_HEIGHT - 2, hint.height()))

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        if self.isEnabled() and (self.underMouse() or self.isDown()):
            path = QPainterPath()
            radius = 7.0
            if self.rounded_bottom:
                path.addRoundedRect(rect, radius, radius)
            else:
                path.moveTo(rect.left(), rect.bottom())
                path.lineTo(rect.left(), rect.top() + radius)
                path.quadTo(rect.left(), rect.top(), rect.left() + radius, rect.top())
                path.lineTo(rect.right() - radius, rect.top())
                path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + radius)
                path.lineTo(rect.right(), rect.bottom())
                path.closeSubpath()
            painter.fillPath(
                path,
                qcolor("fl_control_pressed") if self.isDown() else qcolor("fl_subtle_hover"),
            )
        # Chevron (down when collapsed, up when expanded) in a 32 px slot.
        color = qcolor("fl_text_secondary") if self.isEnabled() else qcolor("fl_text_disabled")
        painter.setPen(QPen(color, 1.3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        cx = rect.right() - CARD_PADDING - 8
        cy = rect.center().y()
        d = 4.0 if not self.isChecked() else -4.0
        painter.drawPolyline([
            QPointF(cx - 5, cy - d / 2),
            QPointF(cx, cy + d / 2),
            QPointF(cx + 5, cy - d / 2),
        ])
        if self.hasFocus() and self.window() is not None and self.window().testAttribute(
            Qt.WidgetAttribute.WA_KeyboardFocusChange
        ):
            painter.setPen(QPen(qcolor("fl_text"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 7, 7)


class SettingsExpander(QFrame):
    """A settings card whose header reveals ``content`` inside the card."""

    expanded_changed = Signal(bool)

    def __init__(
        self,
        icon: Optional[str] = None,
        title: str = "",
        description: Optional[str] = None,
        content: Optional[QWidget] = None,
        control: Optional[QWidget] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("SettingsCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        self._card = _CardContent(icon, title, description, control, self)
        self.header = _ExpanderHeader(self._card, self)
        layout.addWidget(self.header)

        self.body = QFrame()
        self.body.setObjectName("SettingsExpanderBody")
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(CARD_PADDING + 36, 16, CARD_PADDING, 16)
        body_layout.setSpacing(8)
        self._body_layout = body_layout
        if content is not None:
            body_layout.addWidget(content)
        self.body.setMaximumHeight(0)
        self.body.setVisible(False)
        layout.addWidget(self.body)
        self._expanding = False
        self._animation = QPropertyAnimation(self.body, b"maximumHeight", self)
        self._animation.finished.connect(self._on_animation_finished)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    @property
    def title_label(self) -> QLabel:
        return self._card.title_label

    def set_content_margins(self, left: int, top: int, right: int, bottom: int) -> None:
        self._body_layout.setContentsMargins(left, top, right, bottom)

    def add_content(self, widget: QWidget) -> None:
        self._body_layout.addWidget(widget)

    def is_expanded(self) -> bool:
        return self.header.isChecked()

    def set_expanded(self, expanded: bool, *, animate: bool = True, target_height: int = 0) -> None:
        blocked = self.header.blockSignals(True)
        self.header.setChecked(expanded)
        self.header.blockSignals(blocked)
        self.header.rounded_bottom = not expanded
        self.header.update()
        self._animation.stop()
        self._expanding = expanded
        if expanded:
            self.body.setVisible(True)
            end = target_height or max(80, self.body.sizeHint().height())
        else:
            end = 0
        if not animate or not self.isVisible():
            self._on_animation_finished()
            self.expanded_changed.emit(expanded)
            return
        self._animation.setDuration(220 if expanded else 170)
        self._animation.setEasingCurve(
            QEasingCurve.Type.OutCubic if expanded else QEasingCurve.Type.InCubic
        )
        start = self.body.maximumHeight() if self.body.isVisible() else 0
        self._animation.setStartValue(min(start, max(end, self.body.height())))
        self._animation.setEndValue(end)
        self._animation.start()
        self.expanded_changed.emit(expanded)

    def _on_animation_finished(self) -> None:
        if self._expanding:
            # Let the content grow afterwards (e.g. when the window gets taller).
            self.body.setVisible(True)
            self.body.setMaximumHeight(16777215)
        else:
            self.body.setVisible(False)
            self.body.setMaximumHeight(0)


__all__ = [
    "SettingsCard",
    "SettingsExpander",
    "SettingsGroup",
    "SettingsList",
]
