"""Simple settings dialog.

This is intentionally small: it replaces the old menu-bar toggles with
settings accessible from the sidebar cog.
"""

from __future__ import annotations

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QVBoxLayout,
)


class SettingsDialog(QDialog):
    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.settings = settings

        self.setWindowTitle("Settings")
        self.setModal(True)

        root = QVBoxLayout(self)

        form = QFormLayout()

        # Theme
        self.light_theme_cb = QCheckBox("Use light theme")
        theme = str(self.settings.value("theme", "dark_blue.xml"))
        self.light_theme_cb.setChecked("light" in theme)
        form.addRow(self.light_theme_cb)

        # Window effects
        self.translucent_cb = QCheckBox("Translucent window background")
        self.translucent_cb.setChecked(bool(self.settings.value("ui/translucent", True, type=bool)))
        form.addRow(self.translucent_cb)

        self.win_backdrop_cb = QCheckBox("Enable Windows backdrop")
        self.win_backdrop_cb.setChecked(bool(self.settings.value("ui/windows_backdrop", False, type=bool)))
        form.addRow(self.win_backdrop_cb)

        self.backdrop_type = QComboBox()
        self.backdrop_type.addItem("Mica", "mica")
        self.backdrop_type.addItem("Tabbed", "tabbed")
        saved_backdrop_type = str(self.settings.value("ui/windows_backdrop_type", "mica"))
        idx = self.backdrop_type.findData(saved_backdrop_type)
        if idx >= 0:
            self.backdrop_type.setCurrentIndex(idx)
        form.addRow("Backdrop type", self.backdrop_type)

        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def accept(self) -> None:
        # Theme selection
        theme = "light_blue.xml" if self.light_theme_cb.isChecked() else "dark_blue.xml"
        self.settings.setValue("theme", theme)

        # Effects
        self.settings.setValue("ui/translucent", self.translucent_cb.isChecked())
        self.settings.setValue("ui/windows_backdrop", self.win_backdrop_cb.isChecked())
        self.settings.setValue("ui/windows_backdrop_type", self.backdrop_type.currentData())

        super().accept()


__all__ = ["SettingsDialog"]
