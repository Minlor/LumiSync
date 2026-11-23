"""Navigation rail geometry: the edge-attached highlight tab, sizes and
spacing, the sliding accent marker and the badge dot."""

import os
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QWidget  # noqa: E402

from lumisync.gui.theme import apply_theme, qcolor  # noqa: E402
from lumisync.gui.widgets.navigation_shell import (  # noqa: E402
    ICON_ONLY_RAIL_WIDTH,
    INDICATOR_HEIGHT,
    INDICATOR_WIDTH,
    RAIL_BOTTOM_PADDING,
    RAIL_HIGHLIGHT_RIGHT_GAP,
    RAIL_ICON_SIZE,
    RAIL_ITEM_HEIGHT,
    RAIL_ITEM_SIZE,
    RAIL_PILL_HEIGHT,
    RAIL_PILL_WIDTH,
    RAIL_TOP_PADDING,
    RAIL_WIDTH,
    NavigationShell,
    NavRailItemDelegate,
    highlight_rect,
    pill_rect,
)

PAGES = ("devices", "monitor", "music", "pc", "draw")


def close_enough(first: QColor, second: QColor, tolerance: int = 40) -> bool:
    return all(
        abs(a - b) <= tolerance
        for a, b in zip((first.red(), first.green(), first.blue()),
                        (second.red(), second.green(), second.blue()))
    )


class SidebarGeometryTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls):
        existing = QApplication.instance()
        cls.app = existing if isinstance(existing, QApplication) else QApplication([])

    def setUp(self):
        self.previous_style = self.app.styleSheet()
        apply_theme(self.app)
        self.widgets = []

    def tearDown(self):
        for widget in reversed(self.widgets):
            widget.close()
            widget.deleteLater()
        self.app.processEvents()
        self.app.setStyleSheet(self.previous_style)

    def build_shell(self, *, show=True):
        shell = NavigationShell(icon_only=True)
        self.widgets.append(shell)
        for key in PAGES + ("settings",):
            shell.add_page(key=key, title=key.title(), icon=QIcon(), widget=QWidget(),
                           bottom=key == "settings")
            shell.set_page_svg(key, "screen.svg")
        if show:
            shell.resize(900, 640)
            shell.show()
            self.app.processEvents()
        return shell

    # ------------------------------------------------------------ constants

    def test_rail_is_bigger_and_keeps_the_old_constant_names(self):
        self.assertEqual(RAIL_WIDTH, 76)
        self.assertEqual(ICON_ONLY_RAIL_WIDTH, 76)
        self.assertEqual(RAIL_ITEM_HEIGHT, 56)
        self.assertEqual(RAIL_ICON_SIZE, 26)
        self.assertEqual(RAIL_PILL_HEIGHT, 48)
        self.assertEqual(RAIL_ITEM_SIZE, RAIL_PILL_HEIGHT)
        self.assertEqual(RAIL_PILL_WIDTH, RAIL_WIDTH - RAIL_HIGHLIGHT_RIGHT_GAP)
        self.assertEqual((RAIL_TOP_PADDING, RAIL_BOTTOM_PADDING), (12, 12))
        self.assertEqual((INDICATOR_WIDTH, INDICATOR_HEIGHT), (3, 20))

    def test_highlight_starts_at_the_window_edge_and_stops_short_of_the_rail_edge(self):
        item = QRect(0, 2 * RAIL_ITEM_HEIGHT, RAIL_WIDTH, RAIL_ITEM_HEIGHT)
        tab = highlight_rect(item)
        self.assertEqual(tab.left(), 0)
        self.assertEqual(tab.left() + tab.width(), RAIL_WIDTH - 10)
        self.assertEqual(tab.top(), item.top() + 4)
        self.assertEqual(tab.height(), 48)
        # Vertically centred: 4 px of row above and below the tab.
        self.assertEqual(item.bottom() - tab.bottom(), 4)
        self.assertEqual(pill_rect(item), tab)

    # -------------------------------------------------------------- painting

    def paint_item(self, state, *, badge=False):
        shell = self.build_shell(show=False)
        index = shell.nav_list.model().index(1, 0)
        shell.nav_list.item(1).setData(NavRailItemDelegate.BADGE_ROLE, badge)
        image = QImage(RAIL_WIDTH, RAIL_ITEM_HEIGHT, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, RAIL_WIDTH, RAIL_ITEM_HEIGHT)
        option.state = state | QStyle.StateFlag.State_Enabled
        painter = QPainter(image)
        shell.nav_list.itemDelegate().paint(painter, option, index)
        painter.end()
        return image

    def test_selected_and_hovered_tabs_are_flush_left_with_rounded_right_corners(self):
        middle = RAIL_ITEM_HEIGHT // 2
        for state in (QStyle.StateFlag.State_Selected, QStyle.StateFlag.State_MouseOver):
            with self.subTest(state=state):
                image = self.paint_item(state)
                self.assertGreater(image.pixelColor(0, middle).alpha(), 0)   # window edge
                self.assertGreater(image.pixelColor(RAIL_WIDTH - 11, middle).alpha(), 0)
                self.assertEqual(image.pixelColor(RAIL_WIDTH - 9, middle).alpha(), 0)
                self.assertEqual(image.pixelColor(RAIL_WIDTH - 2, middle).alpha(), 0)
                # 4 px gap above and below the tab.
                self.assertEqual(image.pixelColor(2, 2).alpha(), 0)
                self.assertEqual(image.pixelColor(2, RAIL_ITEM_HEIGHT - 2).alpha(), 0)
                # Square left corners, rounded right ones.
                self.assertGreater(image.pixelColor(0, 4).alpha(), 0)
                self.assertGreater(image.pixelColor(0, RAIL_ITEM_HEIGHT - 5).alpha(), 0)
                self.assertEqual(image.pixelColor(RAIL_WIDTH - 11, 4).alpha(), 0)
                self.assertEqual(image.pixelColor(RAIL_WIDTH - 11, RAIL_ITEM_HEIGHT - 5).alpha(), 0)
        idle = self.paint_item(QStyle.StateFlag.State_None)
        self.assertEqual(idle.pixelColor(0, middle).alpha(), 0)

    def test_selected_fill_is_stronger_than_hover(self):
        middle = RAIL_ITEM_HEIGHT // 2
        selected = self.paint_item(QStyle.StateFlag.State_Selected).pixelColor(2, middle)
        hovered = self.paint_item(QStyle.StateFlag.State_MouseOver).pixelColor(2, middle)
        self.assertGreater(selected.alpha(), hovered.alpha())

    def test_icon_is_centred_in_the_tab_and_the_badge_sits_at_its_top_right(self):
        delegate = NavRailItemDelegate()
        painter = MagicMock()
        shell = self.build_shell(show=False)
        index = shell.nav_list.model().index(1, 0)
        shell.nav_list.item(1).setData(NavRailItemDelegate.BADGE_ROLE, True)
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, RAIL_WIDTH, RAIL_ITEM_HEIGHT)
        option.state = QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_Enabled
        delegate.paint(painter, option, index)
        point = painter.drawPixmap.call_args.args[0]
        tab = highlight_rect(option.rect)
        self.assertEqual(point, QPoint((tab.width() - RAIL_ICON_SIZE) // 2, 4 + (48 - RAIL_ICON_SIZE) // 2))
        self.assertLess(point.x() + RAIL_ICON_SIZE / 2, RAIL_WIDTH / 2)
        painter.drawPath.assert_called()
        painter.drawRoundedRect.assert_not_called()
        badge = painter.drawEllipse.call_args.args[0]
        self.assertAlmostEqual(badge.center().x(), point.x() + RAIL_ICON_SIZE, delta=0.5)
        self.assertLess(badge.center().y(), point.y() + RAIL_ICON_SIZE / 4)
        self.assertAlmostEqual(badge.width(), RAIL_ICON_SIZE / 3, delta=0.5)

        image = self.paint_item(QStyle.StateFlag.State_Selected, badge=True)
        centre = badge.center()
        self.assertTrue(close_enough(image.pixelColor(int(centre.x()), int(centre.y())), qcolor("fl_accent")))

    # ---------------------------------------------------------------- layout

    def test_lists_fit_visible_rows_with_padding_and_sit_on_the_window_edge(self):
        shell = self.build_shell()
        self.assertEqual(shell.sidebar.width(), RAIL_WIDTH)
        self.assertEqual(shell.nav_list.height(), len(PAGES) * RAIL_ITEM_HEIGHT)
        self.assertEqual(shell.bottom_nav_list.height(), RAIL_ITEM_HEIGHT)
        self.assertEqual(shell.nav_list.y(), RAIL_TOP_PADDING)
        self.assertEqual(shell.sidebar.height() - shell.bottom_nav_list.geometry().bottom() - 1,
                         RAIL_BOTTOM_PADDING)
        self.assertEqual(shell.content_stack.x(), RAIL_WIDTH)
        for lst in (shell.nav_list, shell.bottom_nav_list):
            item_rect = lst.visualItemRect(lst.item(0))
            self.assertEqual(lst.viewport().mapTo(shell, item_rect.topLeft()).x(), 0)
            self.assertEqual(item_rect.width(), RAIL_WIDTH)
            self.assertEqual(item_rect.height(), RAIL_ITEM_HEIGHT)
        # Rows are evenly spaced; the gap between tabs lives inside the rows.
        second = shell.nav_list.visualItemRect(shell.nav_list.item(1))
        self.assertEqual(second.top(), RAIL_ITEM_HEIGHT)

        shell.set_page_visible("pc", False)
        self.assertEqual(shell.nav_list.height(), (len(PAGES) - 1) * RAIL_ITEM_HEIGHT)

    def assert_indicator_on(self, shell, lst, row):
        item_rect = lst.visualItemRect(lst.item(row))
        centre = lst.viewport().mapTo(shell.sidebar, item_rect.center())
        geometry = shell._indicator.geometry()
        self.assertTrue(shell._indicator.isVisible())
        self.assertEqual(geometry.x(), 0)
        self.assertEqual(geometry.width(), INDICATOR_WIDTH)
        self.assertEqual(geometry.height(), INDICATOR_HEIGHT)
        self.assertLessEqual(abs(geometry.center().y() - centre.y()), 1)

    def test_indicator_lands_on_the_selected_row_in_both_lists(self):
        shell = self.build_shell()
        self.assert_indicator_on(shell, shell.nav_list, 0)
        shell.set_current_by_key("music")
        QTest.qWait(400)
        self.assert_indicator_on(shell, shell.nav_list, 2)
        shell.set_current_by_key("settings")
        QTest.qWait(400)
        self.assert_indicator_on(shell, shell.bottom_nav_list, 0)
        shell.set_current_by_key("devices")
        QTest.qWait(400)
        self.assert_indicator_on(shell, shell.nav_list, 0)

    def test_rendered_tab_runs_from_the_window_edge_on_the_shared_background(self):
        shell = self.build_shell()
        shell.set_current_by_key("monitor")
        QTest.qWait(400)
        image = shell.grab().toImage()
        lst = shell.nav_list
        row = lst.visualItemRect(lst.item(1))
        y = lst.viewport().mapTo(shell, row.center()).y()
        dpr = image.devicePixelRatio()

        def at(x, yy):
            return image.pixelColor(int(x * dpr), int(yy * dpr))

        tab = at(INDICATOR_WIDTH + 2, y)
        beyond = at(RAIL_WIDTH - 4, y)
        content = at(RAIL_WIDTH + 40, y)
        self.assertGreater(tab.lightness(), beyond.lightness())
        # No divider: the rail outside the tab matches the content layer.
        self.assertTrue(close_enough(beyond, content, tolerance=2))
        self.assertTrue(close_enough(at(1, y), qcolor("fl_accent")))


class SidebarLinkTests(unittest.TestCase):
    """Links above Settings open in the browser and are never a destination."""

    @classmethod
    def setUpClass(cls):
        existing = QApplication.instance()
        cls.app = existing if isinstance(existing, QApplication) else QApplication([])

    def shell(self) -> NavigationShell:
        shell = NavigationShell(title="LumiSync", icon_only=True)
        self.addCleanup(shell.deleteLater)
        shell.add_page(key="devices", title="Devices", icon=QIcon(), widget=QWidget())
        shell.add_link(key="github", title="LumiSync on GitHub", svg="github.svg",
                       url="https://github.com/Minlor/LumiSync")
        shell.add_link(key="support", title="Support LumiSync on Ko-fi", svg="kofi.svg",
                       url="https://ko-fi.com/Minlor")
        shell.add_page(key="settings", title="Settings", icon=QIcon(), widget=QWidget(), bottom=True)
        return shell

    def test_links_sit_between_the_pages_and_settings(self):
        shell = self.shell()
        layout = shell.sidebar.layout()
        order = [layout.itemAt(i).widget() for i in range(layout.count())]
        self.assertLess(order.index(shell.nav_list), order.index(shell.link_nav_list))
        self.assertLess(order.index(shell.link_nav_list), order.index(shell.bottom_nav_list))
        self.assertEqual(shell.link_nav_list.count(), 2)
        self.assertEqual(shell.link_nav_list.height(), 2 * RAIL_ITEM_HEIGHT)
        self.assertEqual(shell.link_nav_list.item(0).toolTip(), "LumiSync on GitHub")

    def test_a_link_opens_its_address_and_leaves_the_page_alone(self):
        from unittest.mock import patch

        shell = self.shell()
        opened = []
        shell.link_activated.connect(opened.append)
        with patch("lumisync.gui.widgets.navigation_shell.QDesktopServices.openUrl", return_value=True) as open_url:
            shell.link_nav_list.itemClicked.emit(shell.link_nav_list.item(0))
            shell.link_nav_list.itemActivated.emit(shell.link_nav_list.item(1))
        self.assertEqual([call.args[0].toString() for call in open_url.call_args_list],
                         ["https://github.com/Minlor/LumiSync", "https://ko-fi.com/Minlor"])
        self.assertEqual(opened, ["github", "support"])
        self.assertEqual(shell.current_key(), "devices")
        self.assertEqual(shell.link_nav_list.currentRow(), -1)

    def test_the_logo_files_exist(self):
        from lumisync.gui.resources import ResourceManager

        for name in ("github.svg", "kofi.svg"):
            self.assertIsNotNone(ResourceManager.get_icon_path(name), name)


if __name__ == "__main__":
    unittest.main()
