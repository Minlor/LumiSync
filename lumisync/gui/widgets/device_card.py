"""Compact device card used by the Devices page."""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QEnterEvent, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ... import connection
from ..resources.icons import IconKey, tinted_icon, tinted_pixmap
from ..theme import qcolor
from ..utils.animations import PulseDot
from .product_controls import ProductSlider


CARD_WIDTH = 340
CARD_MIN_HEIGHT = 174


def format_device_output(state: Dict[str, Any]) -> str:
    """Build a compact, honest output summary for a device card."""
    active_output = state.get("active_output")
    if active_output:
        return f"Live output · {active_output}"

    if state.get("power_on") is False:
        return "Output · Off"

    last_output = state.get("last_output")
    source = str(state.get("status_source") or "unknown")
    prefix = "Showing" if source == "confirmed" else "Last sent"

    color_temp = state.get("color_temp")
    color = state.get("color")
    brightness = state.get("brightness")

    if last_output and source != "confirmed":
        detail = str(last_output)
    elif color_temp:
        detail = f"White {int(color_temp)}K"
    elif isinstance(color, (tuple, list)) and len(color) >= 3:
        detail = "#{:02X}{:02X}{:02X}".format(
            int(color[0]), int(color[1]), int(color[2])
        )
    elif state.get("power_on") is True:
        detail = "On"
    elif isinstance(brightness, int):
        detail = f"{max(0, min(100, brightness))}% brightness"
    else:
        return "Output · Not reported"

    if isinstance(brightness, int) and "brightness" not in detail.lower():
        detail += f" · {max(0, min(100, brightness))}%"
    return f"{prefix} · {detail}"


