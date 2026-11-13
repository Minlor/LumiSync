"""Fluent navigation rail (WinUI NavigationView, LeftCompact) + page stack.

- An icon-only rail: each destination is a 40×36 pill with a 20 px icon.
  Hover shows a subtle fill, the current page a slightly stronger one plus a
  3×16 accent bar at the pill's left edge that slides between items.
- Page names live in tooltips and the accessibility tree, not under icons.
- Bottom-pinned items (Settings), and a content stack with cross-fades.
- The rail shares the window background with the content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from ..resources import ResourceManager
from ..theme import qcolor
from ..utils.animations import fade_swap_stack


RAIL_WIDTH = 64
ICON_ONLY_RAIL_WIDTH = 64
RAIL_ITEM_HEIGHT = 44
RAIL_PILL_WIDTH = 40
RAIL_PILL_HEIGHT = 36
RAIL_ITEM_SIZE = RAIL_PILL_HEIGHT  # Backwards-compatible name for the pill height.
RAIL_ICON_SIZE = 20
INDICATOR_WIDTH = 3
INDICATOR_HEIGHT = 16


@dataclass(frozen=True)
class NavItem:
    key: str
    title: str
    icon: object
    widget: QWidget


def pill_rect(rect: QRect) -> QRect:
    """The 40×36 hover/selection pill centred in a rail item."""
    return QRect(
        rect.center().x() - RAIL_PILL_WIDTH // 2 + 1,
        rect.center().y() - RAIL_PILL_HEIGHT // 2 + 1,
        RAIL_PILL_WIDTH,
        RAIL_PILL_HEIGHT,
    )


class NavRailItemDelegate(QStyledItemDelegate):
    """Paints a rail item: the pill (hover/selected) and the tinted icon.

    The accent location bar is an animated overlay owned by the shell. Page
    labels are kept as item data (``LABEL_ROLE``) for callers, never painted.
    """

    SVG_ROLE = int(Qt.ItemDataRole.UserRole) + 1
    # Truthy when the page wants attention (for example an available update).
    BADGE_ROLE = int(Qt.ItemDataRole.UserRole) + 2
    # Short name for the page ("Monitor" for "Monitor Sync"); not painted.
    LABEL_ROLE = int(Qt.ItemDataRole.UserRole) + 3

    def __init__(self, parent=None, *, icon_only: bool = True):
        super().__init__(parent)
        self._icon_only = icon_only
        self._pixmap_cache: Dict[Tuple[str, int, int, float], QPixmap] = {}

    def _pill_rect(self, rect: QRect) -> QRect:
        return pill_rect(rect)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        pill = QRectF(self._pill_rect(rect))

        if selected or hovered:
            if selected and hovered:
                fill = qcolor("fl_subtle_selected")
                fill.setAlpha(fill.alpha() + 8)
            else:
                fill = qcolor("fl_subtle_selected" if selected else "fl_subtle_hover")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            painter.drawRoundedRect(pill, 4, 4)

        widget = option.widget
        window = widget.window() if widget is not None else None
        keyboard = window is not None and window.testAttribute(
            Qt.WidgetAttribute.WA_KeyboardFocusChange
        )
        if option.state & QStyle.StateFlag.State_HasFocus and keyboard:
            painter.setPen(QPen(qcolor("fl_text"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(pill.adjusted(1, 1, -1, -1), 4, 4)

        icon_color = qcolor("fl_text") if selected else qcolor("fl_text_secondary")
        svg_name = index.data(self.SVG_ROLE)
        if isinstance(svg_name, str) and svg_name:
            dpr = widget.devicePixelRatioF() if widget is not None else 1.0
            pm = self._tinted_svg_pixmap(svg_name, QSize(RAIL_ICON_SIZE, RAIL_ICON_SIZE), icon_color, dpr)
            x = int(pill.center().x() - RAIL_ICON_SIZE / 2)
            y = int(pill.center().y() - RAIL_ICON_SIZE / 2)
            painter.drawPixmap(QPoint(x, y), pm)
            if index.data(self.BADGE_ROLE):
                painter.setPen(QPen(qcolor("bg"), 2))
                painter.setBrush(qcolor("fl_accent"))
                painter.drawEllipse(QRectF(x + RAIL_ICON_SIZE - 4, y - 2, 8, 8))

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        return QSize(ICON_ONLY_RAIL_WIDTH, RAIL_ITEM_HEIGHT)

    def _tinted_svg_pixmap(self, svg_name: str, size: QSize, color: QColor, dpr: float = 1.0) -> QPixmap:
        key = (svg_name, size.width(), color.rgba(), dpr)
        cached = self._pixmap_cache.get(key)
        if cached is not None:
            return cached

        pm = QPixmap(int(size.width() * dpr), int(size.height() * dpr))
        pm.fill(Qt.GlobalColor.transparent)
        icon_path = ResourceManager.get_icon_path(svg_name)
        if icon_path is not None:
            renderer = QSvgRenderer(str(icon_path))
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            renderer.render(p, QRectF(0, 0, pm.width(), pm.height()))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            p.fillRect(pm.rect(), color)
            p.end()
        pm.setDevicePixelRatio(dpr)
        self._pixmap_cache[key] = pm
        return pm


class _SelectionIndicator(QWidget):
    """Pill-shaped accent indicator that slides between nav items."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedWidth(INDICATOR_WIDTH)
        self._color = qcolor("fl_accent")
        self.hide()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawRoundedRect(self.rect(), 1.5, 1.5)


