"""A simple pixel-grid canvas for drawing images to a matrix device."""

from __future__ import annotations

from typing import List, Tuple

from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QColor, QPainter, QMouseEvent
from PySide6.QtWidgets import QWidget

RGB = Tuple[int, int, int]


def line_cells(start: Tuple[int, int], end: Tuple[int, int]) -> List[Tuple[int, int]]:
    """Return every grid cell crossed by a stroke using Bresenham's algorithm."""
    x0, y0 = start
    x1, y1 = end
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    error = dx + dy
    cells: List[Tuple[int, int]] = []

    while True:
        cells.append((x0, y0))
        if x0 == x1 and y0 == y1:
            return cells
        doubled = 2 * error
        if doubled >= dy:
            error += dy
            x0 += sx
        if doubled <= dx:
            error += dx
            y0 += sy


class PixelCanvas(QWidget):
    """Editable grid of pixels. Click or drag to paint with the current color."""

    changed = Signal()

    def __init__(self, cols: int = 32, rows: int = 32, parent=None):
        super().__init__(parent)
        self._cols = cols
        self._rows = rows
        self._grid: List[List[RGB]] = [[(0, 0, 0)] * cols for _ in range(rows)]
        self._color: RGB = (255, 0, 0)
        self._last_cell: Tuple[int, int] | None = None
        self.setMinimumSize(256, 256)

    # --- state ---
    def set_matrix_size(self, cols: int, rows: int) -> None:
        self._cols, self._rows = max(1, cols), max(1, rows)
        self.clear()

    def set_color(self, rgb: RGB) -> None:
        self._color = (int(rgb[0]), int(rgb[1]), int(rgb[2]))

    def clear(self) -> None:
        self._grid = [[(0, 0, 0)] * self._cols for _ in range(self._rows)]
        self.update()
        self.changed.emit()

    def fill(self, rgb: RGB) -> None:
        color = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
        self._grid = [[color] * self._cols for _ in range(self._rows)]
        self.update()
        self.changed.emit()

    def get_grid(self) -> List[List[RGB]]:
        return [list(row) for row in self._grid]

    def is_empty(self) -> bool:
        return all(pixel == (0, 0, 0) for row in self._grid for pixel in row)

    def load_grid(self, grid: List[List[RGB]]) -> None:
        if grid and len(grid) == self._rows and len(grid[0]) == self._cols:
            self._grid = [list(row) for row in grid]
            self.update()

    # --- geometry ---
    def _cell_size(self) -> int:
        return max(1, min(self.width() // self._cols, self.height() // self._rows))

    def _grid_origin(self) -> Tuple[int, int]:
        size = self._cell_size()
        grid_width = size * self._cols
        grid_height = size * self._rows
        return (
            max(0, (self.width() - grid_width) // 2),
            max(0, (self.height() - grid_height) // 2),
        )

    def _cell_at(self, pos) -> Tuple[int, int] | None:
        size = self._cell_size()
        origin_x, origin_y = self._grid_origin()
        x = (pos.x() - origin_x) // size
        y = (pos.y() - origin_y) // size
        if 0 <= x < self._cols and 0 <= y < self._rows:
            return int(x), int(y)
        return None

    def _paint_cell(self, cell: Tuple[int, int]) -> bool:
        x, y = cell
        if self._grid[y][x] != self._color:
            self._grid[y][x] = self._color
            return True
        return False

    def _paint_stroke(
        self,
        start: Tuple[int, int],
        end: Tuple[int, int],
    ) -> None:
        changed = False
        for cell in line_cells(start, end):
            changed = self._paint_cell(cell) or changed
        if changed:
            self.update()
            self.changed.emit()

    # --- events ---
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            cell = self._cell_at(event.position().toPoint())
            if cell is not None:
                self._last_cell = cell
                self._paint_stroke(cell, cell)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            cell = self._cell_at(event.position().toPoint())
            if cell is not None:
                start = self._last_cell or cell
                self._paint_stroke(start, cell)
                self._last_cell = cell
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            cell = self._cell_at(event.position().toPoint())
            if cell is not None and self._last_cell is not None:
                self._paint_stroke(self._last_cell, cell)
            self._last_cell = None
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        self._last_cell = None
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        size = self._cell_size()
        origin_x, origin_y = self._grid_origin()
        grid_line = QColor(40, 40, 40)
        for y in range(self._rows):
            for x in range(self._cols):
                r, g, b = self._grid[y][x]
                rect = QRect(
                    origin_x + x * size,
                    origin_y + y * size,
                    size,
                    size,
                )
                painter.fillRect(rect, QColor(r, g, b))
                painter.setPen(grid_line)
                painter.drawRect(rect)
