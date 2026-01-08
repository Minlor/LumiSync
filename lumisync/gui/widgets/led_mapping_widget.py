"""LED-to-screen region mapping widget.

Provides a visual interface for users to assign screen regions to LED positions.
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

from PyQt6.QtCore import Qt, QRect, QPoint, pyqtSignal, QSettings, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QMouseEvent
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QFrame,
    QSizePolicy,
)

if TYPE_CHECKING:
    from ..controllers.sync_controller import SyncController


# Default region layout: 10 LEDs mapped to screen areas
# Format: list of (row, col) tuples where row is 0=top, 1=middle, 2=bottom
# and col is 0-3 for the 4 horizontal segments
DEFAULT_LED_MAPPING = [
    (0, 3),  # LED 0: Top-right
    (0, 2),  # LED 1: Top-center-right
    (0, 1),  # LED 2: Top-center-left
    (0, 0),  # LED 3: Top-left
    (1, 0),  # LED 4: Left side
    (2, 0),  # LED 5: Bottom-left
    (2, 1),  # LED 6: Bottom-center-left
    (2, 2),  # LED 7: Bottom-center-right
    (2, 3),  # LED 8: Bottom-right
    (1, 3),  # LED 9: Right side
]

# Screen region names for display
REGION_NAMES = {
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

# Vibrant test colors for each region - easy to distinguish
TEST_COLORS: Dict[Tuple[int, int], Tuple[int, int, int]] = {
    (0, 0): (255, 0, 0),      # Top-Left: Red
    (0, 1): (255, 165, 0),    # Top-Center-Left: Orange
    (0, 2): (255, 255, 0),    # Top-Center-Right: Yellow
    (0, 3): (0, 255, 0),      # Top-Right: Green
    (1, 0): (255, 0, 255),    # Left: Magenta
    (1, 3): (0, 255, 255),    # Right: Cyan
    (2, 0): (128, 0, 255),    # Bottom-Left: Purple
    (2, 1): (0, 128, 255),    # Bottom-Center-Left: Sky Blue
    (2, 2): (0, 255, 128),    # Bottom-Center-Right: Spring Green
    (2, 3): (255, 255, 255),  # Bottom-Right: White
}

# Fixed colors for each LED position (LED 1-10)
# These stay with the LED regardless of which region it's mapped to
LED_COLORS: List[Tuple[int, int, int]] = [
    (255, 0, 0),      # LED 1: Red
    (255, 165, 0),    # LED 2: Orange
    (255, 255, 0),    # LED 3: Yellow
    (0, 255, 0),      # LED 4: Green
    (255, 0, 255),    # LED 5: Magenta
    (0, 255, 255),    # LED 6: Cyan
    (128, 0, 255),    # LED 7: Purple
    (0, 128, 255),    # LED 8: Sky Blue
    (0, 255, 128),    # LED 9: Spring Green
    (255, 255, 255),  # LED 10: White
]


class ScreenRegionPreview(QFrame):
    """Visual preview of the monitor with draggable regions for LED mapping."""

    region_drag_swap = pyqtSignal(tuple, tuple)  # source_region, target_region

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(280, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFrameShape(QFrame.Shape.Box)
        self.setStyleSheet("background-color: #1a1a2e; border: 2px solid #4a4a6a; border-radius: 4px;")

        self._led_mapping: List[Tuple[int, int]] = list(DEFAULT_LED_MAPPING)
        self._hover_region: Optional[Tuple[int, int]] = None
        # Region colors: dict mapping (row, col) -> QColor
        self._region_colors: Dict[Tuple[int, int], QColor] = {}
        self._show_colors = False
        
        # Drag state
        self._drag_source: Optional[Tuple[int, int]] = None
        self._drag_active = False
        self._drag_pos: Optional[QPoint] = None  # Current mouse position during drag

        self.setMouseTracking(True)
        self.setAcceptDrops(True)

    def set_mapping(self, mapping: List[Tuple[int, int]]) -> None:
        self._led_mapping = list(mapping)
        self.update()

    def get_mapping(self) -> List[Tuple[int, int]]:
        return list(self._led_mapping)

    def set_region_colors(self, colors: Dict[Tuple[int, int], Tuple[int, int, int]]) -> None:
        """Set colors for each region."""
        self._region_colors = {k: QColor(r, g, b) for k, (r, g, b) in colors.items()}
        self._show_colors = bool(colors)
        self.update()

    def clear_colors(self) -> None:
        """Clear region colors."""
        self._region_colors = {}
        self._show_colors = False
        self.update()
    
    def get_led_at_region(self, region: Tuple[int, int]) -> Optional[int]:
        """Get the LED index assigned to a region, or None."""
        for i, r in enumerate(self._led_mapping):
            if r == region:
                return i
        return None

    def _get_region_rect(self, row: int, col: int) -> QRect:
        """Calculate the rectangle for a screen region."""
        margin = 10
        w = self.width() - 2 * margin
        h = self.height() - 2 * margin

        # 3 rows, 4 cols
        cell_w = w // 4
        cell_h = h // 3

        x = margin + col * cell_w
        y = margin + row * cell_h

        return QRect(x, y, cell_w, cell_h)

    def _region_at_pos(self, pos: QPoint) -> Optional[Tuple[int, int]]:
        """Get the region (row, col) at a given position."""
        for row in range(3):
            for col in range(4):
                # Skip middle-center regions (no LEDs there)
                if row == 1 and col in (1, 2):
                    continue
                rect = self._get_region_rect(row, col)
                if rect.contains(pos):
                    return (row, col)
        return None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            region = self._region_at_pos(event.pos())
            if region is not None and self.get_led_at_region(region) is not None:
                # Start drag from this region
                self._drag_source = region
                self._drag_active = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        region = self._region_at_pos(event.pos())
        if region != self._hover_region:
            self._hover_region = region
            self.update()
        
        # Update cursor and drag position based on drag state
        if self._drag_active:
            self._drag_pos = event.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update()  # Redraw to show drag indicator
        elif region is not None and self.get_led_at_region(region) is not None:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_active:
            target_region = self._region_at_pos(event.pos())
            if target_region is not None and target_region != self._drag_source:
                # Emit swap signal
                self.region_drag_swap.emit(self._drag_source, target_region)
            self._drag_source = None
            self._drag_active = False
            self._drag_pos = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_region = None
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

        # Draw all regions
        for row in range(3):
            for col in range(4):
                # Skip middle-center regions
                if row == 1 and col in (1, 2):
                    continue

                rect = self._get_region_rect(row, col)
                region = (row, col)

                # Get LED at this region (only 1 per region)
                led_index = self.get_led_at_region(region)
                is_drag_source = self._drag_active and region == self._drag_source
                is_drag_target = self._drag_active and region == self._hover_region and region != self._drag_source

                # Use test color if available
                if self._show_colors and region in self._region_colors:
                    fill_color = self._region_colors[region]
                    # Highlight drag states
                    if is_drag_source:
                        fill_color = fill_color.darker(150)
                        border_color = QColor(255, 200, 100)
                    elif is_drag_target:
                        fill_color = fill_color.lighter(130)
                        border_color = QColor(100, 255, 100)
                    elif self._hover_region == region:
                        fill_color = fill_color.lighter(120)
                        border_color = QColor(200, 200, 200)
                    else:
                        border_color = fill_color.darker(150)
                else:
                    # Non-test mode colors
                    if is_drag_source:
                        fill_color = QColor(100, 80, 40, 200)
                        border_color = QColor(255, 200, 100)
                    elif is_drag_target:
                        fill_color = QColor(60, 120, 60, 200)
                        border_color = QColor(100, 255, 100)
                    elif self._hover_region == region:
                        fill_color = QColor(80, 80, 120, 180)
                        border_color = QColor(150, 150, 200)
                    elif led_index is not None:
                        fill_color = QColor(60, 100, 60, 150)
                        border_color = QColor(100, 180, 100)
                    else:
                        fill_color = QColor(40, 40, 60, 100)
                        border_color = QColor(80, 80, 100)

                painter.setPen(QPen(border_color, 3 if is_drag_source or is_drag_target else 2))
                painter.setBrush(QBrush(fill_color))
                painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 4, 4)

                # Draw LED number in this region
                if led_index is not None:
                    brightness = (fill_color.red() * 299 + fill_color.green() * 587 + fill_color.blue() * 114) / 1000
                    text_color = QColor(0, 0, 0) if brightness > 128 else QColor(255, 255, 255)
                    painter.setPen(text_color)
                    font = QFont()
                    font.setPointSize(10)
                    font.setBold(True)
                    painter.setFont(font)
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(led_index + 1))

        # Draw center "Monitor" label
        center_rect = QRect(
            self._get_region_rect(1, 1).left(),
            self._get_region_rect(1, 1).top(),
            self._get_region_rect(1, 2).right() - self._get_region_rect(1, 1).left(),
            self._get_region_rect(1, 1).height()
        )
        painter.setPen(QColor(100, 100, 140))
        font = QFont()
        font.setPointSize(11)
        painter.setFont(font)
        painter.drawText(center_rect, Qt.AlignmentFlag.AlignCenter, "Screen Content")
        
        # Draw drag indicator (floating box following mouse)
        if self._drag_active and self._drag_pos is not None and self._drag_source is not None:
            indicator_size = 40
            indicator_rect = QRect(
                self._drag_pos.x() - indicator_size // 2,
                self._drag_pos.y() - indicator_size // 2,
                indicator_size,
                indicator_size
            )
            
            # Get the color of the dragged region
            if self._show_colors and self._drag_source in self._region_colors:
                drag_color = self._region_colors[self._drag_source]
            else:
                drag_color = QColor(100, 180, 100)
            
            # Draw semi-transparent indicator with border
            painter.setPen(QPen(QColor(255, 255, 255), 2))
            painter.setBrush(QBrush(QColor(drag_color.red(), drag_color.green(), drag_color.blue(), 200)))
            painter.drawRoundedRect(indicator_rect, 6, 6)
            
            # Draw LED number in indicator
            led_index = self.get_led_at_region(self._drag_source)
            if led_index is not None:
                brightness = (drag_color.red() * 299 + drag_color.green() * 587 + drag_color.blue() * 114) / 1000
                text_color = QColor(0, 0, 0) if brightness > 128 else QColor(255, 255, 255)
                painter.setPen(text_color)
                font = QFont()
                font.setPointSize(12)
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(indicator_rect, Qt.AlignmentFlag.AlignCenter, str(led_index + 1))


class LedMappingWidget(QWidget):
    """Widget for mapping LED positions to screen regions via drag-and-drop."""

    mapping_changed = pyqtSignal(list)  # Emits the new mapping

    def __init__(self, settings: QSettings, sync_controller: Optional["SyncController"] = None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.sync_controller = sync_controller
        self._test_mode_active = False
        self._test_timer: Optional[QTimer] = None
        self._razer_mode_enabled = False  # Track razer mode to avoid white flash

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Instructions
        instructions = QLabel(
            "Drag regions to swap LED positions. Colors show on your LED strip."
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(instructions)

        # Screen region preview (main interaction area)
        self.screen_preview = ScreenRegionPreview()
        layout.addWidget(self.screen_preview)

        # Current status info
        self.selection_label = QLabel("Drag a region to swap LED positions")
        self.selection_label.setStyleSheet("color: #888; font-style: italic;")
        layout.addWidget(self.selection_label)

        # Buttons
        button_layout = QHBoxLayout()
        
        self.test_button = QPushButton("■ Stop Test")
        self.test_button.setToolTip("Toggle test colors on LED strip")
        self.test_button.clicked.connect(self._toggle_test_mode)
        self.test_button.setCheckable(True)
        self.test_button.setChecked(True)  # Start checked since we auto-start
        button_layout.addWidget(self.test_button)
        
        self.reset_button = QPushButton("Reset to Default")
        self.reset_button.clicked.connect(self._reset_to_default)
        button_layout.addWidget(self.reset_button)

        button_layout.addStretch()
        layout.addLayout(button_layout)

        # Connect signals - drag swap for region reordering
        self.screen_preview.region_drag_swap.connect(self._on_region_swap)

        # Load saved mapping
        self._load_mapping()
    
    def start_test_mode_if_not_active(self) -> None:
        """Start test mode if not already active. Called when widget becomes visible."""
        if not self._test_mode_active:
            self._start_test_mode()

    def stop_test_mode_if_active(self) -> None:
        """Stop test mode if active. Called when widget is hidden."""
        if self._test_mode_active:
            self._stop_test_mode()

    def _load_mapping(self) -> None:
        """Load mapping from settings."""
        try:
            saved = self.settings.value("sync/led_mapping", None)
            if saved:
                mapping = json.loads(saved)
                # Convert lists back to tuples
                mapping = [tuple(m) for m in mapping]
                if len(mapping) == 10:
                    self.screen_preview.set_mapping(mapping)
                    return
        except Exception:
            pass
        # Use default
        self.screen_preview.set_mapping(DEFAULT_LED_MAPPING)

    def _save_mapping(self) -> None:
        """Save mapping to settings."""
        mapping = self.screen_preview.get_mapping()
        # Convert tuples to lists for JSON
        self.settings.setValue("sync/led_mapping", json.dumps(mapping))
        self.mapping_changed.emit(mapping)

    def get_mapping(self) -> List[Tuple[int, int]]:
        """Get the current LED mapping."""
        return self.screen_preview.get_mapping()

    def _on_region_swap(self, source_region: Tuple[int, int], target_region: Tuple[int, int]) -> None:
        """Handle swapping LEDs between two regions."""
        mapping = self.screen_preview.get_mapping()
        
        # Find which LEDs are at source and target
        source_led = None
        target_led = None
        for i, region in enumerate(mapping):
            if region == source_region:
                source_led = i
            elif region == target_region:
                target_led = i
        
        if source_led is None:
            return
        
        # Swap the regions
        if target_led is not None:
            # Swap both LEDs
            mapping[source_led], mapping[target_led] = mapping[target_led], mapping[source_led]
            src_name = REGION_NAMES.get(source_region, "?")
            tgt_name = REGION_NAMES.get(target_region, "?")
            self.selection_label.setText(f"Swapped LED {source_led + 1} ↔ LED {target_led + 1}")
        else:
            # Move source LED to empty target region
            mapping[source_led] = target_region
            tgt_name = REGION_NAMES.get(target_region, "?")
            self.selection_label.setText(f"Moved LED {source_led + 1} → {tgt_name}")
        
        self.screen_preview.set_mapping(mapping)
        self._save_mapping()
        
        # Update displays
        if self._test_mode_active:
            self._update_test_display()

    def _toggle_test_mode(self) -> None:
        """Toggle test mode."""
        if self._test_mode_active:
            self._stop_test_mode()
        else:
            self._start_test_mode()

    def _start_test_mode(self) -> None:
        """Start test mode - show distinct colors for each region."""
        self._test_mode_active = True
        self.test_button.setText("■ Stop Test")
        self.test_button.setChecked(True)
        self.selection_label.setText("Test mode - drag regions to swap LEDs")
        
        # Enable razer mode once at start
        self._enable_razer_mode()
        
        # Update the display
        self._update_test_display()
        
        # Create timer to keep updating (refreshes both UI and device)
        self._test_timer = QTimer(self)
        self._test_timer.timeout.connect(self._refresh_test_colors)
        self._test_timer.start(1000)  # Refresh every 1 second (slower to avoid flashing)

    def _stop_test_mode(self) -> None:
        """Stop test mode."""
        self._test_mode_active = False
        self._razer_mode_enabled = False
        self.test_button.setText("🎨 Test Colors")
        self.test_button.setChecked(False)
        self.selection_label.setText("Drag regions to swap LED positions")
        
        if self._test_timer:
            self._test_timer.stop()
            self._test_timer = None
        
        # Clear colors from display
        self.screen_preview.clear_colors()
        
        # Turn off LEDs (send black)
        self._send_colors_to_strip([(0, 0, 0)] * 10)

    def _enable_razer_mode(self) -> None:
        """Enable razer mode on the device (once)."""
        if self._razer_mode_enabled:
            return
        try:
            from ... import connection
            
            if not self.sync_controller:
                return
            
            self.sync_controller._ensure_server()
            device = self.sync_controller.get_selected_device()
            if not device:
                self.sync_controller._init_device()
                device = self.sync_controller.get_selected_device()
            
            server = self.sync_controller.server
            if device and server:
                connection.switch_razer(server, device, True)
                self._razer_mode_enabled = True
        except Exception:
            pass

    def _refresh_test_colors(self) -> None:
        """Refresh test colors to device (without re-enabling razer mode)."""
        if not self._test_mode_active:
            return
        # Each LED shows the color of the region it's mapped to
        mapping = self.screen_preview.get_mapping()
        led_colors = [TEST_COLORS.get(region, (0, 0, 0)) for region in mapping]
        self._send_colors_to_strip(led_colors, skip_razer_enable=True)

    def _update_test_display(self) -> None:
        """Update the test mode display and send colors to LED strip."""
        mapping = self.screen_preview.get_mapping()
        
        # Each region has a fixed test color (from TEST_COLORS)
        # Each LED shows the color of the region it's mapped to
        # This way when you swap, the physical LED changes to its new region's color
        led_colors = [TEST_COLORS.get(region, (0, 0, 0)) for region in mapping]
        
        # For screen preview, show each region with its fixed test color
        region_colors = dict(TEST_COLORS)
        
        # Show test colors in the screen preview
        self.screen_preview.set_region_colors(region_colors)
        
        # Send region-based colors to physical LED strip
        self._send_colors_to_strip(led_colors, skip_razer_enable=False)

    def _send_colors_to_strip(self, led_colors: List[Tuple[int, int, int]], skip_razer_enable: bool = False) -> None:
        """Send colors to the physical LED strip."""
        try:
            from ...utils import convert_colors
            from ... import connection
            
            if not self.sync_controller:
                return
                
            # Ensure server is initialized
            self.sync_controller._ensure_server()
            
            # Make sure we have a device
            device = self.sync_controller.get_selected_device()
            if not device:
                # Try to re-initialize device
                self.sync_controller._init_device()
                device = self.sync_controller.get_selected_device()
            
            server = self.sync_controller.server
            if device and server:
                # Only enable razer mode if not already enabled (prevents white flash)
                if not skip_razer_enable and not self._razer_mode_enabled:
                    connection.switch_razer(server, device, True)
                    self._razer_mode_enabled = True
                connection.send_razer_data(server, device, convert_colors(led_colors))
        except Exception as e:
            if self._test_mode_active:
                self.selection_label.setText(f"Connection error: {str(e)[:30]}")

    def _get_color_name(self, region: Tuple[int, int]) -> str:
        """Get the color name for a region."""
        color_names = {
            (0, 0): "Red",
            (0, 1): "Orange",
            (0, 2): "Yellow",
            (0, 3): "Green",
            (1, 0): "Magenta",
            (1, 3): "Cyan",
            (2, 0): "Purple",
            (2, 1): "Sky Blue",
            (2, 2): "Spring Green",
            (2, 3): "White",
        }
        return color_names.get(region, "Unknown")

    def _reset_to_default(self) -> None:
        """Reset mapping to default."""
        self.screen_preview.set_mapping(DEFAULT_LED_MAPPING)
        self.selection_label.setText("Mapping reset to default")
        self._save_mapping()
        
        # Update the display
        if self._test_mode_active:
            self._update_test_display()


__all__ = ["LedMappingWidget", "DEFAULT_LED_MAPPING", "REGION_NAMES", "TEST_COLORS"]
