"""LED-to-screen content mapping widget."""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from PySide6.QtCore import QPoint, QRect, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen, QBrush
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ... import connection, led_mapping, utils
from ...drivers import pool
from ...drivers.registry import capabilities_for_device
from ...led_mapping import NormalizedRect
from ...sync import processing
from ..controllers.background import start_task
from ..theme import qcolor
from ..utils.flow_layout import FlowLayout
from .product_controls import ProductSlider

if TYPE_CHECKING:
    from ..controllers.sync_controller import SyncController


DEFAULT_LED_MAPPING = led_mapping.DEFAULT_LEGACY_MAPPING
REGION_NAMES: Dict[Tuple[int, int], str] = {
    (0, 0): "Top-Left",
    (0, 1): "Top-Center-Left",
    (0, 2): "Top-Center-Right",
    (0, 3): "Top-Right",
    (1, 0): "Left",
    (1, 3): "Right",
    (2, 0): "Bottom-Left",
    (2, 1): "Bottom-Center-Left",
    (2, 2): "Bottom-Center-Right",
    (2, 3): "Bottom-Right",
}

TEST_COLORS: List[Tuple[int, int, int]] = [
    (255, 0, 0),
    (255, 165, 0),
    (255, 255, 0),
    (0, 255, 0),
    (255, 0, 255),
    (0, 255, 255),
    (128, 0, 255),
    (0, 128, 255),
    (0, 255, 128),
    (255, 255, 255),
]


def zone_test_color(index: int) -> Tuple[int, int, int]:
    if index < len(TEST_COLORS):
        return TEST_COLORS[index]
    color = QColor.fromHsv((index * 37) % 360, 210, 255)
    return color.red(), color.green(), color.blue()


def rect_test_color(rect: NormalizedRect) -> Tuple[int, int, int]:
    normalized = led_mapping.normalize_rect(rect)
    center_x = normalized["x"] + normalized["w"] / 2
    center_y = normalized["y"] + normalized["h"] / 2
    if abs(center_x - 0.5) < 0.001 and abs(center_y - 0.5) < 0.001:
        return 255, 255, 255

    angle = math.atan2(center_y - 0.5, center_x - 0.5)
    hue = int(((angle + math.pi) / (2 * math.pi)) * 359)
    value = 210 + int(min(45, math.hypot(center_x - 0.5, center_y - 0.5) * 90))
    color = QColor.fromHsv(hue, 220, value)
    return color.red(), color.green(), color.blue()


def fit_led_colors_to_device(
    device: Dict[str, object],
    led_colors: List[Tuple[int, int, int]],
) -> List[Tuple[int, int, int]]:
    """Resize LED mapping colors to the segment count expected by one device."""
    segment_count = connection.get_segment_count(device, default=len(DEFAULT_LED_MAPPING))
    return utils.resample_colors_to_count(led_colors, segment_count)


def fit_led_mapping_to_count(
    mapping: List[NormalizedRect],
    segment_count: int,
) -> List[NormalizedRect]:
    """Compatibility wrapper for tests and callers."""
    return led_mapping.fit_normalized_mapping_to_count(mapping, segment_count)


def zone_text_color(fill: QColor) -> QColor:
    """Choose AA text contrast over the actual composited preview fill."""
    background = qcolor("surface_alt")
    alpha = fill.alphaF()
    channels = [((first * alpha + second * (1 - alpha)) / 255)
                for first, second in zip((fill.red(), fill.green(), fill.blue()),
                                         (background.red(), background.green(), background.blue()))]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    luminance = sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    return QColor("black" if luminance > 0.179 else "white")


