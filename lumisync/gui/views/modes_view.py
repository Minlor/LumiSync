"""Sync Modes view — multi-device-ready layout."""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import QSettings, QTimer, Qt
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ... import connection
from ..controllers.device_controller import DeviceController
from ..controllers.sync_controller import SyncController
from ..theme import qcolor
from ..utils.animations import animate_height
from ..widgets.active_sync_row import ActiveSyncRow
from ..widgets.device_chip import DeviceChipStrip, device_id
from ..widgets.led_mapping_widget import LedMappingWidget


class ModesView(QWidget):
    def __init__(self, sync_controller: SyncController, device_controller: Optional[DeviceController] = None):
        super().__init__()
        self.controller = sync_controller
        self.device_controller = device_controller

        self._mapping_expanded = False
        self._sync_was_running = False
        self._sync_mode_before: Optional[str] = None
        self._mapping_anim = None

        self._build_ui()
        self._connect_signals()
        self._refresh_chip_strips()
        self._refresh_active_syncs()

        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._refresh_active_syncs)
        self.status_timer.start(1000)

    # ------------------------------------------------------------------ build

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        header = QLabel("Sync Modes")
        f = QFont()
        f.setPointSize(18)
        f.setBold(True)
        header.setFont(f)
        root.addWidget(header)

        modes_row = QHBoxLayout()
        modes_row.setSpacing(14)
        modes_row.addWidget(
            self._build_monitor_group(),
            1,
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        modes_row.addWidget(
            self._build_music_group(),
            1,
            alignment=Qt.AlignmentFlag.AlignTop,
        )
        root.addLayout(modes_row)

        root.addWidget(self._build_active_syncs_group())
        root.addStretch(1)

    def _build_monitor_group(self) -> QGroupBox:
        group = QGroupBox("Monitor Sync")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        desc = QLabel("Sample colors from your screen and push them to the lights.")
        desc.setProperty("role", "subtle")
        desc.setStyleSheet(f"color: {qcolor('text_dim').name()}; font-size: 9pt;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.monitor_chips = DeviceChipStrip("Devices")
        self.monitor_chips.selection_changed.connect(
            lambda ids: self._on_mode_devices_changed("monitor", ids)
        )
        layout.addWidget(self.monitor_chips)

        # Brightness
        b_row = QHBoxLayout()
        b_row.addWidget(QLabel("Brightness"))
        self.monitor_brightness_slider = QSlider(Qt.Orientation.Horizontal)
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
        self.monitor_start_button.clicked.connect(self._start_monitor_sync)
        layout.addWidget(self.monitor_start_button)

        return group

    def _build_music_group(self) -> QGroupBox:
        group = QGroupBox("Music Sync")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        desc = QLabel("Push colors based on the audio output amplitude.")
        desc.setProperty("role", "subtle")
        desc.setStyleSheet(f"color: {qcolor('text_dim').name()}; font-size: 9pt;")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.music_chips = DeviceChipStrip("Devices")
        self.music_chips.selection_changed.connect(
            lambda ids: self._on_mode_devices_changed("music", ids)
        )
        layout.addWidget(self.music_chips)

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
        self.music_brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self.music_brightness_slider.setRange(10, 100)
        self.music_brightness_slider.setValue(int(self.controller.get_music_brightness() * 100))
        self.music_brightness_slider.valueChanged.connect(self._on_music_brightness)
        b_row.addWidget(self.music_brightness_slider, 1)
        self.music_brightness_label = QLabel(f"{self.music_brightness_slider.value()}%")
        self.music_brightness_label.setMinimumWidth(40)
        self.music_brightness_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        b_row.addWidget(self.music_brightness_label)
        layout.addLayout(b_row)

        self.music_start_button = QPushButton("Start Music Sync")
        self.music_start_button.setObjectName("Primary")
        self.music_start_button.setProperty("role", "primary")
        self.music_start_button.clicked.connect(self._start_music_sync)
        layout.addWidget(self.music_start_button)

        return group

    def _build_active_syncs_group(self) -> QGroupBox:
        group = QGroupBox("Active Syncs")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        self.active_syncs_layout = QVBoxLayout()
        self.active_syncs_layout.setSpacing(8)
        layout.addLayout(self.active_syncs_layout)

        self.empty_active_label = QLabel("No active syncs.")
        self.empty_active_label.setProperty("role", "hint")
        self.empty_active_label.setStyleSheet(
            f"color: {qcolor('text_disabled').name()}; font-style: italic; padding: 6px 0;"
        )
        layout.addWidget(self.empty_active_label)

        return group

    # ------------------------------------------------------------------ wiring

    def _connect_signals(self) -> None:
        self.controller.sync_started.connect(lambda *_: self._refresh_active_syncs())
        self.controller.sync_stopped.connect(lambda *_: self._refresh_active_syncs())
        self.controller.brightness_changed.connect(self._on_brightness_changed)
        if self.device_controller is not None:
            self.device_controller.devices_discovered.connect(lambda *_: self._refresh_chip_strips())
            self.device_controller.device_added.connect(lambda *_: self._refresh_chip_strips())
            self.device_controller.device_removed.connect(lambda *_: self._refresh_chip_strips())
            self.device_controller.device_selected.connect(lambda *_: self._refresh_chip_strips())
            self.device_controller.device_updated.connect(lambda *_: self._refresh_chip_strips())

    # ------------------------------------------------------------------ chips

    def _refresh_chip_strips(self) -> None:
        if self.device_controller is None:
            return
        devs = self.device_controller.devices
        settings = QSettings("Minlor", "LumiSync")
        default_ids: List[str] = []
        if devs and 0 <= self.device_controller.selected_device_index < len(devs):
            default_ids = [device_id(devs[self.device_controller.selected_device_index])]
        for mode, strip in (("monitor", self.monitor_chips), ("music", self.music_chips)):
            strip.set_devices(devs, default_ids=default_ids)
            saved = settings.value(f"sync/{mode}/devices", None)
            if saved is not None:
                if isinstance(saved, str):
                    ids = [s for s in saved.split(",") if s]
                else:
                    ids = list(saved)
                strip.set_selected_ids(ids)
        self._refresh_led_mapping_zone_count()

    def _on_mode_devices_changed(self, mode: str, ids: List[str]) -> None:
        self._save_mode_devices(mode, ids)
        if mode == "monitor":
            self._refresh_led_mapping_zone_count()

    def _save_mode_devices(self, mode: str, ids: List[str]) -> None:
        settings = QSettings("Minlor", "LumiSync")
        settings.setValue(f"sync/{mode}/devices", ",".join(ids))

    def _refresh_led_mapping_zone_count(self) -> None:
        if not hasattr(self, "led_mapping_widget"):
            return

        aspect_ratio = self._monitor_aspect_ratio()
        selected_devices = self.monitor_chips.selected_devices()
        if not selected_devices:
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
        zone_count, accepted = QInputDialog.getInt(
            self,
            "Adjust Zones",
            "Zones:",
            current_count,
            1,
            255,
            1,
        )
        if not accepted:
            return

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
            if strip is self.monitor_chips:
                self._refresh_led_mapping_zone_count()

    # ------------------------------------------------------------------ LED mapping

    def _toggle_led_mapping(self) -> None:
        self._mapping_expanded = not self._mapping_expanded

        if self._mapping_expanded:
            self._refresh_led_mapping_zone_count()
            self.mapping_toggle_button.setText("LED Mapping  ▼")
            self.led_mapping_container.setVisible(True)
            self._mapping_anim = animate_height(self.led_mapping_container, 400, duration=220)

            if self.controller.is_syncing():
                self._sync_was_running = True
                self._sync_mode_before = self.controller.get_current_sync_mode()
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
                    self._start_monitor_sync()
                elif self._sync_mode_before == "music":
                    self._start_music_sync()
                self._sync_mode_before = None

    # ------------------------------------------------------------------ brightness

    def _on_monitor_brightness(self, value: int) -> None:
        self.controller.set_monitor_brightness(value / 100.0)
        self.monitor_brightness_label.setText(f"{value}%")

    def _on_music_brightness(self, value: int) -> None:
        self.controller.set_music_brightness(value / 100.0)
        self.music_brightness_label.setText(f"{value}%")

    def _on_brightness_changed(self, mode: str, value: float) -> None:
        if mode == "monitor":
            self.monitor_brightness_slider.setValue(int(value * 100))
        elif mode == "music":
            self.music_brightness_slider.setValue(int(value * 100))

    # ------------------------------------------------------------------ start helpers

    def _start_monitor_sync(self) -> None:
        self.controller.start_monitor_sync(self.monitor_chips.selected_devices())

    def _start_music_sync(self) -> None:
        self.controller.start_music_sync(self.music_chips.selected_devices())

    # ------------------------------------------------------------------ active syncs

    def _refresh_active_syncs(self) -> None:
        # Wipe existing rows
        while self.active_syncs_layout.count():
            item = self.active_syncs_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        mode = self.controller.get_current_sync_mode()
        is_running = self.controller.is_syncing()

        if not is_running or mode is None:
            self.empty_active_label.setVisible(True)
            return

        device_names = [
            device.get("model", "Device")
            for device in self.controller.get_selected_devices()
        ]

        row = ActiveSyncRow(mode, device_names, self)
        row.stop_requested.connect(lambda _m: self.controller.stop_sync())
        self.active_syncs_layout.addWidget(row)
        self.empty_active_label.setVisible(False)
