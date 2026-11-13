"""The "Lights" list used by the Monitor Sync and Music Sync pages.

One row per syncable light inside a single Fluent list card: the device
type icon, its name, a ``type · connection · N zones`` line, and a switch
that includes it in the mode. When groups are saved, a first "Group" row
offers a dropdown ("Custom selection", then each group) that applies the
group's members to the switches; flipping a switch by hand returns it to
"Custom selection" (unless the result happens to match a group exactly).

The class keeps its historical name and API (``selection_changed``,
``set_devices``, ``set_groups``, ``selected_devices`` …) so callers do not
change.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ... import connection
from ...drivers.registry import capabilities_for_device, is_cloud
from ...groups import device_key
from ..resources.icons import IconKey
from ..utils.device_identity import device_identity
from .fluent import FluentIcon
from .product_controls import ProductComboBox, ToggleSwitch
from .settings_card import SettingsCard, SettingsList

CUSTOM_SELECTION = "Custom selection"

_ICON_FILES = {
    IconKey.SMART_PLUG: "smart-plug.svg",
    IconKey.LIGHT_SWITCH: "light-switch.svg",
    IconKey.LED_STRIP: "led-strip.svg",
    IconKey.MATRIX_PANEL: "matrix-panel.svg",
    IconKey.LIGHT_BULB: "lightbulb-on.svg",
}


def device_id(device: Dict[str, Any]) -> str:
    """Return the stable identifier used by sync target selections."""
    return device_key(device) or "?"


def group_member_ids(group: Dict[str, Any], valid_ids: Iterable[str]) -> List[str]:
    """Return a group's valid device ids, preserving its stored order."""
    valid = set(valid_ids)
    return [str(member) for member in group.get("devices", []) if str(member) in valid]


def _connection_name(device: Dict[str, Any]) -> str:
    transport = str(device.get("transport") or "lan").lower()
    if transport == "ble":
        return "Bluetooth"
    if transport == "tuya":
        return "Local Wi-Fi"
    if is_cloud(device):
        return "Account"
    return "Local network"


def light_summary(device: Dict[str, Any]) -> str:
    """``LED strip · Local network · 10 zones`` (or the panel size)."""
    try:
        type_name, _icon = device_identity(device)
    except Exception:
        type_name = "Light"
    parts = [type_name, _connection_name(device)]
    transport = str(device.get("transport") or "lan").lower()
    try:
        cap = capabilities_for_device(device)
        if transport == "ble" or cap.is_matrix:
            parts.append(str(device.get("matrix_size") or "32x32").replace("x", "×"))
        elif cap.supports_segments:
            count = connection.get_segment_count(device)
            parts.append(f"{count} zone" + ("" if count == 1 else "s"))
    except Exception:
        pass
    return " · ".join(parts)


def light_icon(device: Dict[str, Any]) -> str:
    try:
        _name, key = device_identity(device)
    except Exception:
        key = IconKey.LIGHT_BULB
    return _ICON_FILES.get(key, "lightbulb-on.svg")


