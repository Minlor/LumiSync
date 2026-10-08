"""Equal-width inventory columns that use the complete viewport."""

from PySide6.QtCore import QRect, Qt

from .flow_layout import FlowLayout


class DeviceGridLayout(FlowLayout):
    def expandingDirections(self):
        return Qt.Orientation.Horizontal

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        if not self._items:
            return margins.top() + margins.bottom()
        minimum = max(item.minimumSize().width() for item in self._items)
        gap = self.horizontalSpacing()
        columns = max(1, min(len(self._items), (area.width() + gap) // (minimum + gap)))
        # Keep the same number of rows while avoiding a mostly empty last row
        # (six devices become 3+3 rather than 5+1 in a wide window).
        rows = (len(self._items) + columns - 1) // columns
        columns = (len(self._items) + rows - 1) // rows
        width, remainder = divmod(max(0, area.width() - (columns - 1) * gap), columns)
        # Align power controls across rows, including mixed lamps and sockets.
        height = max(item.sizeHint().height() for item in self._items)
        # Use modest spare height to line up the final row with the inspector.
        # Cap the growth so a few switches do not become enormous blank tiles.
        if not test_only:
            fitted = (area.height() - (rows - 1) * self.verticalSpacing()) // rows
            height = max(height, min(196, fitted))
        x, y = area.x(), area.y()
        for index, item in enumerate(self._items):
            column = index % columns
            if column == 0 and index:
                x = area.x()
                y += height + self.verticalSpacing()
            cell_width = width + (1 if column < remainder else 0)
            if not test_only:
                item.setGeometry(QRect(x, y, cell_width, height))
            x += cell_width + gap
        return y + height - rect.y() + margins.bottom()
