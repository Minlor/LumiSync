"""Modern sidebar navigation shell - Windows Settings style.

This is a reusable container that replaces a traditional QTabWidget UI:
- Left navigation rail with icon-only buttons (Windows 11 Settings style).
- Optional bottom action buttons (e.g., Settings).
- Right content area provided by a QStackedWidget.

It's designed to be dropped into ``LumiSyncMainWindow`` with minimal changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtGui import (
    QColor,
    QPainter,
    QPixmap,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QWidget,
    QFrame,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QHBoxLayout,
    QVBoxLayout,
    QStackedWidget,
    QToolButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QAbstractButton,
    QSizePolicy,
)

from ..resources import ResourceManager


# Rail dimensions (Windows Settings style)
RAIL_WIDTH = 56
RAIL_ITEM_SIZE = 40
RAIL_ICON_SIZE = 20
RAIL_PADDING = 4


@dataclass(frozen=True)
class NavItem:
    key: str
    title: str
    icon: object  # QIcon
    widget: QWidget


class NavRailItemDelegate(QStyledItemDelegate):
    """Paints a Windows Settings-style icon rail item.

    - Compact pill-shaped selection indicator.
    - Centered icon with subtle hover/selection states.
    - Clean, minimal design with no text labels.
    """

    SVG_ROLE = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap_cache: Dict[Tuple[str, int, int, int], QPixmap] = {}

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = option.rect

        # Centered pill area for hover/selection
        # Rounded rectangular area for hover/selection (wider than tall)
        pill_h = RAIL_ITEM_SIZE
        pill_w = RAIL_ITEM_SIZE + 12
        pill_rect = QRect(
            rect.center().x() - pill_w // 2,
            rect.center().y() - pill_h // 2,
            pill_w,
            pill_h,
        )

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        # Colors (derived from palette; works with qt-material)
        palette = option.palette
        text_color = palette.color(palette.ColorRole.WindowText)
        accent_color = palette.color(palette.ColorRole.Highlight)

        # Icon color states
        icon_color = QColor(text_color)
        if selected:
            icon_color.setAlpha(255)
        elif hovered:
            icon_color.setAlpha(220)
        else:
            icon_color.setAlpha(160)

        # Draw selection/hover rounded rectangle background
        corner_radius = 8
        if selected:
            # Filled rounded box for selected state
            bg = QColor(text_color)
            bg.setAlpha(45)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(pill_rect, corner_radius, corner_radius)

            # Small vertical accent indicator on the left
            indicator_h = 16
            indicator_w = 3
            indicator_rect = QRect(
                rect.left() + 2,
                rect.center().y() - indicator_h // 2,
                indicator_w,
                indicator_h,
            )
            painter.setBrush(accent_color)
            painter.drawRoundedRect(indicator_rect, indicator_w // 2, indicator_w // 2)

        elif hovered:
            # Subtle hover state (rounded rectangle)
            bg = QColor(text_color)
            bg.setAlpha(22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(pill_rect, corner_radius, corner_radius)

        # Draw icon centered
        svg_name = index.data(self.SVG_ROLE)
        if isinstance(svg_name, str) and svg_name:
            icon_size = QSize(RAIL_ICON_SIZE, RAIL_ICON_SIZE)
            pm = self._tinted_svg_pixmap(svg_name, icon_size, icon_color)
            x = rect.center().x() - pm.width() // 2
            y = rect.center().y() - pm.height() // 2
            painter.drawPixmap(QPoint(x, y), pm)

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        # Square items for compact rail
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


class TintedIconButton(QAbstractButton):
    """Compact icon button for the navigation rail."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._svg_path: Optional[str] = None
        self._pixmap_cache: Dict[Tuple[str, int, int, int], QPixmap] = {}
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.setFixedSize(RAIL_WIDTH, RAIL_WIDTH)

    def setSvgPath(self, path: str):
        self._svg_path = path
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(RAIL_WIDTH, RAIL_WIDTH)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()

        # Colors
        palette = self.palette()
        text_color = palette.color(palette.ColorRole.WindowText)

        icon_color = QColor(text_color)
        icon_color.setAlpha(160)

        # Rounded rectangular area for hover (match delegate dimensions)
        pill_h = RAIL_ITEM_SIZE
        pill_w = RAIL_ITEM_SIZE + 12
        pill_rect = QRect(
            rect.center().x() - pill_w // 2,
            rect.center().y() - pill_h // 2,
            pill_w,
            pill_h,
        )

        if self.underMouse():
            icon_color = QColor(text_color)
            icon_color.setAlpha(220)

            # Hover background
            bg = QColor(text_color)
            bg.setAlpha(22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(pill_rect, 8, 8)

        if self.isDown():
            icon_color.setAlpha(180)

        if self._svg_path:
            # Draw centered icon
            icon_size = RAIL_ICON_SIZE
            x = rect.center().x() - icon_size // 2
            y = rect.center().y() - icon_size // 2
            size = QSize(icon_size, icon_size)

            pm = self._tinted_svg_pixmap(self._svg_path, size, icon_color)
            painter.drawPixmap(QPoint(x, y), pm)

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


class NavigationShell(QWidget):
    """Windows Settings-style icon rail + stacked pages container."""

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
        # Settings is now a regular bottom nav item (no special button)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar rail
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(RAIL_WIDTH)

        sb_layout = QVBoxLayout(self.sidebar)
        sb_layout.setContentsMargins(0, 0, 0, 0)
        sb_layout.setSpacing(0)

        # Title label hidden for icon-only mode
        self.title_label = QLabel(title)
        self.title_label.setObjectName("SidebarTitle")
        self.title_label.setVisible(False)

        # Navigation list
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

        # Use custom delegate for Windows Settings style
        self._delegate = NavRailItemDelegate(self.nav_list)
        self.nav_list.setItemDelegate(self._delegate)

        sb_layout.addWidget(self.nav_list)
        
        # Add spacer to push bottom items down
        sb_layout.addStretch(1)

        # Bottom navigation list for settings
        self.bottom_nav_list = QListWidget()
        self.bottom_nav_list.setObjectName("SidebarNavBottom")
        self.bottom_nav_list.setIconSize(QSize(RAIL_ICON_SIZE, RAIL_ICON_SIZE))
        self.bottom_nav_list.setMovement(QListWidget.Movement.Static)
        self.bottom_nav_list.setUniformItemSizes(True)
        self.bottom_nav_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.bottom_nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.bottom_nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.bottom_nav_list.setMouseTracking(True)
        self.bottom_nav_list.setFixedHeight(RAIL_WIDTH)  # Only one item
        self.bottom_nav_list.currentRowChanged.connect(self._on_bottom_row_changed)
        self.bottom_nav_list.setItemDelegate(NavRailItemDelegate(self.bottom_nav_list))
        sb_layout.addWidget(self.bottom_nav_list)

        # Content area
        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("ContentStack")

        root.addWidget(self.sidebar)
        root.addWidget(self.content_stack, 1)

        # Clean, minimal styling
        self.setStyleSheet(
            """
            QFrame#Sidebar {
                background: transparent;
                border: none;
                margin: 0;
                padding: 0;
            }
            QListWidget#SidebarNav {
                background: transparent;
                border: none;
                outline: none;
            }
            QListWidget#SidebarNavBottom {
                background: transparent;
                border: none;
                outline: none;
            }
            QListWidget#SidebarNav::item {
                padding: 0;
                margin: 0;
            }
            """
        )

    def add_page(self, *, key: str, title: str, icon, widget: QWidget, bottom: bool = False) -> None:
        item = NavItem(key=key, title=title, icon=icon, widget=widget)
        self._items.append(item)

        # Icon-only items (no text label)
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
        """Associate an SVG filename with the page key for tinted rendering."""
        for i in range(self.nav_list.count()):
            li = self.nav_list.item(i)
            if li.data(Qt.ItemDataRole.UserRole) == key:
                li.setData(NavRailItemDelegate.SVG_ROLE, svg_filename)
                self.nav_list.viewport().update()
                return
        for i in range(self.bottom_nav_list.count()):
            li = self.bottom_nav_list.item(i)
            if li.data(Qt.ItemDataRole.UserRole) == key:
                li.setData(NavRailItemDelegate.SVG_ROLE, svg_filename)
                self.bottom_nav_list.viewport().update()
                return

    def set_current_by_key(self, key: str) -> None:
        for i in range(self.nav_list.count()):
            li = self.nav_list.item(i)
            if li.data(Qt.ItemDataRole.UserRole) == key:
                self.nav_list.setCurrentRow(i)
                return

        for i in range(self.bottom_nav_list.count()):
            li = self.bottom_nav_list.item(i)
            if li.data(Qt.ItemDataRole.UserRole) == key:
                self.bottom_nav_list.setCurrentRow(i)
                return


    def _on_row_changed(self, row: int) -> None:
        if row < 0:
            return
        # Clear bottom nav selection when main nav is selected
        self.bottom_nav_list.clearSelection()
        self.bottom_nav_list.setCurrentRow(-1)
        # Find the widget index for this main nav item
        key = self.nav_list.item(row).data(Qt.ItemDataRole.UserRole)
        for idx, item in enumerate(self._items):
            if item.key == key:
                self.content_stack.setCurrentIndex(idx)
                return

    def _on_bottom_row_changed(self, row: int) -> None:
        if row < 0:
            return
        # Clear main nav selection when bottom nav is selected
        self.nav_list.clearSelection()
        self.nav_list.setCurrentRow(-1)
        # Find the widget index for this bottom nav item
        key = self.bottom_nav_list.item(row).data(Qt.ItemDataRole.UserRole)
        for idx, item in enumerate(self._items):
            if item.key == key:
                self.content_stack.setCurrentIndex(idx)
                return


__all__ = ["NavigationShell", "NavItem"]
