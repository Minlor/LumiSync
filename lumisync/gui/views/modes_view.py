"""Monitor Sync and Music Sync pages.

Each page has the same frame: a header with the page title and its Start/Stop
action, then Windows-Settings-style groups: the "Lights" list and the mode's
own groups ("Output", "Reaction"), each a stack of Fluent settings cards.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from PySide6.QtCore import QSize, Qt

from ..app_settings import app_settings
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ... import connection
from ...drivers.registry import capabilities_for_device, is_cloud
from ...sync import audio
from ..controllers.device_controller import DeviceController
from ..controllers.sync_controller import SyncController
from ..theme import qcolor
from ..widgets.device_chip import DeviceChipStrip, device_id
from ..widgets.fluent import svg_pixmap
from ..widgets.led_mapping_widget import LedMappingWidget
from ..widgets.product_controls import ProductComboBox, ProductSlider, ToggleSwitch
from ..widgets.settings_card import SettingsCard, SettingsExpander, SettingsGroup


ACCENT_ON = "#08101F"
SELECT_LIGHT_HINT = "Select at least one light to start."


def account_only_lights(devices: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Lights that reach LumiSync only through a vendor account.

    These cannot receive live sync frames, so they never appear as targets.
    Plugs, switches and appliances are not lights and are left out.
    """
    found = []
    for device in devices:
        if device.get("device_kind") in ("smart_plug", "light_switch") or not is_cloud(device):
            continue
        try:
            cap = capabilities_for_device(device)
        except Exception:
            continue
        if cap.supports_streaming or cap.is_appliance:
            continue
        found.append(device)
    return found


def account_only_note(devices: List[Dict[str, Any]]) -> str:
    """One line explaining why account-only lights are missing (issue #53)."""
    if not devices:
        return ""
    first = str(devices[0].get("name") or devices[0].get("model") or "A light")
    others = len(devices) - 1
    subject = (
        f"“{first}” is"
        if not others
        else f"“{first}” and {others} other light{'s' if others != 1 else ''} are"
    )
    return (
        f"{subject} connected through an account only and can't sync live. "
        "Turn on LAN Control in the vendor app, then Find devices."
    )


