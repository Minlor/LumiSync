"""Settings page — groups of settings cards behind four tabs.

A slim selector row under the title picks a tab: General · Sync (Monitor
tuning, Music tuning, Groups) · Updates · About. Only the selected tab's
groups show in the scroll area below. Every preference is a Fluent settings
card: an icon, a title with a short description, and the control on the right.
"""

from __future__ import annotations

import platform
import sys

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..controllers.sync_controller import SYNC_SETTINGS_KEYS, load_sync_settings
from ..utils import firewall
from ..widgets.product_controls import ProductComboBox, ProductSlider, ToggleSwitch
from ..widgets.selector_bar import TAB_PADDING_X, SelectorBar
from ..widgets.settings_card import SettingsCard, SettingsGroup, SettingsList

# Settings › General › Network access, per firewall status.
FIREWALL_DESCRIPTIONS = {
    firewall.STATUS_ALLOWED: "Allowed through Windows Firewall",
    firewall.STATUS_OFF: "Windows Firewall is off",
    firewall.STATUS_BLOCKED: "Blocked by Windows Firewall",
    firewall.STATUS_MISSING: "Not allowed through Windows Firewall yet",
    firewall.STATUS_PUBLIC: "Your network is set to Public in Windows",
}
FIREWALL_CHECKING = "Checking…"


def _bool_value(settings: QSettings, key: str, default: bool) -> bool:
    return str(settings.value(key, default)).lower() in ("true", "1")


REPOSITORY_URL = "https://github.com/Minlor/LumiSync"
AUTHOR_GITHUB_URL = "https://github.com/Minlor"
SUPPORT_URL = "https://ko-fi.com/Minlor"

# The tabs, in order: key, label, and the sections (groups) each one shows.
SETTINGS_TABS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("general", "General", ("general",)),
    ("sync", "Sync", ("monitor", "music", "groups")),
    ("updates", "Updates", ("updates",)),
    ("about", "About", ("about",)),
)
TAB_KEYS = tuple(key for key, _label, _sections in SETTINGS_TABS)
# Section key -> the tab that shows it.
SECTION_TABS = {section: key for key, _label, sections in SETTINGS_TABS for section in sections}
LAST_TAB_KEY = "settings/last_tab"


