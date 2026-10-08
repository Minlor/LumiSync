"""Devices view — multi-device card grid."""

from __future__ import annotations

from typing import Set

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QFrame,
    QGridLayout,
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
from ...drivers.registry import capabilities_for_device
from ...groups import device_key
from ..controllers.device_controller import DeviceController
from ..dialogs.add_device_dialog import AddDeviceDialog
from ..resources.icons import IconKey, tinted_icon
from ..theme import qcolor
from ..utils.animations import animate_height
from ..utils.device_grid import DeviceGridLayout
from ..widgets.device_card import CARD_MIN_WIDTH, DeviceCard
from ..widgets.device_inspector import DeviceInspector


class DevicesView(QWidget):
    """Stable page actions above a responsive inventory and device inspector."""

    def __init__(self, device_controller: DeviceController):
        super().__init__()
        self.controller = device_controller

        self._cards: list[DeviceCard] = []
        self._selected: Set[int] = set()
        self._inspected_index = -1
        self._inspected_key = ""
        self._inspector_animation = None
        self._group_selection_mode = False

        self._build()
        self._connect_signals()
        self._rebuild_cards()

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(18)

        # Header
        page_header = QVBoxLayout()
        page_header.setSpacing(3)
        header = QLabel("Devices")
        header.setProperty("role", "title")
        page_header.addWidget(header)

        intro = QLabel("Control your lights, switches, and smart plugs. Open a device for its available controls and readings.")
        intro.setProperty("role", "pageDescription")
        intro.setWordWrap(True)
        page_header.addWidget(intro)
        root.addLayout(page_header)

        # Toolbar row
        toolbar = self.toolbar = QGridLayout()
        toolbar.setSpacing(10)

        self.find_devices_button = QPushButton("Find Devices")
        self.find_devices_button.setObjectName("Primary")
        self.find_devices_button.setProperty("role", "primary")
        self.find_devices_button.setIcon(tinted_icon(IconKey.REFRESH, "#FFFFFF"))
        self.find_devices_button.setToolTip(
            "Search Wi-Fi and Bluetooth, refresh linked accounts, and update saved device readings"
        )
        self.find_devices_button.setAccessibleName("Find and refresh devices")
        self.find_devices_button.clicked.connect(self.controller.find_devices)
        toolbar.addWidget(self.find_devices_button, 0, 0)

        self.add_button = QPushButton("Add Device")
        self.add_button.setIcon(tinted_icon(IconKey.ADD, qcolor("text")))
        self.add_button.setToolTip("Add a device using its connection details")
        self.add_button.clicked.connect(self._on_add_manual)
        toolbar.addWidget(self.add_button, 0, 1)

        self.accounts_button = QPushButton("Accounts")
        self.accounts_button.setToolTip("Sign in to your Govee, Tuya, or linked LSC account")
        self.accounts_button.clicked.connect(self._open_accounts)
        toolbar.addWidget(self.accounts_button, 0, 2)
        toolbar.setColumnStretch(3, 1)

        self.summary_label = QLabel("")
        self.summary_label.setProperty("role", "subtle")
        toolbar.addWidget(self.summary_label, 0, 3, Qt.AlignmentFlag.AlignRight)

        self.group_mode_button = QPushButton("Create Group")
        self.group_mode_button.setIcon(
            tinted_icon(IconKey.ADD, qcolor("text"))
        )
        self.group_mode_button.setToolTip(
            "Choose devices by clicking their cards, then save them as a group"
        )
        self.group_mode_button.clicked.connect(self._start_group_selection)
        toolbar.addWidget(self.group_mode_button, 0, 4)

        root.addLayout(toolbar)

        # A combined search can partially succeed. Keep each transport visible
        # so an empty result is not confused with unavailable hardware.
        self.search_status = QFrame()
        self.search_status.setObjectName("DeviceSearchStatus")
        status_layout = QGridLayout(self.search_status)
        status_layout.setContentsMargins(14, 9, 14, 9)
        status_layout.setSpacing(18)

        self.network_status = QLabel("Local network · Not checked")
        self.network_status.setProperty("role", "status")
        self.network_status.setWordWrap(True)
        status_layout.addWidget(self.network_status, 0, 0)

        self.bluetooth_status = QLabel("Bluetooth · Not checked")
        self.bluetooth_status.setProperty("role", "status")
        self.bluetooth_status.setWordWrap(True)
        status_layout.addWidget(self.bluetooth_status, 0, 1)
        self.tuya_status = QLabel("Tuya LAN · Not checked")
        self.tuya_status.setProperty("role", "status")
        self.tuya_status.setWordWrap(True)
        status_layout.addWidget(self.tuya_status, 1, 0)
        self.accounts_status = QLabel("Accounts · Not refreshed")
        self.accounts_status.setProperty("role", "status")
        self.accounts_status.setWordWrap(True)
        status_layout.addWidget(self.accounts_status, 1, 1)
        status_layout.setColumnStretch(0, 1)
        status_layout.setColumnStretch(1, 1)

        self.search_status.setVisible(False)
        root.addWidget(self.search_status)

        # Group picking is an explicit mode. In this mode the whole device card
        # becomes the selection target; outside it, the card opens details.
        self.group_bar = QFrame()
        self.group_bar.setObjectName("GroupSelectionBar")
        self.group_bar.setMaximumHeight(0)
        self.group_bar.hide()
        group_layout = QHBoxLayout(self.group_bar)
        group_layout.setContentsMargins(16, 10, 12, 10)
        group_layout.setSpacing(10)

        self.group_selection_label = QLabel(
            "Choose local lights for this sync group"
        )
        self.group_selection_label.setProperty("role", "strong")
        group_layout.addWidget(self.group_selection_label)
        group_layout.addStretch(1)

        self.cancel_group_button = QPushButton("Cancel")
        self.cancel_group_button.clicked.connect(self._cancel_group_selection)
        group_layout.addWidget(self.cancel_group_button)

        self.save_group_button = QPushButton("Save Group")
        self.save_group_button.setObjectName("Primary")
        self.save_group_button.setProperty("role", "primary")
        self.save_group_button.setEnabled(False)
        self.save_group_button.clicked.connect(self._save_group_selection)
        group_layout.addWidget(self.save_group_button)
        root.addWidget(self.group_bar)

        # The inventory fills each row rather than wrapping fixed-width cards.
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.grid_host = QWidget()
        self.grid_layout = DeviceGridLayout(self.grid_host, h_spacing=12, v_spacing=12)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)

        self.scroll.setWidget(self.grid_host)

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
            "No devices yet\n\nChoose Find Devices to search your local network and "
            "nearby Bluetooth panels, or add a device using its connection details."
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setProperty("role", "empty")
        self.empty_label.setVisible(False)
        root.addWidget(self.empty_label)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if not hasattr(self, "inspector_scroll"):
            return
        self._layout_toolbar()
        if self._inspected_index >= 0:
            self._layout_inspector()

    def _layout_toolbar(self) -> None:
        available_width = self.width() - 56
        controls = (self.find_devices_button, self.add_button, self.accounts_button,
                    self.summary_label, self.group_mode_button)
        needed = sum(control.sizeHint().width() for control in controls) + self.toolbar.spacing() * 4
        self.toolbar.removeWidget(self.group_mode_button)
        if available_width < needed:
            self.toolbar.addWidget(self.group_mode_button, 1, 0, 1, 4, Qt.AlignmentFlag.AlignLeft)
        else:
            self.toolbar.addWidget(self.group_mode_button, 0, 4)

    def _inspector_geometry(self) -> tuple[bool, int]:
        available = max(0, self.width() - 56)
        inspector_width = max(400, min(480, int(available * 0.34)))
        # Allow a scrollbar as well as two readable inventory columns. The
        # toolbar spans both panes, so it no longer determines this breakpoint.
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
        self._layout_toolbar()
        return compact

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if watched is self.inspector_scroll.viewport() and event.type() == QEvent.Type.Resize:
            size = event.size()
            self.inspector.set_available_size(size.width(), size.height())
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------ wiring

    def _connect_signals(self) -> None:
        self.controller.devices_discovered.connect(lambda *_: self._rebuild_cards())
        self.controller.device_added.connect(lambda *_: self._rebuild_cards())
        self.controller.device_removed.connect(lambda *_: self._rebuild_cards())
        self.controller.device_state_updated.connect(self._on_device_state_updated)
        self.controller.device_search_started.connect(self._on_search_started)
        self.controller.device_search_finished.connect(self._on_search_finished)
        self.inspector.connection_changed.connect(self.controller.set_connection_at)
        self.inspector.panel_tools_requested.connect(self._open_panel_tools)

    def _open_panel_tools(self, index: int) -> None:
        from ..dialogs.panel_tools_dialog import PanelToolsDialog
        if 0 <= index < len(self.controller.devices):
            PanelToolsDialog(self.controller, index, self).exec()

    def _open_accounts(self) -> None:
        from ..dialogs.accounts_dialog import AccountsDialog
        dialog = AccountsDialog(self.controller, self)
        dialog.exec()

    def _set_transport_status(
        self,
        label: QLabel,
        text: str,
        state: str,
        tooltip: str = "",
    ) -> None:
        label.setText(text)
        label.setProperty("statusState", state)
        label.setToolTip(tooltip)
        label.style().unpolish(label)
        label.style().polish(label)

    def _on_search_started(self) -> None:
        self.find_devices_button.setEnabled(False)
        self.find_devices_button.setText("Finding devices…")
        self.search_status.setVisible(True)
        self._set_transport_status(
            self.network_status, "Local network · Checking…", "warning"
        )
        self._set_transport_status(
            self.bluetooth_status, "Bluetooth · Checking…", "warning"
        )
        self._set_transport_status(self.tuya_status, "Tuya LAN · Checking…", "warning")
        self._set_transport_status(self.accounts_status, "Accounts · Refreshing…", "warning")

    def _on_search_finished(self, summary: dict) -> None:
        self.find_devices_button.setEnabled(True)
        self.find_devices_button.setText("Find Devices")
        accounts = summary.get("accounts", {})
        if accounts.get("errors"):
            refreshed = int(accounts.get("refreshed", 0))
            self._set_transport_status(self.accounts_status, f"Accounts · {refreshed} of {int(accounts.get('total', 0))} refreshed",
                                       "warning", "\n".join(accounts["errors"]))
        elif accounts.get("total"):
            self._set_transport_status(self.accounts_status, f"Accounts · {int(accounts.get('found', 0))} devices refreshed", "online")
        else:
            self._set_transport_status(self.accounts_status, "Accounts · No accounts connected", "offline")
        tuya = summary.get("tuya", {})
        if tuya.get("available"):
            self._set_transport_status(self.tuya_status, f"Tuya LAN · {int(tuya.get('found', 0))} linked devices found", "online")
        else:
            self._set_transport_status(self.tuya_status, "Tuya LAN · Unavailable", "warning", str(tuya.get("error") or "No active local network connection"))

        lan = summary.get("lan", {})
        if lan.get("available"):
            found = int(lan.get("found", 0))
            text = (
                f"Local network · {found} device{'s' if found != 1 else ''} found"
                if found
                else "Local network · Ready, no new devices"
            )
            self._set_transport_status(self.network_status, text, "online")
        else:
            error = str(lan.get("error") or "No active network connection")
            self._set_transport_status(
                self.network_status,
                "Local network · Unavailable",
                "warning",
                error,
            )

        bluetooth = summary.get("bluetooth", {})
        if bluetooth.get("available"):
            found = int(bluetooth.get("found", 0))
            seen = int(bluetooth.get("seen", 0))
            text = (
                f"Bluetooth · {found} supported panel{'s' if found != 1 else ''} found"
                if found
                else "Bluetooth · Ready, no supported panels"
            )
            tooltip = (
                f"{seen} Bluetooth device{'s' if seen != 1 else ''} were visible."
                if seen
                else "Bluetooth is available, but no devices were advertising."
            )
            self._set_transport_status(
                self.bluetooth_status, text, "online", tooltip
            )
        else:
            error = str(
                bluetooth.get("error")
                or "Bluetooth is turned off or unavailable"
            )
            self._set_transport_status(
                self.bluetooth_status,
                "Bluetooth · Unavailable",
                "warning",
                error,
            )

    # ------------------------------------------------------------------ cards

    def _rebuild_cards(self) -> None:
        selected_keys = {device_key(card.device()) for card in self._cards if card._index in self._selected}
        # Wipe existing
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._cards.clear()
        # Refreshes can reorder devices or remove a preceding row. Keep group
        # choices and the open inspector attached to the same device identity.
        self._selected = {index for index, device in enumerate(self.controller.devices)
                          if device_key(device) in selected_keys}

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
            card.set_group_selection_mode(self._group_selection_mode)
            card.set_checked(idx in self._selected)
            card.set_primary(idx == self.controller.selected_device_index)
            card.set_inspected(idx == self._inspected_index)
            card.set_state(self.controller.get_device_state_at(idx))
            card.selection_changed.connect(self._on_card_selection_changed)
            card.details_requested.connect(self._open_inspector)
            card.power_clicked.connect(self.controller.toggle_power_at)
            card.brightness_changed.connect(self.controller.set_brightness_at)
            self._cards.append(card)
            self.grid_layout.addWidget(card)

        if self._inspected_index >= 0:
            self._populate_inspector()
        elif self.inspector_scroll.isVisible():
            self._hide_inspector_immediately()

        self._update_summary()
        self._update_empty_state()
        self.controller.refresh_device_states()

    def _update_empty_state(self) -> None:
        empty = not self.controller.devices
        self.empty_label.setVisible(empty)
        self.content_host.setVisible(not empty)

    def _update_summary(self) -> None:
        total = len(self.controller.devices)
        if total == 0:
            self.summary_label.setText("")
        else:
            self.summary_label.setText(f"{total} device{'s' if total != 1 else ''}")
        has_sync_lights = any(capabilities_for_device(d).supports_streaming for d in self.controller.devices)
        self.group_mode_button.setEnabled(has_sync_lights and not self._group_selection_mode)
        self._update_group_bar()

    def _update_group_bar(self) -> None:
        n = len(self._selected)
        target = 60 if self._group_selection_mode else 0
        self.group_selection_label.setText(
            f"{n} device{'s' if n != 1 else ''} chosen · click cards to change"
            if n
            else "Choose devices by clicking their cards"
        )
        self.save_group_button.setEnabled(n > 0)
        self.group_bar.setVisible(self._group_selection_mode)
        if self.group_bar.maximumHeight() != target:
            animate_height(self.group_bar, target, duration=180)

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
        self._layout_toolbar()

    def _stop_inspector_animation(self) -> None:
        if self._inspector_animation is None:
            return
        try:
            self._inspector_animation.stop()
        except RuntimeError:
            pass
        self._inspector_animation = None

    # ------------------------------------------------------------------ card slots

    def _on_card_selection_changed(self, index: int, selected: bool) -> None:
        if not self._group_selection_mode or not (0 <= index < len(self.controller.devices)):
            return
        if not capabilities_for_device(self.controller.devices[index]).supports_streaming:
            return
        if selected:
            self._selected.add(index)
        else:
            self._selected.discard(index)
        self._update_group_bar()

    def _on_card_primary_clicked(self, index: int) -> None:
        self.controller.select_device(index)
        for card in self._cards:
            card.set_primary(card._index == index)
        if self._inspected_index >= 0:
            self._populate_inspector()

    def _on_card_color_picked(self, index: int, color: QColor) -> None:
        self.controller.set_color_at(index, color.red(), color.green(), color.blue())

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

    # ---------------------------------------------------------- group builder

    def _start_group_selection(self) -> None:
        if not self.controller.devices:
            return
        self._group_selection_mode = True
        self._selected.clear()
        self._close_inspector()
        for card in self._cards:
            card.set_group_selection_mode(True)
            card.set_checked(False)
        self.group_mode_button.setEnabled(False)
        self._update_summary()

    def _cancel_group_selection(self) -> None:
        self._finish_group_selection()

    def _finish_group_selection(self) -> None:
        self._group_selection_mode = False
        self._selected.clear()
        for card in self._cards:
            card.set_checked(False)
            card.set_group_selection_mode(False)
        self._update_summary()

    def _save_group_selection(self) -> None:
        if not self._selected:
            return
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Save Sync Group")
        dialog.setLabelText("Group name")
        dialog.setInputMode(QInputDialog.InputMode.TextInput)
        dialog.setOkButtonText("Save Group")
        dialog.setCancelButtonText("Cancel")

        button_box = dialog.findChild(QDialogButtonBox)
        save_button = (
            button_box.button(QDialogButtonBox.StandardButton.Ok)
            if button_box is not None
            else None
        )
        if save_button is not None:
            save_button.setEnabled(False)
            dialog.textValueChanged.connect(
                lambda text: save_button.setEnabled(bool(text.strip()))
            )

        if not dialog.exec():
            return
        name = dialog.textValue().strip()
        if not name:
            self.controller.status_updated.emit("Enter a name for the sync group.")
            return

        existing = {
            str(group.get("name", "")).strip().casefold()
            for group in self.controller.get_groups()
        }
        if name.casefold() in existing:
            replace = QMessageBox.question(
                self,
                "Replace Sync Group",
                f"A group named '{name}' already exists. Replace its device list?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if replace != QMessageBox.StandardButton.Yes:
                return
        if self.controller.save_group(name, sorted(self._selected)):
            self._finish_group_selection()

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