class ScreenRegionPreview(QFrame):
    """Visual preview of the monitor with draggable content zones."""

    zone_drag_swap = Signal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("background: transparent; border: none;")

        self._mapping: List[NormalizedRect] = led_mapping.generate_screen_mapping(
            len(DEFAULT_LED_MAPPING)
        )
        self._aspect_ratio = led_mapping.DEFAULT_ASPECT_RATIO
        self._zone_colors: Dict[int, QColor] = {}
        self._show_colors = False
        self._hover_zone: Optional[int] = None
        self._drag_source: Optional[int] = None
        self._drag_active = False
        self._drag_pos: Optional[QPoint] = None
        self._keyboard_zone = 0
        self._keyboard_source = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("LED positions on the screen")
        self.setAccessibleDescription("Arrow keys select a zone. Enter chooses a source, then Enter on another zone swaps them. Escape cancels.")

        self.setMouseTracking(True)
        self.setAcceptDrops(True)

    def set_mapping(self, mapping: List[NormalizedRect]) -> None:
        self._mapping = [led_mapping.normalize_rect(rect) for rect in mapping]
        self._keyboard_zone = min(self._keyboard_zone, max(0, len(self._mapping) - 1))
        self._keyboard_source = None
        self.update()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self._mapping:
            return
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_Right, Qt.Key.Key_Down):
            step = -1 if key in (Qt.Key.Key_Left, Qt.Key.Key_Up) else 1
            self._keyboard_zone = (self._keyboard_zone + step) % len(self._mapping)
        elif key in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Space):
            if self._keyboard_source is None:
                self._keyboard_source = self._keyboard_zone
            else:
                source, self._keyboard_source = self._keyboard_source, None
                if source != self._keyboard_zone:
                    self.zone_drag_swap.emit(source, self._keyboard_zone)
        elif key == Qt.Key.Key_Escape:
            self._keyboard_source = None
        else:
            super().keyPressEvent(event)
            return
        self.setAccessibleDescription(f"Zone {self._keyboard_zone + 1} of {len(self._mapping)}. "
                                     + ("Choose the destination and press Enter." if self._keyboard_source is not None else "Press Enter to choose a source."))
        self.update()
        event.accept()

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._keyboard_source = None
        self.update()
        super().focusOutEvent(event)

    def get_mapping(self) -> List[NormalizedRect]:
        return [dict(rect) for rect in self._mapping]

    def set_aspect_ratio(self, aspect_ratio: float) -> None:
        self._aspect_ratio = led_mapping.sanitize_aspect_ratio(aspect_ratio)
        self.update()

    def set_zone_colors(self, colors: Dict[int, Tuple[int, int, int]]) -> None:
        self._zone_colors = {
            index: QColor(r, g, b)
            for index, (r, g, b) in colors.items()
        }
        self._show_colors = bool(colors)
        self.update()

    def clear_colors(self) -> None:
        self._zone_colors = {}
        self._show_colors = False
        self.update()

    def _screen_rect(self) -> QRect:
        margin = 10
        available = self.rect().adjusted(margin, margin, -margin, -margin)
        width = available.width()
        height = available.height()
        target_ratio = self._aspect_ratio
        if width / max(1, height) > target_ratio:
            screen_h = height
            screen_w = int(screen_h * target_ratio)
        else:
            screen_w = width
            screen_h = int(screen_w / target_ratio)
        x = available.left() + (width - screen_w) // 2
        y = available.top() + (height - screen_h) // 2
        return QRect(x, y, screen_w, screen_h)

    def _zone_rect(self, index: int) -> QRect:
        screen = self._screen_rect()
        rect = self._mapping[index]
        x = screen.left() + int(rect["x"] * screen.width())
        y = screen.top() + int(rect["y"] * screen.height())
        w = max(8, int(rect["w"] * screen.width()))
        h = max(8, int(rect["h"] * screen.height()))
        return QRect(x, y, w, h)

    def _zone_at_pos(self, pos: QPoint) -> Optional[int]:
        for index in reversed(range(len(self._mapping))):
            if self._zone_rect(index).contains(pos):
                return index
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            zone = self._zone_at_pos(event.pos())
            if zone is not None:
                self._drag_source = zone
                self._drag_active = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        zone = self._zone_at_pos(event.pos())
        if zone != self._hover_zone:
            self._hover_zone = zone
            self.update()

        if self._drag_active:
            self._drag_pos = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update()
        elif zone is not None:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_active:
            target = self._zone_at_pos(event.pos())
            if (
                target is not None
                and self._drag_source is not None
                and target != self._drag_source
            ):
                self.zone_drag_swap.emit(self._drag_source, target)
            self._drag_source = None
            self._drag_active = False
            self._drag_pos = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_zone = None
        if self._drag_active:
            self._drag_source = None
            self._drag_active = False
            self._drag_pos = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        screen_rect = self._screen_rect()
        painter.setPen(QPen(qcolor("fl_card_stroke"), 1))
        painter.setBrush(QBrush(qcolor("surface_alt")))
        painter.drawRoundedRect(screen_rect, 8, 8)

        center_rect = screen_rect
        painter.setPen(qcolor("fl_text_tertiary"))
        font = QFont(self.font())
        font.setPixelSize(14)
        painter.setFont(font)
        painter.drawText(center_rect, Qt.AlignmentFlag.AlignCenter, "Screen content")
        if self.hasFocus() and self.window() is not None and self.window().testAttribute(
            Qt.WidgetAttribute.WA_KeyboardFocusChange
        ):
            painter.setPen(QPen(qcolor("fl_text"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(screen_rect.adjusted(-3, -3, 3, 3), 10, 10)

        for index, _rect in enumerate(self._mapping):
            rect = self._zone_rect(index).adjusted(2, 2, -2, -2)
            color = self._zone_colors.get(index, qcolor("accent"))
            is_source = (self._drag_active and index == self._drag_source) or index == self._keyboard_source
            is_target = self._drag_active and index == self._hover_zone
            is_hover = index == self._hover_zone or (self.hasFocus() and index == self._keyboard_zone)

            if self._show_colors:
                fill_color = QColor(color)
                if is_source:
                    fill_color = fill_color.darker(150)
                    border_color = QColor(255, 200, 100)
                elif is_target:
                    fill_color = fill_color.lighter(130)
                    border_color = QColor(100, 255, 100)
                elif is_hover:
                    fill_color = fill_color.lighter(120)
                    border_color = QColor(220, 220, 220)
                else:
                    border_color = fill_color.darker(150)
            else:
                fill_color = QColor(qcolor("fl_accent"))
                fill_color.setAlpha(70 if is_hover else 40)
                border_color = qcolor("fl_accent") if is_hover else QColor(255, 255, 255, 60)
                if is_source:
                    fill_color.setAlpha(120)
                    border_color = qcolor("fl_accent_hover")
                elif is_target:
                    fill_color = QColor(qcolor("success"))
                    fill_color.setAlpha(130)
                    border_color = qcolor("success")

            painter.setPen(QPen(border_color, 2 if is_source or is_target else 1))
            painter.setBrush(QBrush(fill_color))
            painter.drawRoundedRect(QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)

            painter.setPen(zone_text_color(fill_color))
            font = QFont(self.font())
            font.setPixelSize(12 if len(self._mapping) < 24 else 10)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(index + 1))

        if self._drag_active and self._drag_pos is not None and self._drag_source is not None:
            indicator_size = 38
            indicator_rect = QRect(
                self._drag_pos.x() - indicator_size // 2,
                self._drag_pos.y() - indicator_size // 2,
                indicator_size,
                indicator_size,
            )
            drag_color = self._zone_colors.get(self._drag_source, qcolor("accent"))
            painter.setPen(QPen(QColor(255, 255, 255), 2))
            painter.setBrush(
                QBrush(QColor(drag_color.red(), drag_color.green(), drag_color.blue(), 200))
            )
            painter.drawRoundedRect(indicator_rect, 6, 6)
            painter.setPen(zone_text_color(QColor(drag_color.red(), drag_color.green(), drag_color.blue(), 200)))
            painter.drawText(
                indicator_rect,
                Qt.AlignmentFlag.AlignCenter,
                str(self._drag_source + 1),
            )


class LedMappingWidget(QWidget):
    """Widget for mapping LED positions to screen content zones."""

    mapping_changed = Signal(list)

    def __init__(
        self,
        settings: QSettings,
        sync_controller: Optional["SyncController"] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.sync_controller = sync_controller
        if sync_controller is not None:
            # The test pattern should look like a live monitor-sync frame. Keep
            # it in step with the brightness and boost controls above it.
            sync_controller.brightness_changed.connect(self._on_preview_settings_changed)
            boost_signal = getattr(sync_controller, "monitor_boost_changed", None)
            if boost_signal is not None:
                boost_signal.connect(self._on_preview_settings_changed)
        self._test_mode_active = False
        self._test_timer: Optional[QTimer] = None
        self._last_test_colors: Optional[List[Tuple[int, int, int]]] = None
        self._razer_mode_enabled = False
        self._segment_count = len(DEFAULT_LED_MAPPING)
        self._aspect_ratio = led_mapping.DEFAULT_ASPECT_RATIO
        self._capture_depth = led_mapping.capture_depth_from_settings(settings)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        instructions = QLabel(
            "Drag zones to swap positions, or use arrow keys and Enter to choose "
            "two zones. Test colors use the current Monitor brightness and Boost."
        )
        instructions.setWordWrap(True)
        instructions.setProperty("role", "subtle")
        layout.addWidget(instructions)

        self.screen_preview = ScreenRegionPreview()
        layout.addWidget(self.screen_preview)

        self.selection_label = QLabel("Drag a zone to swap LED positions")
        self.selection_label.setProperty("role", "hint")
        layout.addWidget(self.selection_label)

        depth_layout = QHBoxLayout()
        depth_layout.setSpacing(12)
        depth_label = QLabel("Capture depth")
        depth_label.setToolTip("How far into the screen each zone samples colors")
        depth_layout.addWidget(depth_label)
        self.capture_depth_slider = ProductSlider(Qt.Orientation.Horizontal)
        self.capture_depth_slider.setRange(
            int(led_mapping.MIN_CAPTURE_DEPTH * 100),
            int(led_mapping.MAX_CAPTURE_DEPTH * 100),
        )
        self.capture_depth_slider.setValue(int(round(self._capture_depth * 100)))
        self.capture_depth_slider.valueChanged.connect(self._on_capture_depth_changed)
        depth_layout.addWidget(self.capture_depth_slider, 1)
        self.capture_depth_label = QLabel(f"{int(round(self._capture_depth * 100))}%")
        self.capture_depth_label.setMinimumWidth(44)
        self.capture_depth_label.setProperty("role", "valueLabel")
        self.capture_depth_slider.setMaximumWidth(360)
        self.capture_depth_slider.setAccessibleName("Capture depth")
        self.capture_depth_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        depth_layout.addWidget(self.capture_depth_label)
        depth_layout.addStretch(1)
        layout.addLayout(depth_layout)

        button_host = QWidget()
        button_layout = FlowLayout(button_host, h_spacing=8, v_spacing=8)

        self.test_button = QPushButton("Test colors")
        self.test_button.setToolTip("Toggle test colors on LED strip")
        self.test_button.clicked.connect(self._toggle_test_mode)
        self.test_button.setCheckable(True)
        self.test_button.setChecked(True)
        button_layout.addWidget(self.test_button)

        self.reverse_button = QPushButton("Reverse order")
        self.reverse_button.clicked.connect(self._reverse_order)
        button_layout.addWidget(self.reverse_button)

        # Strips start in any corner. Moving LED 1 one zone at a time (with
        # Reverse Order for the direction) matches every mounting.
        self.shift_start_back_button = QPushButton("Shift start ◀")
        self.shift_start_back_button.setToolTip("Move LED 1 to the next zone")
        self.shift_start_back_button.setAccessibleName("Shift strip start back by one zone")
        self.shift_start_back_button.clicked.connect(lambda: self._shift_start(1))
        button_layout.addWidget(self.shift_start_back_button)

        self.shift_start_forward_button = QPushButton("Shift start ▶")
        self.shift_start_forward_button.setToolTip("Move LED 1 to the previous zone")
        self.shift_start_forward_button.setAccessibleName("Shift strip start forward by one zone")
        self.shift_start_forward_button.clicked.connect(lambda: self._shift_start(-1))
        button_layout.addWidget(self.shift_start_forward_button)

        self.reset_button = QPushButton("Reset to default")
        self.reset_button.clicked.connect(self._reset_to_default)
        button_layout.addWidget(self.reset_button)

        layout.addWidget(button_host)

        self.screen_preview.zone_drag_swap.connect(self._on_zone_swap)
        self._load_mapping()

    def set_mapping_context(self, segment_count: int, aspect_ratio: float) -> None:
        segment_count = led_mapping.clamp_zone_count(segment_count)
        aspect_ratio = led_mapping.sanitize_aspect_ratio(aspect_ratio)
        count_changed = segment_count != self._segment_count
        aspect_changed = abs(aspect_ratio - self._aspect_ratio) > 0.001
        if not count_changed and not aspect_changed:
            return

        self._segment_count = segment_count
        self._aspect_ratio = aspect_ratio
        self.screen_preview.set_aspect_ratio(aspect_ratio)
        saved_mapping = led_mapping.parse_normalized_mapping(
            self.settings.value(led_mapping.NORMALIZED_MAPPING_KEY, None)
        )
        regenerated = False
        if saved_mapping and len(saved_mapping) == segment_count and not aspect_changed:
            mapping = saved_mapping
        else:
            regenerated = True
            mapping = led_mapping.generate_screen_mapping(
                segment_count,
                aspect_ratio,
                self._capture_depth,
            )
        self.screen_preview.set_mapping(mapping)
        self.selection_label.setText(f"Mapping {segment_count} zones")
        if regenerated:
            self._save_mapping()

        if self._test_mode_active:
            self._update_test_display()

    def set_segment_count(self, segment_count: int) -> None:
        self.set_mapping_context(segment_count, self._aspect_ratio)

    def start_test_mode_if_not_active(self) -> None:
        if not self._test_mode_active:
            self._start_test_mode()

    def stop_test_mode_if_active(self) -> None:
        if self._test_mode_active:
            self._stop_test_mode()

    def _load_mapping(self) -> None:
        mapping = led_mapping.load_mapping_from_settings(
            self.settings,
            self._segment_count,
            self._aspect_ratio,
            self._capture_depth,
        )
        self.screen_preview.set_aspect_ratio(self._aspect_ratio)
        self.screen_preview.set_mapping(mapping)

    def _save_mapping(self) -> None:
        mapping = self.screen_preview.get_mapping()
        self.settings.setValue(
            led_mapping.NORMALIZED_MAPPING_KEY,
            led_mapping.serialize_mapping(mapping),
        )
        self.mapping_changed.emit(mapping)

    def get_mapping(self) -> List[NormalizedRect]:
        return self.screen_preview.get_mapping()

    def _on_zone_swap(self, source_index: int, target_index: int) -> None:
        mapping = self.screen_preview.get_mapping()
        if not (0 <= source_index < len(mapping) and 0 <= target_index < len(mapping)):
            return

        mapping[source_index], mapping[target_index] = mapping[target_index], mapping[source_index]
        self.screen_preview.set_mapping(mapping)
        self.selection_label.setText(
            f"Swapped zone {source_index + 1} ↔ zone {target_index + 1}"
        )
        self._save_mapping()

        if self._test_mode_active:
            self._update_test_display()

    def _reverse_order(self) -> None:
        mapping = list(reversed(self.screen_preview.get_mapping()))
        self.screen_preview.set_mapping(mapping)
        self.selection_label.setText("Zone order reversed")
        self._save_mapping()

        if self._test_mode_active:
            self._update_test_display()

    def _shift_start(self, steps: int) -> None:
        """Rotate which zone LED 1 reads; ``1`` is ``m[1:] + m[:1]``."""
        mapping = self.screen_preview.get_mapping()
        if len(mapping) < 2:
            return
        self.screen_preview.set_mapping(led_mapping.shift_mapping_start(mapping, steps))
        self.selection_label.setText("Strip start moved by one zone")
        self._save_mapping()

        if self._test_mode_active:
            self._update_test_display()

    def _on_capture_depth_changed(self, value: int) -> None:
        self._capture_depth = led_mapping.clamp_capture_depth(value / 100)
        self.capture_depth_label.setText(f"{int(round(self._capture_depth * 100))}%")
        self.settings.setValue(led_mapping.CAPTURE_DEPTH_KEY, self._capture_depth)
        self.screen_preview.set_mapping(
            led_mapping.generate_screen_mapping(
                self._segment_count,
                self._aspect_ratio,
                self._capture_depth,
            )
        )
        self.selection_label.setText(
            f"Capture depth set to {int(round(self._capture_depth * 100))}%"
        )
        self._save_mapping()

        if self._test_mode_active:
            self._update_test_display()

    def _toggle_test_mode(self) -> None:
        if self._test_mode_active:
            self._stop_test_mode()
        else:
            self._start_test_mode()

    def _start_test_mode(self) -> None:
        self._test_mode_active = True
        self.test_button.setText("Stop test")
        self.test_button.setChecked(True)
        self._last_test_colors = None
        self.selection_label.setText("Test mode - drag zones to swap LEDs")
        self._enable_razer_mode()
        self._update_test_display()

        self._test_timer = QTimer(self)
        self._test_timer.timeout.connect(self._refresh_test_colors)
        self._test_timer.start(1000)

    def _stop_test_mode(self) -> None:
        self._test_mode_active = False
        self.test_button.setText("Test colors")
        self.test_button.setChecked(False)
        self.selection_label.setText("Drag a zone to swap LED positions")

        if self._test_timer:
            self._test_timer.stop()
            self._test_timer = None

        self.screen_preview.clear_colors()

        try:
            # The test already owns Razer mode. Re-enabling it while cleaning up
            # creates a competing stream when Monitor Sync starts immediately
            # afterwards, so send the final black frame under existing ownership.
            self._send_colors_to_strip(
                [(0, 0, 0)] * len(self.get_mapping()),
                skip_razer_enable=True,
            )
            self._disable_razer_mode()
        finally:
            if self.sync_controller and not self.sync_controller.is_syncing():
                self.sync_controller.close_server()

    def _enable_razer_mode(self) -> None:
        if self._razer_mode_enabled:
            return
        try:
            if not self.sync_controller:
                return

            devices = self.sync_controller.get_selected_devices()
            if not devices:
                return

            self.sync_controller._ensure_server()
            server = self.sync_controller.server
            if server:
                for device in devices:
                    connection.switch_razer(server, device, True)
                self._razer_mode_enabled = True
        except Exception:
            pass

    def _disable_razer_mode(self) -> None:
        if not self._razer_mode_enabled:
            return
        try:
            if not self.sync_controller or self.sync_controller.is_syncing():
                return

            devices = self.sync_controller.get_selected_devices()
            if not devices:
                return

            self.sync_controller._ensure_server()
            server = self.sync_controller.server
            if server:
                for device in devices:
                    connection.switch_razer(server, device, False)
        except Exception:
            pass
        finally:
            self._razer_mode_enabled = False

    def _on_preview_settings_changed(self, *_args) -> None:
        """Refresh the strip and preview when brightness or boost changes."""
        if self._test_mode_active:
            self._last_test_colors = None
            self._update_test_display()

    def _apply_preview_settings(
        self, colors: List[Tuple[int, int, int]]
    ) -> List[Tuple[int, int, int]]:
        """Apply the live monitor brightness and boost to a test frame.

        LED Mapping used to send raw 255 test colors while Monitor Sync scaled
        every frame by the brightness slider (75% by default), so opening the
        mapper looked like a hidden overdrive. Previewing through the same
        pipeline keeps the two paths honest.
        """
        controller = self.sync_controller
        if controller is None:
            return [(int(r), int(g), int(b)) for r, g, b in colors]

        factor = 1.0
        getter = getattr(controller, "get_monitor_brightness", None)
        if getter is not None:
            factor = max(0.0, min(1.0, float(getter())))
        boost = 1.0
        getter = getattr(controller, "get_monitor_boost", None)
        if getter is not None:
            boost = max(1.0, min(1.5, float(getter())))

        # Same order as live sync: boost first, then the brightness ceiling.
        return processing.apply_brightness(
            processing.apply_brightness_boost(colors, boost), factor
        )

    def _zone_colors(self) -> Dict[int, Tuple[int, int, int]]:
        colors = self._apply_preview_settings(
            [rect_test_color(rect) for rect in self.get_mapping()]
        )
        return {
            index: color for index, color in enumerate(colors)
        }

    def _refresh_test_colors(self) -> None:
        if not self._test_mode_active:
            return
        led_colors = [rect_test_color(rect) for rect in self.get_mapping()]
        self._send_colors_to_strip(led_colors, skip_razer_enable=True)

    def _update_test_display(self) -> None:
        led_colors = [rect_test_color(rect) for rect in self.get_mapping()]
        self.screen_preview.set_zone_colors(self._zone_colors())
        self._send_colors_to_strip(led_colors, skip_razer_enable=False)

    def _send_colors_to_strip(
        self,
        led_colors: List[Tuple[int, int, int]],
        skip_razer_enable: bool = False,
    ) -> None:
        if not self.sync_controller:
            return
        try:
            led_colors = self._apply_preview_settings(led_colors)
            if self._last_test_colors == led_colors:
                return  # The 1 Hz timer re-sends unchanged test colors; skip.
            self._last_test_colors = list(led_colors)

            devices = self.sync_controller.get_selected_devices()
            if not devices:
                return

            # Tuya strips cannot read the Govee sync-server protocol (UDP
            # JSON); drive them through their transport adapter exactly like a
            # live sync run does. Adapter work is queued off the GUI thread:
            # connecting to a Tuya device takes seconds and froze the mapping
            # dialog when done inline.
            tuya_devices = [
                d for d in devices
                if str(d.get("transport", "lan")).lower() == "tuya"
            ]
            govee = [d for d in devices if d not in tuya_devices]

            for device in tuya_devices:
                colors = utils.resample_colors_to_count(
                    led_colors, capabilities_for_device(device).segment_count
                )

                def do_send(dev=device, frame=colors):
                    pool.acquire(dev, self.sync_controller.server).set_segments(frame)

                def on_done(_value, error, dev=device):
                    if error and self._test_mode_active:
                        self.selection_label.setText(f"{dev.get('model', 'Device')}: {error[:26]}")

                start_task(do_send, on_done)
            if govee:
                self.sync_controller._ensure_server()
                server = self.sync_controller.server
                if server:
                    if not skip_razer_enable and not self._razer_mode_enabled:
                        for device in govee:
                            connection.switch_razer(server, device, True)
                        self._razer_mode_enabled = True
                    for device in govee:
                        payload = utils.convert_colors(
                            fit_led_colors_to_device(device, led_colors)
                        )
                        connection.send_razer_data(server, device, payload)
        except Exception as e:
            if self._test_mode_active:
                self.selection_label.setText(f"Connection error: {str(e)[:30]}")

    def _reset_to_default(self) -> None:
        reply = QMessageBox.question(
            self,
            "Reset LED Mapping",
            "Replace the current zone layout with the default mapping?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.screen_preview.set_mapping(
            led_mapping.generate_screen_mapping(
                self._segment_count,
                self._aspect_ratio,
                self._capture_depth,
            )
        )
        self.selection_label.setText("Mapping reset to default")
        self._save_mapping()

        if self._test_mode_active:
            self._update_test_display()


__all__ = [
    "LedMappingWidget",
    "DEFAULT_LED_MAPPING",
    "REGION_NAMES",
    "TEST_COLORS",
    "fit_led_colors_to_device",
    "fit_led_mapping_to_count",
    "rect_test_color",
]
