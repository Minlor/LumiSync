"""Compact device card used by the Devices page."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional
import math

import shiboken6
from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QEnterEvent, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ... import connection
from ...accounts.tuya_energy import meter_fields
from ...drivers.base import DeviceCapabilities
from ...drivers.registry import capabilities_for_device, is_cloud
from ...utils.colors import kelvin_to_rgb
from ..resources.icons import IconKey, tinted_icon, tinted_pixmap
from ..theme import qcolor
from ..utils.animations import StatusDot
from ..utils.device_identity import device_identity
from .color_flyout import (
    ColorFlyout,
    ColorSwatchButton,
    state_display_color,
    swatch_tooltip,
    white_range,
)
from .elided_label import ElidedLabel
from .product_controls import ProductSlider


CARD_MIN_WIDTH = 260
CARD_WIDTH = CARD_MIN_WIDTH  # Compatibility for callers of the former fixed width.

UNKNOWN_VALUE = "—"

# Status tones drive both the dot and the phrase color.
TONE_ONLINE = "online"
TONE_WARNING = "warning"
TONE_OFFLINE = "offline"
TONE_UNKNOWN = "unknown"

_SYNC_OUTPUTS = {"Monitor sync": "Monitor", "Music sync": "Music"}
_NEVER_ANSWERED = (
    "This device hasn't answered a status request yet. Some models never "
    "do; commands may still work."
)
_NO_READBACK = (
    "This device can't report its state, so LumiSync shows the last "
    "command it sent."
)


@dataclass(frozen=True)
class DeviceStatus:
    """One short phrase describing a device, plus its tone and explanation."""

    text: str
    tone: str
    tooltip: str = ""


def connection_name(device: Dict[str, Any]) -> str:
    transport = str(device.get("transport") or "lan").lower()
    if transport == "ble":
        return "Bluetooth"
    if transport == "tuya":
        return "Local Wi-Fi"
    if is_cloud(device):
        return "Account"
    return "Local network"


def device_subline(device: Dict[str, Any], cap: Optional[DeviceCapabilities] = None) -> str:
    """``<type> · <connection>`` plus one key detail, e.g. the zone count."""
    cap = cap or capabilities_for_device(device)
    type_name, _icon = device_identity(device)
    parts = [type_name, connection_name(device)]
    transport = str(device.get("transport") or "lan").lower()
    if device.get("device_kind") in ("light_switch", "smart_plug"):
        pass
    elif transport == "ble" or cap.is_matrix:
        size = str(device.get("matrix_size") or "32x32").replace("x", "×")
        parts.append(size)
    elif transport in ("", "lan") and cap.supports_segments:
        parts.append(f"{connection.get_segment_count(device)} zones")
    elif cap.supports_segments and cap.segment_count > 1:
        parts.append(f"{cap.segment_count} zones")
    return " · ".join(parts)


def _reading(state: Dict[str, Any], field: str) -> Optional[float]:
    value = state.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _output_phrase(
    state: Dict[str, Any],
    cap: Optional[DeviceCapabilities],
    meters: Iterable[str],
    *,
    lead: str,
) -> str:
    """``On · 80%``, ``Off``, ``On · 12.3 W``; empty when unknown.

    Colour lights show their colour in the swatch beside the power button,
    so the phrase never spells out a hex value. A white-only light has no
    swatch and keeps its temperature (``On · 2700K · 80%``).
    """
    power = state.get("power_on") if cap is None or cap.supports_power else None
    if power is False:
        return "Off"

    details = []
    color_temp = state.get("color_temp")
    if color_temp and cap is not None and cap.supports_white and not cap.supports_color:
        details.append(f"{int(color_temp)}K")
    brightness = state.get("brightness")
    if isinstance(brightness, int) and not isinstance(brightness, bool) and (cap is None or cap.supports_brightness):
        details.append(f"{max(0, min(100, brightness))}%")
    watts = _reading(state, "power_w") if "power_w" in set(meters) else None
    if watts is not None:
        details.append(f"{watts:,.1f} W")

    if power is True:
        return " · ".join(["On", *details])
    if details:
        return " · ".join([lead, *details])
    return ""


def _via_account(phrase: str, state: Dict[str, Any]) -> str:
    """Mark a local device whose last command went through its account."""
    if phrase and state.get("command_route") == "account":
        return f"{phrase} · via account"
    return phrase


def _is_color_output(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 7 and text.startswith("#")


def describe_device_status(
    state: Dict[str, Any],
    cap: Optional[DeviceCapabilities] = None,
    meters: Iterable[str] = (),
) -> DeviceStatus:
    """Merge power, output, readings and reachability into one phrase.

    The tone is ``online`` only when the device (or its account) confirmed
    it is reachable, so an unknown or offline device never shows a green dot.
    """
    source = str(state.get("status_source") or "unknown")
    error = state.get("last_error")
    online = bool(state.get("online"))
    active = state.get("active_output")
    meters = tuple(meters)

    if error and source == "error":
        return DeviceStatus("Command failed", TONE_WARNING, str(error))
    if source == "offline":
        return DeviceStatus(
            "Offline", TONE_OFFLINE,
            str(error or "The device isn't answering. Check that it has power and network."),
        )
    if active:
        label = str(active)
        mode = _SYNC_OUTPUTS.get(label)
        return DeviceStatus(
            f"Syncing · {mode}" if mode else f"Live · {label}",
            TONE_ONLINE,
            f"LumiSync is sending {label.lower()} output.",
        )
    if source == "not_seen":
        return DeviceStatus("Not visible", TONE_OFFLINE, "Not seen in the latest Bluetooth search.")
    if source == "confirmed" and online:
        cloud = state.get("status_origin") == "cloud"
        tooltip = "Reported through your vendor account." if cloud else "Reported by the device."
        phrase = _output_phrase(state, cap, meters, lead="Online")
        return DeviceStatus(
            _via_account(phrase or ("Online (account)" if cloud else "Online"), state), TONE_ONLINE, tooltip
        )
    if source == "commanded" and online:
        phrase = _output_phrase(state, cap, meters, lead="Sent")
        last = state.get("last_output")
        if not phrase:
            phrase = "Colour sent" if _is_color_output(last) else f"Sent · {last}" if last else "Ready"
        return DeviceStatus(_via_account(phrase, state), TONE_ONLINE, _NO_READBACK)
    if source == "seen" and online:
        return DeviceStatus("Nearby", TONE_ONLINE, "Visible during the latest Bluetooth search.")
    if source == "unknown":
        tooltip = _NEVER_ANSWERED if state.get("readback_supported", True) else _NO_READBACK
        phrase = _output_phrase(state, cap, meters, lead="Sent")
        return DeviceStatus(phrase or "No status yet", TONE_UNKNOWN, tooltip)
    if source == "pending" or state.get("stale"):
        phrase = _output_phrase(state, cap, meters, lead="Sent")
        if phrase:
            tone = TONE_ONLINE if online and state.get("readback_seen") else TONE_UNKNOWN
            return DeviceStatus(_via_account(phrase, state), tone, "Waiting for the device to confirm its state.")
        return DeviceStatus("Checking…", TONE_UNKNOWN, "Waiting for the device to answer.")
    return DeviceStatus("Offline", TONE_OFFLINE, str(error or ""))


def tone_color(tone: str):
    """Green only for a confirmed-reachable device; caution for problems."""
    if tone == TONE_ONLINE:
        return qcolor("fl_success")
    if tone == TONE_WARNING:
        return qcolor("fl_caution")
    return qcolor("text_disabled")


def power_visual_state(state: Dict[str, Any]) -> str:
    """``on``/``off``/``unknown``, or ``offline`` when the device is unreachable.

    An offline device never shows the accent "on" fill, whatever its last
    known power was; that stays in the button's tooltip.
    """
    if state.get("status_source") in ("offline", "not_seen"):
        return "offline"
    power = state.get("power_on")
    return "on" if power is True else "off" if power is False else "unknown"


def apply_status(label: QLabel, dot: StatusDot, status: DeviceStatus) -> None:
    """Render a status phrase and its dot consistently on cards and panels."""
    label.setText(status.text)
    label.setToolTip(status.tooltip or status.text)
    label.setAccessibleName(f"Status: {status.text}")
    label.setAccessibleDescription(status.tooltip)
    dot.set_color(tone_color(status.tone))
    dot.set_active(status.tone in (TONE_ONLINE, TONE_WARNING))
    dot.setToolTip(status.tooltip or status.text)
    if label.property("statusState") != status.tone:
        label.setProperty("statusState", status.tone)
        style = label.style()
        style.unpolish(label)
        style.polish(label)
        label.update()


class DeviceCard(QFrame):
    """Quick power/brightness/colour surface that opens a complete inspector."""

    details_requested = Signal(int)
    power_clicked = Signal(int)
    brightness_changed = Signal(int, int)
    color_picked = Signal(int, QColor)
    color_temp_changed = Signal(int, int)
    # "More colours…": the Devices page opens the colour dialog. It outlives
    # this card, which a device refresh may rebuild while the dialog is open.
    custom_color_requested = Signal(int)

    def __init__(
        self,
        index: int,
        device: Dict[str, Any],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._index = index
        self._device = dict(device)
        self._cap = capabilities_for_device(self._device)
        self._type_name, self._type_icon = device_identity(self._device)
        self._meter_fields = meter_fields(self._device)
        self.status = DeviceStatus("No status yet", TONE_UNKNOWN, "")
        self._flyout: Optional[ColorFlyout] = None

        self._brightness_timer = QTimer(self)
        self._brightness_timer.setSingleShot(True)
        self._brightness_timer.setInterval(120)
        self._brightness_timer.timeout.connect(self._commit_brightness)

        name = str(self._device.get("name") or self._device.get("model") or "Unknown device")
        cap = self._cap
        self.setObjectName("DeviceCard")
        self.setProperty("card", True)
        self.setProperty("hovered", False)
        self.setProperty("inspected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(f"{name} · {self._type_name} controls")
        self.setAccessibleDescription(
            "Open complete device controls."
            + (" The colour swatch opens quick colours." if cap.supports_color else "")
            + (" The circular button toggles power." if cap.supports_power else "")
            + (" The bottom slider changes brightness." if cap.supports_brightness else "")
        )
        self.setToolTip(f"Open controls for {name}")
        self.setMinimumWidth(CARD_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self._build(name)
        self._set_power_visual("unknown")
        apply_status(self.status_label, self.status_dot, self.status)
        self._set_swatch(None, None)

    def _build(self, name: str) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 12, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(12)

        self.type_icon = QLabel()
        self.type_icon.setFixedSize(32, 32)
        self.type_icon.setPixmap(tinted_pixmap(self._type_icon, qcolor("text_dim"), 30))
        self.type_icon.setAccessibleName(self._type_name)
        self.type_icon.setToolTip(self._type_name)
        header.addWidget(self.type_icon, 0, Qt.AlignmentFlag.AlignVCenter)

        titles = QVBoxLayout()
        titles.setSpacing(1)
        titles.setContentsMargins(0, 0, 0, 0)
        self.name_label = ElidedLabel(name)
        font = self.name_label.font()
        font.setPointSize(11)
        font.setBold(True)
        self.name_label.setFont(font)
        titles.addWidget(self.name_label)
        self.device_summary = ElidedLabel(device_subline(self._device, self._cap))
        self.device_summary.setProperty("role", "subtle")
        self.device_summary.setAccessibleName("Device type and connection")
        titles.addWidget(self.device_summary)
        header.addLayout(titles, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.setContentsMargins(0, 0, 0, 0)
        self.color_swatch = ColorSwatchButton(None, self, diameter=22)
        self.color_swatch.setAccessibleName("Device colour")
        self.color_swatch.clicked.connect(self._open_color_flyout)
        self.color_swatch.setVisible(self._cap.supports_color)
        actions.addWidget(self.color_swatch, 0, Qt.AlignmentFlag.AlignVCenter)

        # Parented at construction: setVisible(True) on a parentless widget
        # would flash it on screen as its own window until the layout adopts it.
        self.power_button = QToolButton(self)
        self.power_button.setObjectName("DevicePowerButton")
        self.power_button.setProperty("powerState", "unknown")
        self.power_button.setFixedSize(46, 46)
        self.power_button.setIconSize(QSize(20, 20))
        self.power_button.setAccessibleName("Toggle device power")
        self.power_button.setToolTip("Turn on")
        self.power_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.power_button.clicked.connect(lambda: self.power_clicked.emit(self._index))
        self.power_button.setVisible(self._cap.supports_power)
        actions.addWidget(self.power_button, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addLayout(actions)
        root.addLayout(header)

        status_row = QHBoxLayout()
        status_row.setSpacing(6)
        status_row.setContentsMargins(0, 0, 0, 0)
        self.status_dot = StatusDot(qcolor("text_disabled"), self, size=8)
        status_row.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.status_label = ElidedLabel("No status yet")
        self.status_label.setProperty("role", "deviceStatus")
        status_row.addWidget(self.status_label, 1)
        root.addLayout(status_row)

        self.brightness_controls = QWidget(self)
        brightness_row = QHBoxLayout(self.brightness_controls)
        brightness_row.setContentsMargins(0, 0, 0, 0)
        brightness_row.setSpacing(8)
        self.brightness_icon = QLabel()
        self.brightness_icon.setPixmap(tinted_pixmap(IconKey.SUN, qcolor("text_dim"), 14))
        brightness_row.addWidget(self.brightness_icon)

        self.brightness_slider = ProductSlider(Qt.Orientation.Horizontal)
        self.brightness_slider.setRange(0, 100)
        self.brightness_slider.setValue(100)
        self.brightness_slider.setTracking(True)
        self.brightness_slider.setAccessibleName("Device brightness")
        self.brightness_slider.setToolTip("Change brightness")
        self.brightness_slider.valueChanged.connect(self._on_brightness_slider_changed)
        brightness_row.addWidget(self.brightness_slider, 1)

        self.brightness_summary = QLabel(UNKNOWN_VALUE)
        self.brightness_summary.setProperty("role", "subtle")
        self.brightness_summary.setMinimumWidth(36)
        self.brightness_summary.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        brightness_row.addWidget(self.brightness_summary)
        self.brightness_controls.setVisible(self._cap.supports_brightness)
        self.brightness_slider.setEnabled(self._cap.supports_brightness)
        root.addWidget(self.brightness_controls)
        # Cards share a row height. Status lines stay aligned across a row;
        # the little spare height of power-only cards collects at the bottom.
        root.addStretch(1)

        for label in (
            self.name_label,
            self.type_icon,
            self.device_summary,
            self.status_dot,
            self.brightness_summary,
            self.brightness_icon,
        ):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # The status phrase keeps mouse hover so its explanation shows as a
        # tooltip; QLabel ignores presses, so a click still opens the card.

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.position().toPoint())
            slider_hit = child is self.brightness_slider or (
                child is not None and self.brightness_slider.isAncestorOf(child)
            )
            if child is self.power_button or child is self.color_swatch or slider_hit:
                super().mousePressEvent(event)
                return
            self.details_requested.emit(self._index)
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.details_requested.emit(self._index)
            event.accept()
            return
        super().keyPressEvent(event)

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

    def set_inspected(self, inspected: bool) -> None:
        if bool(self.property("inspected")) == bool(inspected):
            return
        self.setProperty("inspected", bool(inspected))
        self._repolish()

    def device(self) -> Dict[str, Any]:
        return dict(self._device)

    def set_state(self, state: Dict[str, Any]) -> None:
        """Render confirmed, nearby, or last-commanded device state."""
        if not state:
            return

        self._set_power_visual(power_visual_state(state), state.get("power_on"))

        self.status = describe_device_status(state, self._cap, self._meter_fields)
        apply_status(self.status_label, self.status_dot, self.status)
        if self._cap.supports_color:
            self._set_swatch(*state_display_color(state, self._cap))

        brightness = state.get("brightness")
        editing_brightness = self.brightness_slider.isSliderDown() or self._brightness_timer.isActive()
        if editing_brightness or not self._cap.supports_brightness:
            return
        if isinstance(brightness, int) and not isinstance(brightness, bool):
            bounded = max(0, min(100, brightness))
            self.brightness_summary.setText(f"{bounded}%")
            blocked = self.brightness_slider.blockSignals(True)
            self.brightness_slider.setValue(bounded)
            self.brightness_slider.blockSignals(blocked)
        else:
            self.brightness_summary.setText(UNKNOWN_VALUE)

    def _on_brightness_slider_changed(self, value: int) -> None:
        self.brightness_summary.setText(f"{value}%")
        self._brightness_timer.start()

    def _commit_brightness(self) -> None:
        if self._index >= 0 and self._cap.supports_brightness:
            self.brightness_changed.emit(self._index, self.brightness_slider.value())

    def _set_power_visual(self, state: str, last_known: Optional[bool] = None) -> None:
        visual = (state, last_known if state == "offline" else None)
        if getattr(self, "_power_visual_state", None) == visual:
            return
        self._power_visual_state = visual
        set_power_button_state(self.power_button, state, last_known=last_known)

    # ------------------------------------------------------------------ colour

    def _set_swatch(self, color: Optional[QColor], kelvin: Optional[int]) -> None:
        self.color_swatch.set_color(color, kelvin)
        tip = swatch_tooltip(color, kelvin)
        self.color_swatch.setToolTip(tip)
        self.color_swatch.setAccessibleDescription(tip)

    def current_color(self) -> Optional[QColor]:
        return self.color_swatch.color()

    def color_flyout(self) -> Optional[ColorFlyout]:
        return self._flyout

    def _open_color_flyout(self) -> None:
        if self._index < 0 or not self._cap.supports_color:
            return
        if self._flyout is not None:
            try:
                self._flyout.close()
            except RuntimeError:
                pass
        flyout = ColorFlyout(self, white=white_range(self._cap), current=self.color_swatch.color())
        flyout.color_chosen.connect(self._choose_color)
        flyout.temperature_chosen.connect(self._choose_temperature)
        # Bound to the card: a card rebuilt before the popup has closed never
        # receives the request.
        flyout.more_requested.connect(lambda: QTimer.singleShot(0, self, self._request_custom_color))
        flyout.destroyed.connect(self._forget_flyout)
        self._flyout = flyout
        flyout.popup_below(self.color_swatch)

    def _forget_flyout(self, *_args) -> None:
        self._flyout = None

    def _alive(self) -> bool:
        return shiboken6.isValid(self) and shiboken6.isValid(self.color_swatch)

    def preview_color(self, color: QColor) -> None:
        """Show a colour chosen elsewhere at once (the state confirms it)."""
        if self._alive() and self._cap.supports_color:
            self._set_swatch(QColor(color), None)

    def _choose_color(self, color: QColor) -> None:
        if not self._alive() or self._index < 0 or not self._cap.supports_color:
            return
        self._set_swatch(QColor(color), None)
        self.color_picked.emit(self._index, QColor(color))

    def _choose_temperature(self, kelvin: int) -> None:
        if not self._alive() or self._index < 0:
            return
        self._set_swatch(QColor(*kelvin_to_rgb(int(kelvin))), int(kelvin))
        self.color_temp_changed.emit(self._index, int(kelvin))

    def _request_custom_color(self) -> None:
        if self._alive() and self._index >= 0 and self._cap.supports_color:
            self.custom_color_requested.emit(self._index)

    def _repolish(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


def set_power_button_state(button: QToolButton, state: str, *, last_known: Optional[bool] = None) -> None:
    """Shared look for the round power button on cards and the inspector.

    ``offline`` keeps the button neutral (never the accent "on" fill) and
    names the last known power in the tooltip.
    """
    is_on = state == "on"
    button.setProperty("powerState", state)
    icon_color = {
        "on": qcolor("fl_accent_on"),
        "off": qcolor("fl_text"),
        "offline": qcolor("fl_text_disabled"),
    }.get(state, qcolor("fl_text_secondary"))
    button.setIcon(tinted_icon(IconKey.POWER, icon_color, 20))
    if state == "offline":
        known = "On" if last_known is True else "Off" if last_known is False else None
        button.setToolTip(
            f"Offline · last known: {known}. Click to try turning it {'off' if last_known else 'on'}." if known
            else "Offline · power state unknown. Click to try turning it on."
        )
        button.setAccessibleDescription(f"Offline, last known {known.lower()}" if known else "Offline")
    else:
        button.setToolTip(
            "Turn off" if is_on else "Turn on" if state == "off" else "Power state unknown · click to turn on"
        )
        button.setAccessibleDescription("On" if is_on else "Off" if state == "off" else "Unknown")
    style = button.style()
    style.unpolish(button)
    style.polish(button)


__all__ = [
    "DeviceCard",
    "DeviceStatus",
    "CARD_WIDTH",
    "CARD_MIN_WIDTH",
    "UNKNOWN_VALUE",
    "apply_status",
    "connection_name",
    "describe_device_status",
    "device_subline",
    "power_visual_state",
    "set_power_button_state",
]