class SettingsPage(QWidget):
    def __init__(self, settings: QSettings, main_window=None):
        super().__init__(main_window)
        self.settings = settings
        self._main_window = main_window
        controller = getattr(main_window, "sync_controller", None)
        self._sync_settings = (
            controller.sync_settings
            if controller is not None
            else load_sync_settings(self.settings)
        )
        self.setObjectName("SettingsPage")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.header = QWidget()
        header_layout = QHBoxLayout(self.header)
        self._header_layout = header_layout
        header_layout.setContentsMargins(28, 24, 28, 6)
        title = QLabel("Settings")
        title.setProperty("role", "title")
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        root.addWidget(self.header)

        # The tab selector stays above the scroll area so it never scrolls
        # away. The first tab's text lines up with the title and the cards.
        self.tab_row = QWidget()
        self.tab_row.setObjectName("SettingsTabRow")
        tab_layout = QHBoxLayout(self.tab_row)
        self._tab_layout = tab_layout
        tab_layout.setContentsMargins(28 - TAB_PADDING_X, 0, 28, 8)
        tab_layout.setSpacing(0)
        self.tab_bar = SelectorBar([(key, label) for key, label, _sections in SETTINGS_TABS])
        self.tab_bar.setAccessibleName("Settings sections")
        tab_layout.addWidget(self.tab_bar, 1)
        root.addWidget(self.tab_row)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("SettingsScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self.scroll, 1)

        content = QWidget()
        content.setObjectName("SettingsContent")
        self.scroll.setWidget(content)
        body = QVBoxLayout(content)
        self._body_layout = body
        body.setContentsMargins(28, 4, 28, 28)
        body.setSpacing(28)

        self.general_group = self._build_general_group()
        self.monitor_tuning_group = self._build_monitor_tuning_group()
        self.music_tuning_group = self._build_music_tuning_group()
        self.groups_group = self._build_groups_group()
        self.updates_group = self._build_updates_group()
        self.about_group = self._build_about_group()
        for group in (
            self.general_group,
            self.monitor_tuning_group,
            self.music_tuning_group,
            self.groups_group,
            self.updates_group,
            self.about_group,
        ):
            body.addWidget(group)
        body.addStretch(1)

        self._sections: dict[str, SettingsGroup] = {
            "general": self.general_group,
            "monitor": self.monitor_tuning_group,
            "music": self.music_tuning_group,
            "groups": self.groups_group,
            "updates": self.updates_group,
            "about": self.about_group,
        }
        self._pending_section: str | None = None
        # The Updates tab's attention dot: set when an update shows up while
        # another tab is open, cleared when the Updates tab is opened.
        self._update_attention = False
        self._known_update_version: str | None = None
        self._seen_update_version: str | None = None

        saved = str(self.settings.value(LAST_TAB_KEY, "general") or "").strip().lower()
        self._apply_tab(saved if saved in TAB_KEYS else "general", remember=False)
        self.tab_bar.current_changed.connect(self._on_tab_changed)

        # Ctrl+Tab and Ctrl+Shift+Tab cycle the tabs while this page shows
        # (a window shortcut only fires while its widget is visible).
        self._tab_shortcuts = []
        for sequence, offset in (
            (QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_Tab), 1),
            (QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_Backtab), -1),
            (QKeySequence(Qt.Modifier.CTRL | Qt.Modifier.SHIFT | Qt.Key.Key_Backtab), -1),
            (QKeySequence(Qt.Modifier.CTRL | Qt.Modifier.SHIFT | Qt.Key.Key_Tab), -1),
        ):
            shortcut = QShortcut(sequence, self)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(lambda step=offset: self.cycle_tab(step))
            self._tab_shortcuts.append(shortcut)

        self._latest_release_url = None
        self._connect_update_controller()

    def groups(self) -> list[SettingsGroup]:
        """The page's sections, in order (General … About)."""
        return [
            self.general_group,
            self.monitor_tuning_group,
            self.music_tuning_group,
            self.groups_group,
            self.updates_group,
            self.about_group,
        ]

    # ------------------------------------------------------------------- tabs

    def tab_sections(self) -> list[tuple[str, str, list[SettingsGroup]]]:
        """The tabs in order: key, label and the groups each one shows."""
        return [
            (key, label, [self._sections[section] for section in sections])
            for key, label, sections in SETTINGS_TABS
        ]

    def current_tab(self) -> str:
        return self.tab_bar.current_key() or "general"

    def select_tab(self, key: str) -> None:
        """Show the tab ``key`` (general, sync, updates or about)."""
        if key not in TAB_KEYS:
            raise ValueError(f"Unknown settings tab: {key!r}")
        if key == self.current_tab():
            self._apply_tab(key)
        else:
            self.tab_bar.set_current(key)  # current_changed applies it

    def cycle_tab(self, offset: int) -> None:
        if self.isVisible():
            self.tab_bar.step(offset, wrap=True)

    def show_section(self, key: str) -> None:
        """Open the tab holding ``key`` and bring that section into view.

        ``key`` is a tab (general, sync, updates, about) or a section on the
        Sync tab (monitor, music, groups).
        """
        section = str(key).strip().lower()
        if section in TAB_KEYS:
            tab = section
            section = SETTINGS_TABS[TAB_KEYS.index(tab)][2][0]
        elif section in SECTION_TABS:
            tab = SECTION_TABS[section]
        else:
            raise ValueError(f"Unknown settings section: {key!r}")
        self.select_tab(tab)
        self._scroll_to_section(section)
        # A hidden page has no real geometry yet: scroll again once shown.
        self._pending_section = None if self.isVisible() else section

    def _on_tab_changed(self, key: str) -> None:
        self._apply_tab(key)

    def _apply_tab(self, key: str, *, remember: bool = True) -> None:
        if self.tab_bar.current_key() != key:
            blocked = self.tab_bar.blockSignals(True)
            self.tab_bar.set_current(key)
            self.tab_bar.blockSignals(blocked)
        shown = SETTINGS_TABS[TAB_KEYS.index(key)][2]
        for section, group in self._sections.items():
            group.setVisible(section in shown)
            # A tab with one group already names it: its heading would only
            # repeat the tab label. The Sync tab keeps its three headings.
            group.set_header_visible(len(shown) > 1)
        self._pending_section = None
        # Settle the scroll range now, so scrolling right after a tab change
        # (show_section, ensureWidgetVisible) uses this tab's height.
        self._flush_layout()
        self.scroll.verticalScrollBar().setValue(0)
        if key == "updates":
            self._update_attention = False
            self._seen_update_version = self._known_update_version
        self._refresh_updates_badge()
        if remember:
            self.settings.setValue(LAST_TAB_KEY, key)

    def _flush_layout(self) -> None:
        content = self.scroll.widget()
        if content is not None and content.layout() is not None:
            content.layout().activate()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest)

    def _scroll_to_section(self, section: str) -> None:
        group = self._sections[section]
        bar = self.scroll.verticalScrollBar()
        if section == SETTINGS_TABS[TAB_KEYS.index(SECTION_TABS[section])][2][0]:
            bar.setValue(0)
            return
        self._flush_layout()
        top = self._body_layout.contentsMargins().top()
        bar.setValue(max(0, min(bar.maximum(), group.y() - top)))

    def _scroll_to_pending_section(self) -> None:
        if not shiboken6.isValid(self):
            return
        section = self._pending_section
        if section is None or section not in SETTINGS_TABS[TAB_KEYS.index(self.current_tab())][2]:
            self._pending_section = None
            return
        self._scroll_to_section(section)
        if self.isVisible():
            self._pending_section = None

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if self._pending_section is not None:
            QTimer.singleShot(0, self._scroll_to_pending_section)

    def _set_update_attention(self, visible: bool) -> None:
        if not visible:
            self._known_update_version = None
            self._seen_update_version = None
        elif self.current_tab() == "updates":
            self._seen_update_version = self._known_update_version
        self._update_attention = bool(visible) and self.current_tab() != "updates"
        self._refresh_updates_badge()

    def _refresh_updates_badge(self) -> None:
        self.tab_bar.set_badge("updates", self._update_attention and self.current_tab() != "updates")

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if not hasattr(self, "_body_layout"):
            return
        compact = self.height() < 680 or self.width() < 760
        inset = 16 if compact else 28
        self._header_layout.setContentsMargins(inset, 16 if compact else 24, inset, 4 if compact else 6)
        self._tab_layout.setContentsMargins(inset - TAB_PADDING_X, 0, inset, 4 if compact else 8)
        self._body_layout.setContentsMargins(inset, 2 if compact else 4, inset, 16 if compact else 28)
        self._body_layout.setSpacing(20 if compact else 28)

    # ---------------------------------------------------------------- general

    def _build_general_group(self) -> SettingsGroup:
        from ..utils import autostart

        group = SettingsGroup("General")

        self.tray_check = ToggleSwitch()
        self.tray_check.setAccessibleName("Keep running in the tray")
        self.tray_check.setChecked(_bool_value(self.settings, "ui/minimize_to_tray", True))
        self.tray_check.toggled.connect(
            lambda checked: self.settings.setValue("ui/minimize_to_tray", bool(checked))
        )
        group.add_card(SettingsCard(
            "tray-run.svg", "Keep running in the tray",
            "Closing the window keeps LumiSync syncing from the notification area",
            self.tray_check,
        ))

        on_windows = platform.system() == "Windows"
        self.autostart_check = ToggleSwitch()
        start_title = "Start with Windows" if on_windows else "Start when you log in"
        self.autostart_check.setAccessibleName(start_title)
        if autostart.is_supported():
            self.autostart_check.setChecked(autostart.is_enabled())
            self.autostart_check.toggled.connect(self._on_autostart_toggled)
        else:
            self.autostart_check.setEnabled(False)
            self.autostart_check.setToolTip("Autostart isn't supported on this platform.")
        group.add_card(SettingsCard(
            "startup.svg", start_title,
            "Opens quietly in the tray when you sign in",
            self.autostart_check,
        ))

        self.window_material_combo = ProductComboBox()
        self.window_material_combo.setAccessibleName("Window material")
        self.window_material_combo.setMinimumWidth(200)
        self.window_material_combo.addItem("Acrylic", "acrylic")
        self.window_material_combo.addItem("Mica", "mica")
        self.window_material_combo.addItem("Solid", "solid")
        self.window_material_combo.setToolTip(
            "Choose the background material used by the LumiSync window."
        )
        saved_material = str(self.settings.value("ui/window_material", "acrylic")).lower()
        material_index = self.window_material_combo.findData(saved_material)
        self.window_material_combo.setCurrentIndex(max(0, material_index))
        self.window_material_combo.currentIndexChanged.connect(self._on_window_material_changed)
        group.add_card(SettingsCard(
            "layers.svg", "Window material",
            "Acrylic blurs the desktop, Mica tints with your wallpaper, Solid is opaque",
            self.window_material_combo,
        ))

        # Windows Firewall, for the installed app only (source runs are not
        # checked). The card and its button are parented by add_card before
        # any visibility change.
        self._firewall = getattr(self._main_window, "firewall_monitor", None)
        self.firewall_allow_button = QPushButton("Allow")
        self.firewall_allow_button.setAccessibleName("Allow LumiSync through Windows Firewall")
        self.firewall_allow_button.setToolTip(
            "Adds a Windows Firewall rule for Private and Domain networks (asks for administrator approval)"
        )
        self.firewall_allow_button.clicked.connect(self._allow_firewall)
        self.firewall_card = SettingsCard(
            "network.svg", "Network access", FIREWALL_CHECKING, self.firewall_allow_button,
        )
        group.add_card(self.firewall_card)
        monitor = self._firewall
        self.firewall_card.setVisible(monitor is not None and monitor.is_supported())
        if monitor is not None:
            monitor.changed.connect(lambda *_: self._update_firewall_card())
            monitor.allow_finished.connect(lambda *_: self._update_firewall_card())
        self._update_firewall_card()
        return group

    def _update_firewall_card(self) -> None:
        monitor = self._firewall
        if monitor is None:
            self.firewall_allow_button.setVisible(False)
            return
        state = monitor.state()
        verifying = monitor.is_verifying()
        text = FIREWALL_CHECKING if verifying else FIREWALL_DESCRIPTIONS.get(state.status, FIREWALL_CHECKING)
        self.firewall_card.set_description(text)
        self.firewall_card.description_label.setToolTip(state.detail if not verifying else "")
        self.firewall_allow_button.setVisible(state.can_allow and not verifying)
        self.firewall_allow_button.setEnabled(not monitor.is_allowing())

    def _allow_firewall(self) -> None:
        monitor = self._firewall
        if monitor is None or not monitor.state().can_allow:
            return
        handle = None
        window = self.window()
        if sys.platform == "win32" and window is not None and window.isVisible():
            try:
                handle = int(window.winId())  # The UAC prompt opens in front of it.
            except Exception:
                handle = None
        if monitor.allow(handle, self._on_firewall_allowed):
            self.firewall_allow_button.setEnabled(False)

    def _on_firewall_allowed(self, ok: bool, message: str) -> None:
        if not shiboken6.isValid(self):
            return
        self.firewall_allow_button.setEnabled(True)
        if ok or message == firewall.CANCELLED:
            return
        main_window = self._main_window
        if main_window is not None and hasattr(main_window, "show_notice"):
            main_window.show_notice(message, "error")

    def _on_autostart_toggled(self, checked: bool) -> None:
        from ..utils import autostart

        if not autostart.set_enabled(bool(checked)):
            # Revert the switch if the registry write failed.
            self.autostart_check.blockSignals(True)
            self.autostart_check.setChecked(autostart.is_enabled())
            self.autostart_check.blockSignals(False)
            self.autostart_check._animate_state(self.autostart_check.isChecked())
            main_window = self._main_window
            if main_window is not None and hasattr(main_window, "show_error"):
                main_window.show_error("Error: could not update the Windows autostart entry.")

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

    # ---------------------------------------------------------------- tuning

    def _slider_card(self, icon, title, description, minimum, maximum, value, on_change):
        """A card with a slider and its value on the right."""
        slider = ProductSlider(Qt.Orientation.Horizontal)
        slider.setRange(minimum, maximum)
        slider.setValue(max(minimum, min(maximum, value)))
        slider.setAccessibleName(title)
        slider.valueChanged.connect(on_change)
        slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        slider.setMinimumWidth(96)
        slider.setMaximumWidth(280)
        label = QLabel()
        label.setProperty("role", "valueLabel")
        label.setMinimumWidth(56)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        row.addWidget(slider, 1)
        row.addWidget(label)
        host.setMaximumWidth(352)
        card = SettingsCard(icon, title, description, host)
        return slider, card, label

    def _build_monitor_tuning_group(self) -> SettingsGroup:
        group = SettingsGroup("Monitor tuning")
        sync = self._sync_settings

        self.smoothing_slider, card, self.smoothing_label = self._slider_card(
            "smoothing.svg", "Smoothing", "Higher values fade between screen colors more gently",
            5, 100, int(round(sync.smoothing * 100)), self._on_smoothing,
        )
        self.smoothing_label.setText(f"{sync.smoothing:.2f}")
        group.add_card(card)

        self.saturation_slider, card, self.saturation_label = self._slider_card(
            "droplet.svg", "Color saturation", "Strengthens the colors sent to the lights",
            100, 200, int(round(sync.saturation * 100)), self._on_saturation,
        )
        self.saturation_label.setText(f"{sync.saturation:.2f}×")
        group.add_card(card)

        self.fps_slider, card, self.fps_label = self._slider_card(
            "gauge.svg", "Frame rate", "The most screen frames captured and sent each second",
            10, 120, int(sync.monitor_fps), self._on_fps,
        )
        self.fps_label.setText(f"{int(sync.monitor_fps)} fps")
        group.add_card(card)

        self.gamma_check = ToggleSwitch()
        self.gamma_check.setAccessibleName("Blend zone colors in linear light")
        self.gamma_check.setChecked(bool(sync.gamma_correct))
        self.gamma_check.toggled.connect(self._on_gamma)
        group.add_card(SettingsCard(
            "sun.svg", "Gamma-correct blending",
            "Blend zone colors in linear light (recommended)",
            self.gamma_check,
        ))
        return group

    def _build_music_tuning_group(self) -> SettingsGroup:
        group = SettingsGroup("Music tuning")
        sync = self._sync_settings

        self.gain_slider, card, self.gain_label = self._slider_card(
            "waveform.svg", "Sensitivity", "How strongly quiet passages drive the lights",
            50, 400, int(round(sync.music_gain * 100)), self._on_gain,
        )
        self.gain_label.setText(f"{sync.music_gain:.2f}×")
        group.add_card(card)

        self.music_smoothing_slider, card, self.music_smoothing_label = self._slider_card(
            "smoothing.svg", "Smoothing", "Higher values soften fast changes between beats",
            5, 100, int(round(sync.music_smoothing * 100)), self._on_music_smoothing,
        )
        self.music_smoothing_label.setText(f"{sync.music_smoothing:.2f}")
        group.add_card(card)
        return group

    def _reload_sync(self) -> None:
        self._sync_settings = load_sync_settings(self.settings)
        controller = getattr(self._main_window, "sync_controller", None)
        if controller is not None:
            controller.apply_sync_settings(self._sync_settings)

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

    # ---------------------------------------------------------------- groups

    def _build_groups_group(self) -> SettingsGroup:
        group = SettingsGroup("Groups")
        self._groups_list = SettingsList()
        group.add_card(self._groups_list)
        self._groups_empty = SettingsCard(
            "group.svg", "No groups yet",
            "Select lights on Monitor Sync or Music Sync and choose Save as group. "
            "Saved groups can be applied on both pages.",
        )
        group.add_card(self._groups_empty)
        self._groups_list.hide()

        controller = getattr(self._main_window, "device_controller", None)
        if controller is not None:
            controller.groups_changed.connect(lambda *_: self._refresh_groups())
            self._refresh_groups()
        return group

    def _refresh_groups(self) -> None:
        controller = getattr(self._main_window, "device_controller", None)
        if controller is None:
            return
        self._groups_list.clear()
        groups = controller.get_groups()
        self._groups_list.setVisible(bool(groups))
        self._groups_empty.setVisible(not groups)
        for grp in groups:
            count = len(grp.get("devices", []))
            delete_btn = QPushButton("Delete")
            delete_btn.setAccessibleName(f"Delete the group {grp['name']}")
            delete_btn.clicked.connect(
                lambda _checked=False, name=grp["name"]: self._confirm_delete_group(name)
            )
            row = SettingsCard(
                "group.svg", str(grp["name"]),
                f"{count} light{'s' if count != 1 else ''}",
                delete_btn,
                in_list=True,
            )
            self._groups_list.add_row(row)

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

    # --------------------------------------------------------------- updates

    def _build_updates_group(self) -> SettingsGroup:
        group = SettingsGroup("Updates")

        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.open_release_button = QPushButton("Release notes")
        self.open_release_button.setObjectName("LinkButton")
        self.open_release_button.setVisible(False)
        self.open_release_button.clicked.connect(self._open_latest_release)
        row.addWidget(self.open_release_button)
        self.install_update_button = QPushButton("Update now")
        self.install_update_button.setProperty("role", "primary")
        self.install_update_button.setVisible(False)
        self.install_update_button.clicked.connect(self._install_update)
        row.addWidget(self.install_update_button)
        self.check_updates_button = QPushButton("Check for updates")
        self.check_updates_button.clicked.connect(self._check_for_updates)
        row.addWidget(self.check_updates_button)

        self.update_card = SettingsCard(
            "refresh.svg", "LumiSync updates", "Updates have not been checked yet.", actions,
        )
        self.update_status_label = self.update_card.description_label
        group.add_card(self.update_card)

        self.auto_update_toggle = ToggleSwitch()
        self.auto_update_toggle.setAccessibleName("Download updates automatically")
        on_linux = sys.platform.startswith("linux")
        self.auto_update_toggle.setToolTip(
            "Downloads and installs a verified update when one is found; "
            "the new version starts the next time LumiSync starts."
            if on_linux
            else "Downloads a verified update when one is found; installation still asks first."
        )
        self.auto_update_toggle.setChecked(_bool_value(self.settings, "updates/auto_download", False))
        self.auto_update_toggle.toggled.connect(
            lambda checked: self.settings.setValue("updates/auto_download", bool(checked))
        )
        group.add_card(SettingsCard(
            "download.svg", "Download updates automatically",
            "Restarting still asks first" if on_linux else "Installing still asks first",
            self.auto_update_toggle,
        ))
        return group

    # ----------------------------------------------------------------- about

    def _build_about_group(self) -> SettingsGroup:
        from ... import __version__ as _version  # local import to avoid cycles

        group = SettingsGroup("About")
        self.about_version_label = QLabel(f"Version {_version}")
        self.about_version_label.setProperty("role", "valueLabel")
        group.add_card(SettingsCard(
            "info.svg", "LumiSync",
            "Control Govee, Tuya and LSC lights, switches and smart plugs. Sync local "
            "lights with your screen or music and draw on iDotMatrix panels.",
            self.about_version_label,
        ))

        links = SettingsList()
        self.repository_button = QPushButton("Open")
        self.repository_button.setObjectName("LinkButton")
        self.repository_button.setAccessibleName("Open the GitHub repository")
        self.repository_button.clicked.connect(self._open_repository)
        links.add_row(SettingsCard(
            "github.svg", "GitHub repository", "Source code, issues and release notes",
            self.repository_button, in_list=True,
        ))
        self.author_button = QPushButton("Open")
        self.author_button.setObjectName("LinkButton")
        self.author_button.setAccessibleName("Open Minlor's GitHub profile")
        self.author_button.clicked.connect(self._open_author_profile)
        links.add_row(SettingsCard(
            "github.svg", "Minlor on GitHub", "Other projects by the author of LumiSync",
            self.author_button, in_list=True,
        ))
        self.support_button = QPushButton("Open")
        self.support_button.setObjectName("LinkButton")
        self.support_button.setAccessibleName("Open the Ko-fi page to support LumiSync")
        self.support_button.clicked.connect(self._open_support)
        links.add_row(SettingsCard(
            "kofi.svg", "Support on Ko-fi", "LumiSync is free. A tip helps keep it going",
            self.support_button, in_list=True,
        ))
        self.account_setup_button = QPushButton("Open")
        self.account_setup_button.setObjectName("LinkButton")
        self.account_setup_button.setAccessibleName("Open the account setup guide")
        self.account_setup_button.clicked.connect(self._open_account_setup)
        links.add_row(SettingsCard(
            "link.svg", "Account setup", "How to link Govee, Tuya and LSC accounts",
            self.account_setup_button, in_list=True,
        ))
        self.open_logs_button = QPushButton("Open logs folder")
        self.open_logs_button.setToolTip("Open connection logs and crash diagnostics")
        self.open_logs_button.clicked.connect(self._open_logs)
        links.add_row(SettingsCard(
            "folder.svg", "Logs", "Connection logs and crash diagnostics for bug reports",
            self.open_logs_button, in_list=True,
        ))
        group.add_card(links)

        credit = QLabel("Created by Minlor · Open source under the MIT license.")
        credit.setProperty("role", "hint")
        credit.setWordWrap(True)
        credit.setContentsMargins(2, 6, 0, 0)
        group.add_widget(credit)
        return group

    # ---------------------------------------------------------------- helpers

    def _open_account_setup(self) -> None:
        QDesktopServices.openUrl(QUrl("https://github.com/Minlor/LumiSync#account-setup"))

    def _open_repository(self) -> None:
        QDesktopServices.openUrl(QUrl(REPOSITORY_URL))

    def _open_author_profile(self) -> None:
        QDesktopServices.openUrl(QUrl(AUTHOR_GITHUB_URL))

    def _open_support(self) -> None:
        QDesktopServices.openUrl(QUrl(SUPPORT_URL))

    def _open_logs(self) -> None:
        from ...utils.logging import get_logs_directory
        QDesktopServices.openUrl(QUrl.fromLocalFile(get_logs_directory()))

    def _connect_update_controller(self) -> None:
        controller = getattr(self._main_window, "update_controller", None)
        if controller is None:
            self.check_updates_button.setEnabled(False)
            self.update_status_label.setText("Update checks are unavailable.")
            return

        controller.check_started.connect(self._on_update_check_started)
        controller.check_finished.connect(self._on_update_check_finished)
        download_finished = getattr(controller, "download_finished", None)
        if download_finished is not None:
            download_finished.connect(self._on_update_downloaded)
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

    def _install_update(self) -> None:
        window = self._main_window
        if window is not None and hasattr(window, "show_update_dialog"):
            window.show_update_dialog()

    def _on_update_downloaded(self, _path: str) -> None:
        self._set_update_attention(True)
        self.install_update_button.setText("Restart && update")
        self.install_update_button.setVisible(True)
        if sys.platform.startswith("linux"):
            # The AppImage was already swapped; only the restart is left.
            text = "The update is downloaded, verified and installed. Restart to finish updating."
        else:
            text = "The update is downloaded and verified. Restart to install it."
        self._set_update_status(text, "active")

    def _open_latest_release(self) -> None:
        if self._latest_release_url:
            QDesktopServices.openUrl(QUrl(self._latest_release_url))

    def _on_update_check_started(self) -> None:
        self.check_updates_button.setEnabled(False)
        self.open_release_button.setVisible(False)
        self.install_update_button.setVisible(False)
        self._set_update_status("Checking GitHub releases...")

    def _on_update_check_finished(self, result) -> None:
        self.check_updates_button.setEnabled(True)

        if result.error:
            # No update is offered now; a later check of a seen version stays quiet.
            self._update_attention = False
            self._refresh_updates_badge()
            self._latest_release_url = None
            self.open_release_button.setVisible(False)
            self.install_update_button.setVisible(False)
            self._set_update_status(f"Update check failed: {result.error}", "error")
            return

        if result.is_update_available and result.latest_version:
            self._known_update_version = str(result.latest_version)
            # A periodic recheck of a version already seen stays quiet.
            if self._known_update_version != self._seen_update_version:
                self._set_update_attention(True)
            self._latest_release_url = result.release_url
            self.open_release_button.setVisible(bool(result.release_url))
            controller = getattr(self._main_window, "update_controller", None)
            ready = bool(getattr(controller, "ready_installer", None))
            self.install_update_button.setText(
                "Restart && update" if ready else "Update now"
            )
            self.install_update_button.setVisible(True)
            current = getattr(result, "current_version", None)
            suffix = f" (you have v{current})" if current else ""
            self._set_update_status(
                f"LumiSync v{result.latest_version} is available{suffix}.",
                "active",
            )
            return

        self._set_update_attention(False)
        self._latest_release_url = None
        self.open_release_button.setVisible(False)
        self.install_update_button.setVisible(False)
        self._set_update_status("LumiSync is up to date.", "idle")
