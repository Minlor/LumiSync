"""ActiveSyncRow — one row in the "Active syncs" panel of the Sync Modes view."""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..theme import qcolor
from ..utils.animations import PulseDot


class ActiveSyncRow(QFrame):
    """One running sync — shows mode, devices, pulse dot, stop button."""

    stop_requested = pyqtSignal(str)  # mode

    def __init__(self, mode: str, device_names: List[str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._mode = mode

        self.setObjectName("ActiveSyncRow")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        self._dot = PulseDot(qcolor("success"), self, size=10)
        self._dot.set_active(True)
        layout.addWidget(self._dot)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)

        title = QLabel(mode.capitalize() + " Sync")
        f = title.font()
        f.setBold(True)
        title.setFont(f)
        text_box.addWidget(title)

        sub = QLabel(self._format_devices(device_names))
        sub.setStyleSheet(f"color: {qcolor('text_dim').name()}; font-size: 9pt;")
        text_box.addWidget(sub)

        layout.addLayout(text_box, 1)

        self.stop_button = QPushButton("Stop")
        self.stop_button.setProperty("role", "danger")
        self.stop_button.setObjectName("DangerButton")
        self.stop_button.clicked.connect(lambda: self.stop_requested.emit(self._mode))
        layout.addWidget(self.stop_button)

    def _format_devices(self, names: List[str]) -> str:
        if not names:
            return "No devices"
        if len(names) <= 2:
            return ", ".join(names)
        return f"{names[0]}, {names[1]} +{len(names) - 2} more"


__all__ = ["ActiveSyncRow"]
