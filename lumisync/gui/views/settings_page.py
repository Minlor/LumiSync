"""Settings page — sectioned, multi-device-ready scaffold."""

from __future__ import annotations

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..controllers.sync_controller import SYNC_SETTINGS_KEYS, load_sync_settings
from ...config.options import SYNC
from ..widgets.product_controls import ProductComboBox, ProductSlider, ToggleSwitch


class SettingsPage(QWidget):
    def __init__(self, settings: QSettings, main_window=None):
        super().__init__(main_window)
        self.settings = settings
        self._main_window = main_window

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 28)
        root.setSpacing(18)

        page_header = QVBoxLayout()
        page_header.setSpacing(3)
        header = QLabel("Settings")
        header.setProperty("role", "title")
        page_header.addWidget(header)

        intro = QLabel("Personalize the app and tune how screen and music syncing behaves.")
        intro.setProperty("role", "pageDescription")
        intro.setWordWrap(True)
        page_header.addWidget(intro)
        root.addLayout(page_header)

        body = QHBoxLayout()
        body.setSpacing(18)

        self.section_nav = QListWidget()
        self.section_nav.setObjectName("SettingsSectionNav")
        self.section_nav.setFixedWidth(196)
        self.section_nav.setSpacing(2)
        self.section_nav.setAccessibleName("Settings sections")
        self.section_nav.addItems(
            ["Application", "Monitor Sync", "Music Sync", "Groups", "About"]
        )
        body.addWidget(self.section_nav)

        self.section_stack = QStackedWidget()
        self.section_stack.setObjectName("SettingsSectionStack")
        self.section_stack.addWidget(
            self._section_page(
                "Application",
                "Choose how LumiSync starts, closes, and blends into Windows.",
                self._build_application_group(),
            )
        )
        self.section_stack.addWidget(
            self._section_page(
                "Monitor Sync",
                "Choose a display and tune how its colors are sampled and sent.",
                self._build_display_group(),
                self._build_monitor_tuning_group(),
            )
        )
        self.section_stack.addWidget(
            self._section_page(
                "Music Sync",
                "Fine-tune how quickly audio-reactive lighting responds.",
                self._build_music_tuning_group(),
            )
        )
        self.section_stack.addWidget(
            self._section_page(
                "Groups",
                "Review reusable groups shared by Monitor and Music Sync.",
                self._build_groups_group(),
            )
        )
        self.section_stack.addWidget(
            self._section_page(
                "About",
                "Version, updates, and project links.",
                self._build_about_group(),
            )
        )
        body.addWidget(self.section_stack, 1)
        root.addLayout(body, 1)

        self.section_nav.currentRowChanged.connect(
            self.section_stack.setCurrentIndex
        )
        self.section_nav.setCurrentRow(0)

    @staticmethod
    def _section_page(
        title: str,
        description: str,
        *groups: QGroupBox,
    ) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("SettingsSectionScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        page = QWidget()
        page.setObjectName("SettingsSectionPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 0, 8, 8)
        layout.setSpacing(14)

        heading = QLabel(title)
        heading.setProperty("role", "inspectorTitle")
        layout.addWidget(heading)
        copy = QLabel(description)
        copy.setProperty("role", "pageDescription")
        copy.setWordWrap(True)
        layout.addWidget(copy)
        for group in groups:
            layout.addWidget(group)
        layout.addStretch(1)
        scroll.setWidget(page)
        return scroll

    # ---------------------------------------------------------------- groups

    def _build_application_group(self) -> QGroupBox:
        from ..utils import autostart

        group = QGroupBox("Application")
        form = QFormLayout(group)
        form.setSpacing(8)

        def _bool_setting(key: str, default: bool) -> bool:
            return str(self.settings.value(key, default)).lower() in ("true", "1")

        self.tray_check = ToggleSwitch(
            "Keep running when the window is closed"
        )
        self.tray_check.setChecked(_bool_setting("ui/minimize_to_tray", True))
        self.tray_check.toggled.connect(
            lambda checked: self.settings.setValue("ui/minimize_to_tray", bool(checked))
        )
        form.addRow("Tray", self.tray_check)

        import platform
        start_label = (
            "Start LumiSync when Windows starts"
            if platform.system() == "Windows"
            else "Start LumiSync when you log in"
        )
        self.autostart_check = ToggleSwitch(start_label)
        if autostart.is_supported():
            self.autostart_check.setChecked(autostart.is_enabled())
            self.autostart_check.toggled.connect(self._on_autostart_toggled)
        else:
            self.autostart_check.setEnabled(False)
            self.autostart_check.setToolTip(
                "Autostart isn't supported on this platform."
            )
        form.addRow("Startup", self.autostart_check)

        self.statusbar_check = ToggleSwitch("Show the status bar")
        self.statusbar_check.setChecked(_bool_setting("ui/status_bar", False))
        self.statusbar_check.toggled.connect(self._on_statusbar_toggled)
        form.addRow("Status bar", self.statusbar_check)

        self.window_material_combo = ProductComboBox()
        self.window_material_combo.addItem(
            "Acrylic — blurred desktop",
            "acrylic",
        )
        self.window_material_combo.addItem(
            "Mica — subtle wallpaper tint",
            "mica",
        )
        self.window_material_combo.addItem(
            "Solid Dark — no transparency",
            "solid",
        )
        self.window_material_combo.setToolTip(
            "Choose the background material used by the LumiSync window."
        )
        saved_material = str(
            self.settings.value("ui/window_material", "acrylic")
        ).lower()
        material_index = self.window_material_combo.findData(saved_material)
        self.window_material_combo.setCurrentIndex(max(0, material_index))
        self.window_material_combo.currentIndexChanged.connect(
            self._on_window_material_changed
        )
        form.addRow("Window material", self.window_material_combo)

        return group

    def _on_autostart_toggled(self, checked: bool) -> None:
        from ..utils import autostart

        if not autostart.set_enabled(bool(checked)):
            # Revert the checkbox if the registry write failed.
            self.autostart_check.blockSignals(True)
            self.autostart_check.setChecked(autostart.is_enabled())
            self.autostart_check.blockSignals(False)
            main_window = self._main_window
            if main_window is not None and hasattr(main_window, "show_error"):
                main_window.show_error("Error: could not update the Windows autostart entry.")

    def _on_statusbar_toggled(self, checked: bool) -> None:
        self.settings.setValue("ui/status_bar", bool(checked))
        if self._main_window is not None:
            self._main_window.statusBar().setVisible(bool(checked))

    def _on_window_material_changed(self) -> None:
        requested = str(self.window_material_combo.currentData() or "solid")
        if self._main_window is None:
            self.settings.setValue("ui/window_material", requested)
            return
        active = self._main_window.set_window_material(requested)
        self.set_window_material_value(active)

    def set_window_material_value(self, material: str) -> None:
        """Synchronize the selector without reapplying the material."""

        index = self.window_material_combo.findData(str(material).lower())
        if index < 0 or index == self.window_material_combo.currentIndex():
            return
        blocked = self.window_material_combo.blockSignals(True)
        self.window_material_combo.setCurrentIndex(index)
        self.window_material_combo.blockSignals(blocked)

    def _build_display_group(self) -> QGroupBox:
        group = QGroupBox("Display Sync")
        form = QFormLayout(group)
        form.setSpacing(8)

        self.display_combo = ProductComboBox()
        self._populate_displays()
        saved_display = int(self.settings.value("sync/monitor_display", 0))
        idx = self.display_combo.findData(saved_display)
        if idx >= 0:
            self.display_combo.setCurrentIndex(idx)
        self.display_combo.currentIndexChanged.connect(self._on_display_changed)
        form.addRow("Monitor", self.display_combo)

        return group

    def _build_monitor_tuning_group(self) -> QGroupBox:
        group = QGroupBox("Monitor Response")
        form = QFormLayout(group)
        form.setSpacing(8)

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

        self.gamma_check = ToggleSwitch(
            "Blend zone colors in linear light (recommended)"
        )
        self.gamma_check.setChecked(bool(SYNC.gamma_correct))
        self.gamma_check.toggled.connect(self._on_gamma)
        form.addRow("Gamma", self.gamma_check)

        return group

    def _build_music_tuning_group(self) -> QGroupBox:
        group = QGroupBox("Advanced Audio Response")
        form = QFormLayout(group)
        form.setSpacing(8)

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
        slider = ProductSlider(Qt.Orientation.Horizontal)
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
            "Create groups from Devices, Monitor Sync, or Music Sync. Saved "
            "groups appear as reusable targets in both sync pages."
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
            count = len(grp.get("devices", []))
            label = QLabel(
                f"{grp['name']}  ·  {count} device{'s' if count != 1 else ''}"
            )
            row.addWidget(label)
            row.addStretch(1)
            delete_btn = QPushButton("Delete")
            delete_btn.setObjectName("LinkButton")
            delete_btn.clicked.connect(
                lambda _checked=False, name=grp["name"]: self._confirm_delete_group(name)
            )
            row.addWidget(delete_btn)
            self._groups_list.addLayout(row)

    def _confirm_delete_group(self, name: str) -> None:
        controller = getattr(self._main_window, "device_controller", None)
        if controller is None:
            return
        reply = QMessageBox.question(
            self,
            "Delete Sync Group",
            f"Delete the sync group '{name}'? Devices in the group will not be removed.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            controller.delete_group(name)

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
