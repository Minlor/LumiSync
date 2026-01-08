from __future__ import annotations

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QGroupBox,
    QFormLayout,
    QComboBox,
)


class SettingsPage(QWidget):
    def __init__(self, settings: QSettings, main_window=None):
        super().__init__(main_window)
        self.settings = settings
        self._main_window = main_window  # Store direct reference to main window

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        header = QLabel("Settings")
        header_font = QFont()
        header_font.setPointSize(16)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)

        # Appearance
        appearance_group = QGroupBox("Appearance")
        appearance_form = QFormLayout(appearance_group)

        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Dark", "dark_blue.xml")
        self.theme_combo.addItem("Light", "light_blue.xml")

        saved_theme = str(self.settings.value("theme", "dark_blue.xml"))
        idx = self.theme_combo.findData(saved_theme)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        appearance_form.addRow("Theme", self.theme_combo)

        layout.addWidget(appearance_group)

        # Display Sync (Monitor)
        monitor_group = QGroupBox("Display Sync")
        monitor_form = QFormLayout(monitor_group)

        self.display_combo = QComboBox()
        self._populate_displays()

        saved_display = int(self.settings.value("sync/monitor_display", 0))
        display_idx = self.display_combo.findData(saved_display)
        if display_idx >= 0:
            self.display_combo.setCurrentIndex(display_idx)
        monitor_form.addRow("Display", self.display_combo)

        layout.addWidget(monitor_group)
        layout.addStretch(1)

        # Apply behavior
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.display_combo.currentIndexChanged.connect(self._on_display_changed)

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

    def _on_theme_changed(self) -> None:
        theme = str(self.theme_combo.currentData())
        self.settings.setValue("theme", theme)
        if self._main_window is not None and hasattr(self._main_window, "apply_theme"):
            self._main_window.apply_theme()

    def _on_display_changed(self) -> None:
        display_index = int(self.display_combo.currentData() or 0)
        self.settings.setValue("sync/monitor_display", display_index)
        if self._main_window is not None and hasattr(self._main_window, "sync_controller"):
            try:
                self._main_window.sync_controller.set_monitor_display(display_index)
            except Exception:
                # Avoid crashing settings UI if controller isn't ready
                pass
