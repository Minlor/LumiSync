"""Settings page — sectioned, multi-device-ready scaffold."""

from __future__ import annotations

from PyQt6.QtCore import Qt, QSettings
from PyQt6.QtGui import QDesktopServices, QFont, QGuiApplication
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (
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

from ..theme import qcolor


class SettingsPage(QWidget):
    def __init__(self, settings: QSettings, main_window=None):
        super().__init__(main_window)
        self.settings = settings
        self._main_window = main_window

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        header = QLabel("Settings")
        f = QFont()
        f.setPointSize(18)
        f.setBold(True)
        header.setFont(f)
        root.addWidget(header)

        root.addWidget(self._build_display_group())
        root.addWidget(self._build_defaults_group())
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

    def _build_groups_group(self) -> QGroupBox:
        group = QGroupBox("Sync Groups")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        info = QLabel(
            "Group devices to sync them together with one click. "
            "Available with multi-device sync."
        )
        info.setProperty("disabledHint", True)
        info.setStyleSheet(
            f"color: {qcolor('text_disabled').name()}; font-style: italic; font-size: 9pt;"
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        new_group_btn = QPushButton("New Group")
        new_group_btn.setEnabled(False)
        new_group_btn.setProperty("disabledHint", True)
        new_group_btn.setToolTip("Available once multi-device sync ships.")
        layout.addWidget(new_group_btn, alignment=Qt.AlignmentFlag.AlignLeft)

        return group

    def _build_about_group(self) -> QGroupBox:
        group = QGroupBox("About")
        layout = QVBoxLayout(group)
        layout.setSpacing(6)

        from ... import __version__ as _version  # local import to avoid cycles
        version_label = QLabel(f"<b>LumiSync</b> · v{_version}")
        layout.addWidget(version_label)

        desc = QLabel("Sync your Govee lights with your screen and audio.")
        desc.setStyleSheet(f"color: {qcolor('text_dim').name()}; font-size: 9pt;")
        layout.addWidget(desc)

        link = QPushButton("Open GitHub Repository")
        link.setObjectName("LinkButton")
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/Minlor/LumiSync")))
        layout.addWidget(link, alignment=Qt.AlignmentFlag.AlignLeft)

        return group

    # ---------------------------------------------------------------- helpers

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
