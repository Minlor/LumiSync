"""Simple settings dialog.

This is intentionally small: it replaces the old menu-bar toggles with
settings accessible from the sidebar cog.
"""

from __future__ import annotations

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
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
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Dark", "dark_blue.xml")
        self.theme_combo.addItem("Light", "light_blue.xml")
        theme = str(self.settings.value("theme", "dark_blue.xml"))
        idx = self.theme_combo.findData(theme)
        if idx >= 0:
            self.theme_combo.setCurrentIndex(idx)
        form.addRow("Theme", self.theme_combo)

        root.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def accept(self) -> None:
        theme = str(self.theme_combo.currentData() or "dark_blue.xml")
        self.settings.setValue("theme", theme)

        super().accept()


__all__ = ["SettingsDialog"]
