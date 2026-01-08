"""Modern sidebar navigation shell.

This is a reusable container that replaces a traditional QTabWidget UI:
- Left sidebar navigation rail (icon-only).
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
    QAbstractButton,  # Add this
    QSizePolicy,
)

from ..resources import ResourceManager


@dataclass(frozen=True)
class NavItem:
    key: str
    title: str
    icon: object  # QIcon
    widget: QWidget


class NavRailItemDelegate(QStyledItemDelegate):
    """Paints a modern icon-only navigation rail item.

    - Draws hover/selected rounded background.
    - Draws a left accent bar when selected.
    - Renders SVG icons and tints them based on item state.

    Items should provide the SVG filename via ``Qt.ItemDataRole.DecorationRole``
    or via ``Qt.ItemDataRole.UserRole + 1``.
    """

    SVG_ROLE = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap_cache: Dict[Tuple[str, int, int, int], QPixmap] = {}

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = option.rect

        # Geometry (inner rounded cell)
        cell_margin_x = 8
        cell_margin_y = 4
        cell_rect = QRect(
            rect.left() + cell_margin_x,
            rect.top() + cell_margin_y,
            rect.width() - (cell_margin_x * 2),
            rect.height() - (cell_margin_y * 2),
        )

        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        # Colors (derived from palette; works with qt-material)
        palette = option.palette
        text_color = palette.color(palette.ColorRole.WindowText)

        # Normal/hover/selected icon colors
        icon_color = QColor(text_color)
        icon_color.setAlpha(180)
        if hovered:
            icon_color = QColor(text_color)
            icon_color.setAlpha(220)
        if selected:
            icon_color = QColor(text_color)
            icon_color.setAlpha(255)

        # Backgrounds
        if hovered or selected:
            bg = QColor(text_color)
            bg.setAlpha(28 if hovered else 40)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(cell_rect, 12, 12)

        # Accent bar
        if selected:
            accent = palette.color(palette.ColorRole.Highlight)
            accent.setAlpha(240)
            bar_w = 4
            bar_h = max(18, cell_rect.height() - 18)
            bar_rect = QRect(cell_rect.left() - 6, cell_rect.center().y() - bar_h // 2, bar_w, bar_h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(bar_rect, 2, 2)

        # Icon
        svg_name = index.data(self.SVG_ROLE)
        if isinstance(svg_name, str) and svg_name:
            icon_size = option.decorationSize
            if icon_size.isEmpty():
                icon_size = QSize(22, 22)

            pm = self._tinted_svg_pixmap(svg_name, icon_size, icon_color)
            x = rect.center().x() - pm.width() // 2
            y = rect.center().y() - pm.height() // 2
            painter.drawPixmap(QPoint(x, y), pm)

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        # Consistent rows (navigation rail feel)
        return QSize(option.rect.width(), 56)

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
    """Button that renders a tinted SVG icon."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._svg_path: Optional[str] = None
        self._pixmap_cache: Dict[Tuple[str, int, int, int], QPixmap] = {}
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Minimum
        )

    def setSvgPath(self, path: str):
        self._svg_path = path
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(56, 56)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()

        # Colors
        palette = self.palette()
        text_color = palette.color(palette.ColorRole.WindowText)

        icon_color = QColor(text_color)
        icon_color.setAlpha(180)

        if self.underMouse():
            icon_color = QColor(text_color)
            icon_color.setAlpha(255)

            # Hover bg (subtle pill)
            bg = QColor(text_color)
            bg.setAlpha(26)
            painter.setBrush(bg)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect.adjusted(10, 6, -10, -6), 12, 12)

        if self.isDown():
            icon_color.setAlpha(210)

        if self._svg_path:
            # Draw centered icon
            icon_size = 24
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
    """Sidebar + stacked pages container."""

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
        self._on_settings: Optional[Callable[[], None]] = None

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Sidebar
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        # Icon-only rail stays compact; label mode still supported.
        self.sidebar.setFixedWidth(68 if icon_only else 220)

        sb_layout = QVBoxLayout(self.sidebar)
        sb_layout.setContentsMargins(10, 12, 10, 12)
        sb_layout.setSpacing(10)

        # Optional title label (hidden for icon-only rail)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("SidebarTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.title_label.setVisible(not icon_only)
        sb_layout.addWidget(self.title_label)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("SidebarNav")
        self.nav_list.setIconSize(QSize(24, 24))
        self.nav_list.setMovement(QListWidget.Movement.Static)
        self.nav_list.setUniformItemSizes(True)
        self.nav_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.nav_list.setMouseTracking(True)
        self.nav_list.currentRowChanged.connect(self._on_row_changed)

        # Use delegate so we control tinting + accent bar.
        self._delegate = NavRailItemDelegate(self.nav_list)
        self.nav_list.setItemDelegate(self._delegate)

        sb_layout.addWidget(self.nav_list, 1)

        # Bottom actions
        self.bottom_actions = QFrame()
        self.bottom_actions.setObjectName("SidebarBottom")
        bottom_layout = QVBoxLayout(self.bottom_actions)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(6)

        self.settings_button = TintedIconButton()
        self.settings_button.setObjectName("SidebarSettings")
        self.settings_button.setToolTip("Settings")
        self.settings_button.clicked.connect(self._settings_clicked)
        bottom_layout.addWidget(self.settings_button)

        sb_layout.addWidget(self.bottom_actions)

        # Content
        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("ContentStack")

        root.addWidget(self.sidebar)
        root.addWidget(self.content_stack, 1)

        # Default styling kept minimal; delegate paints item visuals.
        # Use palette-aware colors so this works with qt-material themes.
        self.setStyleSheet(
            """
            QFrame#Sidebar {
                background: rgba(0, 0, 0, 22);
                border-right: 1px solid rgba(255, 255, 255, 18);
            }
            QListWidget#SidebarNav {
                background: transparent;
                border: none;
                outline: none;
            }
            QFrame#SidebarBottom {
                background: transparent;
            }
            """
        )

    def set_settings(self, *, icon_name: str, callback: Callable[[], None]) -> None:
        self.settings_button.setSvgPath(icon_name)
        self._on_settings = callback

    def add_page(self, *, key: str, title: str, icon, widget: QWidget) -> None:
        item = NavItem(key=key, title=title, icon=icon, widget=widget)
        self._items.append(item)

        label = "" if self._icon_only else title
        list_item = QListWidgetItem(label)
        list_item.setToolTip(title)
        list_item.setData(Qt.ItemDataRole.UserRole, key)

        # Store svg filename (preferred) for delegate tinting.
        svg_name = None
        if hasattr(icon, "name"):
            # Not reliable for QIcon; ignore.
            svg_name = None

        # Best-effort: infer svg filename from tooltip/title mapping is not possible here.
        # So we accept an icon but also allow setting SVG role via `set_item_svg`.
        # For now, try obtaining a pixmap-backed name via stable key mapping.
        list_item.setData(NavRailItemDelegate.SVG_ROLE, "")

        self.nav_list.addItem(list_item)

        # Tighter rows for icon-only
        if self._icon_only:
            list_item.setSizeHint(QSize(56, 56))

        self.content_stack.addWidget(widget)

        if self.nav_list.currentRow() < 0:
            self.nav_list.setCurrentRow(0)

    def set_page_svg(self, key: str, svg_filename: str) -> None:
        """Associate an SVG filename with the page key for tinted rendering."""
        for i in range(self.nav_list.count()):
            li = self.nav_list.item(i)
            if li.data(Qt.ItemDataRole.UserRole) == key:
                li.setData(NavRailItemDelegate.SVG_ROLE, svg_filename)
                self.nav_list.viewport().update()
                return

    def set_current_by_key(self, key: str) -> None:
        for i in range(self.nav_list.count()):
            li = self.nav_list.item(i)
            if li.data(Qt.ItemDataRole.UserRole) == key:
                self.nav_list.setCurrentRow(i)
                return

    def _settings_clicked(self) -> None:
        if self._on_settings is not None:
            self._on_settings()

    def _on_row_changed(self, row: int) -> None:
        if row < 0 or row >= self.content_stack.count():
            return
        self.content_stack.setCurrentIndex(row)


__all__ = ["NavigationShell", "NavItem"]
