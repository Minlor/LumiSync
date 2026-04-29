"""Devices view — multi-device card grid."""

from __future__ import annotations

from typing import Set

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..controllers.device_controller import DeviceController
from ..dialogs.add_device_dialog import AddDeviceDialog
from ..theme import qcolor
from ..utils.animations import animate_height
from ..utils.flow_layout import FlowLayout
from ..widgets.device_card import DeviceCard


class DevicesView(QWidget):
    """Card grid of devices with multi-select and bulk actions."""

    def __init__(self, device_controller: DeviceController):
        super().__init__()
        self.controller = device_controller

        self._cards: list[DeviceCard] = []
        self._selected: Set[int] = set()

        self._build()
        self._connect_signals()
        self._rebuild_cards()

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        # Header
        header = QLabel("Devices")
        header.setProperty("role", "header")
        f = QFont()
        f.setPointSize(18)
        f.setBold(True)
        header.setFont(f)
        root.addWidget(header)

        # Toolbar row
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self.discover_button = QPushButton("Discover Devices")
        self.discover_button.setObjectName("Primary")
        self.discover_button.setProperty("role", "primary")
        self.discover_button.clicked.connect(self.controller.discover_devices)
        toolbar.addWidget(self.discover_button)

        self.add_button = QPushButton("Add Manually")
        self.add_button.clicked.connect(self._on_add_manual)
        toolbar.addWidget(self.add_button)

        toolbar.addSpacing(8)
        toolbar.addStretch(1)

        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet(f"color: {qcolor('text_dim').name()}; font-size: 9pt;")
        toolbar.addWidget(self.summary_label)

        self.select_all_button = QPushButton("Select all")
        self.select_all_button.setObjectName("LinkButton")
        self.select_all_button.clicked.connect(self._select_all)
        toolbar.addWidget(self.select_all_button)

        self.clear_select_button = QPushButton("Clear")
        self.clear_select_button.setObjectName("LinkButton")
        self.clear_select_button.clicked.connect(self._clear_selection)
        toolbar.addWidget(self.clear_select_button)

        root.addLayout(toolbar)

        # Bulk action bar (animated, hidden until selection)
        self.bulk_bar = QFrame()
        self.bulk_bar.setObjectName("BulkBar")
        self.bulk_bar.setMaximumHeight(0)
        bulk_layout = QHBoxLayout(self.bulk_bar)
        bulk_layout.setContentsMargins(12, 8, 12, 8)
        bulk_layout.setSpacing(8)

        self.bulk_label = QLabel("")
        self.bulk_label.setStyleSheet(f"color: {qcolor('text').name()}; font-weight: 600;")
        bulk_layout.addWidget(self.bulk_label)
        bulk_layout.addStretch(1)

        self.bulk_on = QPushButton("Turn On")
        self.bulk_on.clicked.connect(lambda: self._bulk_power(True))
        bulk_layout.addWidget(self.bulk_on)

        self.bulk_off = QPushButton("Turn Off")
        self.bulk_off.clicked.connect(lambda: self._bulk_power(False))
        bulk_layout.addWidget(self.bulk_off)

        self.bulk_color = QPushButton("Set Color")
        self.bulk_color.clicked.connect(self._bulk_color)
        bulk_layout.addWidget(self.bulk_color)

        self.bulk_remove = QPushButton("Remove")
        self.bulk_remove.setObjectName("DangerButton")
        self.bulk_remove.setProperty("role", "danger")
        self.bulk_remove.clicked.connect(self._bulk_remove)
        bulk_layout.addWidget(self.bulk_remove)

        root.addWidget(self.bulk_bar)

        # Card grid (FlowLayout in a scroll area)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.grid_host = QWidget()
        self.grid_layout = FlowLayout(self.grid_host, h_spacing=14, v_spacing=14)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)

        self.scroll.setWidget(self.grid_host)
        root.addWidget(self.scroll, 1)

        # Empty state label
        self.empty_label = QLabel(
            "No devices yet.\n\nClick “Discover Devices” to find Govee lights on your "
            "network, or “Add Manually” to add one by IP."
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet(
            f"color: {qcolor('text_dim').name()}; font-size: 11pt; padding: 40px;"
        )
        self.empty_label.setVisible(False)
        root.addWidget(self.empty_label)

    # ------------------------------------------------------------------ wiring

    def _connect_signals(self) -> None:
        self.controller.devices_discovered.connect(lambda *_: self._rebuild_cards())
        self.controller.device_added.connect(lambda *_: self._rebuild_cards())
        self.controller.device_removed.connect(lambda *_: self._rebuild_cards())
        self.controller.device_state_updated.connect(self._on_device_state_updated)
        self.controller.discovery_started.connect(self._on_discovery_started)
        self.controller.discovery_finished.connect(self._on_discovery_finished)

    def _on_discovery_started(self) -> None:
        self.discover_button.setEnabled(False)
        self.discover_button.setText("Discovering…")

    def _on_discovery_finished(self) -> None:
        self.discover_button.setEnabled(True)
        self.discover_button.setText("Discover Devices")

    # ------------------------------------------------------------------ cards

    def _rebuild_cards(self) -> None:
        # Wipe existing
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._cards.clear()
        # Reconcile selection: drop indices that no longer exist
        max_idx = len(self.controller.devices) - 1
        self._selected = {i for i in self._selected if 0 <= i <= max_idx}

        for idx, device in enumerate(self.controller.devices):
            card = DeviceCard(idx, device, self.grid_host)
            card.set_checked(idx in self._selected)
            card.set_primary(idx == self.controller.selected_device_index)
            card.set_state(self.controller.get_device_state_at(idx))
            card.selection_changed.connect(self._on_card_selection_changed)
            card.primary_clicked.connect(self._on_card_primary_clicked)
            card.power_clicked.connect(self.controller.toggle_power_at)
            card.color_picked.connect(self._on_card_color_picked)
            card.brightness_changed.connect(self.controller.set_brightness_at)
            card.remove_requested.connect(self._on_card_remove)
            self._cards.append(card)
            self.grid_layout.addWidget(card)

        self._update_summary()
        self._update_empty_state()
        self.controller.refresh_device_states()

    def _update_empty_state(self) -> None:
        empty = not self.controller.devices
        self.empty_label.setVisible(empty)
        self.scroll.setVisible(not empty)
        # Hide selection actions when there's only 1 or 0 devices
        multi = len(self.controller.devices) > 1
        self.select_all_button.setVisible(multi)
        self.clear_select_button.setVisible(multi)

    def _update_summary(self) -> None:
        total = len(self.controller.devices)
        if total == 0:
            self.summary_label.setText("")
        elif self._selected:
            self.summary_label.setText(f"{len(self._selected)} of {total} selected")
        else:
            self.summary_label.setText(f"{total} device{'s' if total != 1 else ''}")
        self._update_bulk_bar()

    def _update_bulk_bar(self) -> None:
        n = len(self._selected)
        target = 56 if n > 0 else 0
        self.bulk_label.setText(f"{n} selected" if n else "")
        if self.bulk_bar.maximumHeight() != target:
            animate_height(self.bulk_bar, target, duration=180)

    # ------------------------------------------------------------------ card slots

    def _on_card_selection_changed(self, index: int, selected: bool) -> None:
        if selected:
            self._selected.add(index)
        else:
            self._selected.discard(index)
        self._update_summary()

    def _on_card_primary_clicked(self, index: int) -> None:
        self.controller.select_device(index)
        for card in self._cards:
            card.set_primary(card._index == index)

    def _on_card_color_picked(self, index: int, color: QColor) -> None:
        self.controller.set_color_at(index, color.red(), color.green(), color.blue())

    def _on_device_state_updated(self, index: int, state: dict) -> None:
        if 0 <= index < len(self._cards):
            self._cards[index].set_state(state)

    def _on_card_remove(self, index: int) -> None:
        device = self.controller.devices[index]
        reply = QMessageBox.question(
            self, "Remove device",
            f"Remove '{device.get('model', 'Unknown')}' ({device.get('ip', '?')})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.controller.remove_device(index)

    # ------------------------------------------------------------------ bulk

    def _select_all(self) -> None:
        self._selected = set(range(len(self.controller.devices)))
        for card in self._cards:
            card.set_checked(True)
        self._update_summary()

    def _clear_selection(self) -> None:
        self._selected.clear()
        for card in self._cards:
            card.set_checked(False)
        self._update_summary()

    def _bulk_power(self, on: bool) -> None:
        for idx in sorted(self._selected):
            self.controller.turn_on_off_at(idx, on)

    def _bulk_color(self) -> None:
        if not self._selected:
            return
        color = QColorDialog.getColor(QColor("#7C5CFF"), self, "Choose color")
        if not color.isValid():
            return
        for idx in sorted(self._selected):
            self.controller.set_color_at(idx, color.red(), color.green(), color.blue())

    def _bulk_remove(self) -> None:
        if not self._selected:
            return
        reply = QMessageBox.question(
            self, "Remove devices",
            f"Remove {len(self._selected)} selected device(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Remove from highest index down to keep lower indices valid
        for idx in sorted(self._selected, reverse=True):
            self.controller.remove_device(idx)
        self._selected.clear()

    # ------------------------------------------------------------------ misc

    def _on_add_manual(self) -> None:
        dialog = AddDeviceDialog(self)
        if dialog.exec():
            ip = dialog.ip_entry.text()
            model = dialog.model_entry.text() or "Manual Device"
            mac = dialog.mac_entry.text() or None
            port = int(dialog.port_entry.text()) if dialog.port_entry.text() else 4003
            self.controller.add_device_manually(ip, model, mac, port)
