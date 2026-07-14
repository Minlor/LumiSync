"""Sync Modes view — multi-device-ready layout."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ... import connection
from ...sync import audio
from ..controllers.device_controller import DeviceController
from ..controllers.sync_controller import SyncController
from ..utils.animations import animate_height
from ..widgets.device_chip import DeviceChipStrip, device_id
from ..widgets.led_mapping_widget import LedMappingWidget
from ..widgets.product_controls import ProductComboBox, ProductSlider


class ModesView(QWidget):
    def __init__(
        self,
        sync_controller: SyncController,
        device_controller: Optional[DeviceController] = None,
        mode: Optional[str] = None,
    ):
        super().__init__()
        self.controller = sync_controller
        self.device_controller = device_controller
        self.mode = mode if mode in {"monitor", "music"} else None

        self._mapping_expanded = False
        self._sync_was_running = False
        self._sync_mode_before: Optional[str] = None
        self._paused_devices: List[dict] = []
        self._mapping_anim = None

        self._build_ui()
        self._connect_signals()
        self._refresh_chip_strips()

    # ------------------------------------------------------------------ build

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self.scroll)

        content = QWidget()
        self.scroll.setWidget(content)
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(18)

        page_header = QVBoxLayout()
        page_header.setSpacing(3)
        page_title = (
            "Monitor Sync" if self.mode == "monitor"
            else "Music Sync" if self.mode == "music"
            else "Sync Modes"
        )
        page_intro = (
            "Mirror the selected display across a device or saved group."
            if self.mode == "monitor"
            else "Choose how your lights react to music, then start the show."
            if self.mode == "music"
            else "Choose what your lights react to, select the targets, and start syncing."
        )
        header = QLabel(page_title)
        header.setProperty("role", "title")
        page_header.addWidget(header)

        intro = QLabel(page_intro)
        intro.setProperty("role", "pageDescription")
        intro.setWordWrap(True)
        page_header.addWidget(intro)
        root.addLayout(page_header)

        if self.mode == "monitor":
            self.monitor_group = self._build_monitor_group()
            root.addWidget(
                self.monitor_group,
                alignment=Qt.AlignmentFlag.AlignTop,
            )
        elif self.mode == "music":
            self.music_group = self._build_music_group()
            root.addWidget(
                self.music_group,
                alignment=Qt.AlignmentFlag.AlignTop,
            )
        else:
            modes_row = QHBoxLayout()
            modes_row.setSpacing(16)
            self.monitor_group = self._build_monitor_group()
            self.music_group = self._build_music_group()
            modes_row.addWidget(
                self.monitor_group,
                1,
                alignment=Qt.AlignmentFlag.AlignTop,
            )
            modes_row.addWidget(
                self.music_group,
                1,
                alignment=Qt.AlignmentFlag.AlignTop,
            )
            root.addLayout(modes_row)

        root.addStretch(1)

    def _build_monitor_group(self) -> QGroupBox:
        group = QGroupBox("Monitor Sync")
        group.setMinimumHeight(310)
        layout = QVBoxLayout(group)
        layout.setSpacing(13)

        desc = QLabel("Sample colors from your screen and push them to the lights.")
        desc.setProperty("role", "subtle")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.monitor_chips = DeviceChipStrip("Targets")
        self.monitor_chips.selection_changed.connect(
            lambda ids: self._on_mode_devices_changed("monitor", ids)
        )
        layout.addWidget(self.monitor_chips)

        self.monitor_group_button = QPushButton("Create Group from Targets")
        self.monitor_group_button.setToolTip(
            "Save the enabled monitor-sync targets as a reusable group"
        )
        self.monitor_group_button.clicked.connect(
            lambda: self._save_targets_as_group(self.monitor_chips)
        )
        layout.addWidget(
            self.monitor_group_button,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )

        # Brightness
        b_row = QHBoxLayout()
        b_row.addWidget(QLabel("Brightness"))
        self.monitor_brightness_slider = ProductSlider(Qt.Orientation.Horizontal)
        self.monitor_brightness_slider.setRange(10, 100)
        self.monitor_brightness_slider.setValue(int(self.controller.get_monitor_brightness() * 100))
        self.monitor_brightness_slider.valueChanged.connect(self._on_monitor_brightness)
        b_row.addWidget(self.monitor_brightness_slider, 1)
        self.monitor_brightness_label = QLabel(f"{self.monitor_brightness_slider.value()}%")
        self.monitor_brightness_label.setMinimumWidth(40)
        self.monitor_brightness_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        b_row.addWidget(self.monitor_brightness_label)
        layout.addLayout(b_row)

        # LED Mapping toggle + container
        toggle_row = QHBoxLayout()
        self.mapping_toggle_button = QPushButton("LED Mapping  ▶")
        self.mapping_toggle_button.setObjectName("SectionToggle")
        self.mapping_toggle_button.setFlat(True)
        self.mapping_toggle_button.clicked.connect(self._toggle_led_mapping)
        toggle_row.addWidget(self.mapping_toggle_button)
        toggle_row.addStretch(1)
        self.monitor_zones_button = QPushButton("Adjust Zones")
        self.monitor_zones_button.setToolTip(
            "Set the zone count for selected monitor sync devices"
        )
        self.monitor_zones_button.clicked.connect(
            lambda: self._adjust_zones_for_strip(self.monitor_chips)
        )
        toggle_row.addWidget(self.monitor_zones_button)
        layout.addLayout(toggle_row)

        self.led_mapping_container = QFrame()
        self.led_mapping_container.setMaximumHeight(0)
        self.led_mapping_container.setVisible(False)
        container_layout = QVBoxLayout(self.led_mapping_container)
        container_layout.setContentsMargins(0, 6, 0, 0)
        settings = QSettings("Minlor", "LumiSync")
        self.led_mapping_widget = LedMappingWidget(settings, sync_controller=self.controller)
        self.led_mapping_widget.mapping_changed.connect(lambda *_: None)
        container_layout.addWidget(self.led_mapping_widget)
        layout.addWidget(self.led_mapping_container)

        # Start
        self.monitor_start_button = QPushButton("Start Monitor Sync")
        self.monitor_start_button.setObjectName("Primary")
        self.monitor_start_button.setProperty("role", "primary")
        self.monitor_start_button.clicked.connect(self._handle_monitor_action)
        layout.addWidget(self.monitor_start_button)

        return group

    def _build_music_group(self) -> QGroupBox:
        group = QGroupBox("Music Sync")
        group.setMinimumHeight(520)
        layout = QVBoxLayout(group)
        layout.setSpacing(13)

        desc = QLabel(
            "Pick a movement style and color palette, then send the result "
            "to a device or saved group."
        )
        desc.setProperty("role", "subtle")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.music_chips = DeviceChipStrip("Targets")
        self.music_chips.selection_changed.connect(
            lambda ids: self._on_mode_devices_changed("music", ids)
        )
        layout.addWidget(self.music_chips)

        self.music_group_button = QPushButton("Create Group from Targets")
        self.music_group_button.setToolTip(
            "Save the enabled music-sync targets as a reusable group"
        )
        self.music_group_button.clicked.connect(
            lambda: self._save_targets_as_group(self.music_chips)
        )
        layout.addWidget(
            self.music_group_button,
            alignment=Qt.AlignmentFlag.AlignLeft,
        )

        reaction_label = QLabel("REACTION STYLE")
        reaction_label.setProperty("role", "eyebrow")
        layout.addWidget(reaction_label)

        reaction_picker = QFrame()
        reaction_picker.setObjectName("MusicReactionPicker")
        reaction_grid = QGridLayout(reaction_picker)
        reaction_grid.setContentsMargins(0, 0, 0, 0)
        reaction_grid.setHorizontalSpacing(8)
        reaction_grid.setVerticalSpacing(8)

        self.music_reaction_group = QButtonGroup(self)
        self.music_reaction_group.setExclusive(True)
        self.music_reaction_buttons = {}
        self._music_auto_active_reaction: Optional[str] = None
        current_reaction = self.controller.get_music_reaction()
        # Keep the growing reaction library at two rows so the primary action
        # remains visible. Five columns still leave enough room for every
        # concise label at the supported window width.
        reaction_columns = min(5, max(4, (len(audio.REACTIONS) + 1) // 2))
        for column in range(reaction_columns):
            reaction_grid.setColumnStretch(column, 1)
        for index, key in enumerate(audio.REACTIONS):
            button = QPushButton(audio.REACTION_LABELS[key])
            button.setCheckable(True)
            button.setProperty("reactionOption", True)
            button.setProperty("autoActive", False)
            button.setMinimumHeight(44)
            button.setToolTip(audio.REACTION_DESCRIPTIONS[key])
            button.setAccessibleDescription(
                audio.REACTION_DESCRIPTIONS[key]
            )
            button.setChecked(key == current_reaction)
            button.clicked.connect(
                lambda checked=False, reaction=key: (
                    self._on_music_reaction_changed(reaction)
                    if checked else None
                )
            )
            self.music_reaction_group.addButton(button)
            self.music_reaction_buttons[key] = button
            reaction_grid.addWidget(
                button,
                index // reaction_columns,
                index % reaction_columns,
            )
        layout.addWidget(reaction_picker)

        self.music_reaction_description = QLabel(
            audio.REACTION_DESCRIPTIONS.get(
                current_reaction,
                audio.REACTION_DESCRIPTIONS[audio.REACTION_FLOW],
            )
        )
        self.music_reaction_description.setProperty("role", "subtle")
        self.music_reaction_description.setWordWrap(True)
        layout.addWidget(self.music_reaction_description)

        self.music_auto_status = QFrame()
        self.music_auto_status.setObjectName("AutoDirectorStatus")
        self.music_auto_status.setAccessibleName("Auto Director live status")
        auto_status_layout = QHBoxLayout(self.music_auto_status)
        auto_status_layout.setContentsMargins(12, 8, 12, 8)
        auto_status_layout.setSpacing(10)

        self.music_auto_color_dot = QFrame()
        self.music_auto_color_dot.setObjectName("AutoDirectorColorDot")
        self.music_auto_color_dot.setFixedSize(28, 28)
        self.music_auto_color_dot.setAccessibleName(
            "Waiting for Auto Director output color"
        )
        self.music_auto_color_dot.setToolTip("Waiting for live output color")
        auto_status_layout.addWidget(
            self.music_auto_color_dot,
            alignment=Qt.AlignmentFlag.AlignVCenter,
        )

        auto_copy = QVBoxLayout()
        auto_copy.setContentsMargins(0, 0, 0, 0)
        auto_copy.setSpacing(1)
        self.music_auto_status_title = QLabel("Auto Director is ready")
        self.music_auto_status_title.setProperty("role", "autoDirectorTitle")
        auto_copy.addWidget(self.music_auto_status_title)
        self.music_auto_status_detail = QLabel(
            "Start Music Sync to see the selected reaction and live color."
        )
        self.music_auto_status_detail.setProperty("role", "autoDirectorMeta")
        auto_copy.addWidget(self.music_auto_status_detail)
        auto_status_layout.addLayout(auto_copy, 1)

        layout.addWidget(self.music_auto_status)

        palette_row = QHBoxLayout()
        palette_row.setSpacing(10)
        palette_row.addWidget(QLabel("Color palette"))
        self.music_palette_combo = ProductComboBox()
        for key in audio.PALETTES:
            self.music_palette_combo.addItem(
                audio.PALETTE_LABELS.get(key, key), key
            )
        current_palette = self.controller.get_music_palette()
        palette_index = self.music_palette_combo.findData(
            current_palette
        )
        self.music_palette_combo.setCurrentIndex(max(0, palette_index))
        self.music_palette_combo.currentIndexChanged.connect(
            self._on_music_palette_changed
        )
        palette_row.addWidget(self.music_palette_combo, 1)
        layout.addLayout(palette_row)

        self.music_palette_description = QLabel(
            audio.PALETTE_DESCRIPTIONS.get(
                current_palette,
                audio.PALETTE_DESCRIPTIONS[audio.PALETTE_RGB],
            )
        )
        self.music_palette_description.setProperty("role", "subtle")
        self.music_palette_description.setWordWrap(True)
        self.music_palette_combo.setToolTip(
            self.music_palette_description.text()
        )
        self.music_palette_combo.setAccessibleDescription(
            self.music_palette_description.text()
        )
        layout.addWidget(self.music_palette_description)

        zone_row = QHBoxLayout()
        zone_row.addWidget(QLabel("Zones"))
        zone_row.addStretch(1)
        self.music_zones_button = QPushButton("Adjust Zones")
        self.music_zones_button.setToolTip(
            "Set the zone count for selected music sync devices"
        )
        self.music_zones_button.clicked.connect(
            lambda: self._adjust_zones_for_strip(self.music_chips)
        )
        zone_row.addWidget(self.music_zones_button)
        layout.addLayout(zone_row)

        b_row = QHBoxLayout()
        b_row.addWidget(QLabel("Brightness"))
        self.music_brightness_slider = ProductSlider(Qt.Orientation.Horizontal)
        self.music_brightness_slider.setRange(10, 100)
        self.music_brightness_slider.setValue(int(self.controller.get_music_brightness() * 100))
        self.music_brightness_slider.valueChanged.connect(self._on_music_brightness)
        b_row.addWidget(self.music_brightness_slider, 1)
        self.music_brightness_label = QLabel(f"{self.music_brightness_slider.value()}%")
        self.music_brightness_label.setMinimumWidth(40)
        self.music_brightness_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        b_row.addWidget(self.music_brightness_label)
        layout.addLayout(b_row)

        self.music_auto_gain_check = QCheckBox("Ignore master volume")
        self.music_auto_gain_check.setChecked(
            self.controller.get_music_auto_gain()
        )
        self.music_auto_gain_check.setToolTip(
            "Normalize loudness so lowering your speaker/system volume no longer "
            "dims the sync. The music's own dynamics still drive the lights."
        )
        self.music_auto_gain_check.setAccessibleDescription(
            self.music_auto_gain_check.toolTip()
        )
        self.music_auto_gain_check.toggled.connect(self._on_music_auto_gain_toggled)
        layout.addWidget(self.music_auto_gain_check)

        self.music_start_button = QPushButton("Start Music Sync")
        self.music_start_button.setObjectName("Primary")
        self.music_start_button.setProperty("role", "primary")
        self.music_start_button.clicked.connect(self._handle_music_action)
        layout.addWidget(self.music_start_button)

        self._refresh_music_auto_status()

        return group

    # ------------------------------------------------------------------ wiring

    def _connect_signals(self) -> None:
        self.controller.sync_started.connect(self._on_sync_state_changed)
        self.controller.sync_stopped.connect(self._on_sync_state_changed)
        self.controller.brightness_changed.connect(self._on_brightness_changed)
        self.controller.music_auto_state_changed.connect(
            self._on_music_auto_state_changed
        )
        if self.device_controller is not None:
            self.device_controller.devices_discovered.connect(lambda *_: self._refresh_chip_strips())
            self.device_controller.device_added.connect(lambda *_: self._refresh_chip_strips())
            self.device_controller.device_removed.connect(lambda *_: self._refresh_chip_strips())
            self.device_controller.device_selected.connect(lambda *_: self._refresh_chip_strips())
            self.device_controller.device_updated.connect(lambda *_: self._refresh_chip_strips())
            self.device_controller.groups_changed.connect(lambda *_: self._refresh_chip_strips())

    # ------------------------------------------------------------------ chips

    def _refresh_chip_strips(self) -> None:
        if self.device_controller is None:
            return
        devs = self.device_controller.devices
        settings = QSettings("Minlor", "LumiSync")
        default_ids: List[str] = []
        if devs and 0 <= self.device_controller.selected_device_index < len(devs):
            default_ids = [device_id(devs[self.device_controller.selected_device_index])]
        saved_groups = self.device_controller.get_groups()
        strips = []
        if hasattr(self, "monitor_chips"):
            strips.append(("monitor", self.monitor_chips))
        if hasattr(self, "music_chips"):
            strips.append(("music", self.music_chips))
        for mode, strip in strips:
            strip.set_devices(devs, default_ids=default_ids)
            strip.set_groups(saved_groups)
            saved = settings.value(f"sync/{mode}/devices", None)
            if saved is not None:
                if isinstance(saved, str):
                    ids = [s for s in saved.split(",") if s]
                else:
                    ids = list(saved)
                strip.set_selected_ids(ids)
        self._refresh_led_mapping_zone_count()
        self._update_action_states()

    def _on_mode_devices_changed(self, mode: str, ids: List[str]) -> None:
        self._save_mode_devices(mode, ids)
        if mode == "monitor":
            if not ids and self._mapping_expanded:
                self._sync_was_running = False
                self._sync_mode_before = None
                self._toggle_led_mapping()
            self._refresh_led_mapping_zone_count()

        current_mode = (
            self.controller.get_current_sync_mode()
            if self.controller.is_syncing()
            else None
        )
        if current_mode == mode and not self._mapping_expanded:
            selected = (
                self.monitor_chips.selected_devices()
                if mode == "monitor"
                else self.music_chips.selected_devices()
            )
            if selected:
                if mode == "monitor":
                    self.controller.start_monitor_sync(selected)
                else:
                    self.controller.start_music_sync(selected)
            else:
                self.controller.stop_sync(announce=False)
                display_mode = "Monitor" if mode == "monitor" else "Music"
                self.controller.status_updated.emit(
                    f"{display_mode} sync stopped because all targets were turned off."
                )
        self._update_action_states()

    def _update_action_states(self) -> None:
        """Disable actions that cannot succeed without a target device."""
        monitor_ready = bool(
            self.monitor_chips.selected_devices()
        ) if hasattr(self, "monitor_chips") else False
        music_ready = bool(
            self.music_chips.selected_devices()
        ) if hasattr(self, "music_chips") else False
        running = self.controller.is_syncing()
        current_mode = self.controller.get_current_sync_mode() if running else None

        if hasattr(self, "monitor_start_button"):
            self._set_sync_button(
                self.monitor_start_button,
                "Stop Monitor Sync"
                if current_mode == "monitor"
                else "Switch to Monitor Sync"
                if current_mode == "music"
                else "Start Monitor Sync",
                danger=current_mode == "monitor",
                enabled=current_mode == "monitor" or monitor_ready,
            )
            self.monitor_zones_button.setEnabled(monitor_ready)
            self.mapping_toggle_button.setEnabled(monitor_ready)
            self.monitor_group_button.setEnabled(monitor_ready)
        if hasattr(self, "music_start_button"):
            self._set_sync_button(
                self.music_start_button,
                "Stop Music Sync"
                if current_mode == "music"
                else "Switch to Music Sync"
                if current_mode == "monitor"
                else "Start Music Sync",
                danger=current_mode == "music",
                enabled=current_mode == "music" or music_ready,
            )
            self.music_zones_button.setEnabled(music_ready)
            self.music_group_button.setEnabled(music_ready)

        monitor_hint = (
            "Stop monitor sync"
            if current_mode == "monitor"
            else "" if monitor_ready else "Select at least one device first"
        )
        music_hint = (
            "Stop music sync"
            if current_mode == "music"
            else "" if music_ready else "Select at least one device first"
        )
        if hasattr(self, "monitor_start_button"):
            self.monitor_start_button.setToolTip(monitor_hint)
            self.monitor_zones_button.setToolTip(
                "Set the zone count for selected monitor sync devices"
                if monitor_ready else monitor_hint
            )
            self.mapping_toggle_button.setToolTip(
                "Map each LED zone to a region of the selected display"
                if monitor_ready else monitor_hint
            )
        if hasattr(self, "music_start_button"):
            self.music_start_button.setToolTip(music_hint)
            self.music_zones_button.setToolTip(
                "Set the zone count for selected music sync devices"
                if music_ready else music_hint
            )

    @staticmethod
    def _set_sync_button(
        button: QPushButton,
        text: str,
        *,
        danger: bool,
        enabled: bool,
    ) -> None:
        button.setText(text)
        button.setObjectName("DangerButton" if danger else "Primary")
        button.setProperty("role", "danger" if danger else "primary")
        button.setEnabled(enabled)
        style = button.style()
        style.unpolish(button)
        style.polish(button)
        button.update()

    def _save_mode_devices(self, mode: str, ids: List[str]) -> None:
        settings = QSettings("Minlor", "LumiSync")
        settings.setValue(f"sync/{mode}/devices", ",".join(ids))

    def _save_targets_as_group(self, strip: DeviceChipStrip) -> None:
        if self.device_controller is None:
            self.controller.status_updated.emit("Group creation is unavailable.")
            return
        selected_ids = set(strip.selected_ids())
        indexes = [
            index
            for index, device in enumerate(self.device_controller.devices)
            if device_id(device) in selected_ids
        ]
        if not indexes:
            self.controller.status_updated.emit(
                "Enable at least one target before creating a group."
            )
            return

        name, accepted = QInputDialog.getText(
            self,
            "Create Sync Group",
            "Group name",
        )
        name = name.strip()
        if not accepted or not name:
            return
        existing = {
            str(group.get("name", "")).strip().casefold()
            for group in self.device_controller.get_groups()
        }
        if name.casefold() in existing:
            self.controller.status_updated.emit(
                f"A group named '{name}' already exists."
            )
            return
        self.device_controller.save_group(name, indexes)

    def _refresh_led_mapping_zone_count(self) -> None:
        if not hasattr(self, "led_mapping_widget"):
            return

        aspect_ratio = self._monitor_aspect_ratio()
        selected_devices = self.monitor_chips.selected_devices()
        if not selected_devices:
            self.controller.set_devices([])
            self.led_mapping_widget.set_mapping_context(
                connection.get_segment_count({}),
                aspect_ratio,
            )
            return

        self.controller.selected_devices = [
            dict(device)
            for device in selected_devices
            if device
        ]
        segment_count = connection.get_segment_count(selected_devices[0])
        self.led_mapping_widget.set_mapping_context(segment_count, aspect_ratio)

    def _monitor_aspect_ratio(self) -> float:
        screens = list(QGuiApplication.screens())
        display_index = int(self.controller.get_monitor_display())
        if 0 <= display_index < len(screens):
            geometry = screens[display_index].geometry()
            return geometry.width() / max(1, geometry.height())
        return 16 / 9

    def _adjust_zones_for_strip(self, strip: DeviceChipStrip) -> None:
        if self.device_controller is None:
            self.controller.status_updated.emit("Zone settings are unavailable.")
            return

        selected_devices = strip.selected_devices()
        if not selected_devices:
            self.controller.status_updated.emit("Select at least one device first.")
            return

        current_count = connection.get_segment_count(selected_devices[0])
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Adjust Zone Count")
        dialog.setLabelText("Number of LED zones")
        dialog.setInputMode(QInputDialog.InputMode.IntInput)
        dialog.setIntRange(1, 255)
        dialog.setIntStep(1)
        dialog.setIntValue(current_count)
        dialog.setOkButtonText("Save Zones")
        if not dialog.exec():
            return
        zone_count = dialog.intValue()

        selected_ids = {device_id(device) for device in selected_devices}
        changed = 0
        for index, device in enumerate(self.device_controller.devices):
            selected = device_id(device) in selected_ids
            if selected and self.device_controller.set_zone_count_at(index, zone_count):
                changed += 1

        if changed:
            plural = "" if changed == 1 else "s"
            self.controller.status_updated.emit(
                f"Zone count set to {zone_count} for {changed} device{plural}."
            )
            if (
                hasattr(self, "monitor_chips")
                and strip is self.monitor_chips
            ):
                self._refresh_led_mapping_zone_count()

    # ------------------------------------------------------------------ LED mapping

    def _toggle_led_mapping(self) -> None:
        if not self._mapping_expanded and not self.monitor_chips.selected_devices():
            self.controller.status_updated.emit(
                "Select at least one monitor sync device before opening LED Mapping."
            )
            return
        self._mapping_expanded = not self._mapping_expanded

        if self._mapping_expanded:
            self._refresh_led_mapping_zone_count()
            self.mapping_toggle_button.setText("LED Mapping  ▼")
            self.led_mapping_container.setVisible(True)
            target_height = max(400, self.led_mapping_widget.sizeHint().height() + 12)
            self._mapping_anim = animate_height(
                self.led_mapping_container, target_height, duration=220
            )

            if self.controller.is_syncing():
                self._sync_was_running = True
                self._sync_mode_before = self.controller.get_current_sync_mode()
                self._paused_devices = self.controller.get_selected_devices()
                self.controller.stop_sync()

            self.led_mapping_widget.start_test_mode_if_not_active()
        else:
            self.mapping_toggle_button.setText("LED Mapping  ▶")
            self._mapping_anim = animate_height(
                self.led_mapping_container, 0, duration=180,
                on_finished=lambda: self.led_mapping_container.setVisible(False),
            )

            self.led_mapping_widget.stop_test_mode_if_active()

            if self._sync_was_running:
                self._sync_was_running = False
                if self._sync_mode_before == "monitor":
                    self.controller.start_monitor_sync(self._paused_devices)
                elif self._sync_mode_before == "music":
                    self.controller.start_music_sync(self._paused_devices)
                self._sync_mode_before = None
                self._paused_devices = []

    # ------------------------------------------------------------------ brightness

    def _on_music_reaction_changed(self, reaction: str) -> None:
        self.controller.set_music_reaction(reaction)
        self.music_reaction_description.setText(
            audio.REACTION_DESCRIPTIONS.get(
                reaction,
                audio.REACTION_DESCRIPTIONS[audio.REACTION_FLOW],
            )
        )
        self._refresh_music_auto_status()

    def _on_music_auto_state_changed(self, reaction: str, color) -> None:
        if (
            not hasattr(self, "music_auto_status")
            or self.controller.get_music_reaction() != audio.REACTION_AUTO
            or reaction not in audio.REACTIONS
            or reaction == audio.REACTION_AUTO
        ):
            return

        try:
            red, green, blue = (
                max(0, min(255, int(channel))) for channel in color
            )
        except (TypeError, ValueError):
            return

        reaction_label = audio.REACTION_LABELS.get(reaction, reaction)
        palette_key = str(self.music_palette_combo.currentData())
        palette_label = audio.PALETTE_LABELS.get(palette_key, palette_key)
        self.music_auto_status.setVisible(True)
        self.music_auto_status_title.setText(
            f"Now directing · {reaction_label}"
        )
        self.music_auto_status_detail.setText(
            f"{palette_label} palette · reacting live to audio"
        )

        color_value = QColor(red, green, blue)
        color_hex = color_value.name().upper()
        border_hex = color_value.lighter(145).name().upper()
        self.music_auto_color_dot.setStyleSheet(
            "QFrame#AutoDirectorColorDot {"
            f"background-color: {color_hex};"
            f"border: 2px solid {border_hex};"
            "border-radius: 14px;"
            "}"
        )
        self.music_auto_color_dot.setAccessibleName(
            f"Current Auto Director output color {color_hex}"
        )
        self.music_auto_color_dot.setToolTip(
            f"Live output color {color_hex}"
        )
        self._set_music_auto_reaction_highlight(reaction)

    def _refresh_music_auto_status(self) -> None:
        if not hasattr(self, "music_auto_status"):
            return
        enabled = self.controller.get_music_reaction() == audio.REACTION_AUTO
        self.music_auto_status.setVisible(enabled)
        if not enabled:
            self._set_music_auto_reaction_highlight(None)
            return

        listening = (
            self.controller.is_syncing()
            and self.controller.get_current_sync_mode() == "music"
        )
        self.music_auto_status_title.setText(
            "Auto Director is listening"
            if listening
            else "Auto Director is ready"
        )
        self.music_auto_status_detail.setText(
            "Play audio to reveal the selected reaction and live color."
            if listening
            else "Start Music Sync to see the selected reaction and live color."
        )
        self.music_auto_color_dot.setStyleSheet("")
        self.music_auto_color_dot.setAccessibleName(
            "Waiting for Auto Director output color"
        )
        self.music_auto_color_dot.setToolTip("Waiting for live output color")
        self._set_music_auto_reaction_highlight(None)

    def _set_music_auto_reaction_highlight(
        self,
        reaction: Optional[str],
    ) -> None:
        if not hasattr(self, "music_reaction_buttons"):
            return
        if reaction == self._music_auto_active_reaction:
            return
        self._music_auto_active_reaction = reaction
        for key, button in self.music_reaction_buttons.items():
            active = key == reaction and key != audio.REACTION_AUTO
            if bool(button.property("autoActive")) == active:
                continue
            button.setProperty("autoActive", active)
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def _on_music_palette_changed(self, _index: int = 0) -> None:
        palette = self.music_palette_combo.currentData()
        if palette:
            palette = str(palette)
            self.controller.set_music_palette(palette)
            description = audio.PALETTE_DESCRIPTIONS.get(
                palette,
                audio.PALETTE_DESCRIPTIONS[audio.PALETTE_RGB],
            )
            self.music_palette_description.setText(description)
            self.music_palette_combo.setToolTip(description)
            self.music_palette_combo.setAccessibleDescription(description)

    def _on_monitor_brightness(self, value: int) -> None:
        self.controller.set_monitor_brightness(value / 100.0)
        self.monitor_brightness_label.setText(f"{value}%")

    def _on_music_brightness(self, value: int) -> None:
        self.controller.set_music_brightness(value / 100.0)
        self.music_brightness_label.setText(f"{value}%")

    def _on_music_auto_gain_toggled(self, enabled: bool) -> None:
        self.controller.set_music_auto_gain(enabled)

    def _on_brightness_changed(self, mode: str, value: float) -> None:
        if mode == "monitor" and hasattr(self, "monitor_brightness_slider"):
            self.monitor_brightness_slider.setValue(int(value * 100))
        elif mode == "music" and hasattr(self, "music_brightness_slider"):
            self.music_brightness_slider.setValue(int(value * 100))

    # ------------------------------------------------------------------ start helpers

    def _start_monitor_sync(self) -> None:
        if hasattr(self, "monitor_chips"):
            self.controller.start_monitor_sync(
                self.monitor_chips.selected_devices()
            )

    def _start_music_sync(self) -> None:
        if hasattr(self, "music_chips"):
            self.controller.start_music_sync(
                self.music_chips.selected_devices()
            )

    def _handle_monitor_action(self) -> None:
        if (
            self.controller.is_syncing()
            and self.controller.get_current_sync_mode() == "monitor"
        ):
            self.controller.stop_sync()
        else:
            self._start_monitor_sync()

    def _handle_music_action(self) -> None:
        if (
            self.controller.is_syncing()
            and self.controller.get_current_sync_mode() == "music"
        ):
            self.controller.stop_sync()
        else:
            self._start_music_sync()

    def _on_sync_state_changed(self, *_args) -> None:
        self._update_action_states()
        self._refresh_music_auto_status()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt API
        # LED test mode owns the lights temporarily. Leaving the page must end
        # it and restore any sync that was paused to open the mapper.
        if self._mapping_expanded:
            self._toggle_led_mapping()
        super().hideEvent(event)
