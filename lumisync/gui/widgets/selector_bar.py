"""WinUI SelectorBar: a slim row of text tabs that switches a page's view.

- Text tabs in sentence case. The current tab has primary text and a short
  accent underline; the others have secondary text that brightens on hover.
  No boxes, no fill and no divider, so it reads on the opaque, Mica and
  Acrylic backdrops alike.
- One tab stop for the whole bar. Left/Right (and Up/Down) move to the
  previous or next tab, Home and End to the first and last.
- A tab can carry the same small attention dot the navigation rail uses.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QSizePolicy, QWidget

from ..theme import qcolor

SELECTOR_HEIGHT = 40
SELECTOR_SPACING = 4
TAB_PADDING_X = 12
INDICATOR_WIDTH = 16
INDICATOR_HEIGHT = 3
BADGE_DIAMETER = 6
BADGE_GAP = 6
FOCUS_RADIUS = 4


class SelectorItem(QAbstractButton):
    """One text tab. The bar owns focus and keyboard handling."""

    def __init__(self, key: str, text: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.key = key
        self.setText(text)
        self.setAccessibleName(text)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._badge = False

    # ------------------------------------------------------------------ badge

    def badge(self) -> bool:
        return self._badge

    def set_badge(self, visible: bool) -> None:
        visible = bool(visible)
        if visible == self._badge:
            return
        self._badge = visible
        self.setAccessibleDescription("Needs attention" if visible else "")
        self.updateGeometry()
        self.update()

    # ----------------------------------------------------------------- layout

    def _text_width(self) -> int:
        return QFontMetrics(self.font()).horizontalAdvance(self.text())

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        width = self._text_width() + 2 * TAB_PADDING_X
        if self._badge:
            width += BADGE_GAP + BADGE_DIAMETER
        return QSize(width, SELECTOR_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        return self.sizeHint()

    # ------------------------------------------------------------------ paint

    def _keyboard_focus(self) -> bool:
        bar = self.parentWidget()
        if bar is None or not bar.hasFocus() or not self.isChecked():
            return False
        window = self.window()
        return window is not None and window.testAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect())
        checked = self.isChecked()
        hovered = self.underMouse()
        if not self.isEnabled():
            color = qcolor("fl_text_disabled")
        elif self.isDown():
            color = qcolor("fl_text_tertiary")
        elif checked or hovered:
            color = qcolor("fl_text")
        else:
            color = qcolor("fl_text_secondary")

        text_width = self._text_width()
        text_rect = QRectF(TAB_PADDING_X, 0, text_width, rect.height() - INDICATOR_HEIGHT)
        painter.setFont(self.font())
        painter.setPen(color)
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignCenter), self.text())

        if self._badge:
            x = TAB_PADDING_X + text_width + BADGE_GAP
            y = text_rect.center().y() - QFontMetrics(self.font()).capHeight() / 2 - 1
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor("fl_accent"))
            painter.drawEllipse(QRectF(x, y, BADGE_DIAMETER, BADGE_DIAMETER))

        if checked:
            # The short accent pill under the text, as in WinUI's SelectorBar.
            indicator = QRectF(
                text_rect.center().x() - INDICATOR_WIDTH / 2,
                rect.height() - INDICATOR_HEIGHT - 2,
                INDICATOR_WIDTH,
                INDICATOR_HEIGHT,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor("fl_accent"))
            painter.drawRoundedRect(indicator, INDICATOR_HEIGHT / 2, INDICATOR_HEIGHT / 2)

        if self._keyboard_focus():
            painter.setPen(QPen(qcolor("fl_text"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), FOCUS_RADIUS, FOCUS_RADIUS)
        painter.end()


class SelectorBar(QWidget):
    """A row of text tabs with one current tab; emits its key on change."""

    current_changed = Signal(str)

    def __init__(self, items: Sequence[Tuple[str, str]], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("SelectorBar")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(SELECTOR_HEIGHT)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SELECTOR_SPACING)
        self._items: Dict[str, SelectorItem] = {}
        self._order: List[str] = []
        self._current: Optional[str] = None
        for key, text in items:
            item = SelectorItem(key, text, self)
            item.clicked.connect(lambda _checked=False, k=key: self._on_clicked(k))
            layout.addWidget(item)
            self._items[key] = item
            self._order.append(key)
        layout.addStretch(1)
        if self._order:
            self.set_current(self._order[0], emit=False)

    # ----------------------------------------------------------------- public

    def keys(self) -> List[str]:
        return list(self._order)

    def item(self, key: str) -> SelectorItem:
        return self._items[key]

    def current_key(self) -> Optional[str]:
        return self._current

    def set_current(self, key: str, *, emit: bool = True) -> None:
        if key not in self._items:
            raise KeyError(key)
        changed = key != self._current
        self._current = key
        for item_key, item in self._items.items():
            item.setChecked(item_key == key)
        if changed and emit:
            self.current_changed.emit(key)

    def set_badge(self, key: str, visible: bool) -> None:
        self._items[key].set_badge(visible)

    def has_badge(self, key: str) -> bool:
        return self._items[key].badge()

    def step(self, offset: int, *, wrap: bool = False) -> None:
        """Move ``offset`` tabs from the current one."""
        if not self._order:
            return
        index = self._order.index(self._current) if self._current in self._order else 0
        target = index + offset
        if wrap:
            target %= len(self._order)
        else:
            target = max(0, min(len(self._order) - 1, target))
        self.set_current(self._order[target])

    # ----------------------------------------------------------------- events

    def _on_clicked(self, key: str) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        # A click on the current tab keeps it checked.
        self.set_current(key)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        key = event.key()
        if event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier:
            super().keyPressEvent(event)
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self.step(-1)
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self.step(1)
        elif key == Qt.Key.Key_Home and self._order:
            self.set_current(self._order[0])
        elif key == Qt.Key.Key_End and self._order:
            self.set_current(self._order[-1])
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().focusInEvent(event)
        self._repaint_items()

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().focusOutEvent(event)
        self._repaint_items()

    def _repaint_items(self) -> None:
        for item in self._items.values():
            item.update()


__all__ = ["SelectorBar", "SelectorItem", "SELECTOR_HEIGHT", "TAB_PADDING_X"]