class NoteLabel(QLabel):
    """A wrapped secondary-text note with a small leading icon."""

    def __init__(self, icon: str = "info.svg", icon_color: str = "fl_text_secondary"):
        super().__init__()
        self._icon = icon
        self._icon_color = icon_color
        self.setProperty("role", "subtle")
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setContentsMargins(24, 0, 0, 0)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().paintEvent(event)
        painter = QPainter(self)
        line = self.fontMetrics().height()
        y = max(0, (line - 16) // 2)
        painter.drawPixmap(2, y, svg_pixmap(self._icon, qcolor(self._icon_color), 16, widget=self))


def notify_user(view: Any, message: str, kind: str = "info") -> None:
    """Tell the user about an action they just took.

    The status bar is optional (and hidden by default), so feedback the user
    must see also goes to the window's notices (toasts). ``view`` only needs
    a ``controller`` with ``status_updated``; ``window()`` is optional.
    """
    view.controller.status_updated.emit(message)
    window_getter = getattr(view, "window", None)
    window = window_getter() if callable(window_getter) else None
    notice = getattr(window, "show_notice", None)
    if callable(notice):
        notice(message, kind)


def _button_icon(name: str, color: str) -> QIcon:
    """A 14 px glyph with 6 px of breathing room before the button text."""
    glyph = svg_pixmap(name, QColor(color), 14)
    ratio = glyph.devicePixelRatio()
    padded = QPixmap(int(20 * ratio), int(14 * ratio))
    padded.setDevicePixelRatio(ratio)
    padded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(padded)
    painter.drawPixmap(0, 0, glyph)
    painter.end()
    return QIcon(padded)


class ModesView(QWidget):
    def __init__(
        self,
        sync_controller: SyncController,
        device_controller: Optional[DeviceController] = None,
        mode: Optional[str] = None,
    ):
        super().__init__()
        self.setObjectName("ModesView")
        self.controller = sync_controller
        self.device_controller = device_controller
        self.mode = mode if mode in {"monitor", "music"} else None

        self._mapping_expanded = False
        self._sync_was_running = False
        self._sync_mode_before: Optional[str] = None
        self._paused_devices: List[dict] = []
        self._action_hints: Dict[str, QLabel] = {}
        self._account_notes: Dict[str, QLabel] = {}

        self._build_ui()
        self._connect_signals()
        self._refresh_chip_strips()

    # ------------------------------------------------------------------ build

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        page_title = (
            "Monitor Sync" if self.mode == "monitor"
            else "Music Sync" if self.mode == "music"
            else "Sync Modes"
        )
        # The header stays outside the scroll area so Start/Stop is always
        # reachable, however far the controls below are scrolled.
        self.header = QWidget()
        self.header.setObjectName("SyncPageHeader")
        header_layout = QHBoxLayout(self.header)
        self._header_layout = header_layout
        header_layout.setContentsMargins(28, 24, 28, 16)
        header_layout.setSpacing(12)
        heading = QLabel(page_title)
        heading.setProperty("role", "title")
        header_layout.addWidget(heading, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addStretch(1)
        outer.addWidget(self.header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll, 1)

        content = QWidget()
        self.scroll.setWidget(content)
        root = QVBoxLayout(content)
        self._content_layout = root
        root.setContentsMargins(28, 4, 28, 28)
        root.setSpacing(28)

        if self.mode == "monitor":
            self.monitor_group = self._build_monitor_group()
            root.addWidget(self.monitor_group)
        elif self.mode == "music":
            self.music_group = self._build_music_group()
            root.addWidget(self.music_group)
        else:
            # Legacy combined view: both modes stacked on one page.
            self.monitor_group = self._build_monitor_group()
            self.music_group = self._build_music_group()
            root.addWidget(self.monitor_group)
            root.addWidget(self.music_group)
        root.addStretch(1)

        if self.mode:
            button = self.monitor_start_button if self.mode == "monitor" else self.music_start_button
            header_layout.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
            self.action_hint = self._action_hints[self.mode]
            self.account_only_note = self._account_notes[self.mode]

    @staticmethod
    def _section() -> QWidget:
        section = QWidget()
        section.setObjectName("SyncSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(28)
        return section

    def _panel_title(self, layout: QVBoxLayout, text: str) -> None:
        # Only the combined legacy view stacks both modes on one page.
        if self.mode is None:
            title = QLabel(text)
            title.setProperty("role", "header")
            layout.addWidget(title)

    def _make_start_button(self, mode: str) -> QPushButton:
        button = QPushButton("Start Monitor Sync" if mode == "monitor" else "Start Music Sync")
        button.setObjectName("Primary")
        button.setProperty("role", "primary")
        button.setMinimumWidth(176)
        button.setIconSize(QSize(20, 14))
        button.setIcon(_button_icon("play.svg", ACCENT_ON))
        button.clicked.connect(
            self._handle_monitor_action if mode == "monitor" else self._handle_music_action
        )
        return button

    def _build_lights_section(self, mode: str, layout: QVBoxLayout) -> tuple[DeviceChipStrip, QPushButton]:
        lights = QWidget()
        lights.setObjectName("LightsSection")
        lights_layout = QVBoxLayout(lights)
        lights_layout.setContentsMargins(0, 0, 0, 0)
        lights_layout.setSpacing(8)

        strip = DeviceChipStrip("Lights")
        strip.selection_changed.connect(
            lambda ids, target_mode=mode: self._on_mode_devices_changed(target_mode, ids)
        )
        save_button = QPushButton("Save as group")
        save_button.setObjectName("LinkButton")
        save_button.setToolTip("Save the selected lights as a group for Monitor and Music Sync")
        save_button.setAccessibleName("Save the selected lights as a group")
        save_button.clicked.connect(lambda: self._save_targets_as_group(strip))
        strip.add_action(save_button)
        lights_layout.addWidget(strip)

        hint = NoteLabel("info.svg", "fl_caution")
        hint.hide()
        lights_layout.addWidget(hint)
        self._action_hints[mode] = hint

        note = NoteLabel("info.svg", "fl_text_secondary")
        note.setAccessibleName("Lights that cannot sync live")
        note.hide()
        lights_layout.addWidget(note)
        self._account_notes[mode] = note
        layout.addWidget(lights)
        return strip, save_button

    @staticmethod
    def _value_label(value: int) -> QLabel:
        label = QLabel(f"{value}%")
        label.setProperty("role", "valueLabel")
        label.setMinimumWidth(44)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return label

    @staticmethod
    def _slider_control(slider: ProductSlider, value_label: QLabel) -> QWidget:
        host = QWidget()
        host.setObjectName("SliderControl")
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        slider.setMinimumWidth(96)
        slider.setMaximumWidth(280)
        row.addWidget(slider, 1)
        row.addWidget(value_label)
        host.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        host.setMaximumWidth(340)
        return host

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if not hasattr(self, "_content_layout"):
            return
        compact = self.height() < 680
        narrow = self.width() < 760
        inset = 16 if compact or narrow else 28
        self._header_layout.setContentsMargins(inset, 16 if compact else 24, inset, 12 if compact else 16)
        self._content_layout.setContentsMargins(inset, 2 if compact else 4, inset, 16 if compact else 28)
        self._content_layout.setSpacing(20 if compact else 28)
        for name in ("monitor_group", "music_group"):
            if hasattr(self, name):
                getattr(self, name).layout().setSpacing(20 if compact else 28)
        for _mode, strip in self._strips():
            strip.set_compact(compact)
        if hasattr(self, "_reaction_picker"):
            # Very narrow pages pick the style from a dropdown; otherwise the
            # tiles reflow into as many balanced columns as fit.
            self._reaction_picker.setVisible(not narrow)
            self.music_reaction_combo.setVisible(narrow)
            count = len(self.music_reaction_buttons)
            available = max(200, self.width() - inset * 2 - 2 * 16 - 2)
            needed = max(button.sizeHint().width() for button in self.music_reaction_buttons.values())
            columns = max(2, min(count, int((available + 8) / (needed + 8))))
            rows = -(-count // columns)
            columns = -(-count // rows)
            if columns != self._reaction_columns:
                self._reaction_columns = columns
                for index, button in enumerate(self.music_reaction_buttons.values()):
                    self._reaction_grid.removeWidget(button)
                    self._reaction_grid.addWidget(button, index // columns, index % columns)
                for column in range(count):
                    self._reaction_grid.setColumnStretch(column, 1 if column < columns else 0)

    def _build_monitor_group(self) -> QWidget:
        group = self._section()
        layout = group.layout()
        assert isinstance(layout, QVBoxLayout)
        self._panel_title(layout, "Monitor Sync")

        self.monitor_chips, self.monitor_group_button = self._build_lights_section("monitor", layout)

        output = SettingsGroup("Output")
        self.display_combo = ProductComboBox()
        self.display_combo.setAccessibleName("Display to mirror")
        self.display_combo.setToolTip("The screen whose colors are sent to the lights")
        self.display_combo.setMinimumWidth(200)
        self.display_combo.setMaximumWidth(340)
        self.display_combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._populate_displays()
        getter = getattr(self.controller, "get_monitor_display", None)
        saved_display = (
            int(getter()) if getter is not None
            else int(app_settings().value("sync/monitor_display", 0) or 0)
        )
        index = self.display_combo.findData(saved_display)
        if index >= 0:
            self.display_combo.setCurrentIndex(index)
        self.display_combo.currentIndexChanged.connect(self._on_display_changed)
        self.display_card = output.add_card(SettingsCard(
            "screen.svg", "Display", "The screen whose colors are sent to the lights",
            self.display_combo,
        ))

        self.monitor_brightness_slider = ProductSlider(Qt.Orientation.Horizontal)
        self.monitor_brightness_slider.setRange(10, 100)
        self.monitor_brightness_slider.setAccessibleName("Monitor sync brightness")
        self.monitor_brightness_slider.setToolTip(
            "Scales every frame. 100% uses the strip's full range; the default is 75%."
        )
        self.monitor_brightness_slider.setValue(int(self.controller.get_monitor_brightness() * 100))
        self.monitor_brightness_slider.valueChanged.connect(self._on_monitor_brightness)
        self.monitor_brightness_label = self._value_label(self.monitor_brightness_slider.value())
        output.add_card(SettingsCard(
            "sun.svg", "Brightness", "Scales every frame; the default is 75%",
            self._slider_control(self.monitor_brightness_slider, self.monitor_brightness_label),
        ))

        # Brightness boost: lifts midtones without changing hue. This is a
        # tone-map, not an overdrive; 255 per channel is still the ceiling.
        self.monitor_boost_slider = ProductSlider(Qt.Orientation.Horizontal)
        self.monitor_boost_slider.setRange(100, 150)
        self.monitor_boost_slider.setSingleStep(5)
        self.monitor_boost_slider.setPageStep(10)
        self.monitor_boost_slider.setAccessibleName("Monitor sync brightness boost")
        self.monitor_boost_slider.setToolTip(
            "Lifts dim and midtone content while preserving hue. "
            "100% leaves colors untouched; higher values trade highlight "
            "headroom for a brighter ambient scene."
        )
        boost_getter = getattr(self.controller, "get_monitor_boost", None)
        boost_value = float(boost_getter()) if boost_getter is not None else 1.0
        self.monitor_boost_slider.setValue(int(round(boost_value * 100)))
        self.monitor_boost_slider.valueChanged.connect(self._on_monitor_boost)
        self.monitor_boost_label = self._value_label(self.monitor_boost_slider.value())
        output.add_card(SettingsCard(
            "boost.svg", "Boost", "Lifts dim and midtone scenes while keeping their hue",
            self._slider_control(self.monitor_boost_slider, self.monitor_boost_label),
        ))

        self.monitor_zones_button = QPushButton("Adjust…")
        self.monitor_zones_button.setMinimumWidth(96)
        self.monitor_zones_button.clicked.connect(
            lambda: self._adjust_zones_for_strip(self.monitor_chips)
        )
        output.add_card(SettingsCard(
            "zones.svg", "Zones", "How many LED zones the selected lights have",
            self.monitor_zones_button,
        ))

        # Advanced: the per-zone screen layout expands inside its own card.
        settings = app_settings()
        self.led_mapping_widget = LedMappingWidget(settings, sync_controller=self.controller)
        self.led_mapping_widget.mapping_changed.connect(lambda *_: None)
        self.led_layout_expander = SettingsExpander(
            "led-layout.svg", "LED layout",
            "Match each LED zone to a region of the display",
            content=self.led_mapping_widget,
        )
        self.led_layout_expander.set_content_margins(16, 16, 16, 16)
        self.mapping_toggle_button = self.led_layout_expander.header
        self.mapping_toggle_button.setAccessibleName("LED layout")
        self.mapping_toggle_button.clicked.connect(self._toggle_led_mapping)
        self.led_mapping_container = self.led_layout_expander.body
        output.add_card(self.led_layout_expander)
        layout.addWidget(output)

        self.monitor_start_button = self._make_start_button("monitor")
        if self.mode is None:
            layout.addWidget(self.monitor_start_button, 0, Qt.AlignmentFlag.AlignLeft)
        return group

    def _build_music_group(self) -> QWidget:
        group = self._section()
        layout = group.layout()
        assert isinstance(layout, QVBoxLayout)
        self._panel_title(layout, "Music Sync")

        self.music_chips, self.music_group_button = self._build_lights_section("music", layout)

        reaction_group = SettingsGroup("Reaction")
        current_reaction = self.controller.get_music_reaction()
        style_card = QFrame()
        style_card.setObjectName("SettingsCard")
        style_card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        style_layout = QVBoxLayout(style_card)
        style_layout.setContentsMargins(17, 13, 17, 17)
        style_layout.setSpacing(14)
        style_head = QHBoxLayout()
        style_head.setSpacing(16)
        icon_label = QLabel()
        icon_label.setPixmap(svg_pixmap("waveform.svg", qcolor("fl_text_secondary"), 20))
        icon_label.setFixedSize(20, 20)
        style_head.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        style_text = QVBoxLayout()
        style_text.setSpacing(1)
        style_title = QLabel("Style")
        style_title.setProperty("role", "settingsTitle")
        style_text.addWidget(style_title)
        self.music_reaction_description = QLabel(
            audio.REACTION_DESCRIPTIONS.get(
                current_reaction,
                audio.REACTION_DESCRIPTIONS[audio.REACTION_FLOW],
            )
        )
        self.music_reaction_description.setProperty("role", "settingsDescription")
        self.music_reaction_description.setWordWrap(True)
        style_text.addWidget(self.music_reaction_description)
        style_head.addLayout(style_text, 1)
        style_layout.addLayout(style_head)

        reaction_picker = QFrame()
        self._reaction_picker = reaction_picker
        reaction_picker.setObjectName("MusicReactionPicker")
        reaction_grid = QGridLayout(reaction_picker)
        self._reaction_grid = reaction_grid
        reaction_grid.setContentsMargins(0, 0, 0, 0)
        reaction_grid.setHorizontalSpacing(8)
        reaction_grid.setVerticalSpacing(8)
        self._reaction_columns = 0

        self.music_reaction_group = QButtonGroup(self)
        self.music_reaction_group.setExclusive(True)
        self.music_reaction_buttons = {}
        self._music_auto_active_reaction: Optional[str] = None
        reaction_columns = min(5, max(4, (len(audio.REACTIONS) + 1) // 2))
        for index, key in enumerate(audio.REACTIONS):
            button = QPushButton(audio.REACTION_LABELS[key])
            button.setCheckable(True)
            button.setProperty("reactionOption", True)
            button.setProperty("autoActive", False)
            button.setMinimumHeight(36)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setToolTip(audio.REACTION_DESCRIPTIONS[key])
            button.setAccessibleDescription(audio.REACTION_DESCRIPTIONS[key])
            button.setChecked(key == current_reaction)
            button.clicked.connect(
                lambda checked=False, reaction=key: (
                    self._on_music_reaction_changed(reaction)
                    if checked else None
                )
            )
            self.music_reaction_group.addButton(button)
            self.music_reaction_buttons[key] = button
            reaction_grid.addWidget(button, index // reaction_columns, index % reaction_columns)
        style_layout.addWidget(reaction_picker)

        self.music_reaction_combo = ProductComboBox()
        self.music_reaction_combo.setAccessibleName("Music reaction style")
        for key in audio.REACTIONS:
            self.music_reaction_combo.addItem(audio.REACTION_LABELS[key], key)
        self.music_reaction_combo.setCurrentIndex(max(0, self.music_reaction_combo.findData(current_reaction)))
        self.music_reaction_combo.currentIndexChanged.connect(
            lambda: self._on_music_reaction_changed(str(self.music_reaction_combo.currentData()))
        )
        self.music_reaction_combo.hide()
        style_layout.addWidget(self.music_reaction_combo)
        self.music_reaction_combo.setToolTip(self.music_reaction_description.text())
        self.music_reaction_combo.setAccessibleDescription(self.music_reaction_description.text())
        reaction_group.add_card(style_card)

        self.music_auto_status = QFrame()
        self.music_auto_status.setObjectName("SettingsCard")
        self.music_auto_status.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.music_auto_status.setAccessibleName("Auto Director live status")
        self.music_auto_status.setMinimumHeight(64)
        auto_status_layout = QHBoxLayout(self.music_auto_status)
        auto_status_layout.setContentsMargins(13, 10, 17, 10)
        auto_status_layout.setSpacing(12)

        self.music_auto_color_dot = QFrame()
        self.music_auto_color_dot.setObjectName("AutoDirectorColorDot")
        self.music_auto_color_dot.setFixedSize(28, 28)
        self.music_auto_color_dot.setAccessibleName("Waiting for Auto Director output color")
        self.music_auto_color_dot.setToolTip("Waiting for live output color")
        auto_status_layout.addWidget(self.music_auto_color_dot, alignment=Qt.AlignmentFlag.AlignVCenter)

        auto_copy = QVBoxLayout()
        auto_copy.setContentsMargins(0, 0, 0, 0)
        auto_copy.setSpacing(1)
        self.music_auto_status_title = QLabel("Auto Director is ready")
        self.music_auto_status_title.setProperty("role", "settingsTitle")
        auto_copy.addWidget(self.music_auto_status_title)
        self.music_auto_status_detail = QLabel(
            "Start Music Sync to see the selected reaction and live color."
        )
        self.music_auto_status_detail.setProperty("role", "settingsDescription")
        self.music_auto_status_title.setWordWrap(True)
        self.music_auto_status_detail.setWordWrap(True)
        auto_copy.addWidget(self.music_auto_status_detail)
        auto_status_layout.addLayout(auto_copy, 1)
        reaction_group.add_card(self.music_auto_status)
        layout.addWidget(reaction_group)

        output = SettingsGroup("Output")
        self.music_palette_combo = ProductComboBox()
        self.music_palette_combo.setAccessibleName("Music color palette")
        self.music_palette_combo.setMinimumWidth(180)
        for key in audio.PALETTES:
            self.music_palette_combo.addItem(audio.PALETTE_LABELS.get(key, key), key)
        current_palette = self.controller.get_music_palette()
        palette_index = self.music_palette_combo.findData(current_palette)
        self.music_palette_combo.setCurrentIndex(max(0, palette_index))
        self.music_palette_combo.currentIndexChanged.connect(self._on_music_palette_changed)
        palette_text = audio.PALETTE_DESCRIPTIONS.get(
            current_palette,
            audio.PALETTE_DESCRIPTIONS[audio.PALETTE_RGB],
        )
        palette_card = SettingsCard("palette.svg", "Palette", palette_text, self.music_palette_combo)
        output.add_card(palette_card)
        self.music_palette_description = palette_card.description_label
        self.music_palette_combo.setToolTip(palette_text)
        self.music_palette_combo.setAccessibleDescription(palette_text)

        self.music_brightness_slider = ProductSlider(Qt.Orientation.Horizontal)
        self.music_brightness_slider.setRange(10, 100)
        self.music_brightness_slider.setAccessibleName("Music sync brightness")
        self.music_brightness_slider.setValue(int(self.controller.get_music_brightness() * 100))
        self.music_brightness_slider.valueChanged.connect(self._on_music_brightness)
        self.music_brightness_label = self._value_label(self.music_brightness_slider.value())
        output.add_card(SettingsCard(
            "sun.svg", "Brightness", "The ceiling for every reaction",
            self._slider_control(self.music_brightness_slider, self.music_brightness_label),
        ))

        self.music_auto_gain_check = ToggleSwitch()
        self.music_auto_gain_check.setChecked(self.controller.get_music_auto_gain())
        gain_tip = (
            "Normalize loudness so lowering your speaker/system volume no longer "
            "dims the sync. The music's own dynamics still drive the lights."
        )
        self.music_auto_gain_check.setToolTip(gain_tip)
        self.music_auto_gain_check.setAccessibleName("Ignore master volume")
        self.music_auto_gain_check.setAccessibleDescription(gain_tip)
        self.music_auto_gain_check.toggled.connect(self._on_music_auto_gain_toggled)
        output.add_card(SettingsCard(
            "volume.svg", "Ignore master volume",
            "Lowering the system volume no longer dims the lights",
            self.music_auto_gain_check,
        ))

        self.music_zones_button = QPushButton("Adjust…")
        self.music_zones_button.setMinimumWidth(96)
        self.music_zones_button.clicked.connect(
            lambda: self._adjust_zones_for_strip(self.music_chips)
        )
        output.add_card(SettingsCard(
            "zones.svg", "Zones", "How many LED zones the selected lights have",
            self.music_zones_button,
        ))
        layout.addWidget(output)

        self.music_start_button = self._make_start_button("music")
        if self.mode is None:
            layout.addWidget(self.music_start_button, 0, Qt.AlignmentFlag.AlignLeft)

        self._refresh_music_auto_status()

        return group

    # ------------------------------------------------------------------ wiring

    def _connect_signals(self) -> None:
        self.controller.sync_started.connect(self._on_sync_state_changed)
        self.controller.sync_stopped.connect(self._on_sync_state_changed)
        self.controller.brightness_changed.connect(self._on_brightness_changed)
        boost_signal = getattr(self.controller, "monitor_boost_changed", None)
        if boost_signal is not None:
            boost_signal.connect(self._on_monitor_boost_changed)
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

    def _strips(self) -> List[tuple[str, DeviceChipStrip]]:
        strips = []
        if hasattr(self, "monitor_chips"):
            strips.append(("monitor", self.monitor_chips))
        if hasattr(self, "music_chips"):
            strips.append(("music", self.music_chips))
        return strips

    def _refresh_chip_strips(self) -> None:
        if self.device_controller is None:
            return
        inventory = list(self.device_controller.devices)
        devs = [d for d in inventory if capabilities_for_device(d).supports_streaming]
        settings = app_settings()
        default_ids: List[str] = []
        selected = self.device_controller.get_selected_device()
        if selected and selected in devs:
            default_ids = [device_id(selected)]
        elif devs:
            default_ids = [device_id(devs[0])]
        saved_groups = self.device_controller.get_groups()
        for mode, strip in self._strips():
            strip.set_devices(devs, default_ids=default_ids)
            strip.set_groups(saved_groups)
            saved = settings.value(f"sync/{mode}/devices", None)
            if saved is not None:
                if isinstance(saved, str):
                    ids = [s for s in saved.split(",") if s]
                else:
                    ids = list(saved)
                strip.set_selected_ids(ids)
        self._refresh_account_note(inventory)
        self._refresh_led_mapping_zone_count()
        self._update_action_states()

    def _refresh_account_note(self, inventory: List[Dict[str, Any]]) -> None:
        hidden = account_only_lights(inventory)
        text = account_only_note(hidden)
        names = "\n".join(
            str(device.get("name") or device.get("model") or "Light") for device in hidden
        )
        for note in self._account_notes.values():
            note.setText(text)
            note.setToolTip(names)
            note.setVisible(bool(text))

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
                if not self._can_start_sync(selected):
                    return
                if mode == "monitor":
                    self.controller.start_monitor_sync(selected)
                else:
                    self.controller.start_music_sync(selected)
            else:
                self.controller.stop_sync(announce=False)
                display_mode = "Monitor" if mode == "monitor" else "Music"
                notify_user(
                    self,
                    f"{display_mode} sync stopped because all lights were turned off."
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
        no_target = "Select at least one light first"
        capture_ok, capture_reason = True, ""
        probe = getattr(self.controller, "monitor_capture_supported", None)
        if self.mode != "music" and callable(probe):
            try:
                capture_ok, capture_reason = probe()
            except Exception:
                capture_ok, capture_reason = True, ""

        if hasattr(self, "monitor_start_button"):
            self._set_sync_button(
                self.monitor_start_button,
                "Stop Monitor Sync"
                if current_mode == "monitor"
                else "Switch to Monitor Sync"
                if current_mode == "music"
                else "Start Monitor Sync",
                danger=current_mode == "monitor",
                # Wayland and other unsupported sessions say why up front
                # instead of failing after the lights switched mode.
                enabled=current_mode == "monitor" or (monitor_ready and capture_ok),
            )
            self.monitor_zones_button.setEnabled(monitor_ready)
            self.mapping_toggle_button.setEnabled(monitor_ready)
            self.monitor_group_button.setEnabled(monitor_ready)
            self.monitor_start_button.setToolTip(
                "Stop monitor sync" if current_mode == "monitor"
                else capture_reason if not capture_ok
                else "" if monitor_ready else no_target
            )
            self.monitor_zones_button.setToolTip(
                "Set the zone count for the selected lights" if monitor_ready else no_target
            )
            self.mapping_toggle_button.setToolTip(
                "Map each LED zone to a region of the selected display" if monitor_ready else no_target
            )
            self.monitor_group_button.setToolTip(
                "Save the selected lights as a group for Monitor and Music Sync"
                if monitor_ready else no_target
            )
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
            self.music_start_button.setToolTip(
                "Stop music sync" if current_mode == "music"
                else "" if music_ready else no_target
            )
            self.music_zones_button.setToolTip(
                "Set the zone count for the selected lights" if music_ready else no_target
            )
            self.music_group_button.setToolTip(
                "Save the selected lights as a group for Monitor and Music Sync"
                if music_ready else no_target
            )

        # Speak up only when something blocks the start action.
        for mode, strip in self._strips():
            hint = self._action_hints.get(mode)
            if hint is None:
                continue
            text = SELECT_LIGHT_HINT if strip.devices() and not strip.selected_devices() else ""
            if mode == "monitor" and not capture_ok and current_mode != "monitor":
                text = capture_reason
            hint.setText(text)
            hint.setVisible(bool(text))

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
        if not enabled:
            button.setIcon(_button_icon("play.svg", "#8A8A8A"))
        elif danger:
            button.setIcon(_button_icon("stop.svg", "#200A0D"))
        else:
            button.setIcon(_button_icon("play.svg", ACCENT_ON))
        style = button.style()
        style.unpolish(button)
        style.polish(button)
        button.update()

    def _save_mode_devices(self, mode: str, ids: List[str]) -> None:
        settings = app_settings()
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
            notify_user(self, "Select at least one light before saving a group.")
            return

        name, accepted = QInputDialog.getText(
            self,
            "Save as Group",
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
            notify_user(self, f"A group named '{name}' already exists.", "error")
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

    def _populate_displays(self) -> None:
        self.display_combo.clear()
        screens = list(QGuiApplication.screens())
        primary = QGuiApplication.primaryScreen()
        for i, screen in enumerate(screens):
            geo = screen.geometry()
            label = f"Display {i + 1} · {geo.width()}×{geo.height()}"
            if primary is not None and screen is primary:
                label += " (primary)"
            self.display_combo.addItem(label, i)
        if not screens:
            self.display_combo.addItem("Default display", 0)

    def _on_display_changed(self, _index: int = 0) -> None:
        data = self.display_combo.currentData()
        display_index = int(data) if data is not None else 0
        app_settings().setValue("sync/monitor_display", display_index)
        setter = getattr(self.controller, "set_monitor_display", None)
        if setter is not None:
            try:
                setter(display_index)
            except Exception:
                pass
        self._refresh_led_mapping_zone_count()

    def _adjust_zones_for_strip(self, strip: DeviceChipStrip) -> None:
        if self.device_controller is None:
            self.controller.status_updated.emit("Zone settings are unavailable.")
            return

        selected_devices = strip.selected_devices()
        if not selected_devices:
            notify_user(self, "Select at least one light first.")
            return

        current_count = connection.get_segment_count(selected_devices[0])
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Zones")
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
            notify_user(
                self,
                f"Zone count set to {zone_count} for {changed} light{plural}.", "success"
            )
            if (
                hasattr(self, "monitor_chips")
                and strip is self.monitor_chips
            ):
                self._refresh_led_mapping_zone_count()

    # ------------------------------------------------------------------ LED mapping

    def _sync_mapping_button(self) -> None:
        button = self.mapping_toggle_button
        blocked = button.blockSignals(True)
        button.setChecked(self._mapping_expanded)
        button.blockSignals(blocked)
        button.update()
        button.setAccessibleDescription(
            "Hide the LED layout" if self._mapping_expanded else "Show the LED layout"
        )

    def _toggle_led_mapping(self) -> None:
        if not self._mapping_expanded and not self.monitor_chips.selected_devices():
            notify_user(self, "Select at least one light before opening the LED layout.")
            self._sync_mapping_button()
            return
        self._mapping_expanded = not self._mapping_expanded
        self._sync_mapping_button()

        if self._mapping_expanded:
            self._refresh_led_mapping_zone_count()
            target_height = max(400, self.led_mapping_widget.sizeHint().height() + 34)
            self.led_layout_expander.set_expanded(True, target_height=target_height)

            if self.controller.is_syncing():
                self._sync_was_running = True
                self._sync_mode_before = self.controller.get_current_sync_mode()
                self._paused_devices = self.controller.get_selected_devices()
                self.controller.stop_sync()

            self.led_mapping_widget.start_test_mode_if_not_active()
        else:
            self.led_layout_expander.set_expanded(False)

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
        self.music_reaction_buttons[reaction].setChecked(True)
        blocked = self.music_reaction_combo.blockSignals(True)
        self.music_reaction_combo.setCurrentIndex(self.music_reaction_combo.findData(reaction))
        self.music_reaction_combo.blockSignals(blocked)
        self.music_reaction_description.setText(
            audio.REACTION_DESCRIPTIONS.get(
                reaction,
                audio.REACTION_DESCRIPTIONS[audio.REACTION_FLOW],
            )
        )
        self.music_reaction_combo.setToolTip(self.music_reaction_description.text())
        self.music_reaction_combo.setAccessibleDescription(self.music_reaction_description.text())
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

    def _on_monitor_boost(self, value: int) -> None:
        self.controller.set_monitor_boost(value / 100.0)
        self.monitor_boost_label.setText(f"{value}%")

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

    def _on_monitor_boost_changed(self, value: float) -> None:
        if hasattr(self, "monitor_boost_slider"):
            self.monitor_boost_slider.setValue(int(round(value * 100)))

    # ------------------------------------------------------------------ start helpers

    def _can_start_sync(self, selected) -> bool:
        blocked = []
        for device in selected:
            preferred = False
            if self.device_controller is not None:
                getter = getattr(self.device_controller, "is_cloud_preferred", None)
                if getter is not None:
                    try:
                        preferred = bool(getter(device))
                    except Exception:
                        preferred = False
            if is_cloud(device) or preferred:
                blocked.append(
                    device.get("name") or device.get("model") or "a device"
                )
        if blocked:
            names = ", ".join(blocked[:3])
            suffix = "" if len(blocked) <= 3 else f" and {len(blocked) - 3} more"
            notify_user(
                self,
                f"{names}{suffix} can only be reached through the cloud right now. "
                "Screen and music sync need local Wi-Fi or Bluetooth; connect "
                "them over LAN first.",
                "error",
            )
            return False
        if self.device_controller is not None and any(
            self.device_controller.is_device_busy(device, allowed_outputs=("Monitor sync", "Music sync"))
            for device in selected
        ):
            notify_user(self, "Finish the current drawing or device command before starting sync.", "error")
            return False
        return True

    def _start_monitor_sync(self) -> None:
        if hasattr(self, "monitor_chips"):
            selected = self.monitor_chips.selected_devices()
            if self._can_start_sync(selected):
                self.controller.start_monitor_sync(selected)

    def _start_music_sync(self) -> None:
        if hasattr(self, "music_chips"):
            selected = self.music_chips.selected_devices()
            if self._can_start_sync(selected):
                self.controller.start_music_sync(selected)

    def _handle_monitor_action(self) -> None:
        self._stop_mapping_test_for_sync()
        if (
            self.controller.is_syncing()
            and self.controller.get_current_sync_mode() == "monitor"
        ):
            self.controller.stop_sync()
        else:
            self._start_monitor_sync()

    def _handle_music_action(self) -> None:
        self._stop_mapping_test_for_sync()
        if (
            self.controller.is_syncing()
            and self.controller.get_current_sync_mode() == "music"
        ):
            self.controller.stop_sync()
        else:
            self._start_music_sync()

    def _stop_mapping_test_for_sync(self) -> None:
        """Release LED Mapping ownership before starting a live sync stream."""
        if not self._mapping_expanded:
            return

        # Closing the mapper normally resumes whatever it paused. A sync action
        # is about to make an explicit choice, so discard that automatic resume
        # and let the action start exactly one stream after the test timer stops.
        self._sync_was_running = False
        self._sync_mode_before = None
        self._paused_devices = []
        self._toggle_led_mapping()

    def _on_sync_state_changed(self, *_args) -> None:
        self._update_action_states()
        self._refresh_music_auto_status()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt API
        # LED test mode owns the lights temporarily. Leaving the page must end
        # it and restore any sync that was paused to open the mapper.
        if self._mapping_expanded:
            self._toggle_led_mapping()
        super().hideEvent(event)
