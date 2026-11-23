"""Fluent navigation rail (WinUI NavigationView, LeftCompact) + page stack.

- An icon-only rail: each destination is a tab attached to the window's left
  edge (square left corners, rounded right ones) that stops 10 px short of
  the rail's right edge, with a 26 px icon centred in it. Hover shows a
  subtle fill, the current page a slightly stronger one plus a 3×20 accent
  bar on the window edge that slides between items.
- Page names live in tooltips and the accessibility tree, not under icons.
- Bottom-pinned items (Settings), and a content stack with cross-fades.
- The rail shares the window background with the content; no divider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
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


RAIL_WIDTH = 76
ICON_ONLY_RAIL_WIDTH = 76
# Each row is 56 px; the 48 px highlight tab leaves 4 px above and below, so
# the gap between rows lives inside the row and list height stays rows × 56.
RAIL_ITEM_HEIGHT = 56
RAIL_HIGHLIGHT_INSET = 4
RAIL_HIGHLIGHT_RIGHT_GAP = 10
RAIL_HIGHLIGHT_RADIUS = 10
RAIL_PILL_WIDTH = ICON_ONLY_RAIL_WIDTH - RAIL_HIGHLIGHT_RIGHT_GAP
RAIL_PILL_HEIGHT = RAIL_ITEM_HEIGHT - 2 * RAIL_HIGHLIGHT_INSET
RAIL_ITEM_SIZE = RAIL_PILL_HEIGHT  # Backwards-compatible name for the highlight height.
RAIL_ICON_SIZE = 26
RAIL_TOP_PADDING = 12
RAIL_BOTTOM_PADDING = 12
INDICATOR_WIDTH = 3
INDICATOR_HEIGHT = 20


@dataclass(frozen=True)
class NavItem:
    key: str
    title: str
    icon: object
    widget: QWidget


def highlight_rect(rect: QRect) -> QRect:
    """The hover/selection tab of a rail item: flush with the item's left
    edge (the window edge), ending ``RAIL_HIGHLIGHT_RIGHT_GAP`` short of its
    right edge, ``RAIL_HIGHLIGHT_INSET`` px in from its top and bottom."""
    return QRect(
        rect.left(),
        rect.top() + RAIL_HIGHLIGHT_INSET,
        max(0, rect.width() - RAIL_HIGHLIGHT_RIGHT_GAP),
        max(0, rect.height() - 2 * RAIL_HIGHLIGHT_INSET),
    )


# Backwards-compatible name: the highlight used to be a centred pill.
pill_rect = highlight_rect


def tab_path(rect: QRectF, radius: float) -> QPainterPath:
    """A rectangle with square left corners and rounded right corners."""
    r = max(0.0, min(radius, rect.width() / 2, rect.height() / 2))
    path = QPainterPath()
    path.moveTo(rect.left(), rect.top())
    path.lineTo(rect.right() - r, rect.top())
    path.arcTo(QRectF(rect.right() - 2 * r, rect.top(), 2 * r, 2 * r), 90, -90)
    path.lineTo(rect.right(), rect.bottom() - r)
    path.arcTo(QRectF(rect.right() - 2 * r, rect.bottom() - 2 * r, 2 * r, 2 * r), 0, -90)
    path.lineTo(rect.left(), rect.bottom())
    path.closeSubpath()
    return path


def badge_rect(icon_x: float, icon_y: float) -> QRectF:
    """The attention dot, centred on the icon's top-right corner area and
    scaled with the icon (8 px at the old 24 px icon size)."""
    diameter = RAIL_ICON_SIZE / 3
    centre_x = icon_x + RAIL_ICON_SIZE
    centre_y = icon_y + RAIL_ICON_SIZE / 12
    return QRectF(centre_x - diameter / 2, centre_y - diameter / 2, diameter, diameter)


class NavRailItemDelegate(QStyledItemDelegate):
    """Paints a rail item: the edge tab (hover/selected) and the tinted icon.

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
        return highlight_rect(rect)

    @staticmethod
    def _fill_for(selected: bool, hovered: bool, pressed: bool) -> QColor:
        """Translucent overlay fill, so the tab reads on opaque, Mica and
        Acrylic backgrounds alike."""
        if pressed:
            fill = qcolor("fl_subtle_selected")
            fill.setAlpha(min(255, fill.alpha() + 16))
        elif selected and hovered:
            fill = qcolor("fl_subtle_selected")
            fill.setAlpha(min(255, fill.alpha() + 8))
        else:
            fill = qcolor("fl_subtle_selected" if selected else "fl_subtle_hover")
        return fill

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        pressed = hovered and bool(QGuiApplication.mouseButtons() & Qt.MouseButton.LeftButton)
        tab = QRectF(self._pill_rect(rect))

        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._fill_for(selected, hovered, pressed))
            painter.drawPath(tab_path(tab, RAIL_HIGHLIGHT_RADIUS))

        widget = option.widget
        window = widget.window() if widget is not None else None
        keyboard = window is not None and window.testAttribute(
            Qt.WidgetAttribute.WA_KeyboardFocusChange
        )
        if option.state & QStyle.StateFlag.State_HasFocus and keyboard:
            painter.setPen(QPen(qcolor("fl_text"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(tab_path(tab.adjusted(1, 1, -1, -1), RAIL_HIGHLIGHT_RADIUS - 1))

        icon_color = qcolor("fl_text") if selected else qcolor("fl_text_secondary")
        svg_name = index.data(self.SVG_ROLE)
        if isinstance(svg_name, str) and svg_name:
            dpr = widget.devicePixelRatioF() if widget is not None else 1.0
            pm = self._tinted_svg_pixmap(svg_name, QSize(RAIL_ICON_SIZE, RAIL_ICON_SIZE), icon_color, dpr)
            # Centred in the tab (a little left of the rail's centre).
            x = round(tab.left() + (tab.width() - RAIL_ICON_SIZE) / 2)
            y = round(tab.top() + (tab.height() - RAIL_ICON_SIZE) / 2)
            painter.drawPixmap(QPoint(x, y), pm)
            if index.data(self.BADGE_ROLE):
                painter.setPen(QPen(qcolor("bg"), 2))
                painter.setBrush(qcolor("fl_accent"))
                painter.drawEllipse(badge_rect(x, y))

        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        # Repaint on press and release so the pressed fill never lingers.
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            view = self.parent()
            if isinstance(view, QAbstractItemView):
                view.viewport().update(option.rect)
        return super().editorEvent(event, model, option, index)

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
    """Accent bar on the window edge that slides between nav items; square
    on the left (against the edge), rounded on the right."""

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
        painter.drawPath(tab_path(QRectF(self.rect()), INDICATOR_WIDTH / 2))


class NavigationShell(QWidget):
    """Icon rail + stacked pages container with animated transitions."""

    # A page's key, whenever the rail opens that page.
    page_activated = Signal(str)

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
        sb_layout.setContentsMargins(0, RAIL_TOP_PADDING, 0, RAIL_BOTTOM_PADDING)
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
        # No frame even without the theme, so the tab starts at the window edge.
        self.nav_list.setFrameShape(QFrame.Shape.NoFrame)
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
        self.bottom_nav_list.setFrameShape(QFrame.Shape.NoFrame)
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

    def current_key(self) -> Optional[str]:
        """The key of the page the rail has selected, if any."""
        for lst in (self.nav_list, self.bottom_nav_list):
            row = lst.currentRow()
            if row >= 0:
                return lst.item(row).data(Qt.ItemDataRole.UserRole)
        return None

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
        self.page_activated.emit(str(key))

    def _move_indicator_to(self, lst: QListWidget, row: int, *, animate: bool = True) -> None:
        item_rect = lst.visualItemRect(lst.item(row))
        # Map to sidebar coords: lst is inside sb_layout
        top_left = lst.mapTo(self.sidebar, item_rect.topLeft())
        # The 3×20 accent bar sits on the window edge inside the tab,
        # vertically centred on it.
        tab = highlight_rect(QRect(top_left, item_rect.size()))
        target = QRect(
            tab.left(),
            tab.top() + (tab.height() - INDICATOR_HEIGHT) // 2,
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
