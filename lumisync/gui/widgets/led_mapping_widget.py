"""LED-to-screen content mapping widget."""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from PyQt6.QtCore import QPoint, QRect, QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen, QBrush
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ... import connection, led_mapping, utils
from ...led_mapping import NormalizedRect
from ..theme import qcolor

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


class ScreenRegionPreview(QFrame):
    """Visual preview of the monitor with draggable content zones."""

    zone_drag_swap = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFrameShape(QFrame.Shape.Box)
        self.setStyleSheet(
            f"background-color: {qcolor('surface').name()};"
            f"border: 1px solid {qcolor('border').name()};"
            f"border-radius: 8px;"
        )

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

        self.setMouseTracking(True)
        self.setAcceptDrops(True)

    def set_mapping(self, mapping: List[NormalizedRect]) -> None:
        self._mapping = [led_mapping.normalize_rect(rect) for rect in mapping]
        self.update()

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
        painter.setPen(QPen(qcolor("border"), 1))
        painter.setBrush(QBrush(qcolor("surface_alt")))
        painter.drawRoundedRect(screen_rect, 6, 6)

        center_rect = screen_rect
        painter.setPen(qcolor("text_dim"))
        font = QFont()
        font.setPointSize(11)
        painter.setFont(font)
        painter.drawText(center_rect, Qt.AlignmentFlag.AlignCenter, "Screen Content")

        for index, _rect in enumerate(self._mapping):
            rect = self._zone_rect(index).adjusted(2, 2, -2, -2)
            color = self._zone_colors.get(index, qcolor("accent"))
            is_source = self._drag_active and index == self._drag_source
            is_target = self._drag_active and index == self._hover_zone
            is_hover = index == self._hover_zone

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
                fill_color = QColor(qcolor("accent"))
                fill_color.setAlpha(75 if is_hover else 45)
                border_color = qcolor("accent_bright") if is_hover else qcolor("border_strong")
                if is_source:
                    fill_color.setAlpha(120)
                    border_color = qcolor("accent_bright")
                elif is_target:
                    fill_color = QColor(qcolor("success"))
                    fill_color.setAlpha(130)
                    border_color = qcolor("success")

            painter.setPen(QPen(border_color, 3 if is_source or is_target else 2))
            painter.setBrush(QBrush(fill_color))
            painter.drawRoundedRect(rect, 4, 4)

            brightness = (
                fill_color.red() * 299
                + fill_color.green() * 587
                + fill_color.blue() * 114
            ) / 1000
            text_color = QColor(0, 0, 0) if brightness > 128 else QColor(255, 255, 255)
            painter.setPen(text_color)
            font = QFont()
            font.setPointSize(9 if len(self._mapping) < 24 else 7)
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
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(
                indicator_rect,
                Qt.AlignmentFlag.AlignCenter,
                str(self._drag_source + 1),
            )


class LedMappingWidget(QWidget):
    """Widget for mapping LED positions to screen content zones."""

    mapping_changed = pyqtSignal(list)

    def __init__(
        self,
        settings: QSettings,
        sync_controller: Optional["SyncController"] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.sync_controller = sync_controller
        self._test_mode_active = False
        self._test_timer: Optional[QTimer] = None
        self._razer_mode_enabled = False
        self._segment_count = len(DEFAULT_LED_MAPPING)
        self._aspect_ratio = led_mapping.DEFAULT_ASPECT_RATIO
        self._capture_depth = led_mapping.capture_depth_from_settings(settings)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        instructions = QLabel(
            "Drag zones to swap LED positions. Colors show on your LED strip."
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet(f"color: {qcolor('text_dim').name()}; font-size: 9pt;")
        layout.addWidget(instructions)

        self.screen_preview = ScreenRegionPreview()
        layout.addWidget(self.screen_preview)

        self.selection_label = QLabel("Drag a zone to swap LED positions")
        self.selection_label.setStyleSheet(
            f"color: {qcolor('text_disabled').name()}; font-style: italic;"
        )
        layout.addWidget(self.selection_label)

        depth_layout = QHBoxLayout()
        depth_layout.addWidget(QLabel("Capture depth"))
        self.capture_depth_slider = QSlider(Qt.Orientation.Horizontal)
        self.capture_depth_slider.setRange(
            int(led_mapping.MIN_CAPTURE_DEPTH * 100),
            int(led_mapping.MAX_CAPTURE_DEPTH * 100),
        )
        self.capture_depth_slider.setValue(int(round(self._capture_depth * 100)))
        self.capture_depth_slider.valueChanged.connect(self._on_capture_depth_changed)
        depth_layout.addWidget(self.capture_depth_slider, 1)
        self.capture_depth_label = QLabel(f"{int(round(self._capture_depth * 100))}%")
        self.capture_depth_label.setMinimumWidth(42)
        self.capture_depth_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        depth_layout.addWidget(self.capture_depth_label)
        layout.addLayout(depth_layout)

        button_layout = QHBoxLayout()

        self.test_button = QPushButton("■ Stop Test")
        self.test_button.setToolTip("Toggle test colors on LED strip")
        self.test_button.clicked.connect(self._toggle_test_mode)
        self.test_button.setCheckable(True)
        self.test_button.setChecked(True)
        button_layout.addWidget(self.test_button)

        self.reverse_button = QPushButton("Reverse Order")
        self.reverse_button.clicked.connect(self._reverse_order)
        button_layout.addWidget(self.reverse_button)

        self.reset_button = QPushButton("Reset to Default")
        self.reset_button.clicked.connect(self._reset_to_default)
        button_layout.addWidget(self.reset_button)

        button_layout.addStretch()
        layout.addLayout(button_layout)

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
        self.test_button.setText("■ Stop Test")
        self.test_button.setChecked(True)
        self.selection_label.setText("Test mode - drag zones to swap LEDs")
        self._enable_razer_mode()
        self._update_test_display()

        self._test_timer = QTimer(self)
        self._test_timer.timeout.connect(self._refresh_test_colors)
        self._test_timer.start(1000)

    def _stop_test_mode(self) -> None:
        self._test_mode_active = False
        self._razer_mode_enabled = False
        self.test_button.setText("Test Colors")
        self.test_button.setChecked(False)
        self.selection_label.setText("Drag a zone to swap LED positions")

        if self._test_timer:
            self._test_timer.stop()
            self._test_timer = None

        self.screen_preview.clear_colors()

        try:
            self._send_colors_to_strip([(0, 0, 0)] * len(self.get_mapping()))
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

    def _zone_colors(self) -> Dict[int, Tuple[int, int, int]]:
        return {
            index: rect_test_color(rect)
            for index, rect in enumerate(self.get_mapping())
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
        try:
            if not self.sync_controller:
                return

            devices = self.sync_controller.get_selected_devices()
            if not devices:
                return

            self.sync_controller._ensure_server()
            server = self.sync_controller.server
            if server:
                if not skip_razer_enable and not self._razer_mode_enabled:
                    for device in devices:
                        connection.switch_razer(server, device, True)
                    self._razer_mode_enabled = True
                for device in devices:
                    payload = utils.convert_colors(
                        fit_led_colors_to_device(device, led_colors)
                    )
                    connection.send_razer_data(server, device, payload)
        except Exception as e:
            if self._test_mode_active:
                self.selection_label.setText(f"Connection error: {str(e)[:30]}")

    def _reset_to_default(self) -> None:
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