class NavigationShell(QWidget):
    """Icon rail + stacked pages container with animated transitions."""

    def __init__(
        self,
        title: str = "LumiSync",
        parent: Optional[QWidget] = None,
        *,
        icon_only: bool = True,
    ):
        super().__init__(parent)

        self._items: List[NavItem] = []
        # The rail is always icon-only now; the flag stays for callers.
        self._icon_only = True
        self._rail_width = ICON_ONLY_RAIL_WIDTH

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(self._rail_width)

        sb_layout = QVBoxLayout(self.sidebar)
        sb_layout.setContentsMargins(0, 6, 0, 8)
        sb_layout.setSpacing(0)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("SidebarTitle")
        self.title_label.setVisible(False)
        self.title_label.setContentsMargins(20, 4, 12, 16)
        sb_layout.addWidget(self.title_label)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("SidebarNav")
        self.nav_list.setAccessibleName("Main navigation")
        self.nav_list.setIconSize(QSize(RAIL_ICON_SIZE, RAIL_ICON_SIZE))
        self.nav_list.setMovement(QListWidget.Movement.Static)
        self.nav_list.setUniformItemSizes(True)
        self.nav_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_list.setMouseTracking(True)
        self.nav_list.currentRowChanged.connect(self._on_row_changed)
        self.nav_list.setItemDelegate(
            NavRailItemDelegate(self.nav_list)
        )
        sb_layout.addWidget(self.nav_list)

        sb_layout.addStretch(1)

        self.bottom_nav_list = QListWidget()
        self.bottom_nav_list.setObjectName("SidebarNavBottom")
        self.bottom_nav_list.setAccessibleName("Application navigation")
        self.bottom_nav_list.setIconSize(QSize(RAIL_ICON_SIZE, RAIL_ICON_SIZE))
        self.bottom_nav_list.setMovement(QListWidget.Movement.Static)
        self.bottom_nav_list.setUniformItemSizes(True)
        self.bottom_nav_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.bottom_nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.bottom_nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.bottom_nav_list.setMouseTracking(True)
        self.bottom_nav_list.setFixedHeight(RAIL_ITEM_HEIGHT)
        self.bottom_nav_list.currentRowChanged.connect(self._on_bottom_row_changed)
        self.bottom_nav_list.setItemDelegate(
            NavRailItemDelegate(self.bottom_nav_list)
        )
        sb_layout.addWidget(self.bottom_nav_list)

        # Animated selection indicator (parented to sidebar so its coords are sidebar-relative)
        self._indicator = _SelectionIndicator(self.sidebar)
        self._indicator_anim = QPropertyAnimation(self._indicator, b"geometry", self)
        self._indicator_anim.setDuration(240)
        self._indicator_anim.setEasingCurve(QEasingCurve.Type.OutQuint)

        # Content area
        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("ContentStack")

        root.addWidget(self.sidebar)
        root.addWidget(self.content_stack, 1)

    # ------------------------------------------------------------------ public

    def add_page(
        self,
        *,
        key: str,
        title: str,
        icon,
        widget: QWidget,
        bottom: bool = False,
        label: Optional[str] = None,
    ) -> None:
        """Add a page. ``title`` is the tooltip and accessible name; the rail
        shows only the icon. ``label`` (a short name) is kept as item data."""
        item = NavItem(key=key, title=title, icon=icon, widget=widget)
        self._items.append(item)

        list_item = QListWidgetItem("")
        list_item.setToolTip(title)
        # No visible text: expose the full title to assistive technology.
        list_item.setData(Qt.ItemDataRole.AccessibleTextRole, title)
        list_item.setData(
            Qt.ItemDataRole.AccessibleDescriptionRole,
            f"Open the {title} page",
        )
        list_item.setData(Qt.ItemDataRole.UserRole, key)
        list_item.setData(NavRailItemDelegate.SVG_ROLE, "")
        list_item.setData(NavRailItemDelegate.LABEL_ROLE, label or title)
        list_item.setSizeHint(QSize(self._rail_width, RAIL_ITEM_HEIGHT))

        if bottom:
            self.bottom_nav_list.addItem(list_item)
        else:
            self.nav_list.addItem(list_item)
        self._fit_list_height(self.bottom_nav_list if bottom else self.nav_list)
        self.content_stack.addWidget(widget)

        if self.nav_list.currentRow() < 0 and not bottom:
            self.nav_list.setCurrentRow(0)

    def set_page_visible(self, key: str, visible: bool) -> None:
        """Show or hide a page's rail item.

        Hiding the current page moves to the first visible page so the
        content area never shows a destination the rail no longer offers.
        """
        found = self._find_row(key)
        if found is None:
            return
        lst, row = found
        if lst.isRowHidden(row) == (not visible):
            return
        lst.setRowHidden(row, not visible)
        self._fit_list_height(lst)
        if not visible and lst.currentRow() == row:
            fallback = self._first_visible_row(self.nav_list)
            if fallback is not None and fallback != row:
                self.nav_list.setCurrentRow(fallback)
            else:
                fallback = self._first_visible_row(self.bottom_nav_list)
                if fallback is not None:
                    self.bottom_nav_list.setCurrentRow(fallback)
        self._reposition_indicator()

    def is_page_visible(self, key: str) -> bool:
        found = self._find_row(key)
        return found is not None and not found[0].isRowHidden(found[1])

    def set_page_svg(self, key: str, svg_filename: str) -> None:
        for lst in (self.nav_list, self.bottom_nav_list):
            for i in range(lst.count()):
                li = lst.item(i)
                if li.data(Qt.ItemDataRole.UserRole) == key:
                    li.setData(NavRailItemDelegate.SVG_ROLE, svg_filename)
                    lst.viewport().update()
                    return

    def set_page_badge(self, key: str, visible: bool) -> None:
        """Show or hide a small attention dot on a page's rail icon."""
        for lst in (self.nav_list, self.bottom_nav_list):
            for i in range(lst.count()):
                li = lst.item(i)
                if li.data(Qt.ItemDataRole.UserRole) == key:
                    li.setData(NavRailItemDelegate.BADGE_ROLE, bool(visible))
                    lst.viewport().update()
                    return

    def set_current_by_key(self, key: str) -> None:
        found = self._find_row(key)
        if found is None or found[0].isRowHidden(found[1]):
            return
        found[0].setCurrentRow(found[1])

    # ------------------------------------------------------------------ slots

    def _on_row_changed(self, row: int) -> None:
        if row < 0:
            return
        self.bottom_nav_list.blockSignals(True)
        self.bottom_nav_list.clearSelection()
        self.bottom_nav_list.setCurrentRow(-1)
        self.bottom_nav_list.blockSignals(False)

        key = self.nav_list.item(row).data(Qt.ItemDataRole.UserRole)
        self._activate_key(key, source_list=self.nav_list, row=row)

    def _on_bottom_row_changed(self, row: int) -> None:
        if row < 0:
            return
        self.nav_list.blockSignals(True)
        self.nav_list.clearSelection()
        self.nav_list.setCurrentRow(-1)
        self.nav_list.blockSignals(False)

        key = self.bottom_nav_list.item(row).data(Qt.ItemDataRole.UserRole)
        self._activate_key(key, source_list=self.bottom_nav_list, row=row)

    # ------------------------------------------------------------------ helpers

    def _find_row(self, key: str) -> Optional[Tuple[QListWidget, int]]:
        for lst in (self.nav_list, self.bottom_nav_list):
            for i in range(lst.count()):
                if lst.item(i).data(Qt.ItemDataRole.UserRole) == key:
                    return lst, i
        return None

    @staticmethod
    def _first_visible_row(lst: QListWidget) -> Optional[int]:
        return next(
            (row for row in range(lst.count()) if not lst.isRowHidden(row)),
            None,
        )

    @staticmethod
    def _fit_list_height(lst: QListWidget) -> None:
        # QListWidget's cached size hint can lag behind dynamically added or
        # hidden rows. Size the rail from its visible rows so a page can never
        # exist in the stack while being clipped out of view.
        visible = sum(1 for row in range(lst.count()) if not lst.isRowHidden(row))
        lst.setFixedHeight(visible * RAIL_ITEM_HEIGHT)

    def _reposition_indicator(self) -> None:
        for lst in (self.nav_list, self.bottom_nav_list):
            row = lst.currentRow()
            if row >= 0 and not lst.isRowHidden(row):
                self._move_indicator_to(lst, row, animate=False)
                return

    def _activate_key(self, key: str, *, source_list: QListWidget, row: int) -> None:
        # Find the stack index for this key
        for idx, item in enumerate(self._items):
            if item.key == key:
                fade_swap_stack(self.content_stack, idx)
                break
        self._move_indicator_to(source_list, row)

    def _move_indicator_to(self, lst: QListWidget, row: int, *, animate: bool = True) -> None:
        item_rect = lst.visualItemRect(lst.item(row))
        # Map to sidebar coords: lst is inside sb_layout
        top_left = lst.mapTo(self.sidebar, item_rect.topLeft())
        # The 3×16 accent bar sits at the pill's left edge, vertically centred.
        pill = pill_rect(QRect(top_left, item_rect.size()))
        target = QRect(
            pill.left(),
            pill.center().y() - INDICATOR_HEIGHT // 2 + 1,
            INDICATOR_WIDTH,
            INDICATOR_HEIGHT,
        )
        if not animate or not self._indicator.isVisible():
            self._indicator_anim.stop()
            self._indicator.setGeometry(target)
            self._indicator.show()
            return
        self._indicator_anim.stop()
        self._indicator_anim.setStartValue(self._indicator.geometry())
        self._indicator_anim.setEndValue(target)
        self._indicator_anim.start()

    def showEvent(self, event):
        super().showEvent(event)
        # Position the indicator on first show.
        self._reposition_indicator()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # The bottom list moves with the window height; keep the marker on it.
        if self._indicator.isVisible():
            self._reposition_indicator()


__all__ = ["NavigationShell", "NavItem"]