class DeviceCard(QFrame):
    """Quick power/brightness surface that opens a complete inspector."""

    selection_changed = Signal(int, bool)
    details_requested = Signal(int)
    primary_clicked = Signal(int)
    power_clicked = Signal(int)
    color_picked = Signal(int, QColor)
    brightness_changed = Signal(int, int)
    color_temp_changed = Signal(int, int)
    remove_requested = Signal(int)
    zone_count_requested = Signal(int)
    zone_count_reset_requested = Signal(int)

    def __init__(
        self,
        index: int,
        device: Dict[str, Any],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._index = index
        self._device = dict(device)
        self._current_color = qcolor("accent")
        self._power_on: Optional[bool] = None
        self._is_primary = False
        self._is_selected = False
        self._group_selection_mode = False

        self._brightness_timer = QTimer(self)
        self._brightness_timer.setSingleShot(True)
        self._brightness_timer.setInterval(120)
        self._brightness_timer.timeout.connect(self._commit_brightness)

        self.setObjectName("DeviceCard")
        self.setProperty("card", True)
        self.setProperty("hovered", False)
        self.setProperty("selected", False)
        self.setProperty("inspected", False)
        self.setProperty("groupSelection", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(
            f"{self._device.get('model', 'Unknown')} device controls"
        )
        self.setAccessibleDescription(
            "Open complete device controls. The circular button toggles power "
            "and the bottom slider changes brightness."
        )
        self.setToolTip(
            f"Open controls for {self._device.get('model', 'this device')}"
        )
        self.setFixedWidth(CARD_WIDTH)
        self.setMinimumHeight(CARD_MIN_HEIGHT)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred
        )

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(26)
        shadow.setOffset(0, 7)
        shadow.setColor(QColor(0, 0, 0, 68))
        self.setGraphicsEffect(shadow)

        self._build()
        self._set_power_visual("unknown")

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 15, 18, 15)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(10)

        self.status_dot = PulseDot(qcolor("success"), self, size=8)
        self.status_dot.set_active(False)
        header.addWidget(self.status_dot)

        self.name_label = QLabel(self._device.get("model", "Unknown"))
        font = self.name_label.font()
        font.setPointSize(11)
        font.setBold(True)
        self.name_label.setFont(font)
        self.name_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        header.addWidget(self.name_label, 1)

        self.group_badge = QLabel("In group")
        self.group_badge.setProperty("role", "pill")
        self.group_badge.setVisible(False)
        header.addWidget(self.group_badge)

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
        root.addLayout(header)

        transport_row = QHBoxLayout()
        transport_row.setSpacing(7)
        self.transport_icon = QLabel()
        icon_key, transport_name = self._transport_details()
        self.transport_icon.setPixmap(
            tinted_pixmap(icon_key, qcolor("text_dim"), 15)
        )
        self.transport_icon.setToolTip(transport_name)
        self.transport_icon.setAccessibleName(transport_name)
        transport_row.addWidget(self.transport_icon)

        self.device_summary = QLabel(self._format_subline())
        self.device_summary.setProperty("role", "subtle")
        transport_row.addWidget(self.device_summary)
        transport_row.addStretch(1)
        root.addLayout(transport_row)

        state_row = QHBoxLayout()
        state_row.setSpacing(8)
        self.primary_label = QLabel("Default")
        self.primary_label.setProperty("role", "pill")
        self.primary_label.setVisible(False)
        state_row.addWidget(self.primary_label)

        self.power_state_label = QLabel("Power unknown")
        self.power_state_label.setProperty("role", "subtle")
        state_row.addWidget(self.power_state_label)
        state_row.addStretch(1)

        self.state_detail_label = QLabel("Status pending")
        self.state_detail_label.setProperty("role", "status")
        state_row.addWidget(self.state_detail_label)
        root.addLayout(state_row)

        self.output_label = QLabel("Output · Not reported")
        self.output_label.setProperty("role", "subtle")
        self.output_label.setAccessibleName("Current device output")
        root.addWidget(self.output_label)

        brightness_row = QHBoxLayout()
        brightness_row.setSpacing(8)
        brightness_icon = QLabel()
        brightness_icon.setPixmap(
            tinted_pixmap(IconKey.SUN, qcolor("text_dim"), 14)
        )
        brightness_row.addWidget(brightness_icon)

        self.brightness_slider = ProductSlider(Qt.Orientation.Horizontal)
        self.brightness_slider.setRange(0, 100)
        self.brightness_slider.setValue(100)
        self.brightness_slider.setTracking(True)
        self.brightness_slider.setAccessibleName("Device brightness")
        self.brightness_slider.setToolTip("Change brightness")
        self.brightness_slider.valueChanged.connect(
            self._on_brightness_slider_changed
        )
        brightness_row.addWidget(self.brightness_slider, 1)

        self.brightness_summary = QLabel("100%")
        self.brightness_summary.setProperty("role", "subtle")
        self.brightness_summary.setMinimumWidth(38)
        self.brightness_summary.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        brightness_row.addWidget(self.brightness_summary)
        root.addLayout(brightness_row)

        for label in (
            self.name_label,
            self.group_badge,
            self.device_summary,
            self.primary_label,
            self.power_state_label,
            self.state_detail_label,
            self.output_label,
            self.brightness_summary,
            brightness_icon,
            self.transport_icon,
        ):
            label.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
            )

    def _format_subline(self) -> str:
        transport = str(self._device.get("transport", "lan")).lower()
        if transport == "ble":
            return f"{self._device.get('matrix_size', '32x32')} matrix"
        if transport == "tuya":
            version = self._device.get("protocol_version", "3.3")
            return f"Smart light · protocol {version}"
        zones = connection.get_segment_count(self._device)
        source = (
            "custom" if self._device.get("segment_count_override") else "default"
        )
        return f"{zones} zones · {source} layout"

    def _transport_details(self) -> tuple[IconKey, str]:
        transport = str(self._device.get("transport", "lan")).lower()
        if transport == "ble":
            return IconKey.BLUETOOTH, "Bluetooth connection"
        if transport == "tuya":
            return IconKey.NETWORK, "Local Tuya connection"
        return IconKey.NETWORK, "Local network connection"

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self._group_selection_mode:
                self.set_checked(not self._is_selected)
                self.selection_changed.emit(self._index, self._is_selected)
                event.accept()
                return
            child = self.childAt(event.pos())
            slider_hit = child is self.brightness_slider or (
                child is not None and self.brightness_slider.isAncestorOf(child)
            )
            if child is self.power_button or slider_hit:
                super().mousePressEvent(event)
                return
            self.details_requested.emit(self._index)
            event.accept()
            return
        super().mousePressEvent(event)

    def enterEvent(self, event: QEnterEvent) -> None:
        self.setProperty("hovered", True)
        self._repolish()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.setProperty("hovered", False)
        self._repolish()
        super().leaveEvent(event)

    def set_index(self, index: int) -> None:
        self._index = index

    def set_primary(self, primary: bool) -> None:
        self._is_primary = bool(primary)
        self.setProperty("primary", self._is_primary)
        self.primary_label.setVisible(self._is_primary)
        self._repolish()

    def set_inspected(self, inspected: bool) -> None:
        self.setProperty("inspected", bool(inspected))
        self._repolish()

    def set_checked(self, checked: bool) -> None:
        self._is_selected = bool(checked)
        self.setProperty("selected", self._is_selected)
        self.group_badge.setVisible(
            self._group_selection_mode and self._is_selected
        )
        self._repolish()

    def set_group_selection_mode(self, enabled: bool) -> None:
        self._group_selection_mode = bool(enabled)
        self.setProperty("groupSelection", self._group_selection_mode)
        self.power_button.setEnabled(not self._group_selection_mode)
        self.brightness_slider.setEnabled(not self._group_selection_mode)
        self.group_badge.setVisible(
            self._group_selection_mode and self._is_selected
        )
        self.setAccessibleDescription(
            "Click to add or remove this device from the new group."
            if self._group_selection_mode
            else "Open complete device controls. The circular button toggles "
            "power and the bottom slider changes brightness."
        )
        self.setToolTip(
            "Add or remove this device from the new group"
            if self._group_selection_mode
            else f"Open controls for {self._device.get('model', 'this device')}"
        )
        self._repolish()

    def device(self) -> Dict[str, Any]:
        return dict(self._device)

    def set_state(self, state: Dict[str, Any]) -> None:
        """Render confirmed, nearby, or last-commanded device state."""
        if not state:
            return

        online = bool(state.get("online"))
        stale = bool(state.get("stale"))
        error = state.get("last_error")
        source = str(state.get("status_source") or "unknown")
        active_output = state.get("active_output")
        self.status_dot.set_active(
            bool(active_output) or (online and source != "offline")
        )

        power = state.get("power_on")
        if power is True:
            self._power_on = True
            self.power_state_label.setText("Power on")
            self._set_power_visual("on")
        elif power is False:
            self._power_on = False
            self.power_state_label.setText("Power off")
            self._set_power_visual("off")
        else:
            self._power_on = None
            self.power_state_label.setText("Power unknown")
            self._set_power_visual("unknown")

        if error and source == "offline":
            status_text, status_state, tooltip = "Offline", "warning", str(error)
        elif active_output:
            status_text, status_state = "Active", "online"
            tooltip = f"LumiSync is sending {str(active_output).lower()} output."
        elif source == "confirmed" and online:
            status_text, status_state = "Online", "online"
            tooltip = "Status confirmed by the device."
        elif source == "commanded" and online:
            status_text, status_state = "Last sent", "online"
            tooltip = "This is the last command LumiSync sent successfully."
        elif source == "seen" and online:
            status_text, status_state = "Nearby", "online"
            tooltip = "Visible during the latest Bluetooth search."
        elif source == "not_seen":
            status_text, status_state = "Not visible", "offline"
            tooltip = "Not seen in the latest Bluetooth search."
        elif source == "pending" or stale:
            status_text, status_state = "Refreshing", "warning"
            tooltip = "Waiting for status confirmation."
        elif source == "unknown" and not state.get("readback_supported", True):
            status_text, status_state = "State unknown", "offline"
            tooltip = "This panel does not provide live state readback."
        else:
            status_text, status_state, tooltip = "Offline", "offline", ""

        self.state_detail_label.setText(status_text)
        self.state_detail_label.setProperty("statusState", status_state)
        self.state_detail_label.setToolTip(tooltip)
        self.state_detail_label.style().unpolish(self.state_detail_label)
        self.state_detail_label.style().polish(self.state_detail_label)

        self.output_label.setText(format_device_output(state))
        if active_output:
            self.output_label.setToolTip(
                f"LumiSync is sending {str(active_output).lower()} output."
            )
        elif source == "confirmed":
            self.output_label.setToolTip("Current output reported by the device.")
        elif source == "commanded":
            self.output_label.setToolTip(
                "Last successful command; Bluetooth output cannot be read live."
            )
        else:
            self.output_label.setToolTip(
                "The device has not reported its output yet."
            )

        brightness = state.get("brightness")
        if isinstance(brightness, int):
            bounded = max(0, min(100, brightness))
            self.brightness_summary.setText(f"{bounded}%")
            blocked = self.brightness_slider.blockSignals(True)
            self.brightness_slider.setValue(bounded)
            self.brightness_slider.blockSignals(blocked)
        else:
            self.brightness_summary.setText("—")

        color = state.get("color")
        if isinstance(color, (tuple, list)) and len(color) >= 3:
            self._current_color = QColor(
                int(color[0]), int(color[1]), int(color[2])
            )

    def _on_brightness_slider_changed(self, value: int) -> None:
        self.brightness_summary.setText(f"{value}%")
        self._brightness_timer.start()

    def _commit_brightness(self) -> None:
        if self._index >= 0:
            self.brightness_changed.emit(
                self._index, self.brightness_slider.value()
            )

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

    def _repolish(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


__all__ = [
    "DeviceCard",
    "CARD_WIDTH",
    "CARD_MIN_HEIGHT",
    "format_device_output",
]
