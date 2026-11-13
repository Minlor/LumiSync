"""App-wide Fluent (WinUI 3) behaviour the stylesheet cannot express.

``FluentStyle`` is a ``QProxyStyle`` over Fusion that ``apply_theme`` installs
on the application. Its ``polish`` hook runs for every widget, so controls
built anywhere in the app (including files that never import this module)
get the same treatment:

* every ``QComboBox`` gets ``FluentComboDelegate`` and a frameless,
  translucent popup so the rounded flyout corners render;
* menus and tooltips become translucent so their QSS radius renders;
* keyboard focus draws a WinUI-style ring; mouse focus draws nothing.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QModelIndex, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QComboBox,
    QMenu,
    QProxyStyle,
    QStyle,
    QStyledItemDelegate,
    QStyleFactory,
    QStyleOption,
    QStyleOptionViewItem,
    QWidget,
)

from .tokens import TOKENS, qcolor

COMBO_POPUP_NAME = "FluentComboPopup"
COMBO_ITEM_HEIGHT = 36
_UPGRADED = "fluentUpgraded"

# Popup windows are separate top-level windows. Their own stylesheet is the
# only way to style the combo container: QStyleSheetStyle deliberately skips
# QComboBoxPrivateContainer unless the widget carries a stylesheet itself.
_COMBO_POPUP_QSS = """
#FluentComboPopup {
    background: %(fl_flyout)s;
    border: 1px solid %(fl_flyout_stroke)s;
    border-radius: 8px;
}
#FluentComboPopup QAbstractItemView {
    background: transparent;
    border: none;
    padding: 4px;
    outline: 0;
}
#FluentComboPopup QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 6px 2px 6px 0;
}
#FluentComboPopup QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 0.24);
    border-radius: 2px;
    min-height: 24px;
}
#FluentComboPopup QScrollBar::add-line:vertical,
#FluentComboPopup QScrollBar::sub-line:vertical,
#FluentComboPopup QScrollBar::add-page:vertical,
#FluentComboPopup QScrollBar::sub-page:vertical {
    background: transparent;
    border: none;
    height: 0;
}
""" % TOKENS


# --------------------------------------------------------------------- fonts

_families: Optional[set[str]] = None


def _available_families() -> set[str]:
    global _families
    if _families is None:
        _families = set(QFontDatabase.families())
    return _families


def fluent_font(
    pixel_size: int,
    weight: int = 400,
    *,
    display: bool = False,
    base: Optional[QFont] = None,
) -> QFont:
    """A QFont on the Fluent type ramp for custom-painted text.

    Segoe UI Variable ships Semibold as its own family, so a semibold request
    picks that family instead of letting Qt synthesize (or round up to) Bold.
    """
    font = QFont(base) if base is not None else QFont(QApplication.font())
    families = _available_families()
    optical = "Display" if display else "Text"
    candidates = []
    if weight >= 600:
        candidates.append(f"Segoe UI Variable {optical} Semibold")
    candidates += [f"Segoe UI Variable {optical}", "Segoe UI"]
    for family in candidates:
        if family in families:
            font.setFamilies([family])
            break
    font.setPixelSize(pixel_size)
    font.setWeight(QFont.Weight(weight))
    return font


# ------------------------------------------------------------------ combo box

class FluentComboDelegate(QStyledItemDelegate):
    """WinUI ComboBoxItem: 32 px rounded rows, hover fill, and an accent
    pill on the item that is currently chosen."""

    def __init__(self, combo: QComboBox):
        super().__init__(combo)
        self._combo = combo

    def _is_current(self, index: QModelIndex) -> bool:
        combo = self._combo
        return (
            index.row() == combo.currentIndex()
            and index.parent() == combo.rootModelIndex()
        )

    @staticmethod
    def _is_separator(index: QModelIndex) -> bool:
        return index.data(Qt.ItemDataRole.AccessibleDescriptionRole) == "separator"

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802 - Qt API
        if self._is_separator(index):
            return QSize(0, 9)
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        width = option.fontMetrics.horizontalAdvance(text) + 40
        if index.data(Qt.ItemDataRole.DecorationRole) is not None:
            width += 28
        return QSize(width, COMBO_ITEM_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(option.rect)

        if self._is_separator(index):
            painter.setPen(QPen(qcolor("fl_divider"), 1))
            y = rect.center().y()
            painter.drawLine(int(rect.left() + 4), int(y), int(rect.right() - 4), int(y))
            painter.restore()
            return

        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        hovered = bool(
            option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver)
        )
        current = self._is_current(index)
        pill = rect.adjusted(0, 2, 0, -2)

        fill: Optional[QColor] = None
        if current:
            fill = qcolor("fl_subtle_selected")
            if hovered and enabled:
                fill = QColor(255, 255, 255, 31)
        elif hovered and enabled:
            fill = qcolor("fl_subtle_hover")
        if fill is not None:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(pill, 4, 4)
        if current:
            bar = QRectF(pill.left(), pill.center().y() - 8, 3, 16)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor("fl_accent"))
            painter.drawRoundedRect(bar, 1.5, 1.5)

        x = pill.left() + 12
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon is not None and hasattr(icon, "paint"):
            icon_rect = QRect(int(x), int(pill.center().y() - 8), 16, 16)
            icon.paint(painter, icon_rect)
            x += 26

        painter.setFont(option.font)
        painter.setPen(qcolor("fl_text") if enabled else qcolor("fl_text_disabled"))
        text_rect = QRectF(x, pill.top(), pill.right() - x - 8, pill.height())
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        elided = option.fontMetrics.elidedText(
            text, Qt.TextElideMode.ElideRight, int(text_rect.width())
        )
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            elided,
        )
        painter.restore()


def upgrade_combo(combo: QComboBox) -> None:
    """Give a combo the Fluent delegate and a rounded, translucent popup.

    Idempotent. A delegate an app deliberately installed is left alone; only
    Qt's two built-in combo delegates are replaced.
    """
    if combo.property(_UPGRADED):
        return
    try:
        # view() creates the popup container, and creating it force-installs
        # Qt's default delegate, so the container must exist first.
        view = combo.view()
    except RuntimeError:
        return
    if view is None:
        return
    delegate = combo.itemDelegate()
    class_name = delegate.metaObject().className() if delegate is not None else ""
    if delegate is None or class_name in ("QComboMenuDelegate", "QComboBoxDelegate"):
        combo.setItemDelegate(FluentComboDelegate(combo))
    view.setMouseTracking(True)
    view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    container = view.parentWidget()
    if container is not None and container.isWindow():
        container.setObjectName(COMBO_POPUP_NAME)
        if not container.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
            container.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
            container.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
            container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        container.setStyleSheet(_COMBO_POPUP_QSS)
    combo.setProperty(_UPGRADED, True)


def _make_translucent_popup(widget: QWidget) -> None:
    """Menus and tooltips: let the QSS radius show instead of square corners.

    Must happen before the native window exists; Qt polishes these popups
    before creating them, so this is the moment.
    """
    if widget.property(_UPGRADED) or widget.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
        return
    widget.setProperty(_UPGRADED, True)
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
    widget.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    widget.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)


# --------------------------------------------------------------------- style

def keyboard_focus(option: QStyleOption) -> bool:
    state = option.state
    return bool(state & QStyle.StateFlag.State_HasFocus) and bool(
        state & QStyle.StateFlag.State_KeyboardFocusChange
    )


def paint_focus_ring(painter: QPainter, rect: QRectF, radius: float = 4.0) -> None:
    """WinUI focus visual: a 2 px light ring hugging the control."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(qcolor("fl_text"), 2))
    painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)
    painter.restore()


