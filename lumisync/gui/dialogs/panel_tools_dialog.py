"""iDotMatrix controls confirmed in the supplied 2.1.6 Android app."""

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from ...drivers import pool
from ..controllers.background import start_task


class PanelToolsDialog(QDialog):
    def __init__(self, controller, index: int, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.device = dict(controller.devices[index])
        self._busy = False
        self._task = None
        self.setWindowTitle("iDotMatrix panel tools")
        self.setObjectName("PanelToolsDialog")
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)
        label = QLabel("Panel tools")
        label.setProperty("role", "title")
        label.setWordWrap(True)
        layout.addWidget(label)
        name = QLabel(str(self.device.get("name") or self.device.get("model") or "iDotMatrix panel"))
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setProperty("role", "pageDescription")
        name.setWordWrap(True)
        layout.addWidget(name)
        hint = QLabel("Stop active screen, music, or drawing output before selecting a panel mode.")
        hint.setProperty("role", "subtle")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Panel mode")
        layout.addWidget(self.tabs)
        forms = {}
        for title in ("Clock", "Display", "Countdown", "Scoreboard"):
            page = QWidget()
            form = QFormLayout(page)
            form.setContentsMargins(12, 16, 12, 16)
            form.setSpacing(10)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            self.tabs.addTab(page, title)
            forms[title] = form
        self.style = QSpinBox()
        self.style.setRange(0, 7)
        self.style.setAccessibleName("Clock style")
        forms["Clock"].addRow("Clock style", self.style)
        self.twenty_four = QCheckBox("24-hour clock")
        self.twenty_four.setChecked(True)
        forms["Clock"].addRow(self.twenty_four)
        self.rotate = QCheckBox("Rotate display 180°")
        forms["Display"].addRow(self.rotate)
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 255)
        self.minutes.setValue(5)
        self.minutes.setAccessibleName("Countdown minutes")
        forms["Countdown"].addRow("Minutes", self.minutes)
        self.seconds = QSpinBox()
        self.seconds.setRange(0, 59)
        self.seconds.setAccessibleName("Countdown seconds")
        forms["Countdown"].addRow("Seconds", self.seconds)
        self.left = QSpinBox()
        self.right = QSpinBox()
        self.left.setRange(0, 999)
        self.right.setRange(0, 999)
        self.left.setAccessibleName("Left score")
        self.right.setAccessibleName("Right score")
        forms["Scoreboard"].addRow("Left score", self.left)
        forms["Scoreboard"].addRow("Right score", self.right)
        self.buttons = []
        for tab, title, method, args in (
            ("Clock", "Show clock and sync time", "show_clock", lambda: (self.style.value(), self.twenty_four.isChecked())),
            ("Display", "Apply rotation", "set_rotation", lambda: (self.rotate.isChecked(),)),
            ("Countdown", "Start countdown", "show_countdown", lambda: (self.minutes.value(), self.seconds.value())),
            ("Scoreboard", "Show scoreboard", "show_scoreboard", lambda: (self.left.value(), self.right.value())),
        ):
            button = QPushButton(title)
            button.setObjectName("Primary")
            button.setAutoDefault(False)
            button.clicked.connect(lambda checked=False, m=method, a=args, t=title: self._send(m, a(), t))
            self.buttons.append(button)
            forms[tab].addRow(button)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.status)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.resize(520, 440)

    def _set_status(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _send(self, method: str, args: tuple, label: str) -> None:
        if self.controller.is_device_busy(self.device):
            self._set_status("Stop the current output before selecting a panel mode.")
            return
        if self._busy:
            return
        self._busy = True
        self._label = label
        for button in self.buttons:
            button.setEnabled(False)
        self._set_status("Sending to panel…")
        self._task = start_task(lambda: getattr(pool.acquire(self.device), method)(*args), self._completed)

    @Slot(object, object)
    def _completed(self, _value, error) -> None:
        self._busy = False
        self._task = None
        for button in self.buttons:
            button.setEnabled(True)
        self._set_status(error or "Panel command sent.")
        if not error:
            self.controller.mark_device_output(self.device, self._label)

    def reject(self) -> None:
        if self._busy:
            self._set_status("The panel command is finishing. Close this dialog when it completes.")
            return
        super().reject()
