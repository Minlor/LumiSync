"""Settings page — sectioned, multi-device-ready scaffold."""

from __future__ import annotations

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..controllers.sync_controller import SYNC_SETTINGS_KEYS, load_sync_settings
from ...sync import audio
from ...config.options import SYNC


class SettingsPage(QWidget):
    def __init__(self, settings: QSettings, main_window=None):
        super().__init__(main_window)
        self.settings = settings
        self._main_window = main_window

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        header = QLabel("Settings")
        header.setProperty("role", "title")
        root.addWidget(header)

        root.addWidget(self._build_display_group())
        root.addWidget(self._build_defaults_group())
        root.addWidget(self._build_sync_tuning_group())
        root.addWidget(self._build_groups_group())
        root.addWidget(self._build_about_group())

        root.addStretch(1)

    # ---------------------------------------------------------------- groups

    def _build_display_group(self) -> QGroupBox:
        group = QGroupBox("Display Sync")
        form = QFormLayout(group)
        form.setSpacing(8)

        self.display_combo = QComboBox()
        self._populate_displays()
        saved_display = int(self.settings.value("sync/monitor_display", 0))
        idx = self.display_combo.findData(saved_display)
        if idx >= 0:
            self.display_combo.setCurrentIndex(idx)
        self.display_combo.currentIndexChanged.connect(self._on_display_changed)
        form.addRow("Monitor", self.display_combo)

        per_device = QComboBox()
        per_device.addItem("All devices use the same display", "shared")
        per_device.setEnabled(False)
        per_device.setProperty("disabledHint", True)
        per_device.setToolTip("Per-device display assignment ships with multi-device sync.")
        form.addRow("Assignment", per_device)

        return group

    def _build_defaults_group(self) -> QGroupBox:
        group = QGroupBox("Defaults")
        form = QFormLayout(group)
        form.setSpacing(8)

        # Default brightness — wires through to QSettings, future controllers can read it
        b_row = QHBoxLayout()
        self.default_brightness = QSlider(Qt.Orientation.Horizontal)
        self.default_brightness.setRange(10, 100)
        self.default_brightness.setValue(int(self.settings.value("defaults/brightness", 85)))
        self.default_brightness.valueChanged.connect(self._on_default_brightness)
        b_row.addWidget(self.default_brightness, 1)
        self.default_brightness_label = QLabel(f"{self.default_brightness.value()}%")
        self.default_brightness_label.setMinimumWidth(40)
        self.default_brightness_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        b_row.addWidget(self.default_brightness_label)
        form.addRow("Default brightness", b_row)

        per_dev_mapping = QCheckBox("Use per-device LED mapping (coming with multi-device sync)")
        per_dev_mapping.setEnabled(False)
        per_dev_mapping.setProperty("disabledHint", True)
        per_dev_mapping.setToolTip("Available once multi-device sync ships.")
        form.addRow("LED mapping", per_dev_mapping)

        return group

    def _build_sync_tuning_group(self) -> QGroupBox:
        group = QGroupBox("Sync Tuning")
        form = QFormLayout(group)
        form.setSpacing(8)

        # --- Monitor ---
        self.smoothing_slider, smoothing_row, self.smoothing_label = self._slider_row(
            5, 100, int(round(SYNC.smoothing * 100)), self._on_smoothing
        )
        self.smoothing_label.setText(f"{SYNC.smoothing:.2f}")
        form.addRow("Monitor smoothing", smoothing_row)

        self.saturation_slider, saturation_row, self.saturation_label = self._slider_row(
            100, 200, int(round(SYNC.saturation * 100)), self._on_saturation
        )
        self.saturation_label.setText(f"{SYNC.saturation:.2f}×")
        form.addRow("Color saturation", saturation_row)

        self.fps_slider, fps_row, self.fps_label = self._slider_row(
            10, 120, int(SYNC.monitor_fps), self._on_fps
        )
        self.fps_label.setText(f"{int(SYNC.monitor_fps)} fps")
        form.addRow("Max frame rate", fps_row)

        self.gamma_check = QCheckBox("Blend zone colors in linear light (recommended)")
        self.gamma_check.setChecked(bool(SYNC.gamma_correct))
        self.gamma_check.toggled.connect(self._on_gamma)
        form.addRow("Gamma", self.gamma_check)

        # --- Music ---
        self.palette_combo = QComboBox()
        for key in audio.PALETTES:
            self.palette_combo.addItem(audio.PALETTE_LABELS.get(key, key), key)
        current_palette = self.palette_combo.findData(SYNC.music_palette)
        if current_palette >= 0:
            self.palette_combo.setCurrentIndex(current_palette)
        self.palette_combo.currentIndexChanged.connect(self._on_palette)
        form.addRow("Music palette", self.palette_combo)

        self.gain_slider, gain_row, self.gain_label = self._slider_row(
            50, 400, int(round(SYNC.music_gain * 100)), self._on_gain
        )
        self.gain_label.setText(f"{SYNC.music_gain:.2f}×")
        form.addRow("Music sensitivity", gain_row)

        self.music_smoothing_slider, music_smoothing_row, self.music_smoothing_label = self._slider_row(
            5, 100, int(round(SYNC.music_smoothing * 100)), self._on_music_smoothing
        )
        self.music_smoothing_label.setText(f"{SYNC.music_smoothing:.2f}")
        form.addRow("Music smoothing", music_smoothing_row)

        return group

    def _slider_row(self, minimum, maximum, value, on_change):
        """Build a horizontal slider paired with a right-aligned value label."""
        row = QHBoxLayout()
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(max(minimum, min(maximum, value)))
        slider.valueChanged.connect(on_change)
        row.addWidget(slider, 1)
        label = QLabel()
        label.setMinimumWidth(48)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(label)
        return slider, row, label

    def _reload_sync(self) -> None:
        load_sync_settings(self.settings)

    def _on_smoothing(self, value: int) -> None:
        self.smoothing_label.setText(f"{value / 100:.2f}")
        self.settings.setValue(SYNC_SETTINGS_KEYS["smoothing"], value / 100)
        self._reload_sync()

    def _on_saturation(self, value: int) -> None:
        self.saturation_label.setText(f"{value / 100:.2f}×")
        self.settings.setValue(SYNC_SETTINGS_KEYS["saturation"], value / 100)
        self._reload_sync()

    def _on_fps(self, value: int) -> None:
        self.fps_label.setText(f"{value} fps")
        self.settings.setValue(SYNC_SETTINGS_KEYS["monitor_fps"], value)
        self._reload_sync()

    def _on_gamma(self, checked: bool) -> None:
        self.settings.setValue(SYNC_SETTINGS_KEYS["gamma_correct"], bool(checked))
        self._reload_sync()

    def _on_palette(self) -> None:
        key = self.palette_combo.currentData()
        if key:
            self.settings.setValue(SYNC_SETTINGS_KEYS["music_palette"], key)
            self._reload_sync()

    def _on_gain(self, value: int) -> None:
        self.gain_label.setText(f"{value / 100:.2f}×")
        self.settings.setValue(SYNC_SETTINGS_KEYS["music_gain"], value / 100)
        self._reload_sync()

    def _on_music_smoothing(self, value: int) -> None:
        self.music_smoothing_label.setText(f"{value / 100:.2f}")
        self.settings.setValue(SYNC_SETTINGS_KEYS["music_smoothing"], value / 100)
        self._reload_sync()

    def _build_groups_group(self) -> QGroupBox:
        group = QGroupBox("Sync Groups")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        info = QLabel(
            "Select devices in the Devices tab and use 'Save as Group' to create "
            "a group. Saved groups appear here."
        )
        info.setProperty("role", "subtle")
        info.setWordWrap(True)
        layout.addWidget(info)

        self._groups_list = QVBoxLayout()
        self._groups_list.setSpacing(4)
        layout.addLayout(self._groups_list)

        controller = getattr(self._main_window, "device_controller", None)
        if controller is not None:
            controller.groups_changed.connect(lambda *_: self._refresh_groups())
            self._refresh_groups()

        return group

    def _refresh_groups(self) -> None:
        controller = getattr(self._main_window, "device_controller", None)
        if controller is None:
            return
        # Clear existing rows.
        while self._groups_list.count():
            item = self._groups_list.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            elif item.layout() is not None:
                self._clear_layout(item.layout())

        groups = controller.get_groups()
        if not groups:
            empty = QLabel("No groups yet.")
            empty.setProperty("role", "hint")
            self._groups_list.addWidget(empty)
            return

        for grp in groups:
            row = QHBoxLayout()
            label = QLabel(f"{grp['name']}  ·  {len(grp.get('devices', []))} device(s)")
            row.addWidget(label)
            row.addStretch(1)
            delete_btn = QPushButton("Delete")
            delete_btn.setObjectName("LinkButton")
            delete_btn.clicked.connect(
                lambda _checked=False, name=grp["name"]: controller.delete_group(name)
            )
            row.addWidget(delete_btn)
            self._groups_list.addLayout(row)

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _build_about_group(self) -> QGroupBox:
        group = QGroupBox("About")
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        from ... import __version__ as _version  # local import to avoid cycles
        version_label = QLabel(f"<b>LumiSync</b> · v{_version}")
        layout.addWidget(version_label)

        desc = QLabel("Sync your Govee lights with your screen and audio.")
        desc.setProperty("role", "subtle")
        layout.addWidget(desc)

        self.update_status_label = QLabel("Updates have not been checked yet.")
        self.update_status_label.setProperty("role", "subtle")
        self.update_status_label.setWordWrap(True)
        layout.addWidget(self.update_status_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)

        self.check_updates_button = QPushButton("Check for updates")
        self.check_updates_button.clicked.connect(self._check_for_updates)
        buttons.addWidget(self.check_updates_button)

        self.open_release_button = QPushButton("Open Release")
        self.open_release_button.setObjectName("LinkButton")
        self.open_release_button.setVisible(False)
        self.open_release_button.clicked.connect(self._open_latest_release)
        buttons.addWidget(self.open_release_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        link = QPushButton("Open GitHub Repository")
        link.setObjectName("LinkButton")
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/Minlor/LumiSync")))
        layout.addWidget(link, alignment=Qt.AlignmentFlag.AlignLeft)

        self._latest_release_url = None
        self._connect_update_controller()

        return group

    # ---------------------------------------------------------------- helpers

    def _connect_update_controller(self) -> None:
        controller = getattr(self._main_window, "update_controller", None)
        if controller is None:
            self.check_updates_button.setEnabled(False)
            self.update_status_label.setText("Update checks are unavailable.")
            return

        controller.check_started.connect(self._on_update_check_started)
        controller.check_finished.connect(self._on_update_check_finished)
        if controller.last_result is not None:
            self._on_update_check_finished(controller.last_result)

    def _set_update_status(self, text: str, state: str | None = None) -> None:
        self.update_status_label.setText(text)
        self.update_status_label.setProperty("state", state or "")
        self.update_status_label.style().unpolish(self.update_status_label)
        self.update_status_label.style().polish(self.update_status_label)

    def _check_for_updates(self) -> None:
        controller = getattr(self._main_window, "update_controller", None)
        if controller is not None:
            controller.check_now()

    def _open_latest_release(self) -> None:
        if self._latest_release_url:
            QDesktopServices.openUrl(QUrl(self._latest_release_url))

    def _on_update_check_started(self) -> None:
        self.check_updates_button.setEnabled(False)
        self.open_release_button.setVisible(False)
        self._set_update_status("Checking GitHub releases...")

    def _on_update_check_finished(self, result) -> None:
        self.check_updates_button.setEnabled(True)

        if result.error:
            self._latest_release_url = None
            self.open_release_button.setVisible(False)
            self._set_update_status(f"Update check failed: {result.error}", "error")
            return

        if result.is_update_available and result.latest_version:
            self._latest_release_url = result.release_url
            self.open_release_button.setVisible(bool(result.release_url))
            self._set_update_status(
                f"Update available: v{result.latest_version}",
                "active",
            )
            return

        self._latest_release_url = None
        self.open_release_button.setVisible(False)
        self._set_update_status("LumiSync is up to date.", "idle")

    def _populate_displays(self) -> None:
        self.display_combo.clear()
        screens = list(QGuiApplication.screens())
        primary = QGuiApplication.primaryScreen()
        for i, screen in enumerate(screens):
            geo = screen.geometry()
            label = f"Display {i + 1}: {geo.width()}×{geo.height()}"
            if primary is not None and screen is primary:
                label += " (Primary)"
            self.display_combo.addItem(label, i)
        if not screens:
            self.display_combo.addItem("Default", 0)

    def _on_display_changed(self) -> None:
        display_index = int(self.display_combo.currentData() or 0)
        self.settings.setValue("sync/monitor_display", display_index)
        if self._main_window is not None and hasattr(self._main_window, "sync_controller"):
            try:
                self._main_window.sync_controller.set_monitor_display(display_index)
            except Exception:
                pass

    def _on_default_brightness(self, value: int) -> None:
        self.default_brightness_label.setText(f"{value}%")
        self.settings.setValue("defaults/brightness", value)
