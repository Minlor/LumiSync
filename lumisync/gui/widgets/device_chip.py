"""Device chip + chip strip — used by the Sync Modes view to pick which
devices participate in each sync mode.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from ..theme import qcolor


def device_id(device: Dict[str, Any]) -> str:
    """Stable identifier for a device — MAC if available, else IP."""
    return str(device.get("mac") or device.get("ip") or device.get("model") or "?")


class DeviceChipStrip(QFrame):
    """Horizontal strip of device chips for "devices included in this sync".

    Emits `selection_changed(list[str])` of selected device ids.
    """

    selection_changed = pyqtSignal(list)

    def __init__(self, label: str = "Devices", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("DeviceChipStrip")

        self._devices: List[Dict[str, Any]] = []
        self._selected_ids: Set[str] = set()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._label = QLabel(label + ":")
        self._label.setStyleSheet(f"color: {qcolor('text_dim').name()}; font-size: 9pt;")
        layout.addWidget(self._label)

        # Chip container — chips added dynamically
        self._chips_layout = QHBoxLayout()
        self._chips_layout.setSpacing(4)
        layout.addLayout(self._chips_layout)
        layout.addStretch(1)

        self._edit_button = QToolButton()
        self._edit_button.setText("Edit ▾")
        self._edit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._edit_button.setStyleSheet(
            "QToolButton { background: transparent; border: none; "
            f"color: {qcolor('accent_bright').name()}; padding: 2px 4px; font-size: 9pt; }}"
            "QToolButton:hover { color: white; }"
        )
        self._edit_button.clicked.connect(self._open_picker)
        layout.addWidget(self._edit_button)

    # ------------------------------------------------------------------ public

    def set_devices(self, devices: List[Dict[str, Any]], default_ids: Optional[List[str]] = None) -> None:
        self._devices = list(devices)
        # Keep selection only for ids that still exist; if empty, use caller
        # defaults so new mode selections start conservatively.
        valid_ids = {device_id(d) for d in self._devices}
        self._selected_ids = {sid for sid in self._selected_ids if sid in valid_ids}
        if not self._selected_ids and self._devices:
            defaults = [sid for sid in (default_ids or []) if sid in valid_ids]
            self._selected_ids = set(defaults or [device_id(self._devices[0])])
        self._refresh_chips()

    def set_selected_ids(self, ids: List[str]) -> None:
        valid_ids = {device_id(d) for d in self._devices}
        self._selected_ids = {sid for sid in ids if sid in valid_ids}
        self._refresh_chips()

    def selected_ids(self) -> List[str]:
        return [sid for sid in (device_id(d) for d in self._devices) if sid in self._selected_ids]

    def selected_devices(self) -> List[Dict[str, Any]]:
        return [d for d in self._devices if device_id(d) in self._selected_ids]

    # ------------------------------------------------------------------ render

    def _refresh_chips(self) -> None:
        # Clear chips
        while self._chips_layout.count():
            item = self._chips_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not self._devices:
            empty = QLabel("No devices")
            empty.setProperty("role", "subtle")
            empty.setStyleSheet(f"color: {qcolor('text_disabled').name()}; font-size: 9pt; font-style: italic;")
            self._chips_layout.addWidget(empty)
            return

        # Show up to 4 chips inline; overflow → "+N more"
        selected = self.selected_devices()
        visible = selected[:4]
        overflow = max(0, len(selected) - len(visible))

        for d in visible:
            chip = self._make_chip(d)
            self._chips_layout.addWidget(chip)

        if overflow:
            more = QLabel(f"+{overflow} more")
            more.setStyleSheet(f"color: {qcolor('text_dim').name()}; font-size: 9pt;")
            self._chips_layout.addWidget(more)

        if not selected:
            none = QLabel("(none — sync disabled)")
            none.setStyleSheet(f"color: {qcolor('warning').name()}; font-size: 9pt;")
            self._chips_layout.addWidget(none)

    def _make_chip(self, device: Dict[str, Any]) -> QPushButton:
        chip = QPushButton(device.get("model", "?"))
        chip.setProperty("chip", True)
        chip.setProperty("active", True)
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.setToolTip(device.get("ip", ""))
        chip.clicked.connect(self._open_picker)
        return chip

    def _open_picker(self) -> None:
        if not self._devices:
            return
        menu = QMenu(self)
        actions = []
        for d in self._devices:
            sid = device_id(d)
            label = f"{d.get('model', '?')}  ({d.get('ip', '?')})"
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(sid in self._selected_ids)
            actions.append((act, sid))

        menu.addSeparator()
        all_act = menu.addAction("Select all")
        none_act = menu.addAction("Select none")

        chosen = menu.exec(self._edit_button.mapToGlobal(self._edit_button.rect().bottomLeft()))
        if chosen is None:
            return

        if chosen is all_act:
            self._selected_ids = {device_id(d) for d in self._devices}
        elif chosen is none_act:
            self._selected_ids = set()
        else:
            for act, sid in actions:
                if act is chosen:
                    if act.isChecked():
                        self._selected_ids.add(sid)
                    else:
                        self._selected_ids.discard(sid)
                    break

        self._refresh_chips()
        self.selection_changed.emit(self.selected_ids())


__all__ = ["DeviceChipStrip", "device_id"]
