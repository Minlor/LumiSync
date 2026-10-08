"""Electrical readings and on-demand monthly energy history for smart plugs."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QDate, QDateTime, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QBoxLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ...accounts.errors import AccountError
from ...accounts.manager import account_manager
from ...accounts.tuya_energy import energy_history_spec, meter_fields
from ..controllers.background import start_task
from .energy_chart import DailyEnergyChart
from .product_controls import ProductComboBox

READINGS = {
    "power_w": ("Power draw", "W", 1),
    "voltage_v": ("Voltage", "V", 1),
    "current_a": ("Current", "A", 3),
    "energy_kwh": ("Device energy counter", "kWh", 3),
}

READINGS_REFRESH_MS = 5_000


class DeviceEnergy(QWidget):
    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._device = {}
        self._fields = []
        self._task = None
        self._request_context = None
        self._cache = {}
        self._compact = False
        self._last_state = {}
        self._history_reported = False
        self._poll = QTimer(self)
        self._poll.setInterval(READINGS_REFRESH_MS)
        self._poll.timeout.connect(self._request_refresh)
        root = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(18)
        self.live_panel = QFrame()
        self.live_panel.setObjectName("EnergyReadings")
        layout = QVBoxLayout(self.live_panel)
        self._live_layout = layout
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)
        header = QHBoxLayout()
        title = QLabel("Electrical readings")
        title.setProperty("role", "sectionTitle")
        header.addWidget(title, 1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setProperty("role", "ghost")
        self.refresh_button.setMinimumHeight(44)
        self.refresh_button.setAccessibleName("Refresh device readings")
        self.refresh_button.setToolTip("Request the latest electrical readings")
        self.refresh_button.clicked.connect(self.refresh_requested)
        header.addWidget(self.refresh_button)
        layout.addLayout(header)
        self._rows, self.values = {}, {}
        details = QGridLayout()
        details.setHorizontalSpacing(18)
        details.setVerticalSpacing(12)
        details.setColumnStretch(0, 1)
        details.setColumnStretch(1, 1)
        for field, (label, _unit, _digits) in READINGS.items():
            row = QWidget()
            row_layout = QVBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(3)
            caption = QLabel("Current power draw" if field == "power_w" else label)
            caption.setProperty("role", "subtle")
            row_layout.addWidget(caption)
            value = QLabel("Not reported")
            value.setProperty("role", "meterPrimary" if field == "power_w" else "meterSecondary")
            value.setAccessibleName(label)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            row_layout.addWidget(value)
            self._rows[field], self.values[field] = row, value
            if field == "power_w":
                layout.addWidget(row)
            elif field == "energy_kwh":
                details.addWidget(row, 1, 0, 1, 2)
            else:
                details.addWidget(row, 0, 0 if field == "voltage_v" else 1)
        layout.addLayout(details)
        self.readings_note = QLabel("Readings have not been reported yet.")
        self.readings_note.setProperty("role", "subtle")
        self.readings_note.setWordWrap(True)
        layout.addWidget(self.readings_note)
        self.scope_note = QLabel("Readings and energy usage cover the whole device.")
        self.scope_note.setProperty("role", "subtle")
        self.scope_note.setWordWrap(True)
        layout.addWidget(self.scope_note)
        root.addWidget(self.live_panel)

        self.history_controls = QFrame()
        self.history_controls.setObjectName("EnergyHistory")
        history_layout = QVBoxLayout(self.history_controls)
        self._history_layout = history_layout
        history_layout.setContentsMargins(16, 16, 16, 16)
        history_layout.setSpacing(12)
        history_title = QLabel("Energy usage")
        history_title.setProperty("role", "sectionTitle")
        history_layout.addWidget(history_title)
        row = QHBoxLayout()
        self.month = ProductComboBox()
        self.month.setAccessibleName("Energy usage month")
        self.month.setMinimumHeight(44)
        now = QDate.currentDate()
        for offset in range(12):
            month = now.addMonths(-offset)
            self.month.addItem(month.toString("MMMM yyyy"), month.toString("yyyy-MM"))
        row.addWidget(self.month, 1)
        self.load_button = QPushButton("Load usage")
        self.load_button.setMinimumHeight(44)
        self.load_button.clicked.connect(self.load_history)
        row.addWidget(self.load_button)
        history_layout.addLayout(row)
        self.total = QLabel("Usage not loaded")
        self.total.setProperty("role", "subtle")
        self.total.setWordWrap(True)
        history_layout.addWidget(self.total)
        self.history_note = QLabel("Choose a month and load its daily energy totals from your account.")
        self.history_note.setProperty("role", "subtle")
        self.history_note.setWordWrap(True)
        self.history_note.setTextFormat(Qt.TextFormat.PlainText)
        history_layout.addWidget(self.history_note)
        self.chart = DailyEnergyChart()
        self.chart.hide()
        history_layout.addWidget(self.chart)
        self.breakdown_button = QPushButton("Show daily readings")
        self.breakdown_button.setProperty("role", "ghost")
        self.breakdown_button.setMinimumHeight(44)
        self.breakdown_button.setCheckable(True)
        self.breakdown_button.toggled.connect(self._toggle_breakdown)
        self.breakdown_button.hide()
        history_layout.addWidget(self.breakdown_button)
        self.daily = QTableWidget(0, 2)
        self.daily.setObjectName("EnergyDailyTable")
        self.daily.setAccessibleName("Daily energy usage")
        self.daily.setHorizontalHeaderLabels(["Day", "Energy (kWh)"])
        self.daily.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.daily.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.daily.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.daily.setShowGrid(False)
        self.daily.verticalHeader().hide()
        self.daily.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.daily.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.daily.setMaximumHeight(184)
        self.daily.hide()
        history_layout.addWidget(self.daily)
        root.addWidget(self.history_controls)
        self.month.currentIndexChanged.connect(self._month_changed)
        self.hide()

    def set_available_size(self, width: int, height: int) -> None:
        self._compact = height < 720
        side_by_side = width >= 600
        self.layout().setDirection(QBoxLayout.Direction.LeftToRight if side_by_side else QBoxLayout.Direction.TopToBottom)
        self.layout().setSpacing(10 if self._compact else 18)
        for layout in (self._live_layout, self._history_layout):
            layout.setContentsMargins(10 if self._compact else 16, 10 if self._compact else 16,
                                      10 if self._compact else 16, 10 if self._compact else 16)
            layout.setSpacing(8 if self._compact else 12)
        self._toggle_breakdown(self.breakdown_button.isChecked())
        self.set_state(self._last_state)

    def _context(self):
        return (self._device.get("account_id"), self._device.get("device_id"), self.month.currentData())

    def set_device(self, device, state):
        previous = self._context()[:2]
        self._device = dict(device)
        self._fields = meter_fields(device)
        history = bool(device.get("account_id") and energy_history_spec(device)
                       and device.get("vendor_account") in ("tuya", "lsc"))
        self.setVisible(bool(self._fields or history))
        self.live_panel.setVisible(bool(self._fields))
        for field, row in self._rows.items():
            row.setVisible(field in self._fields)
            self.values[field].setText("Not reported")
        self.refresh_button.setVisible(bool(self._fields))
        self.readings_note.setVisible(bool(self._fields))
        self.scope_note.setVisible(bool(device.get("tuya_channel")))
        self.history_controls.setVisible(history)
        changed = previous != self._context()[:2]
        if changed:
            self.month.setCurrentIndex(0)
            self._reset_history()
        self.load_button.setEnabled(self._task is None)
        self.set_state(state)
        self._update_polling()
        if changed and self.isVisible():
            QTimer.singleShot(0, self._request_refresh)

    def set_state(self, state):
        self._last_state = dict(state)
        for field in self._fields:
            value = state.get(field)
            _, unit, digits = READINGS[field]
            valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
            self.values[field].setText(f"{value:,.{digits}f} {unit}" if valid else "Not reported")
            self.values[field].setProperty("reported", valid)
            self.values[field].style().unpolish(self.values[field])
            self.values[field].style().polish(self.values[field])
        if state.get("stale") or state.get("online") is False:
            self.readings_note.setText("Last reported readings · Device unavailable")
            self.readings_note.show()
        else:
            has_readings = any(isinstance(state.get(field), (int, float)) and not isinstance(state[field], bool)
                               and math.isfinite(state[field]) and state[field] >= 0 for field in self._fields)
            checked = state.get("last_seen")
            timestamp = (QDateTime.fromSecsSinceEpoch(int(checked)).toString("HH:mm:ss")
                         if isinstance(checked, (int, float)) and math.isfinite(checked) and checked > 0 else "")
            note = (f"Checked {timestamp} · Refreshes every 5 s." if timestamp
                    else "Refreshes every 5 seconds while open.")
            self.readings_note.setText(note if has_readings else "Waiting for device readings. Checking every 5 seconds.")
            self.readings_note.show()
        self.live_panel.setToolTip(self.readings_note.text())

    def _update_polling(self):
        if self.isVisible() and self._fields:
            if not self._poll.isActive():
                self._poll.start()
        else:
            self._poll.stop()

    def _request_refresh(self):
        if self.isVisible() and self._fields:
            self.refresh_requested.emit()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_polling()
        QTimer.singleShot(0, self._request_refresh)

    def hideEvent(self, event):
        self._poll.stop()
        super().hideEvent(event)

    def _reset_history(self):
        self._history_reported = False
        self._set_total("Usage not loaded")
        self.history_note.setText("Choose a month and load its daily energy totals from your account.")
        self.chart.set_days([])
        self.chart.hide()
        self.breakdown_button.setChecked(False)
        self.breakdown_button.hide()
        self.daily.setRowCount(0)
        self.daily.hide()

    def _set_total(self, text, reported=False):
        self.total.setText(text)
        self.total.setProperty("role", "energyTotal" if reported else "subtle")
        self.total.style().unpolish(self.total)
        self.total.style().polish(self.total)

    def _toggle_breakdown(self, show):
        self.daily.setVisible(show and self.daily.rowCount() > 0)
        self.chart.setVisible(self._history_reported and (not self._compact or show))
        self.breakdown_button.setText("Hide daily readings" if show else "Show daily readings")

    def _month_changed(self, *_args):
        self._reset_history()
        cached = self._cache.get(self._context())
        if cached and time.monotonic() - cached[0] < 300:
            self._show_history(cached[1])

    def load_history(self):
        if self._task is not None or self.history_controls.isHidden():
            return
        device, month = dict(self._device), self.month.currentData()
        self._request_context = self._context()
        cached = self._cache.get(self._request_context)
        if cached and time.monotonic() - cached[0] < 300:
            self._show_history(cached[1])
            return
        self.load_button.setEnabled(False)
        self.load_button.setText("Loading…")
        self.history_note.setText("Loading usage for " + self.month.currentText() + "…")

        def read():
            client = account_manager.client_for(device)
            if not hasattr(client, "query_energy_history"):
                raise AccountError("Energy history is not available through this connection.", "unsupported")
            try:
                return client.query_energy_history(device, month)
            except AccountError as exc:
                if exc.code == "service":
                    raise AccountError("The account service is not providing energy history for this device. Live readings are still available.", "unsupported") from None
                raise

        self._task = start_task(read, self._history_finished)

    def _history_finished(self, value, error):
        context = self._request_context
        self._task = None
        self._request_context = None
        self.load_button.setEnabled(True)
        self.load_button.setText("Load usage")
        if value is not None:
            if len(self._cache) >= 48:
                self._cache.pop(next(iter(self._cache)))
            self._cache[context] = (time.monotonic(), value)
        if context != self._context():
            return
        if error:
            self._reset_history()
            self._set_total("Energy usage unavailable")
            self.history_note.setText(str(error) + " Use Load usage to try again.")
        elif value is not None:
            self._show_history(value)

    def _show_history(self, history):
        total = history.get("total_kwh")
        self._history_reported = total is not None
        reported, expected = history["reported_days"], history["expected_days"]
        self._set_total(f"{total:,.3f} kWh" if total is not None else "No reported usage for this month", total is not None)
        note = f"{reported} of {expected} days reported."
        if reported < expected:
            note += " Outlined days have no reading."
        if history.get("month") == QDate.currentDate().toString("yyyy-MM"):
            note += " Today's total may still change."
        self.history_note.setText(note if total is not None else "Your account has not reported energy totals for this month. Choose another month or try again later.")
        self.chart.set_days(history["days"])
        self.chart.setVisible(total is not None and (not self._compact or self.breakdown_button.isChecked()))
        self.breakdown_button.setVisible(total is not None)
        self.daily.setRowCount(len(history["days"]))
        for row, day in enumerate(history["days"]):
            date = QDate.fromString(day["date"], "yyyy-MM-dd")
            self.daily.setItem(row, 0, QTableWidgetItem(date.toString("d MMM") if date.isValid() else day["date"]))
            value = day["energy_kwh"]
            energy = QTableWidgetItem(f"{value:,.3f}" if value is not None else "Not reported")
            energy.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.daily.setItem(row, 1, energy)
        self.daily.setVisible(total is not None and self.breakdown_button.isChecked())
