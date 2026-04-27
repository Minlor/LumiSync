"""DeviceCard — a single device tile used in the Devices view grid."""

from __future__ import annotations

from typing import Any, Dict, Optional

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QEnterEvent, QMouseEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..theme import qcolor
from ..utils.animations import PulseDot


CARD_WIDTH = 300
CARD_MIN_HEIGHT = 170


class DeviceCard(QFrame):
    """Single-device tile with inline controls.

    Signals:
        selection_changed(index, selected)
        primary_clicked(index)         emitted when card body is clicked, used to set primary device
        power_clicked(index, on)
        color_picked(index, QColor)
        brightness_changed(index, value)
        remove_requested(index)
    """

    selection_changed = pyqtSignal(int, bool)
    primary_clicked = pyqtSignal(int)
    power_clicked = pyqtSignal(int, bool)
    color_picked = pyqtSignal(int, QColor)
    brightness_changed = pyqtSignal(int, int)
    remove_requested = pyqtSignal(int)

    def __init__(self, index: int, device: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._index = index
        self._device = dict(device)
        self._current_color = QColor("#7C5CFF")
        self._is_primary = False
        self._is_selected = False

        self.setObjectName("DeviceCard")
        self.setProperty("card", True)
        self.setProperty("hovered", False)
        self.setProperty("selected", False)
        self.setFixedWidth(CARD_WIDTH)
        self.setMinimumHeight(CARD_MIN_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        # Subtle shadow
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 90))
        self.setGraphicsEffect(shadow)

        self._build()

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        # Header row: select checkbox + status dot + name + overflow
        header = QHBoxLayout()
        header.setSpacing(8)

        self.checkbox = QCheckBox()
        self.checkbox.toggled.connect(self._on_checkbox_toggled)
        header.addWidget(self.checkbox)

        self.status_dot = PulseDot(qcolor("success"), self, size=8)
        # Default: dim until we know reachability. No active probe today.
        self.status_dot.set_active(False)
        header.addWidget(self.status_dot)

        self.name_label = QLabel(self._device.get("model", "Unknown"))
        f = self.name_label.font()
        f.setPointSize(11)
        f.setBold(True)
        self.name_label.setFont(f)
        self.name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        header.addWidget(self.name_label, 1)

        self.menu_button = QToolButton()
        self.menu_button.setText("⋮")
        self.menu_button.setProperty("role", "ghost")
        self.menu_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.menu_button.setStyleSheet(
            "QToolButton { background: transparent; border: none; "
            "color: " + qcolor("text_dim").name() + "; padding: 2px 6px; font-size: 14pt; }"
            "QToolButton:hover { color: " + qcolor("text").name() + "; }"
        )
        self.menu_button.clicked.connect(self._show_menu)
        header.addWidget(self.menu_button)

        root.addLayout(header)

        # Sub-line: IP · Port · Source
        sub = QLabel(self._format_subline())
        sub.setProperty("role", "subtle")
        sub.setStyleSheet(f"color: {qcolor('text_dim').name()}; font-size: 9pt;")
        root.addWidget(sub)

        # Brightness row
        bright_row = QHBoxLayout()
        bright_row.setSpacing(8)
        bright_label = QLabel("☀")
        bright_label.setStyleSheet(f"color: {qcolor('text_dim').name()};")
        bright_row.addWidget(bright_label)

        self.brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self.brightness_slider.setRange(0, 100)
        self.brightness_slider.setValue(100)
        self.brightness_slider.valueChanged.connect(self._on_brightness)
        bright_row.addWidget(self.brightness_slider, 1)

        self.brightness_value = QLabel("100%")
        self.brightness_value.setMinimumWidth(36)
        self.brightness_value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.brightness_value.setStyleSheet(f"color: {qcolor('text_dim').name()}; font-size: 9pt;")
        bright_row.addWidget(self.brightness_value)
        root.addLayout(bright_row)

        # Action row: power / color
        actions = QHBoxLayout()
        actions.setSpacing(6)

        self.on_button = QPushButton("On")
        self.on_button.setProperty("role", "primary")
        self.on_button.clicked.connect(lambda: self.power_clicked.emit(self._index, True))
        actions.addWidget(self.on_button)

        self.off_button = QPushButton("Off")
        self.off_button.clicked.connect(lambda: self.power_clicked.emit(self._index, False))
        actions.addWidget(self.off_button)

        actions.addStretch(1)

        self.color_swatch = QPushButton()
        self.color_swatch.setObjectName("ColorSwatch")
        self.color_swatch.setToolTip("Set color")
        self.color_swatch.clicked.connect(self._on_color_clicked)
        self._refresh_swatch()
        actions.addWidget(self.color_swatch)

        root.addLayout(actions)

    def _format_subline(self) -> str:
        ip = self._device.get("ip", "?")
        port = self._device.get("port", "?")
        source = "Manual" if self._device.get("manual") else "Discovered"
        return f"{ip} · :{port} · {source}"

    # ------------------------------------------------------------------ events

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            # Click anywhere on the card body (not on a child control) sets primary
            child = self.childAt(event.pos())
            if child is None or child is self.name_label:
                self.primary_clicked.emit(self._index)
        super().mousePressEvent(event)

    def enterEvent(self, event: QEnterEvent) -> None:
        self.setProperty("hovered", True)
        self._repolish()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.setProperty("hovered", False)
        self._repolish()
        super().leaveEvent(event)

    # ------------------------------------------------------------------ slots

    def _on_checkbox_toggled(self, checked: bool) -> None:
        self._is_selected = bool(checked)
        self.setProperty("selected", self._is_selected)
        self._repolish()
        self.selection_changed.emit(self._index, self._is_selected)

    def _on_brightness(self, value: int) -> None:
        self.brightness_value.setText(f"{value}%")
        self.brightness_changed.emit(self._index, value)

    def _on_color_clicked(self) -> None:
        color = QColorDialog.getColor(self._current_color, self, "Choose color")
        if color.isValid():
            self._current_color = color
            self._refresh_swatch()
            self.color_picked.emit(self._index, color)

    def _show_menu(self) -> None:
        menu = QMenu(self)
        remove_action = menu.addAction("Remove device")
        chosen = menu.exec(self.menu_button.mapToGlobal(self.menu_button.rect().bottomLeft()))
        if chosen == remove_action:
            self.remove_requested.emit(self._index)

    # ------------------------------------------------------------------ public

    def set_index(self, index: int) -> None:
        self._index = index

    def set_primary(self, primary: bool) -> None:
        self._is_primary = bool(primary)
        self.status_dot.set_active(self._is_primary)

    def set_checked(self, checked: bool) -> None:
        if self.checkbox.isChecked() != checked:
            self.checkbox.setChecked(checked)

    def device(self) -> Dict[str, Any]:
        return dict(self._device)

    def _refresh_swatch(self) -> None:
        c = self._current_color
        self.color_swatch.setStyleSheet(
            "QPushButton#ColorSwatch {"
            f"background: {c.name()};"
            f"border: 2px solid {qcolor('border').name()};"
            "border-radius: 6px; min-width: 28px; min-height: 28px; max-width: 28px; max-height: 28px;"
            "}"
            "QPushButton#ColorSwatch:hover {"
            f"border-color: {qcolor('text').name()};"
            "}"
        )

    def _repolish(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


__all__ = ["DeviceCard", "CARD_WIDTH", "CARD_MIN_HEIGHT"]
