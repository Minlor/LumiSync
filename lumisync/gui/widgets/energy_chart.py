"""Daily energy bars, drawn from reported account values only."""

from __future__ import annotations

import math

from PySide6.QtCore import QDate, QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from ..theme import qcolor


class DailyEnergyChart(QWidget):
    """A compact time series with the same exact readings available by keyboard."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._days = []
        self._selected = -1
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("Energy used each day")
        self.setAccessibleDescription("Daily energy readings in kilowatt-hours. Use Left and Right to inspect days.")

    def sizeHint(self):  # noqa: N802 - Qt API
        return QSize(280, 160)

    def set_days(self, days):
        self._days = [dict(day) for day in days]
        self._selected = -1
        self.setAccessibleDescription("Use Left and Right to inspect days. " + "; ".join(self.day_description(i) for i in range(len(self._days))))
        self.update()

    @staticmethod
    def _value(day):
        value = day.get("energy_kwh")
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0 else None

    def day_description(self, index):
        if not 0 <= index < len(self._days):
            return ""
        day = self._days[index]
        date = QDate.fromString(day.get("date", ""), "yyyy-MM-dd")
        label = date.toString("d MMMM yyyy") if date.isValid() else day.get("date", "")
        value = self._value(day)
        return f"{label}: {value:,.3f} kWh" if value is not None else f"{label}: Not reported"

    def _plot(self):
        return QRectF(8, 30, max(1, self.width() - 16), max(1, self.height() - 58))

    def _index_at(self, point):
        plot = self._plot()
        if not self._days or not plot.contains(point):
            return -1
        return min(len(self._days) - 1, int((point.x() - plot.left()) / plot.width() * len(self._days)))

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt API
        index = self._index_at(event.position())
        if index != self._selected:
            self._selected = index
            self.update()
        if index >= 0:
            QToolTip.showText(event.globalPosition().toPoint(), self.day_description(index), self)
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt API
        if not self.hasFocus():
            self._selected = -1
            self.update()
        QToolTip.hideText()
        super().leaveEvent(event)

    def keyPressEvent(self, event):  # noqa: N802 - Qt API
        if self._days and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home, Qt.Key.Key_End):
            if event.key() == Qt.Key.Key_Home:
                self._selected = 0
            elif event.key() == Qt.Key.Key_End:
                self._selected = len(self._days) - 1
            elif self._selected < 0:
                self._selected = 0 if event.key() == Qt.Key.Key_Right else len(self._days) - 1
            else:
                self._selected = max(0, min(len(self._days) - 1, self._selected + (1 if event.key() == Qt.Key.Key_Right else -1)))
            self.setAccessibleDescription(self.day_description(self._selected))
            QToolTip.showText(self.mapToGlobal(self.rect().center()), self.day_description(self._selected), self)
            self.update()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event):  # noqa: N802 - Qt API
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):  # noqa: N802 - Qt API
        super().focusOutEvent(event)
        self._selected = -1
        QToolTip.hideText()
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt API
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.hasFocus():
            painter.setPen(QPen(qcolor("accent_bright"), 1.5))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 6, 6)
        if not self._days:
            return
        plot = self._plot()
        values = [self._value(day) for day in self._days]
        maximum = max((value for value in values if value is not None), default=0)
        painter.setPen(qcolor("text_dim"))
        painter.drawText(QRectF(plot.left(), 3, plot.width(), 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         f"Daily use · up to {maximum:,.3f} kWh")
        painter.setPen(QPen(qcolor("text_disabled"), 1))
        painter.drawLine(plot.bottomLeft(), plot.bottomRight())
        step = plot.width() / len(values)
        for index, value in enumerate(values):
            x = plot.left() + step * index + step * 0.14
            width = max(2, step * 0.72)
            if value is None:
                # An outlined gap marker differs from a reported zero.
                painter.setPen(QPen(qcolor("text_dim"), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(QRectF(x, plot.bottom() - 4, width, 4))
                continue
            height = max(2, value / maximum * (plot.height() - 6)) if maximum > 0 else 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor("accent_bright" if index == self._selected else "accent"))
            painter.drawRoundedRect(QRectF(x, plot.bottom() - height, width, height), min(2, width / 3), 2)
        painter.setPen(qcolor("text_dim"))
        for index, alignment in ((0, Qt.AlignmentFlag.AlignLeft), (len(self._days) - 1, Qt.AlignmentFlag.AlignRight)):
            date = QDate.fromString(self._days[index].get("date", ""), "yyyy-MM-dd")
            painter.drawText(QRectF(plot.left(), plot.bottom() + 5, plot.width(), 22), alignment | Qt.AlignmentFlag.AlignVCenter,
                             date.toString("d MMM"))
