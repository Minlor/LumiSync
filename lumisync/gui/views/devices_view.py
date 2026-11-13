"""Devices view — multi-device card grid."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import TYPE_CHECKING, Dict, Optional, Tuple

import shiboken6
from PySide6.QtCore import QEvent, QRectF, QSize, QTimer, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ... import connection
from ...groups import device_key
from ..controllers.device_controller import DeviceController
from ..dialogs.add_device_dialog import AddDeviceDialog
from ..resources.icons import IconKey, tinted_pixmap, tinted_icon
from ..theme import qcolor
from ..utils import firewall
from ..utils.device_grid import DeviceGridLayout
from ..widgets.color_flyout import remember_recent_color
from ..widgets.device_card import CARD_MIN_WIDTH, DeviceCard
from ..widgets.device_inspector import DeviceInspector
from ..widgets.info_bar import SEVERITY_CAUTION, InfoBar, paint_severity_icon

if TYPE_CHECKING:
    from ..controllers.firewall_monitor import FirewallMonitor

FIREWALL_TITLE = "Windows Firewall is blocking LumiSync"
FIREWALL_MESSAGE = (
    "LumiSync can't hear your lights on the network, so it can't find them or follow them "
    "when their address changes."
)
FIREWALL_BLOCK_ALL_MESSAGE = (
    "Windows Firewall is set to block all incoming connections, so LumiSync can't hear your "
    "lights. Turn that off in Windows Security › Firewall & network protection."
)
FIREWALL_PUBLIC_TITLE = "Your network is set to Public"
FIREWALL_PUBLIC_MESSAGE = (
    "Windows Firewall keeps LumiSync from hearing your lights on public networks. If this is "
    "your home network, set it to Private in Windows Settings › Network & internet."
)


class SearchButton(QPushButton):
    """Find devices: shows "Searching…" with a spinning arc while busy.

    The icon follows the button's text colour from the stylesheet, so it
    stays legible whatever the accent fill is.
    """

    IDLE_TEXT = "Find devices"
    BUSY_TEXT = "Searching…"

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(self.IDLE_TEXT, parent)
        self._searching = False
        self._angle = 0
        self._spin = QTimer(self)
        self._spin.setInterval(40)
        self._spin.timeout.connect(self._tick)
        self.setIconSize(QSize(16, 16))
        self._update_icon()

    def is_searching(self) -> bool:
        return self._searching

    def set_searching(self, searching: bool) -> None:
        self._searching = bool(searching)
        self.setText(self.BUSY_TEXT if searching else self.IDLE_TEXT)
        self.setEnabled(not searching)
        if searching:
            self._spin.start()
        else:
            self._spin.stop()
            self._angle = 0
        self.setAccessibleDescription("Searching for devices" if searching else "")
        self._update_icon()

    def _tick(self) -> None:
        self._angle = (self._angle + 30) % 360
        self._update_icon()

    def _foreground(self) -> QColor:
        group = QPalette.ColorGroup.Disabled if not self.isEnabled() else QPalette.ColorGroup.Active
        return self.palette().color(group, QPalette.ColorRole.ButtonText)

    def _spinner_pixmap(self, color: QColor) -> QPixmap:
        ratio = max(1.0, self.devicePixelRatioF())
        side = 16
        pixmap = QPixmap(int(side * ratio), int(side * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(2, 2, side - 4, side - 4)
        track = QColor(color)
        track.setAlphaF(0.25)
        painter.setPen(QPen(track, 2))
        painter.drawEllipse(rect)
        painter.setPen(QPen(color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(rect, int(-self._angle * 16), 100 * 16)
        painter.end()
        return pixmap

    def _update_icon(self) -> None:
        color = self._foreground()
        pixmap = self._spinner_pixmap(color) if self._searching else tinted_pixmap(IconKey.REFRESH, color, 16)
        icon = QIcon()
        for mode in (QIcon.Mode.Normal, QIcon.Mode.Disabled, QIcon.Mode.Active):
            icon.addPixmap(pixmap, mode)
        self.setIcon(icon)

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange, QEvent.Type.EnabledChange):
            self._update_icon()


def caution_dot_icon(side: int = 14) -> QIcon:
    """A painted caution dot, used as the Accounts button's attention icon."""
    pixmap = QPixmap(side * 2, side * 2)
    pixmap.setDevicePixelRatio(2.0)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    paint_severity_icon(painter, QRectF(0.5, 0.5, side - 1, side - 1), SEVERITY_CAUTION)
    painter.end()
    icon = QIcon()
    for mode in (QIcon.Mode.Normal, QIcon.Mode.Disabled, QIcon.Mode.Active):
        icon.addPixmap(pixmap, mode)
    return icon