class FluentStyle(QProxyStyle):
    """Fusion with Fluent focus visuals and central control upgrades."""

    def __init__(self) -> None:
        base = QStyleFactory.create("Fusion")
        if base is not None:
            super().__init__(base)
        else:  # pragma: no cover - Fusion ships with every Qt build
            super().__init__()

    def polish(self, arg):  # type: ignore[override]  # noqa: D401 - Qt overloads
        if isinstance(arg, QComboBox):
            upgrade_combo(arg)
        elif isinstance(arg, QMenu):
            _make_translucent_popup(arg)
        elif isinstance(arg, QWidget) and arg.metaObject().className() == "QTipLabel":
            _make_translucent_popup(arg)
        return super().polish(arg)

    def drawPrimitive(self, element, option, painter, widget=None):  # noqa: N802 - Qt API
        if element == QStyle.PrimitiveElement.PE_FrameFocusRect:
            # Item views and labels never get the dotted Fusion rectangle.
            if isinstance(widget, (QAbstractButton, QComboBox)) and keyboard_focus(option):
                radius = 20.0 if widget.objectName() == "DevicePowerButton" else 4.0
                paint_focus_ring(painter, QRectF(widget.rect()), radius)
            return
        super().drawPrimitive(element, option, painter, widget)

    def styleHint(self, hint, option=None, widget=None, returnData=None):  # noqa: N802 - Qt API
        if hint == QStyle.StyleHint.SH_ToolTipLabel_Opacity:
            return 255
        if hint == QStyle.StyleHint.SH_ComboBox_PopupFrameStyle:
            return 0
        return super().styleHint(hint, option, widget, returnData)


def build_palette() -> QPalette:
    """A Qt palette mirroring the Fluent tokens for unstyled painting."""
    palette = QPalette()
    base = qcolor("fl_base")
    text = qcolor("fl_text")
    accent = qcolor("fl_accent")
    roles = QPalette.ColorRole
    palette.setColor(roles.Window, base)
    palette.setColor(roles.WindowText, text)
    palette.setColor(roles.Base, QColor("#2B2B2B"))
    palette.setColor(roles.AlternateBase, QColor("#262626"))
    palette.setColor(roles.Text, text)
    palette.setColor(roles.PlaceholderText, qcolor("text_disabled"))
    palette.setColor(roles.Button, QColor("#2D2D2D"))
    palette.setColor(roles.ButtonText, text)
    palette.setColor(roles.BrightText, text)
    palette.setColor(roles.Highlight, accent)
    palette.setColor(roles.HighlightedText, qcolor("fl_accent_on"))
    palette.setColor(roles.Link, qcolor("fl_accent_text"))
    palette.setColor(roles.ToolTipBase, QColor("#2C2C2C"))
    palette.setColor(roles.ToolTipText, text)
    palette.setColor(roles.Mid, QColor("#3A3A3A"))
    palette.setColor(roles.Midlight, QColor("#454545"))
    palette.setColor(roles.Dark, QColor("#141414"))
    palette.setColor(roles.Shadow, QColor("#000000"))
    for role in (roles.WindowText, roles.Text, roles.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#5D5D5D"))
    palette.setColor(QPalette.ColorGroup.Disabled, roles.PlaceholderText, QColor("#5D5D5D"))
    palette.setColor(QPalette.ColorGroup.Inactive, roles.Highlight, accent)
    return palette


__all__ = [
    "COMBO_ITEM_HEIGHT",
    "COMBO_POPUP_NAME",
    "FluentComboDelegate",
    "FluentStyle",
    "build_palette",
    "fluent_font",
    "keyboard_focus",
    "paint_focus_ring",
    "upgrade_combo",
]