class _LightRow(SettingsCard):
    """One light: icon, name, summary and an include switch."""

    toggled = Signal(str, bool)

    def __init__(self, device: Dict[str, Any], selected: bool):
        self.device_id = device_id(device)
        self.name = str(device.get("name") or device.get("model") or "Device")
        self.switch = ToggleSwitch()
        self.switch.setChecked(selected)
        self.switch.setAccessibleName(f"Include {self.name}")
        location = str(device.get("ip") or device.get("ble_address") or device.get("mac") or "")
        tip = f"Include or exclude {self.name} from this sync mode"
        self.switch.setToolTip(tip + (f"\n{location}" if location else ""))
        super().__init__(light_icon(device), self.name, light_summary(device), self.switch, in_list=True)
        self.set_title_role("rowTitle")
        self.title_label.setWordWrap(False)
        self.setMinimumHeight(60)
        self.setProperty("clickable", True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.switch.toggled.connect(lambda checked: self.toggled.emit(self.device_id, checked))

    def text(self) -> str:
        """The light's name (kept so callers can read rows like buttons)."""
        return self.name

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        # The whole row is a hit target for its switch, like WinUI cards.
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.switch.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class DeviceChipStrip(QFrame):
    """The lights that take part in one sync mode, plus saved groups.

    ``selection_changed`` always emits resolved device ids. Applying a group
    is a convenient UI operation rather than a second persistence model.
    """

    selection_changed = Signal(list)

    def __init__(self, label: str = "Lights", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("DeviceChipStrip")
        self.setFrameShape(QFrame.Shape.NoFrame)

        self._devices: List[Dict[str, Any]] = []
        self._groups: List[Dict[str, Any]] = []
        self._selected_ids: Set[str] = set()
        self._rows: List[_LightRow] = []
        # Historical name: the per-light include controls (now switches).
        self._target_buttons: List[ToggleSwitch] = []
        self._compact = False
        self._signature: Optional[tuple] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)
        self._root = root

        heading = QHBoxLayout()
        heading.setContentsMargins(1, 0, 0, 0)
        heading.setSpacing(8)
        self._heading = heading
        self._label = QLabel(label)
        self._label.setProperty("role", "groupHeader")
        heading.addWidget(self._label, 0, Qt.AlignmentFlag.AlignVCenter)
        heading.addStretch(1)
        root.addLayout(heading)

        self.list = SettingsList()
        root.addWidget(self.list)

        # The group picker row is built once and re-used across refreshes.
        self.group_combo = ProductComboBox()
        self.group_combo.setAccessibleName("Apply a saved group")
        self.group_combo.setMinimumWidth(180)
        self.group_combo.activated.connect(self._on_group_activated)
        self._group_row = SettingsCard(
            "group.svg", "Group", "Apply the lights of a saved group", self.group_combo,
            in_list=True,
        )
        self._group_row.setParent(self)
        self._group_row.hide()

        self._empty_devices = QFrame()
        self._empty_devices.setObjectName("SettingsCard")
        empty_layout = QHBoxLayout(self._empty_devices)
        empty_layout.setContentsMargins(16, 14, 16, 14)
        empty_layout.setSpacing(12)
        empty_layout.addWidget(FluentIcon("lightbulb-on.svg", "fl_text_secondary", 20), 0, Qt.AlignmentFlag.AlignTop)
        empty_text = QLabel("No lights can sync yet. Find or add a local light in Devices.")
        empty_text.setWordWrap(True)
        empty_text.setProperty("role", "settingsDescription")
        empty_layout.addWidget(empty_text, 1)
        self._empty_text = empty_text
        root.addWidget(self._empty_devices)
        self._empty_devices.hide()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    def add_action(self, button: QPushButton) -> None:
        """Keep target management beside the heading instead of another row."""
        self._heading.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_compact(self, compact: bool) -> None:
        """Tighter rows for short windows."""
        if compact == self._compact:
            return
        self._compact = compact
        for row in self._rows:
            row.setMinimumHeight(52 if compact else 60)

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

    def devices(self) -> List[Dict[str, Any]]:
        return list(self._devices)

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

    def rows(self) -> List[_LightRow]:
        return list(self._rows)

    def toggles(self) -> List[ToggleSwitch]:
        return list(self._target_buttons)

    def row_names(self) -> List[str]:
        return [row.name for row in self._rows]

    def usable_groups(self) -> List[tuple[str, List[str]]]:
        valid_ids = {device_id(device) for device in self._devices}
        usable = []
        for group in self._groups:
            members = group_member_ids(group, valid_ids)
            if members:
                usable.append((str(group.get("name") or "Group"), members))
        return usable

    def apply_group(self, name: str) -> bool:
        """Select exactly the members of the saved group ``name``."""
        for group_name, members in self.usable_groups():
            if group_name == name:
                self._selected_ids = set(members)
                self._commit_selection()
                return True
        return False

    # ------------------------------------------------------------------ render

    def _matching_group(self) -> Optional[str]:
        for name, members in self.usable_groups():
            if set(members) == self._selected_ids:
                return name
        return None

    def _layout_signature(self) -> tuple:
        return (
            tuple((device_id(d), str(d.get("name") or d.get("model") or ""), light_summary(d))
                  for d in self._devices),
            tuple((name, tuple(members)) for name, members in self.usable_groups()),
        )

    def _refresh_targets(self) -> None:
        signature = self._layout_signature()
        if signature == self._signature and self._rows:
            # Same lights and groups: only the selection may have changed.
            self._commit_selection(emit=False)
            return
        self._signature = signature
        # Detach the persistent group row before the list is rebuilt.
        self._group_row.setParent(self)
        self._group_row.hide()
        self.list.clear()
        self._rows = []
        self._target_buttons = []

        groups = self.usable_groups()
        if groups and self._devices:
            blocked = self.group_combo.blockSignals(True)
            self.group_combo.clear()
            self.group_combo.addItem(CUSTOM_SELECTION, None)
            for name, members in groups:
                self.group_combo.addItem(name, name)
                count = len(members)
                self.group_combo.setItemData(
                    self.group_combo.count() - 1,
                    f"{count} light{'s' if count != 1 else ''}",
                    Qt.ItemDataRole.ToolTipRole,
                )
            match = self._matching_group()
            self.group_combo.setCurrentIndex(max(0, self.group_combo.findData(match)) if match else 0)
            self.group_combo.blockSignals(blocked)
            self.list.add_row(self._group_row)
            self._group_row.show()

        for device in self._devices:
            sid = device_id(device)
            row = _LightRow(device, sid in self._selected_ids)
            if self._compact:
                row.setMinimumHeight(52)
            row.toggled.connect(self._on_row_toggled)
            self.list.add_row(row)
            self._rows.append(row)
            self._target_buttons.append(row.switch)

        has_devices = bool(self._devices)
        self.list.setVisible(has_devices)
        self._empty_devices.setVisible(not has_devices)

    # ------------------------------------------------------------------ actions

    def _on_row_toggled(self, target_id: str, checked: bool) -> None:
        if checked:
            self._selected_ids.add(target_id)
        else:
            self._selected_ids.discard(target_id)
        self._sync_group_combo()
        self.selection_changed.emit(self.selected_ids())

    def _sync_group_combo(self) -> None:
        if self.group_combo.count() == 0:
            return
        match = self._matching_group()
        blocked = self.group_combo.blockSignals(True)
        self.group_combo.setCurrentIndex(max(0, self.group_combo.findData(match)) if match else 0)
        self.group_combo.blockSignals(blocked)

    def _on_group_activated(self, index: int) -> None:
        name = self.group_combo.itemData(index)
        if not name:
            return
        self.apply_group(str(name))

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

    def _commit_selection(self, emit: bool = True) -> None:
        # Update switches in place (no rebuild), so focus and hover survive.
        for row in self._rows:
            selected = row.device_id in self._selected_ids
            if row.switch.isChecked() != selected:
                blocked = row.switch.blockSignals(True)
                row.switch.setChecked(selected)
                row.switch.blockSignals(blocked)
                row.switch._animate_state(selected)
        self._sync_group_combo()
        if emit:
            self.selection_changed.emit(self.selected_ids())


__all__ = ["DeviceChipStrip", "device_id", "group_member_ids", "light_summary"]