class AccountsButton(QPushButton):
    """The Accounts header button; a caution dot leads the label while an
    account needs attention (an icon, so it never covers the text)."""

    def __init__(self, text: str, parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self._attention = False
        self.setIconSize(QSize(14, 14))

    def has_attention(self) -> bool:
        return self._attention

    def set_attention(self, attention: bool) -> None:
        if self._attention == bool(attention):
            return
        self._attention = bool(attention)
        self.setIcon(caution_dot_icon() if attention else QIcon())


class ProgressLine(QWidget):
    """A 2 px indeterminate accent line (WinUI's indeterminate ProgressBar)."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(2)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName("Searching for devices")
        self._running = False
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance)

    def is_running(self) -> bool:
        return self._running

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        if running:
            self._phase = 0.0
            self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def _advance(self) -> None:
        self._phase = (self._phase + 0.012) % 1.0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        if not self._running:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = float(self.width())
        segment = max(48.0, width * 0.32)
        start = -segment + (width + segment) * self._phase
        rect = QRectF(max(0.0, start), 0, min(width, start + segment) - max(0.0, start), self.height())
        if rect.width() <= 0:
            return
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qcolor("fl_accent"))
        painter.drawRoundedRect(rect, 1, 1)


class DevicesView(QWidget):
    """Stable page actions above a responsive inventory and device inspector."""

    def __init__(
        self,
        device_controller: DeviceController,
        firewall_monitor: Optional[FirewallMonitor] = None,
    ):
        super().__init__()
        self.controller = device_controller
        # Shared Windows Firewall state; None for pages built without one.
        self._firewall = firewall_monitor

        self._cards: list[DeviceCard] = []
        self._inspected_index = -1
        self._inspected_key = ""
        self._inspector_animation = None
        self._card_sync_pending = False
        # Connectivity problems reported by the last search.
        self._network_problem = False
        self._bluetooth_problem = False
        # InfoBar key -> the problem signature the user closed it on.
        self._dismissed: Dict[str, object] = {}
        self._issue_account_ids: list[str] = []

        # Keep the online/offline indicator honest: local devices are polled
        # while this page is visible, instead of only refreshing after a
        # command (which left unplugged devices showing "Online" forever).
        self._status_poll_timer = QTimer(self)
        self._status_poll_timer.setInterval(15_000)
        self._status_poll_timer.timeout.connect(self._poll_status)
        self._status_poll_timer.start()
        self._status_tick = 0

        self._build()
        self._connect_signals()
        self._rebuild_cards()

    def _poll_status(self) -> None:
        if not self.isVisible():
            return
        self._status_tick += 1
        if self._status_tick % 4 == 0:
            # Account/cloud status is slower and rate-limited, so refresh it
            # once a minute instead of every 15 seconds.
            self.controller.refresh_account_device_states()
        else:
            self.controller.refresh_local_device_states()
        self._recheck_network()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        # Refresh everything once when the page is opened rather than waiting
        # for the slower local/cloud cadence (the page may have been hidden for
        # hours). Local Tuya devices still fall back to cloud if LAN is down.
        QTimer.singleShot(400, self.controller.refresh_device_states)
        if self._firewall is not None:
            # A firewall setting may have changed meanwhile (at most once a
            # minute; the first check is the window's startup check).
            self._firewall.recheck_if_stale()

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(0)

        # Header: the page title with its actions on the same row.
        header_row = self.header_row = QHBoxLayout()
        header_row.setSpacing(8)
        header = QLabel("Devices")
        header.setProperty("role", "title")
        header_row.addWidget(header, 0, Qt.AlignmentFlag.AlignVCenter)
        header_row.addStretch(1)

        self.find_devices_button = SearchButton()
        self.find_devices_button.setObjectName("Primary")
        self.find_devices_button.setProperty("role", "primary")
        self.find_devices_button.setToolTip(
            "Search Wi-Fi and Bluetooth, refresh linked accounts, and update saved device readings"
        )
        self.find_devices_button.setAccessibleName("Find and refresh devices")
        self.find_devices_button.clicked.connect(self.controller.find_devices)
        header_row.addWidget(self.find_devices_button)

        self.add_button = QPushButton("Add device")
        self.add_button.setIcon(tinted_icon(IconKey.ADD, qcolor("text")))
        self.add_button.setToolTip("Add a device using its connection details")
        self.add_button.clicked.connect(self._on_add_manual)
        header_row.addWidget(self.add_button)

        self.accounts_button = AccountsButton("Accounts")
        self.accounts_button.setToolTip("Sign in to your Govee, Tuya, or linked LSC account")
        self.accounts_button.clicked.connect(lambda: self._open_accounts())
        header_row.addWidget(self.accounts_button)
        root.addLayout(header_row)
        root.addSpacing(12)

        # Page-level problems (accounts, network, Bluetooth) as InfoBars. They
        # replace the old per-transport status panel: nothing shows when all
        # is well.
        self.info_host = QWidget()
        info_layout = QVBoxLayout(self.info_host)
        info_layout.setContentsMargins(0, 0, 0, 10)
        info_layout.setSpacing(8)
        self.account_info_bar = InfoBar(SEVERITY_CAUTION, "", "", "Open Accounts")
        self.account_info_bar.setObjectName("AccountsInfoBar")
        self.account_info_bar.action_triggered.connect(self._open_issue_account)
        self.account_info_bar.closed.connect(lambda: self._dismiss("accounts"))
        self.network_info_bar = InfoBar(
            SEVERITY_CAUTION,
            "No local network connection",
            "Lights on your Wi-Fi can't be found or controlled until this PC is on the network.",
        )
        self.network_info_bar.setObjectName("NetworkInfoBar")
        self.network_info_bar.closed.connect(lambda: self._dismiss("network"))
        self.firewall_info_bar = InfoBar(SEVERITY_CAUTION, FIREWALL_TITLE, FIREWALL_MESSAGE, "Allow")
        self.firewall_info_bar.setObjectName("FirewallInfoBar")
        self.firewall_info_bar.action_button.setAccessibleName("Allow LumiSync through Windows Firewall")
        self.firewall_info_bar.action_triggered.connect(self._allow_firewall)
        self.firewall_info_bar.closed.connect(lambda: self._dismiss("firewall"))
        self.bluetooth_info_bar = InfoBar(
            SEVERITY_CAUTION,
            "Bluetooth is off",
            "Turn on Bluetooth to reach your Bluetooth panels.",
        )
        self.bluetooth_info_bar.setObjectName("BluetoothInfoBar")
        self.bluetooth_info_bar.closed.connect(lambda: self._dismiss("bluetooth"))
        for bar in self._info_bars():
            bar.hide()
            info_layout.addWidget(bar)
        self.info_host.hide()
        root.addWidget(self.info_host)

        # Indeterminate progress while Find devices runs. It always takes its
        # 2 px so the grid never jumps when a search starts or ends.
        self.search_progress = ProgressLine()
        root.addWidget(self.search_progress)
        root.addSpacing(4)

        # The inventory fills each row rather than wrapping fixed-width cards.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        # Cards hug their content: the grid takes its natural height and the
        # remaining space stays below it instead of stretching every card.
        scroll_body = QWidget()
        scroll_body_layout = QVBoxLayout(scroll_body)
        scroll_body_layout.setContentsMargins(0, 0, 0, 0)
        scroll_body_layout.setSpacing(0)
        self.grid_host = QWidget()
        self.grid_layout = DeviceGridLayout(self.grid_host, h_spacing=12, v_spacing=12)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        scroll_body_layout.addWidget(self.grid_host)
        scroll_body_layout.addStretch(1)

        self.scroll.setWidget(scroll_body)

        self.inspector = DeviceInspector()
        self.inspector.close_requested.connect(self._close_inspector)
        self.inspector.power_clicked.connect(self.controller.toggle_power_at)
        self.inspector.refresh_requested.connect(self.controller.refresh_device_state_at)
        self.inspector.set_default_requested.connect(
            self._on_card_primary_clicked
        )
        self.inspector.color_picked.connect(self._on_card_color_picked)
        self.inspector.brightness_changed.connect(
            self.controller.set_brightness_at
        )
        self.inspector.color_temp_changed.connect(
            self.controller.set_color_temperature_at
        )
        self.inspector.zone_count_requested.connect(self._on_card_zone_count)
        self.inspector.zone_count_reset_requested.connect(
            self._on_card_zone_count_reset
        )
        self.inspector.remove_requested.connect(self._on_card_remove)

        self.inspector_scroll = QScrollArea()
        self.inspector_scroll.setObjectName("DeviceInspectorScroll")
        self.inspector_scroll.setWidgetResizable(True)
        self.inspector_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.inspector_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.inspector_scroll.setWidget(self.inspector)
        self.inspector_scroll.viewport().installEventFilter(self)
        self.inspector_scroll.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding
        )
        self.inspector_scroll.setMinimumWidth(0)
        self.inspector_scroll.setMaximumWidth(0)
        self.inspector_scroll.setVisible(False)

        self.content_host = QWidget()
        body = QHBoxLayout(self.content_host)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)
        self.main_column = QWidget()
        self.main_column.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        content_layout = QVBoxLayout(self.main_column)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self.scroll, 1)
        body.addWidget(self.main_column, 1)
        body.addWidget(self.inspector_scroll)
        root.addWidget(self.content_host, 1)

        # Empty state label
        self.empty_label = QLabel(
            "No devices yet\n\nChoose Find devices to search your local network and "
            "nearby Bluetooth panels, or add a device using its connection details."
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setProperty("role", "empty")
        self.empty_label.setVisible(False)
        root.addWidget(self.empty_label, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if not hasattr(self, "inspector_scroll"):
            return
        if self._inspected_index >= 0:
            self._layout_inspector()

    def _inspector_geometry(self) -> tuple[bool, int]:
        available = max(0, self.width() - 56)
        inspector_width = max(400, min(480, int(available * 0.34)))
        # Allow a scrollbar as well as two readable inventory columns. The
        # header spans both panes, so it does not determine this breakpoint.
        main_minimum = CARD_MIN_WIDTH * 2 + 12 + 10
        body_height = self.height() - self.content_host.y() - self.layout().contentsMargins().bottom()
        compact = available < inspector_width + 16 + main_minimum or body_height < 460
        return compact, inspector_width

    def _layout_inspector(self) -> bool:
        compact, inspector_width = self._inspector_geometry()
        self.main_column.setVisible(not compact)
        self.inspector_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding if compact else QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Expanding,
        )
        self.inspector.setMaximumWidth(880 if compact else 16777215)
        self.inspector_scroll.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        self._stop_inspector_animation()
        self.inspector_scroll.setMinimumWidth(0 if compact else inspector_width)
        self.inspector_scroll.setMaximumWidth(16777215 if compact else inspector_width)
        return compact

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if watched is self.inspector_scroll.viewport() and event.type() == QEvent.Type.Resize:
            size = event.size()
            self.inspector.set_available_size(size.width(), size.height())
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------ wiring

    def _connect_signals(self) -> None:
        # Device cards are only rebuilt when the visible device set actually
        # changes: every card is a full widget tree, and rebuilding them on
        # each refresh made the grid flash ("a bunch of mini windows") and
        # spiked CPU. State updates already flow through device_state_updated.
        self.controller.devices_discovered.connect(lambda *_: self._sync_cards())
        self.controller.device_added.connect(lambda *_: self._sync_cards())
        self.controller.device_removed.connect(lambda *_: self._sync_cards())
        self.controller.device_updated.connect(lambda *_: self._sync_cards())
        self.controller.device_state_updated.connect(self._on_device_state_updated)
        self.controller.device_search_started.connect(self._on_search_started)
        self.controller.device_search_finished.connect(self._on_search_finished)
        self.controller.account_issues_changed.connect(lambda *_: self._update_info_bars())
        if self._firewall is not None:
            self._firewall.changed.connect(lambda *_: self._update_info_bars())
            self._firewall.allow_finished.connect(lambda *_: self._update_info_bars())
        self.inspector.connection_changed.connect(self.controller.set_connection_at)
        self.inspector.panel_tools_requested.connect(self._open_panel_tools)

    def _open_panel_tools(self, index: int) -> None:
        from ..dialogs.panel_tools_dialog import PanelToolsDialog
        if 0 <= index < len(self.controller.devices):
            PanelToolsDialog(self.controller, index, self).exec()

    def _open_accounts(self, account_id: Optional[str] = None) -> None:
        from ..dialogs.accounts_dialog import AccountsDialog
        dialog = AccountsDialog(self.controller, self, select_account=account_id)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _open_issue_account(self) -> None:
        self._open_accounts(self._issue_account_ids[0] if self._issue_account_ids else None)

    # ------------------------------------------------------------------ search

    def _on_search_started(self) -> None:
        # No toast and no status panel: the button and a thin progress line
        # say a search is running.
        self.find_devices_button.set_searching(True)
        self.search_progress.set_running(True)

    def _on_search_finished(self, summary: dict) -> None:
        self.find_devices_button.set_searching(False)
        self.search_progress.set_running(False)
        summary = summary if isinstance(summary, dict) else {}
        lan = summary.get("lan")
        if isinstance(lan, dict):
            self._network_problem = lan.get("available") is False
        bluetooth = summary.get("bluetooth")
        if isinstance(bluetooth, dict):
            self._bluetooth_problem = bluetooth.get("available") is False
        self._update_info_bars()

    def _recheck_network(self) -> None:
        """Clear the network InfoBar as soon as a route exists again."""
        if not self._network_problem:
            return
        probe = getattr(self.controller, "_local_network_available", None)
        try:
            available = probe() if callable(probe) else None
        except Exception:
            available = None
        if available is True:
            self._network_problem = False
            self._update_info_bars()

    # ------------------------------------------------------------------ info bars

    def account_issue_parts(self) -> Dict[str, Tuple[str, str]]:
        """Account id -> (label, reason) for accounts that need attention."""
        issues = getattr(self.controller, "account_issues", None)
        if not isinstance(issues, dict):
            return {}
        getter = getattr(self.controller, "account_issue", None)
        parts: Dict[str, Tuple[str, str]] = {}
        for account_id, text in issues.items():
            pair = getter(account_id) if callable(getter) else None
            if isinstance(pair, tuple) and len(pair) == 2:
                parts[str(account_id)] = (str(pair[0]), str(pair[1]))
            else:
                label, _sep, reason = str(text).partition(": ")
                parts[str(account_id)] = (label, reason)
        return parts

    def _has_bluetooth_devices(self) -> bool:
        devices = getattr(self.controller, "devices", None)
        if not isinstance(devices, list):
            return False
        return any(str(device.get("transport") or "").lower() == "ble"
                   for device in devices if isinstance(device, Mapping))

    def _dismiss(self, key: str) -> None:
        self._dismissed[key] = self._signature(key)
        self._update_info_host()

    def _signature(self, key: str) -> object:
        if key == "accounts":
            return tuple(sorted(self.account_issue_parts().items()))
        if key == "network":
            return self._network_problem
        if key == "firewall":
            state = self._firewall.state() if self._firewall is not None else None
            return (state.status, state.block_all) if state is not None else None
        return self._bluetooth_problem

    def _info_bars(self) -> Tuple[InfoBar, ...]:
        return (self.account_info_bar, self.network_info_bar, self.firewall_info_bar, self.bluetooth_info_bar)

    # ------------------------------------------------------------------ firewall

    def _firewall_problem(self) -> bool:
        monitor = self._firewall
        if monitor is None or monitor.is_verifying():
            # A successful Allow hides the bar while the re-check confirms it.
            return False
        return monitor.state().status in (firewall.STATUS_MISSING, firewall.STATUS_BLOCKED, firewall.STATUS_PUBLIC)

    def _update_firewall_bar(self) -> bool:
        problem = self._firewall_problem()
        if problem and self._firewall is not None:
            state = self._firewall.state()
            if state.status == firewall.STATUS_PUBLIC:
                # LumiSync never allows itself on public networks.
                self.firewall_info_bar.set_content(SEVERITY_CAUTION, FIREWALL_PUBLIC_TITLE, FIREWALL_PUBLIC_MESSAGE)
            elif state.block_all:
                self.firewall_info_bar.set_content(SEVERITY_CAUTION, FIREWALL_TITLE, FIREWALL_BLOCK_ALL_MESSAGE)
            else:
                self.firewall_info_bar.set_content(SEVERITY_CAUTION, FIREWALL_TITLE, FIREWALL_MESSAGE, "Allow")
            self.firewall_info_bar.action_button.setEnabled(not self._firewall.is_allowing())
        self._show_bar("firewall", self.firewall_info_bar, problem)
        return problem

    def _window_handle(self) -> Optional[int]:
        """The top-level window's handle, so the UAC prompt opens in front."""
        window = self.window()
        if sys.platform != "win32" or window is None or not window.isVisible():
            return None
        try:
            return int(window.winId())
        except Exception:
            return None

    def _allow_firewall(self) -> None:
        monitor = self._firewall
        if monitor is None or not monitor.state().can_allow:
            return
        if monitor.allow(self._window_handle(), self._on_firewall_allowed):
            self.firewall_info_bar.action_button.setEnabled(False)

    def _on_firewall_allowed(self, ok: bool, message: str) -> None:
        if not shiboken6.isValid(self):
            return
        self.firewall_info_bar.action_button.setEnabled(True)
        if not ok and message != firewall.CANCELLED:
            # Declining the UAC prompt is a choice, not an error; anything
            # else gets one toast. The bar stays until a re-check clears it.
            self.controller.notice.emit(message, "error")

    def _show_bar(self, key: str, bar: InfoBar, problem: bool) -> None:
        if not problem:
            self._dismissed.pop(key, None)
            bar.hide()
            return
        bar.setVisible(self._dismissed.get(key) != self._signature(key))

    def _update_info_bars(self) -> None:
        parts = self.account_issue_parts()
        self._issue_account_ids = list(parts)
        if len(parts) == 1:
            label, reason = next(iter(parts.values()))
            self.account_info_bar.set_content(SEVERITY_CAUTION, f"Couldn't refresh {label}", reason, "Open Accounts")
        elif parts:
            labels = ", ".join(label for label, _reason in parts.values())
            self.account_info_bar.set_content(
                SEVERITY_CAUTION,
                f"{len(parts)} accounts couldn't refresh",
                f"{labels}. Open Accounts to reconnect them.",
                "Open Accounts",
            )
        self._show_bar("accounts", self.account_info_bar, bool(parts))
        self._show_bar("network", self.network_info_bar, self._network_problem)
        self._update_firewall_bar()
        self._show_bar(
            "bluetooth", self.bluetooth_info_bar, self._bluetooth_problem and self._has_bluetooth_devices()
        )
        self._update_accounts_badge(parts)
        self._update_info_host()

    def _update_info_host(self) -> None:
        visible = any(not bar.isHidden() for bar in self._info_bars())
        self.info_host.setVisible(visible)

    def _update_accounts_badge(self, parts: Dict[str, Tuple[str, str]]) -> None:
        self.accounts_button.set_attention(bool(parts))
        if parts:
            names = "\n".join(f"• {label}" for label, _reason in parts.values())
            self.accounts_button.setToolTip(f"Needs attention:\n{names}")
            self.accounts_button.setAccessibleDescription(
                f"{len(parts)} account{'s' if len(parts) != 1 else ''} need attention"
            )
        else:
            self.accounts_button.setToolTip("Sign in to your Govee, Tuya, or linked LSC account")
            self.accounts_button.setAccessibleDescription("")

    # ------------------------------------------------------------------ cards

    _CARD_SIGNATURE_FIELDS = ("name", "model", "sku", "transport", "device_id", "mac")

    def _card_signature(self, device: dict) -> tuple:
        return tuple(str(device.get(field) or "") for field in self._CARD_SIGNATURE_FIELDS)

    def _sync_cards(self) -> None:
        """Coalesce refresh bursts and skip rebuilds when nothing changed."""
        if self._card_sync_pending:
            return
        self._card_sync_pending = True
        # Bound to this view: a page deleted meanwhile never rebuilds.
        QTimer.singleShot(0, self, self._apply_device_list)

    def _apply_device_list(self) -> None:
        self._card_sync_pending = False
        devices = self.controller.devices
        current = [self._card_signature(card.device()) for card in self._cards]
        wanted = [self._card_signature(device) for device in devices]
        if current == wanted:
            # Same devices in the same order: keep the existing cards. States
            # arrive via device_state_updated, so nothing visible goes stale.
            for index, card in enumerate(self._cards):
                card.set_inspected(index == self._inspected_index)
            return
        self._rebuild_cards()

    def _rebuild_cards(self) -> None:
        # Wipe existing
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._cards.clear()
        # Refreshes can reorder devices or remove a preceding row. Keep the
        # open inspector attached to the same device identity.
        self._inspected_index = next(
            (
                index
                for index, device in enumerate(self.controller.devices)
                if self._device_key(device) == self._inspected_key
            ),
            -1,
        )
        if self._inspected_index < 0:
            self._inspected_key = ""

        for idx, device in enumerate(self.controller.devices):
            card = DeviceCard(idx, device, self.grid_host)
            card.set_inspected(idx == self._inspected_index)
            card.set_state(self.controller.get_device_state_at(idx))
            card.details_requested.connect(self._open_inspector)
            card.power_clicked.connect(self.controller.toggle_power_at)
            card.brightness_changed.connect(self.controller.set_brightness_at)
            card.color_picked.connect(self._on_card_color_picked)
            card.color_temp_changed.connect(self.controller.set_color_temperature_at)
            card.custom_color_requested.connect(self._pick_custom_color)
            self._cards.append(card)
            self.grid_layout.addWidget(card)

        if self._inspected_index >= 0:
            self._populate_inspector()
        elif self.inspector_scroll.isVisible():
            self._hide_inspector_immediately()

        self._update_empty_state()
        # A Bluetooth panel may have been added or removed.
        self._update_info_bars()
        self.controller.refresh_device_states()

    def _update_empty_state(self) -> None:
        empty = not self.controller.devices
        self.empty_label.setVisible(empty)
        self.content_host.setVisible(not empty)

    @staticmethod
    def _device_key(device: dict) -> str:
        return device_key(device)

    def _open_inspector(self, index: int) -> None:
        if not (0 <= index < len(self.controller.devices)):
            return
        self._stop_inspector_animation()
        self._inspected_index = index
        self._inspected_key = self._device_key(self.controller.devices[index])
        for card in self._cards:
            card.set_inspected(card._index == index)
        self._populate_inspector()

        self._layout_inspector()
        self.inspector_scroll.setVisible(True)

    def _populate_inspector(self) -> None:
        index = self._inspected_index
        if not (0 <= index < len(self.controller.devices)):
            return
        self.inspector.set_device(
            index,
            self.controller.devices[index],
            self.controller.get_device_state_at(index),
            primary=index == self.controller.selected_device_index,
        )

    def _close_inspector(self) -> None:
        for card in self._cards:
            card.set_inspected(False)
        self._hide_inspector_immediately()

    def _hide_inspector_immediately(self) -> None:
        self._stop_inspector_animation()
        self._inspected_index = -1
        self._inspected_key = ""
        self.inspector_scroll.setMinimumWidth(0)
        self.inspector_scroll.setMaximumWidth(0)
        self.inspector_scroll.setVisible(False)
        self.main_column.show()

    def _stop_inspector_animation(self) -> None:
        if self._inspector_animation is None:
            return
        try:
            self._inspector_animation.stop()
        except RuntimeError:
            pass
        self._inspector_animation = None

    # ------------------------------------------------------------------ card slots

    def _on_card_primary_clicked(self, index: int) -> None:
        self.controller.select_device(index)
        if self._inspected_index >= 0:
            self._populate_inspector()

    def _on_card_color_picked(self, index: int, color: QColor) -> None:
        self.controller.set_color_at(index, color.red(), color.green(), color.blue())

    def _card_for_key(self, key: str) -> tuple[int, Optional[DeviceCard]]:
        index = next((i for i, device in enumerate(self.controller.devices) if self._device_key(device) == key), -1)
        card = self._cards[index] if 0 <= index < len(self._cards) else None
        if card is not None and (not shiboken6.isValid(card) or self._device_key(card.device()) != key):
            card = None  # Deleted, or a rebuild for the new order is still pending.
        return index, card

    def _pick_custom_color(self, index: int) -> None:
        """"More colours…" for a card, keyed by the device, not the card.

        The colour dialog runs a nested event loop. A search finishing
        meanwhile can rebuild (delete) every card, so the dialog is parented
        to this page and the result is applied to whatever card now shows
        the same device.
        """
        if not (0 <= index < len(self.controller.devices)):
            return
        key = self._device_key(self.controller.devices[index])
        _index, card = self._card_for_key(key)
        initial = (card.current_color() if card is not None else None) or QColor("#FFFFFF")
        chosen = QColorDialog.getColor(initial, self, "Choose device colour")
        if not shiboken6.isValid(self) or not chosen.isValid():
            return
        remember_recent_color(chosen)
        index, card = self._card_for_key(key)
        if index < 0:
            return  # The device was removed while the dialog was open.
        if card is not None:
            card.preview_color(chosen)
        self.controller.set_color_at(index, chosen.red(), chosen.green(), chosen.blue())

    def _on_device_state_updated(self, index: int, state: dict) -> None:
        if 0 <= index < len(self._cards):
            self._cards[index].set_state(state)
        if index == self._inspected_index:
            self.inspector.set_state(state)

    def _on_card_remove(self, index: int) -> None:
        if not (0 <= index < len(self.controller.devices)):
            return
        device = self.controller.devices[index]
        address = (
            device.get("ble_address")
            or device.get("ip")
            or device.get("mac")
            or "unknown address"
        )
        reply = QMessageBox.question(
            self, "Remove device",
            f"Remove '{device.get('model', 'Unknown')}' ({address})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.controller.remove_device(index)

    def _on_card_zone_count(self, index: int) -> None:
        if not (0 <= index < len(self.controller.devices)):
            return
        device = self.controller.devices[index]
        current = connection.get_segment_count(device)
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Set Zone Count")
        dialog.setLabelText("Number of LED zones")
        dialog.setInputMode(QInputDialog.InputMode.IntInput)
        dialog.setIntRange(1, 255)
        dialog.setIntStep(1)
        dialog.setIntValue(current)
        dialog.setOkButtonText("Save Zones")
        if dialog.exec():
            self.controller.set_zone_count_at(index, dialog.intValue())

    def _on_card_zone_count_reset(self, index: int) -> None:
        self.controller.set_zone_count_at(index, None)

    # ------------------------------------------------------------------ misc

    def _on_add_manual(self) -> None:
        dialog = AddDeviceDialog(self)
        if dialog.exec():
            if dialog.device_type() == "ble":
                address = dialog.ble_address_entry.text().strip()
                model = dialog.model_entry.text() or "iDotMatrix"
                self.controller.add_ble_device_manually(
                    address, model, dialog.matrix_size()
                )
                return
            if dialog.device_type() == "tuya":
                self.controller.add_tuya_device_manually(
                    dialog.ip_entry.text(),
                    dialog.tuya_device_id(),
                    dialog.tuya_local_key(),
                    dialog.model_entry.text() or "LSC / Tuya Light",
                    dialog.tuya_version(),
                    dp_schema=dialog.tuya_schema_combo.currentData(),
                    remember=dialog.remember_key.isChecked(),
                )
                return
            ip = dialog.ip_entry.text()
            model = dialog.model_entry.text() or "Manual Device"
            mac = dialog.mac_entry.text() or None
            port = int(dialog.port_entry.text()) if dialog.port_entry.text() else 4003
            self.controller.add_device_manually(ip, model, mac, port)
