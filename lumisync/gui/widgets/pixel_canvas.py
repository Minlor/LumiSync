"""A simple pixel-grid canvas for drawing images to a matrix device."""

from __future__ import annotations

from typing import List, Tuple

from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QColor, QPainter, QMouseEvent
from PySide6.QtWidgets import QWidget

RGB = Tuple[int, int, int]


class PixelCanvas(QWidget):
    """Editable grid of pixels. Click or drag to paint with the current color."""

    changed = Signal()

    def __init__(self, cols: int = 32, rows: int = 32, parent=None):
        super().__init__(parent)
        self._cols = cols
        self._rows = rows
        self._grid: List[List[RGB]] = [[(0, 0, 0)] * cols for _ in range(rows)]
        self._color: RGB = (255, 0, 0)
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

    def load_grid(self, grid: List[List[RGB]]) -> None:
        if grid and len(grid) == self._rows and len(grid[0]) == self._cols:
            self._grid = [list(row) for row in grid]
            self.update()

    # --- geometry ---
    def _cell_size(self) -> int:
        return max(1, min(self.width() // self._cols, self.height() // self._rows))

    def _cell_at(self, pos) -> Tuple[int, int] | None:
        size = self._cell_size()
        x = pos.x() // size
        y = pos.y() // size
        if 0 <= x < self._cols and 0 <= y < self._rows:
            return int(x), int(y)
        return None

    def _paint_at(self, pos) -> None:
        cell = self._cell_at(pos)
        if cell is None:
            return
        x, y = cell
        if self._grid[y][x] != self._color:
            self._grid[y][x] = self._color
            self.update()
            self.changed.emit()

    # --- events ---
    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._paint_at(event.position().toPoint())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton:
            self._paint_at(event.position().toPoint())

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        size = self._cell_size()
        grid_line = QColor(40, 40, 40)
        for y in range(self._rows):
            for x in range(self._cols):
                r, g, b = self._grid[y][x]
                rect = QRect(x * size, y * size, size, size)
                painter.fillRect(rect, QColor(r, g, b))
                painter.setPen(grid_line)
                painter.drawRect(rect)
