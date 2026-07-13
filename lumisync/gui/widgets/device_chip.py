"""Visible target controls used by the Sync Modes view.

Every device and saved group is shown directly.  The controls behave like
small power buttons: enabled targets participate in the mode, while a group
button enables or disables all of its current members.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..resources.icons import IconKey, tinted_icon
from ..theme.tokens import TOKENS
from ..utils.flow_layout import FlowLayout


def device_id(device: Dict[str, Any]) -> str:
    """Return the stable identifier used by sync target selections."""
    return str(device.get("mac") or device.get("ip") or device.get("model") or "?")


def group_member_ids(group: Dict[str, Any], valid_ids: Iterable[str]) -> List[str]:
    """Return a group's valid device ids, preserving its stored order."""
    valid = set(valid_ids)
    return [str(member) for member in group.get("devices", []) if str(member) in valid]


class DeviceChipStrip(QFrame):
    """Direct device and group enable controls for one sync mode.

    ``selection_changed`` always emits resolved device ids. Group selection is
    therefore a convenient UI operation rather than a second persistence model.
    """

    selection_changed = Signal(list)

    def __init__(self, label: str = "Targets", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("DeviceChipStrip")

        self._devices: List[Dict[str, Any]] = []
        self._groups: List[Dict[str, Any]] = []
        self._selected_ids: Set[str] = set()
        self._target_buttons: List[QPushButton] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(7)

        heading = QHBoxLayout()
        heading.setSpacing(8)
        self._label = QLabel(label)
        self._label.setProperty("role", "sectionLabel")
        heading.addWidget(self._label)
        heading.addStretch(1)
        self._summary = QLabel()
        self._summary.setProperty("role", "subtle")
        heading.addWidget(self._summary)
        root.addLayout(heading)

        self._device_host = QWidget()
        self._device_layout = FlowLayout(
            self._device_host,
            h_spacing=6,
            v_spacing=6,
        )
        root.addWidget(self._device_host)

        self._group_heading = QLabel("Saved groups")
        self._group_heading.setProperty("role", "subtle")
        root.addWidget(self._group_heading)

        self._group_host = QWidget()
        self._group_layout = FlowLayout(
            self._group_host,
            h_spacing=6,
            v_spacing=6,
        )
        root.addWidget(self._group_host)

        self._empty_groups = QLabel(
            "No groups yet — create one from these targets or on Devices."
        )
        self._empty_groups.setProperty("role", "hint")
        self._empty_groups.setWordWrap(True)
        root.addWidget(self._empty_groups)

    # ------------------------------------------------------------------ public

    def set_devices(
        self,
        devices: List[Dict[str, Any]],
        default_ids: Optional[List[str]] = None,
    ) -> None:
        self._devices = list(devices)
        valid_ids = {device_id(device) for device in self._devices}
        self._selected_ids.intersection_update(valid_ids)
        if not self._selected_ids and self._devices:
            defaults = [sid for sid in (default_ids or []) if sid in valid_ids]
            self._selected_ids = set(defaults or [device_id(self._devices[0])])
        self._refresh_targets()

    def set_groups(self, groups: List[Dict[str, Any]]) -> None:
        self._groups = [dict(group) for group in groups if group.get("name")]
        self._refresh_targets()

    def set_selected_ids(self, ids: List[str]) -> None:
        valid_ids = {device_id(device) for device in self._devices}
        self._selected_ids = {sid for sid in ids if sid in valid_ids}
        self._refresh_targets()

    def selected_ids(self) -> List[str]:
        return [
            sid
            for sid in (device_id(device) for device in self._devices)
            if sid in self._selected_ids
        ]

    def selected_devices(self) -> List[Dict[str, Any]]:
        return [
            device
            for device in self._devices
            if device_id(device) in self._selected_ids
        ]

    # ------------------------------------------------------------------ render

    @staticmethod
    def _clear_layout(layout: FlowLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def _refresh_targets(self) -> None:
        self._clear_layout(self._device_layout)
        self._clear_layout(self._group_layout)
        self._target_buttons = []

        valid_ids = {device_id(device) for device in self._devices}
        for device in self._devices:
            sid = device_id(device)
            model = str(device.get("model") or "Device")
            location = str(device.get("ip") or device.get("mac") or "")
            button = self._make_target_button(
                model,
                sid in self._selected_ids,
                "device",
                f"Include or exclude {model} from this sync mode"
                + (f"\n{location}" if location else ""),
            )
            button.clicked.connect(
                lambda checked=False, target_id=sid: self._toggle_device(target_id)
            )
            self._device_layout.addWidget(button)

        if not self._devices:
            empty = QLabel("No devices available")
            empty.setProperty("role", "warn")
            self._device_layout.addWidget(empty)

        group_count = 0
        for group in self._groups:
            members = group_member_ids(group, valid_ids)
            if not members:
                continue
            group_count += 1
            enabled = all(member in self._selected_ids for member in members)
            name = str(group.get("name") or "Group")
            count_text = "device" if len(members) == 1 else "devices"
            button = self._make_target_button(
                f"{name} · {len(members)}",
                enabled,
                "group",
                f"Enable or disable all {len(members)} {count_text} in {name}",
            )
            button.clicked.connect(
                lambda checked=False, member_ids=tuple(members): self._toggle_group(member_ids)
            )
            self._group_layout.addWidget(button)

        self._group_heading.setVisible(bool(group_count))
        self._group_host.setVisible(bool(group_count))
        self._empty_groups.setVisible(not group_count)

        selected = len(self._selected_ids)
        total = len(self._devices)
        self._summary.setText(f"{selected} of {total} on" if total else "None available")
        self._summary.setProperty("state", "active" if selected else "inactive")
        self._repolish(self._summary)

    def _make_target_button(
        self,
        text: str,
        enabled: bool,
        kind: str,
        tooltip: str,
    ) -> QPushButton:
        button = QPushButton(text)
        button.setCheckable(True)
        button.setChecked(enabled)
        button.setProperty("chip", True)
        button.setProperty("active", enabled)
        button.setProperty("targetKind", kind)
        button.setIcon(
            tinted_icon(
                IconKey.POWER,
                TOKENS["accent_bright"] if enabled else TOKENS["text_disabled"],
                14,
            )
        )
        button.setIconSize(QSize(14, 14))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tooltip)
        state = "enabled" if enabled else "disabled"
        button.setAccessibleName(f"{text}, {state} for this sync mode")
        self._target_buttons.append(button)
        return button

    # ------------------------------------------------------------------ actions

    def _toggle_device(self, target_id: str) -> None:
        if target_id in self._selected_ids:
            self._selected_ids.remove(target_id)
        else:
            self._selected_ids.add(target_id)
        self._commit_selection()

    def _toggle_group(self, member_ids: Iterable[str]) -> None:
        members = set(member_ids)
        if members and members.issubset(self._selected_ids):
            self._selected_ids.difference_update(members)
        else:
            self._selected_ids.update(members)
        self._commit_selection()

    def _commit_selection(self) -> None:
        self._refresh_targets()
        self.selection_changed.emit(self.selected_ids())


__all__ = ["DeviceChipStrip", "device_id", "group_member_ids"]
