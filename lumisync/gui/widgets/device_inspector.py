"""Right-side device inspector used by the Devices page."""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ... import connection
from ...sku_catalog import capabilities_for
from ..resources.icons import IconKey, tinted_icon, tinted_pixmap
from ..theme import qcolor
from .device_card import format_device_output
from .product_controls import ProductSlider


class DeviceInspector(QFrame):
    """Progressively reveals controls for one selected device."""

    close_requested = Signal()
    power_clicked = Signal(int)
    set_default_requested = Signal(int)
    color_picked = Signal(int, QColor)
    brightness_changed = Signal(int, int)
    color_temp_changed = Signal(int, int)
    zone_count_requested = Signal(int)
    zone_count_reset_requested = Signal(int)
    remove_requested = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._index = -1
        self._device: Dict[str, Any] = {}
        self._current_color = qcolor("accent")
        self._ct_min = 2000
        self._ct_max = 9000

        self._brightness_timer = QTimer(self)
        self._brightness_timer.setSingleShot(True)
        self._brightness_timer.setInterval(180)
        self._brightness_timer.timeout.connect(self._commit_brightness)
        self._temperature_timer = QTimer(self)
        self._temperature_timer.setSingleShot(True)
        self._temperature_timer.setInterval(180)
        self._temperature_timer.timeout.connect(self._commit_temperature)

        self.setObjectName("DeviceInspector")
        self.setMinimumWidth(414)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)
        titles = QVBoxLayout()
        titles.setSpacing(2)

        eyebrow = QLabel("DEVICE CONTROLS")
        eyebrow.setProperty("role", "eyebrow")
        titles.addWidget(eyebrow)

        self.title_label = QLabel("Device")
        self.title_label.setProperty("role", "inspectorTitle")
        titles.addWidget(self.title_label)
        header.addLayout(titles, 1)

        self.power_button = QToolButton()
        self.power_button.setObjectName("DevicePowerButton")
        self.power_button.setProperty("powerState", "unknown")
        self.power_button.setFixedSize(46, 46)
        self.power_button.setIconSize(QSize(20, 20))
        self.power_button.setAccessibleName("Toggle device power")
        self.power_button.setToolTip("Turn on")
        self.power_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.power_button.clicked.connect(
            lambda: self.power_clicked.emit(self._index)
        )
        self._power_shadow = QGraphicsDropShadowEffect(self.power_button)
        self._power_shadow.setOffset(0, 0)
        self._power_shadow.setBlurRadius(0)
        self._power_shadow.setColor(QColor(0, 0, 0, 0))
        self._power_shadow.setEnabled(False)
        self.power_button.setGraphicsEffect(self._power_shadow)
        header.addWidget(self.power_button)

        self.close_button = QToolButton()
        self.close_button.setObjectName("InspectorCloseButton")
        self.close_button.setText("×")
        self.close_button.setAccessibleName("Close device controls")
        self.close_button.setToolTip("Close device controls")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self.close_requested)
        header.addWidget(self.close_button)
        root.addLayout(header)

        self.meta_label = QLabel("")
        self.meta_label.setProperty("role", "subtle")
        self.meta_label.setWordWrap(True)
        root.addWidget(self.meta_label)

        status_panel = QFrame()
        status_panel.setObjectName("InspectorSection")
        status_layout = QVBoxLayout(status_panel)
        status_layout.setContentsMargins(14, 12, 14, 12)
        status_layout.setSpacing(5)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.power_label = QLabel("Power unknown")
        self.power_label.setProperty("role", "strong")
        status_row.addWidget(self.power_label)
        status_row.addStretch(1)
        self.status_label = QLabel("Status pending")
        self.status_label.setProperty("role", "status")
        status_row.addWidget(self.status_label)
        status_layout.addLayout(status_row)

        self.output_label = QLabel("Output · Not reported")
        self.output_label.setProperty("role", "subtle")
        self.output_label.setWordWrap(True)
        status_layout.addWidget(self.output_label)
        root.addWidget(status_panel)

        controls_label = QLabel("QUICK CONTROLS")
        controls_label.setProperty("role", "eyebrow")
        root.addWidget(controls_label)

        controls = QFrame()
        controls.setObjectName("InspectorSection")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(14, 12, 14, 14)
        controls_layout.setSpacing(10)

        brightness_header = QHBoxLayout()
        brightness_header.setSpacing(8)
        brightness_icon = QLabel()
        brightness_icon.setPixmap(
            tinted_pixmap(IconKey.SUN, qcolor("text_dim"), 14)
        )
        brightness_header.addWidget(brightness_icon)
        brightness_header.addWidget(QLabel("Brightness"))
        brightness_header.addStretch(1)
        self.brightness_value = QLabel("100%")
        self.brightness_value.setProperty("role", "subtle")
        brightness_header.addWidget(self.brightness_value)
        controls_layout.addLayout(brightness_header)

        self.brightness_slider = ProductSlider(Qt.Orientation.Horizontal)
        self.brightness_slider.setRange(0, 100)
        self.brightness_slider.setValue(100)
        self.brightness_slider.setAccessibleName("Device brightness")
        self.brightness_slider.valueChanged.connect(self._on_brightness)
        controls_layout.addWidget(self.brightness_slider)

        color_row = QHBoxLayout()
        color_row.setSpacing(10)
        color_copy = QVBoxLayout()
        color_copy.setSpacing(2)
        color_copy.addWidget(QLabel("Color"))
        color_hint = QLabel("Choose the light's static color")
        color_hint.setProperty("role", "subtle")
        color_copy.addWidget(color_hint)
        color_row.addLayout(color_copy, 1)

        self.color_button = QPushButton()
        self.color_button.setObjectName("InspectorColorSwatch")
        self.color_button.setAccessibleName("Choose device color")
        self.color_button.setToolTip("Choose color")
        self.color_button.clicked.connect(self._pick_color)
        color_row.addWidget(self.color_button)
        controls_layout.addLayout(color_row)

        self.temperature_header = QHBoxLayout()
        self.temperature_header.setSpacing(8)
        self.temperature_icon = QLabel()
        self.temperature_icon.setPixmap(
            tinted_pixmap(IconKey.THERMOMETER, qcolor("text_dim"), 14)
        )
        self.temperature_header.addWidget(self.temperature_icon)
        self.temperature_title = QLabel("White temperature")
        self.temperature_header.addWidget(self.temperature_title)
        self.temperature_header.addStretch(1)
        self.temperature_value = QLabel("4000K")
        self.temperature_value.setProperty("role", "subtle")
        self.temperature_header.addWidget(self.temperature_value)
        controls_layout.addLayout(self.temperature_header)

        self.temperature_slider = ProductSlider(Qt.Orientation.Horizontal)
        self.temperature_slider.setRange(self._ct_min, self._ct_max)
        self.temperature_slider.setValue(4000)
        self.temperature_slider.setAccessibleName("White color temperature")
        self.temperature_slider.valueChanged.connect(self._on_temperature)
        controls_layout.addWidget(self.temperature_slider)
        self._temperature_widgets = (
            self.temperature_icon,
            self.temperature_title,
            self.temperature_value,
            self.temperature_slider,
        )
        root.addWidget(controls)

        info_label = QLabel("DEVICE INFO")
        info_label.setProperty("role", "eyebrow")
        root.addWidget(info_label)

        info = QFrame()
        info.setObjectName("InspectorSection")
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_layout.setSpacing(8)
        self.connection_value = self._add_info_row(
            info_layout, "Connection", "—"
        )
        self.address_value = self._add_info_row(info_layout, "Address", "—")
        self.port_value = self._add_info_row(
            info_layout, "Control port", "—"
        )
        self.identifier_value = self._add_info_row(
            info_layout, "Identifier", "—"
        )
        self.zones_value = self._add_info_row(info_layout, "Layout", "—")
        self.readback_value = self._add_info_row(
            info_layout, "State reporting", "—"
        )
        root.addWidget(info)

        self.default_button = QPushButton("Set as Default")
        self.default_button.clicked.connect(
            lambda: self.set_default_requested.emit(self._index)
        )
        root.addWidget(self.default_button)

        zone_actions = QHBoxLayout()
        zone_actions.setSpacing(8)
        self.zones_button = QPushButton("Set Zones")
        self.zones_button.clicked.connect(
            lambda: self.zone_count_requested.emit(self._index)
        )
        zone_actions.addWidget(self.zones_button)
        self.reset_zones_button = QPushButton("Use Default")
        self.reset_zones_button.clicked.connect(
            lambda: self.zone_count_reset_requested.emit(self._index)
        )
        zone_actions.addWidget(self.reset_zones_button)
        root.addLayout(zone_actions)

        self.remove_button = QPushButton("Remove Device")
        self.remove_button.setProperty("role", "danger")
        self.remove_button.setIcon(
            tinted_icon(IconKey.TRASH, "#FFFFFF", 16)
        )
        self.remove_button.clicked.connect(
            lambda: self.remove_requested.emit(self._index)
        )
        root.addWidget(self.remove_button)
        root.addStretch(1)

        self._refresh_color_button()

    @staticmethod
    def _add_info_row(layout: QVBoxLayout, title: str, value: str) -> QLabel:
        row = QHBoxLayout()
        row.setSpacing(8)
        title_label = QLabel(title)
        title_label.setProperty("role", "subtle")
        row.addWidget(title_label)
        row.addStretch(1)
        value_label = QLabel(value)
        value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        row.addWidget(value_label)
        layout.addLayout(row)
        return value_label

    def set_device(
        self,
        index: int,
        device: Dict[str, Any],
        state: Dict[str, Any],
        *,
        primary: bool,
    ) -> None:
        self._brightness_timer.stop()
        self._temperature_timer.stop()
        self._index = index
        self._device = dict(device)
        self.title_label.setText(str(device.get("model") or "Unknown device"))
        self.meta_label.setText(self._format_meta(device))

        transport = str(device.get("transport") or "lan").lower()
        self.connection_value.setText(
            "Bluetooth" if transport == "ble" else "LSC / Tuya"
            if transport == "tuya"
            else "Local network"
        )
        self.address_value.setText(
            str(
                device.get("ble_address")
                or device.get("ip")
                or device.get("mac")
                or "—"
            )
        )
        port = device.get("port")
        if not port and transport == "lan":
            port = connection.get_device_port(device)
        self.port_value.setText(str(port) if port else "—")
        identifier = (
            device.get("device_id")
            or device.get("mac")
            or device.get("ble_address")
            or "—"
        )
        self.identifier_value.setText(str(identifier))
        self.readback_value.setText(
            "Live"
            if state.get("readback_supported", transport != "ble")
            else "Last command only"
        )
        if transport == "ble":
            self.zones_value.setText(
                f"{device.get('matrix_size', '32x32')} matrix"
            )
        else:
            count = connection.get_segment_count(device)
            source = "custom" if device.get("segment_count_override") else "default"
            self.zones_value.setText(f"{count} zones · {source}")

        cap = capabilities_for(device.get("model") or device.get("sku"))
        supported = bool(cap and cap.color_temp_max > cap.color_temp_min > 0)
        if supported:
            self._ct_min = int(cap.color_temp_min)
            self._ct_max = int(cap.color_temp_max)
            self.temperature_slider.setRange(self._ct_min, self._ct_max)
        for widget in self._temperature_widgets:
            widget.setVisible(supported)

        self.default_button.setEnabled(not primary)
        self.default_button.setText(
            "Default Device" if primary else "Set as Default"
        )
        self.reset_zones_button.setEnabled(
            bool(device.get("segment_count_override"))
        )
        self.zones_button.setVisible(transport != "ble")
        self.reset_zones_button.setVisible(transport != "ble")
        self.set_state(state)

    def set_state(self, state: Dict[str, Any]) -> None:
        if not state:
            return
        self.readback_value.setText(
            "Live" if state.get("readback_supported", True)
            else "Last command only"
        )
        power = state.get("power_on")
        self.power_label.setText(
            "Power on" if power is True else "Power off"
            if power is False
            else "Power unknown"
        )
        self._set_power_visual(
            "on" if power is True else "off" if power is False else "unknown"
        )

        status_text, status_state, tooltip = self._status_copy(state)
        self.status_label.setText(status_text)
        self.status_label.setProperty("statusState", status_state)
        self.status_label.setToolTip(tooltip)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        self.output_label.setText(format_device_output(state))

        brightness = state.get("brightness")
        if isinstance(brightness, int):
            bounded = max(0, min(100, brightness))
            blocked = self.brightness_slider.blockSignals(True)
            self.brightness_slider.setValue(bounded)
            self.brightness_slider.blockSignals(blocked)
            self.brightness_value.setText(f"{bounded}%")

        color = state.get("color")
        if isinstance(color, (tuple, list)) and len(color) >= 3:
            self._current_color = QColor(
                int(color[0]), int(color[1]), int(color[2])
            )
            self._refresh_color_button()

        color_temp = state.get("color_temp")
        if color_temp:
            bounded_temp = max(
                self._ct_min, min(self._ct_max, int(color_temp))
            )
            blocked = self.temperature_slider.blockSignals(True)
            self.temperature_slider.setValue(bounded_temp)
            self.temperature_slider.blockSignals(blocked)
            self.temperature_value.setText(f"{bounded_temp}K")

    @staticmethod
    def _status_copy(state: Dict[str, Any]) -> tuple[str, str, str]:
        source = str(state.get("status_source") or "unknown")
        active = state.get("active_output")
        error = state.get("last_error")
        if error and source == "offline":
            return "Offline", "warning", str(error)
        if active:
            return "Active", "online", f"Sending {str(active).lower()} output"
        if source == "confirmed" and state.get("online"):
            return "Online", "online", "Status confirmed by the device"
        if source == "commanded" and state.get("online"):
            return "Last sent", "online", "Showing the last successful command"
        if source == "seen" and state.get("online"):
            return "Nearby", "online", "Visible during the latest search"
        if source == "not_seen":
            return "Not visible", "offline", "Not seen in the latest search"
        if source == "pending" or state.get("stale"):
            return "Refreshing", "warning", "Waiting for status confirmation"
        return "State unknown", "offline", "Live state is not available"

    @staticmethod
    def _format_meta(device: Dict[str, Any]) -> str:
        transport = str(device.get("transport") or "lan").lower()
        if transport == "ble":
            return "Bluetooth matrix panel · Last-commanded output"
        if transport == "tuya":
            return "Local Tuya light · Direct network control"
        return "LAN light · Confirmed device readback when supported"

    def _set_power_visual(self, state: str) -> None:
        is_on = state == "on"
        self.power_button.setProperty("powerState", state)
        self.power_button.setIcon(
            tinted_icon(
                IconKey.POWER,
                "#FFFFFF" if is_on else qcolor("text_dim"),
                20,
            )
        )
        self.power_button.setToolTip(
            "Turn off"
            if is_on
            else "Turn on"
            if state == "off"
            else "Power state unknown · click to turn on"
        )
        self.power_button.setAccessibleDescription(
            "On" if is_on else "Off" if state == "off" else "Unknown"
        )
        if is_on:
            glow = qcolor("accent_bright")
            glow.setAlpha(170)
            self._power_shadow.setColor(glow)
            self._power_shadow.setBlurRadius(26)
            self._power_shadow.setEnabled(True)
        else:
            self._power_shadow.setEnabled(False)
            self._power_shadow.setColor(QColor(0, 0, 0, 0))
            self._power_shadow.setBlurRadius(0)
        self.power_button.style().unpolish(self.power_button)
        self.power_button.style().polish(self.power_button)

    def _on_brightness(self, value: int) -> None:
        self.brightness_value.setText(f"{value}%")
        self._brightness_timer.start()

    def _commit_brightness(self) -> None:
        if self._index >= 0:
            self.brightness_changed.emit(
                self._index, self.brightness_slider.value()
            )

    def _on_temperature(self, value: int) -> None:
        self.temperature_value.setText(f"{value}K")
        self._temperature_timer.start()

    def _commit_temperature(self) -> None:
        if self._index >= 0:
            self.color_temp_changed.emit(
                self._index, self.temperature_slider.value()
            )

    def _pick_color(self) -> None:
        chosen = QColorDialog.getColor(
            self._current_color, self, "Choose device color"
        )
        if chosen.isValid() and self._index >= 0:
            self._current_color = chosen
            self._refresh_color_button()
            self.color_picked.emit(self._index, chosen)

    def _refresh_color_button(self) -> None:
        self.color_button.setStyleSheet(
            "QPushButton#InspectorColorSwatch {"
            f"background: {self._current_color.name()};"
            f"border: 2px solid {qcolor('border_strong').name()};"
            "border-radius: 12px;"
            "min-width: 44px; min-height: 44px;"
            "max-width: 44px; max-height: 44px; padding: 0;"
            "}"
            "QPushButton#InspectorColorSwatch:hover {"
            f"border-color: {qcolor('text').name()};"
            "}"
        )
        self.color_button.setToolTip(
            f"Current color {self._current_color.name().upper()}"
        )


__all__ = ["DeviceInspector"]
