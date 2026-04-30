"""Modern sidebar navigation shell — Windows Settings style.

- Left icon-only navigation rail with animated selection indicator
- Bottom-pinned action items (Settings)
- Right content area is a QStackedWidget with cross-fade transitions
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
)
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..resources import ResourceManager
from ..theme import qcolor
from ..utils.animations import fade_swap_stack


RAIL_WIDTH = 60
RAIL_ITEM_SIZE = 40
RAIL_ICON_SIZE = 20


@dataclass(frozen=True)
class NavItem:
    key: str
    title: str
    icon: object
    widget: QWidget


class NavRailItemDelegate(QStyledItemDelegate):
    """Paints icon-only rail items. Selection pill is drawn by the
    parent shell as an animated overlay; we just draw the icon and an
    optional hover background here."""

    SVG_ROLE = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap_cache: Dict[Tuple[str, int, int, int], QPixmap] = {}

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        text = qcolor("text")

        if selected:
            icon_color = QColor(text)
            icon_color.setAlpha(255)
        elif hovered:
            icon_color = QColor(text)
            icon_color.setAlpha(220)
            # Hover background pill
            pill_w = RAIL_ITEM_SIZE + 12
            pill_h = RAIL_ITEM_SIZE
            pill_rect = QRect(
                rect.center().x() - pill_w // 2,
                rect.center().y() - pill_h // 2,
                pill_w, pill_h,
            )
            bg = QColor(text)
            bg.setAlpha(22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(pill_rect, 8, 8)
        else:
            icon_color = QColor(text)
            icon_color.setAlpha(150)

        svg_name = index.data(self.SVG_ROLE)
        if isinstance(svg_name, str) and svg_name:
            size = QSize(RAIL_ICON_SIZE, RAIL_ICON_SIZE)
            pm = self._tinted_svg_pixmap(svg_name, size, icon_color)
            x = rect.center().x() - pm.width() // 2
            y = rect.center().y() - pm.height() // 2
            painter.drawPixmap(QPoint(x, y), pm)

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        return QSize(RAIL_WIDTH, RAIL_WIDTH)

    def _tinted_svg_pixmap(self, svg_name: str, size: QSize, color: QColor) -> QPixmap:
        key = (svg_name, size.width(), size.height(), color.rgba())
        cached = self._pixmap_cache.get(key)
        if cached is not None:
            return cached

        icon_path = ResourceManager.get_icon_path(svg_name)
        if icon_path is None:
            pm = QPixmap(size)
            pm.fill(Qt.GlobalColor.transparent)
            self._pixmap_cache[key] = pm
            return pm

        renderer = QSvgRenderer(str(icon_path))
        pm = QPixmap(size)
        pm.fill(Qt.GlobalColor.transparent)

        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(p)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(pm.rect(), color)
        p.end()

        self._pixmap_cache[key] = pm
        return pm


class _SelectionIndicator(QWidget):
    """Pill-shaped accent indicator that slides between nav items."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedWidth(3)
        self._color = qcolor("accent")
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
        self._icon_only = icon_only

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(RAIL_WIDTH)

        sb_layout = QVBoxLayout(self.sidebar)
        sb_layout.setContentsMargins(0, 8, 0, 8)
        sb_layout.setSpacing(0)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("SidebarTitle")
        self.title_label.setVisible(False)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("SidebarNav")
        self.nav_list.setIconSize(QSize(RAIL_ICON_SIZE, RAIL_ICON_SIZE))
        self.nav_list.setMovement(QListWidget.Movement.Static)
        self.nav_list.setUniformItemSizes(True)
        self.nav_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_list.setMouseTracking(True)
        self.nav_list.currentRowChanged.connect(self._on_row_changed)
        self.nav_list.setItemDelegate(NavRailItemDelegate(self.nav_list))
        sb_layout.addWidget(self.nav_list)

        sb_layout.addStretch(1)

        self.bottom_nav_list = QListWidget()
        self.bottom_nav_list.setObjectName("SidebarNavBottom")
        self.bottom_nav_list.setIconSize(QSize(RAIL_ICON_SIZE, RAIL_ICON_SIZE))
        self.bottom_nav_list.setMovement(QListWidget.Movement.Static)
        self.bottom_nav_list.setUniformItemSizes(True)
        self.bottom_nav_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.bottom_nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.bottom_nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.bottom_nav_list.setMouseTracking(True)
        self.bottom_nav_list.setFixedHeight(RAIL_WIDTH)
        self.bottom_nav_list.currentRowChanged.connect(self._on_bottom_row_changed)
        self.bottom_nav_list.setItemDelegate(NavRailItemDelegate(self.bottom_nav_list))
        sb_layout.addWidget(self.bottom_nav_list)

        # Animated selection indicator (parented to sidebar so its coords are sidebar-relative)
        self._indicator = _SelectionIndicator(self.sidebar)
        self._indicator_anim = QPropertyAnimation(self._indicator, b"geometry", self)
        self._indicator_anim.setDuration(180)
        self._indicator_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Content area
        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("ContentStack")

        root.addWidget(self.sidebar)
        root.addWidget(self.content_stack, 1)

    # ------------------------------------------------------------------ public

    def add_page(self, *, key: str, title: str, icon, widget: QWidget, bottom: bool = False) -> None:
        item = NavItem(key=key, title=title, icon=icon, widget=widget)
        self._items.append(item)

        list_item = QListWidgetItem("")
        list_item.setToolTip(title)
        list_item.setData(Qt.ItemDataRole.UserRole, key)
        list_item.setData(NavRailItemDelegate.SVG_ROLE, "")
        list_item.setSizeHint(QSize(RAIL_WIDTH, RAIL_WIDTH))

        if bottom:
            self.bottom_nav_list.addItem(list_item)
        else:
            self.nav_list.addItem(list_item)
        self.content_stack.addWidget(widget)

        if self.nav_list.currentRow() < 0 and not bottom:
            self.nav_list.setCurrentRow(0)

    def set_page_svg(self, key: str, svg_filename: str) -> None:
        for lst in (self.nav_list, self.bottom_nav_list):
            for i in range(lst.count()):
                li = lst.item(i)
                if li.data(Qt.ItemDataRole.UserRole) == key:
                    li.setData(NavRailItemDelegate.SVG_ROLE, svg_filename)
                    lst.viewport().update()
                    return

    def set_current_by_key(self, key: str) -> None:
        for lst in (self.nav_list, self.bottom_nav_list):
            for i in range(lst.count()):
                li = lst.item(i)
                if li.data(Qt.ItemDataRole.UserRole) == key:
                    lst.setCurrentRow(i)
                    return

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

    def _activate_key(self, key: str, *, source_list: QListWidget, row: int) -> None:
        # Find the stack index for this key
        for idx, item in enumerate(self._items):
            if item.key == key:
                fade_swap_stack(self.content_stack, idx)
                break
        self._move_indicator_to(source_list, row)

    def _move_indicator_to(self, lst: QListWidget, row: int) -> None:
        item_rect = lst.visualItemRect(lst.item(row))
        # Map to sidebar coords: lst is inside sb_layout
        top_left = lst.mapTo(self.sidebar, item_rect.topLeft())
        # Indicator: 16 px tall, vertically centered in the rail item
        ind_h = 18
        target = QRect(
            2,
            top_left.y() + (item_rect.height() - ind_h) // 2,
            3,
            ind_h,
        )
        if not self._indicator.isVisible():
            self._indicator.setGeometry(target)
            self._indicator.show()
            return
        self._indicator_anim.stop()
        self._indicator_anim.setStartValue(self._indicator.geometry())
        self._indicator_anim.setEndValue(target)
        self._indicator_anim.start()

    def showEvent(self, event):
        super().showEvent(event)
        # Position indicator on first show
        if self.nav_list.currentRow() >= 0:
            self._move_indicator_to(self.nav_list, self.nav_list.currentRow())


__all__ = ["NavigationShell", "NavItem"]
