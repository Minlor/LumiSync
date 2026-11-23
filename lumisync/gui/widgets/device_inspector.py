"""Right-side device inspector used by the Devices page."""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ... import connection
from ...accounts.tuya_energy import meter_fields
from ...drivers.registry import capabilities_for_device, is_cloud, lighting_options
from ..resources.icons import IconKey, tinted_icon, tinted_pixmap
from ..theme import TOKENS, qcolor
from ..utils.animations import StatusDot
from ..utils.device_identity import device_identity
from .color_flyout import (
    ColorSwatchButton,
    SwatchChoice,
    SwatchRow,
    preset_choices,
    recent_choices,
    remember_recent_color,
    state_display_color,
    swatch_tooltip,
    white_range,
)
from .device_card import (
    UNKNOWN_VALUE,
    apply_status,
    connection_name,
    describe_device_status,
    power_visual_state,
    set_power_button_state,
)
from .device_energy import DeviceEnergy
from .elided_label import ElidedLabel
from .product_controls import ProductSlider, ProductComboBox, ToggleSwitch


def _section(object_name: str = "InspectorSection") -> QFrame:
    """A Fluent card section (the app stylesheet paints ``card=true``)."""
    frame = QFrame()
    frame.setObjectName(object_name)
    frame.setProperty("card", True)
    return frame


def _divider() -> QWidget:
    line = QWidget()
    line.setObjectName("InspectorDivider")
    line.setFixedHeight(1)
    line.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    line.setStyleSheet(f"QWidget#InspectorDivider {{ background: {TOKENS['fl_divider']}; }}")
    return line


class DeviceInspector(QFrame):
    """Progressively reveals controls for one selected device."""

    close_requested = Signal()
    power_clicked = Signal(int)
    set_default_requested = Signal(int)
    color_picked = Signal(int, QColor)
    brightness_changed = Signal(int, int)
    color_temp_changed = Signal(int, int)
    zone_count_requested = Signal(int)
    zone_count_reset_requested = Signal(int)
    remove_requested = Signal(int)
    connection_changed = Signal(int, str)
    panel_tools_requested = Signal(int)
    refresh_requested = Signal(int)
    # USB lighting options (each driver declares which it has).
    color_correction_changed = Signal(int, bool)
    lighting_layout_changed = Signal(int, str)
    lighting_effect_changed = Signal(int, str)
    lighting_effect_speed_changed = Signal(int, int)
    lighting_effect_reversed_changed = Signal(int, bool)
    lighting_effect_multicolor_changed = Signal(int, bool)
    lighting_sync_enabled_changed = Signal(int, bool)
    lighting_sync_interval_changed = Signal(int, str)
    lighting_secondary_color_enabled_changed = Signal(int, bool)
    lighting_secondary_color_changed = Signal(int, QColor)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._index = -1
        self._device: Dict[str, Any] = {}
        self._current_color: Optional[QColor] = None
        self._current_kelvin: Optional[int] = None
        self._ct_min = 2000
        self._ct_max = 9000
        self._cap = capabilities_for_device({})
        self._meters: tuple = ()
        self.status = describe_device_status({})

        self._brightness_timer = QTimer(self)
        self._brightness_timer.setSingleShot(True)
        self._brightness_timer.setInterval(180)
        self._brightness_timer.timeout.connect(self._commit_brightness)
        self._temperature_timer = QTimer(self)
        self._temperature_timer.setSingleShot(True)
        self._temperature_timer.setInterval(180)
        self._temperature_timer.timeout.connect(self._commit_temperature)

        self.setObjectName("DeviceInspector")
        # Leave room for a vertical scrollbar inside the 414 px side pane.
        self.setMinimumWidth(380)
        # Hug short switch/plug controls instead of drawing an empty panel to
        # the bottom of the page. The surrounding scroll area owns overflow.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._available_height = 800
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        # Header: what the device is, its power, and a way out.
        header = QHBoxLayout()
        header.setSpacing(10)
        self.type_icon = QLabel()
        self.type_icon.setFixedSize(32, 38)
        header.addWidget(self.type_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        titles = QVBoxLayout()
        titles.setSpacing(2)

        self.title_label = ElidedLabel("Device")
        self.title_label.setProperty("role", "inspectorTitle")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        titles.addWidget(self.title_label)
        self.type_label = ElidedLabel("")
        self.type_label.setProperty("role", "subtle")
        titles.addWidget(self.type_label)
        header.addLayout(titles, 1)

        self.power_button = QToolButton()
        self.power_button.setObjectName("DevicePowerButton")
        self.power_button.setProperty("powerState", "unknown")
        self.power_button.setFixedSize(46, 46)
        self.power_button.setIconSize(QSize(20, 20))
        self.power_button.setAccessibleName("Toggle device power")
        self.power_button.setToolTip("Turn on")
        self.power_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.power_button.clicked.connect(
            lambda: self.power_clicked.emit(self._index)
        )
        header.addWidget(self.power_button)

        self.close_button = QToolButton()
        self.close_button.setObjectName("InspectorCloseButton")
        self.close_button.setText("×")
        self.close_button.setFixedSize(44, 44)
        self.close_button.setAccessibleName("Close device controls")
        self.close_button.setToolTip("Close device controls")
        self.close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_button.clicked.connect(self.close_requested)
        header.addWidget(self.close_button)
        root.addLayout(header)

        # One status line: the same merged phrase the device card shows,
        # plus the colour the light is showing.
        status_row = QHBoxLayout()
        status_row.setContentsMargins(2, 0, 0, 0)
        status_row.setSpacing(6)
        self.status_dot = StatusDot(qcolor("text_disabled"), self, size=8)
        status_row.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.status_swatch = ColorSwatchButton(None, self, diameter=14)
        self.status_swatch.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.status_swatch.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.status_swatch.setCursor(Qt.CursorShape.ArrowCursor)
        self.status_swatch.setAccessibleName("Current colour")
        status_row.addWidget(self.status_swatch, 0, Qt.AlignmentFlag.AlignVCenter)
        self.status_label = ElidedLabel("No status yet")
        self.status_label.setProperty("role", "deviceStatus")
        status_row.addWidget(self.status_label, 1)
        root.addLayout(status_row)

        self.controls_label = QLabel("Light")
        self.controls_label.setProperty("role", "sectionTitle")
        root.addWidget(self.controls_label)

        self.controls_panel = controls = _section()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(16, 14, 16, 16)
        controls_layout.setSpacing(12)

        self.brightness_controls = QWidget()
        brightness_layout = QVBoxLayout(self.brightness_controls)
        brightness_layout.setContentsMargins(0, 0, 0, 0)
        brightness_layout.setSpacing(6)

        brightness_header = QHBoxLayout()
        brightness_header.setSpacing(8)
        brightness_icon = QLabel()
        brightness_icon.setPixmap(
            tinted_pixmap(IconKey.SUN, qcolor("text_dim"), 14)
        )
        brightness_header.addWidget(brightness_icon)
        brightness_header.addWidget(QLabel("Brightness"))
        brightness_header.addStretch(1)
        self.brightness_value = QLabel(UNKNOWN_VALUE)
        self.brightness_value.setProperty("role", "subtle")
        brightness_header.addWidget(self.brightness_value)
        brightness_layout.addLayout(brightness_header)

        self.brightness_slider = ProductSlider(Qt.Orientation.Horizontal)
        self.brightness_slider.setRange(0, 100)
        self.brightness_slider.setValue(100)
        self.brightness_slider.setAccessibleName("Device brightness")
        self.brightness_slider.valueChanged.connect(self._on_brightness)
        brightness_layout.addWidget(self.brightness_slider)
        controls_layout.addWidget(self.brightness_controls)

        self.color_divider = _divider()
        controls_layout.addWidget(self.color_divider)

        self.color_controls = QWidget()
        color_layout = QVBoxLayout(self.color_controls)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(10)
        color_row = QHBoxLayout()
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.setSpacing(10)
        color_copy = QVBoxLayout()
        color_copy.setSpacing(1)
        color_copy.addWidget(QLabel("Colour"))
        self.color_hint = ElidedLabel(swatch_tooltip(None, action=""))
        self.color_hint.setProperty("role", "subtle")
        color_copy.addWidget(self.color_hint)
        color_row.addLayout(color_copy, 1)

        self.color_button = ColorSwatchButton(None, self, diameter=30)
        self.color_button.setObjectName("InspectorColorSwatch")
        self.color_button.setAccessibleName("Choose any colour")
        self.color_button.setToolTip("More colours…")
        self.color_button.clicked.connect(self._pick_color)
        color_row.addWidget(self.color_button, 0, Qt.AlignmentFlag.AlignVCenter)
        color_layout.addLayout(color_row)

        self.color_presets = SwatchRow(self, diameter=22, spacing=6)
        self.color_presets.chosen.connect(self._choose_preset)
        color_layout.addWidget(self.color_presets)
        controls_layout.addWidget(self.color_controls)

        self.temperature_divider = _divider()
        controls_layout.addWidget(self.temperature_divider)

        self.temperature_controls = QWidget()
        temperature_layout = QVBoxLayout(self.temperature_controls)
        temperature_layout.setContentsMargins(0, 0, 0, 0)
        temperature_layout.setSpacing(6)

        self.temperature_header = QHBoxLayout()
        self.temperature_header.setSpacing(8)
        self.temperature_icon = QLabel()
        self.temperature_icon.setPixmap(
            tinted_pixmap(IconKey.THERMOMETER, qcolor("text_dim"), 14)
        )
        self.temperature_header.addWidget(self.temperature_icon)
        self.temperature_title = QLabel("White temperature")
        self.temperature_header.addWidget(self.temperature_title)
        self.temperature_header.addStretch(1)
        self.temperature_value = QLabel(UNKNOWN_VALUE)
        self.temperature_value.setProperty("role", "subtle")
        self.temperature_header.addWidget(self.temperature_value)
        temperature_layout.addLayout(self.temperature_header)

        self.temperature_slider = ProductSlider(Qt.Orientation.Horizontal)
        self.temperature_slider.setRange(self._ct_min, self._ct_max)
        self.temperature_slider.setValue(4000)
        self.temperature_slider.setAccessibleName("White color temperature")
        self.temperature_slider.valueChanged.connect(self._on_temperature)
        temperature_layout.addWidget(self.temperature_slider)
        controls_layout.addWidget(self.temperature_controls)
        self._temperature_widgets = (
            self.temperature_icon,
            self.temperature_title,
            self.temperature_value,
            self.temperature_slider,
        )
        root.addWidget(controls)

        self._build_usb_lighting_options(root)

        self.energy = DeviceEnergy()
        self.energy.refresh_requested.connect(lambda: self.refresh_requested.emit(self._index))
        root.addWidget(self.energy)

        self.details_toggle = QPushButton("Device details")
        self.details_toggle.setProperty("role", "ghost")
        self.details_toggle.setMinimumHeight(44)
        self.details_toggle.setCheckable(True)
        self.details_toggle.setAccessibleName("Show device connection details and setup actions")
        root.addWidget(self.details_toggle)
        self.details_panel = QWidget()
        detail_layout = QVBoxLayout(self.details_panel)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(10)
        self.details_toggle.toggled.connect(self._toggle_details)
        self.details_panel.hide()

        info = _section()
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(16, 12, 16, 12)
        info_layout.setSpacing(8)
        self._info_rows = {}
        self._info_titles = {}
        self.connection_value = self._add_info_row(
            info_layout, "Connection", UNKNOWN_VALUE
        )
        self.address_value = self._add_info_row(info_layout, "Address", UNKNOWN_VALUE)
        self.port_value = self._add_info_row(
            info_layout, "Control port", UNKNOWN_VALUE
        )
        self.identifier_value = self._add_info_row(
            info_layout, "Identifier", UNKNOWN_VALUE
        )
        self.zones_value = self._add_info_row(info_layout, "Layout", UNKNOWN_VALUE)
        self.readback_value = self._add_info_row(
            info_layout, "State reporting", UNKNOWN_VALUE
        )
        detail_layout.addWidget(info)

        self.connection_combo = ProductComboBox()
        self.connection_combo.setAccessibleName("Device connection method")
        self.connection_combo.currentIndexChanged.connect(self._connection_selected)
        detail_layout.addWidget(self.connection_combo)
        self.panel_tools_button = QPushButton("Panel tools")
        self.panel_tools_button.clicked.connect(lambda: self.panel_tools_requested.emit(self._index))
        detail_layout.addWidget(self.panel_tools_button)

        self.default_button = QPushButton("Set as Default")
        self.default_button.clicked.connect(
            lambda: self.set_default_requested.emit(self._index)
        )
        detail_layout.addWidget(self.default_button)

        zone_actions = QHBoxLayout()
        zone_actions.setSpacing(8)
        self.zones_button = QPushButton("Set Zones")
        self.zones_button.clicked.connect(
            lambda: self.zone_count_requested.emit(self._index)
        )
        zone_actions.addWidget(self.zones_button)
        self.reset_zones_button = QPushButton("Use Default")
        self.reset_zones_button.clicked.connect(
            lambda: self.zone_count_reset_requested.emit(self._index)
        )
        zone_actions.addWidget(self.reset_zones_button)
        detail_layout.addLayout(zone_actions)

        self.remove_button = QPushButton("Remove Device")
        self.remove_button.setProperty("role", "danger")
        self.remove_button.setIcon(
            tinted_icon(IconKey.TRASH, qcolor("bg"), 16)
        )
        self.remove_button.clicked.connect(
            lambda: self.remove_requested.emit(self._index)
        )
        detail_layout.addWidget(self.remove_button)
        root.addWidget(self.details_panel)
        root.addStretch(1)

        self._show_color(None, None)

    def _build_usb_lighting_options(self, root: QVBoxLayout) -> None:
        """The driver's lighting options (colour correction, layout, effects, second colour), USB lighting only."""
        self.usb_options_label = QLabel("Board lighting")  # Retitled per device by its driver.
        self.usb_options_label.setProperty("role", "sectionTitle")
        root.addWidget(self.usb_options_label)

        self.usb_options_panel = panel = _section()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)
        # Each row with the divider above it; only rows the driver offers show.
        self._option_rows: list = []

        def add_row(row: QWidget) -> QWidget:
            divider = _divider()
            layout.addWidget(divider)
            layout.addWidget(row)
            self._option_rows.append((divider, row))
            return row

        def option_row(title: str, hint: str, control: QWidget, *, wrap: bool = False) -> tuple:
            container, _title, hint_label = titled_option_row(title, hint, control, wrap=wrap)
            return container, hint_label

        def titled_option_row(title: str, hint: str, control: QWidget, *, wrap: bool = False) -> tuple:
            container = QWidget()
            row = QHBoxLayout(container)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            copy = QVBoxLayout()
            copy.setSpacing(1)
            title_label = QLabel(title)
            copy.addWidget(title_label)
            # A wrapping hint for text that names the zones ("DPI lights and logo, ...").
            hint_label = QLabel(hint) if wrap else ElidedLabel(hint)
            hint_label.setWordWrap(wrap)
            hint_label.setProperty("role", "subtle")
            copy.addWidget(hint_label)
            row.addLayout(copy, 1)
            row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
            add_row(container)
            return container, title_label, hint_label

        self.color_correction_switch = ToggleSwitch()
        self.color_correction_switch.setAccessibleName("Colour correction")
        self.color_correction_switch.setToolTip(
            "Converts colours for the board's LEDs so dim and pastel colours look as on screen"
        )
        self.color_correction_switch.toggled.connect(self._color_correction_toggled)
        self.color_correction_row, self.color_correction_hint = option_row(
            "Colour correction", "Colours match the screen: neutral white, true pastels", self.color_correction_switch
        )

        self.layout_combo = ProductComboBox()
        self.layout_combo.setAccessibleName("Lighting layout")
        # Item labels and values come from the device's driver.
        self.layout_combo.addItem("Whole board", "board")
        self.layout_combo.addItem("Per zone", "zones")
        self.layout_combo.currentIndexChanged.connect(self._layout_selected)
        self.layout_row, self.layout_hint = option_row("Layout", "", self.layout_combo, wrap=True)

        # Built-in effects (the driver lists them, with what each one takes).
        self._effect_options = None
        self.effect_combo = ProductComboBox()
        self.effect_combo.setAccessibleName("Lighting effect")
        self.effect_combo.currentIndexChanged.connect(self._effect_selected)
        self.effect_row, self.effect_hint = option_row("Effect", "Runs on the device itself", self.effect_combo)

        self.effect_speed_row = QWidget()
        speed_layout = QVBoxLayout(self.effect_speed_row)
        speed_layout.setContentsMargins(0, 0, 0, 0)
        speed_layout.setSpacing(2)
        speed_layout.addWidget(QLabel("Speed"))
        self.effect_speed_slider = ProductSlider(Qt.Orientation.Horizontal)
        self.effect_speed_slider.setRange(1, 5)
        self.effect_speed_slider.setPageStep(1)
        self.effect_speed_slider.setAccessibleName("Effect speed")
        self.effect_speed_slider.valueChanged.connect(self._on_effect_speed)
        speed_layout.addWidget(self.effect_speed_slider)
        speed_ends = QHBoxLayout()
        speed_ends.setContentsMargins(0, 0, 0, 0)
        for text, stretch in (("Slow", 1), ("Fast", 0)):
            label = QLabel(text)
            label.setProperty("role", "subtle")
            speed_ends.addWidget(label, stretch)
        speed_layout.addLayout(speed_ends)
        add_row(self.effect_speed_row)
        self._effect_speed_timer = QTimer(self)
        self._effect_speed_timer.setSingleShot(True)
        self._effect_speed_timer.setInterval(250)
        self._effect_speed_timer.timeout.connect(self._commit_effect_speed)

        self.effect_direction_combo = ProductComboBox()
        self.effect_direction_combo.setAccessibleName("Effect direction")
        self.effect_direction_combo.addItem("", False)
        self.effect_direction_combo.addItem("", True)
        self.effect_direction_combo.currentIndexChanged.connect(self._effect_direction_selected)
        self.effect_direction_row, _hint = option_row(
            "Direction", "Which way the effect moves", self.effect_direction_combo
        )

        self.effect_multicolor_switch = ToggleSwitch()
        self.effect_multicolor_switch.setAccessibleName("Multicolour")
        self.effect_multicolor_switch.toggled.connect(self._effect_multicolor_toggled)
        self.effect_multicolor_row, _hint = option_row(
            "Multicolour", "The effect's own colours instead of yours", self.effect_multicolor_switch
        )
        self._effect_rows = (
            self.effect_row, self.effect_speed_row, self.effect_direction_row, self.effect_multicolor_row,
        )

        # A second colour for part of the device (a mouse's logo); the driver
        # supplies the labels. Hidden for effects that show no colour.
        self._secondary_color = None
        self._secondary_effect_has_color = True
        self.secondary_color_switch = ToggleSwitch()
        self.secondary_color_switch.toggled.connect(self._secondary_color_toggled)
        self.secondary_color_row, self.secondary_color_title, self.secondary_color_hint = titled_option_row(
            "", "", self.secondary_color_switch, wrap=True
        )
        self.secondary_color_button = ColorSwatchButton(None, self, diameter=30)
        self.secondary_color_button.setObjectName("InspectorSecondaryColorSwatch")
        self.secondary_color_button.clicked.connect(self._pick_secondary_color)
        self.secondary_color_value_row, self.secondary_color_value_title, self.secondary_color_value_hint = (
            titled_option_row("", "", self.secondary_color_button)
        )

        # Opt-in Monitor/Music sync (the driver supplies the labels and warning).
        self._sync_options = None
        self.sync_switch = ToggleSwitch()
        self.sync_switch.setAccessibleName("Monitor and Music sync")
        self.sync_switch.toggled.connect(self._sync_toggled)
        self.sync_row, self.sync_hint = option_row(
            "Monitor and Music sync", "", self.sync_switch, wrap=True
        )
        self.sync_interval_combo = ProductComboBox()
        self.sync_interval_combo.setAccessibleName("Sync updates")
        self.sync_interval_combo.currentIndexChanged.connect(self._sync_interval_selected)
        self.sync_interval_row, interval_hint = option_row("Sync updates", "", self.sync_interval_combo)
        interval_hint.hide()
        root.addWidget(panel)

    def _update_option_dividers(self) -> None:
        """A divider above every shown row except the first."""
        shown = False
        for divider, row in self._option_rows:
            divider.setVisible(shown and not row.isHidden())
            shown = shown or not row.isHidden()

    def _show_usb_lighting_options(self, device: Dict[str, Any]) -> None:
        """The driver's lighting options for this device (None: hide the card)."""
        self._effect_speed_timer.stop()
        options = lighting_options(device)
        visible = options is not None
        self.usb_options_label.setVisible(visible)
        self.usb_options_panel.setVisible(visible)
        if options is None:
            self._effect_options = None
            self._secondary_color = None
            return
        from ...drivers import led_color

        self.usb_options_label.setText(options.title)
        self.color_correction_row.setVisible(options.color_correction)
        self.color_correction_switch.setToolTip(options.correction_tip)
        blocked = self.color_correction_switch.blockSignals(True)
        self.color_correction_switch.setChecked(led_color.color_correction(device) == led_color.COLOR_CORRECTION_SRGB)
        self.color_correction_switch.blockSignals(blocked)
        self.layout_row.setVisible(options.has_layout)
        names = options.zone_names
        blocked = self.layout_combo.blockSignals(True)
        self.layout_combo.setItemText(0, options.whole_label)
        self.layout_combo.setItemData(0, options.layout_whole)
        self.layout_combo.setItemText(1, options.zones_label)
        self.layout_combo.setItemData(1, options.layout_zones)
        self.layout_combo.setCurrentIndex(1 if options.per_zone else 0)
        self.layout_combo.blockSignals(blocked)
        self.layout_hint.setText(options.zones_description if options.per_zone else options.whole_description)
        self.layout_combo.setToolTip("Zones in sync order: " + ", ".join(names))
        self._show_sync_options(options.sync)
        self._show_secondary_color_option(options.secondary_color)
        self._show_effect_options(options.effects)

    def _show_secondary_color_option(self, option: Any) -> None:
        """The second-colour switch and, while it is on, its colour row (labels from the driver)."""
        self._secondary_color = option
        if option is None:
            return
        self.secondary_color_title.setText(option.title)
        self.secondary_color_hint.setText(option.description)
        self.secondary_color_switch.setAccessibleName(option.title)
        blocked = self.secondary_color_switch.blockSignals(True)
        self.secondary_color_switch.setChecked(option.enabled)
        self.secondary_color_switch.blockSignals(blocked)
        self.secondary_color_value_title.setText(option.color_title)
        self.secondary_color_button.setAccessibleName(f"Choose the {option.color_title.lower()}")
        self._show_secondary_color_value()

    def _secondary_qcolor(self) -> Optional[QColor]:
        """The saved second colour, or None (it follows the main colour)."""
        option = self._secondary_color
        if option is None or option.color is None:
            return None
        return QColor(*option.color)

    def _show_secondary_color_value(self) -> None:
        color = self._secondary_qcolor()
        shown = color if color is not None else self._current_color
        self.secondary_color_button.set_color(shown, None)
        self.secondary_color_value_hint.setText(
            "Same as the main colour" if color is None else swatch_tooltip(color, action="")
        )
        self.secondary_color_button.setToolTip(swatch_tooltip(shown, action="click to change"))

    def _update_secondary_color_rows(self) -> None:
        """Show the second-colour rows when the driver offers them and the effect takes a colour."""
        option = self._secondary_color
        offered = option is not None and self._secondary_effect_has_color
        self.secondary_color_row.setVisible(offered)
        self.secondary_color_value_row.setVisible(offered and self.secondary_color_switch.isChecked())

    def _show_sync_options(self, sync: Any) -> None:
        """The opt-in sync switch, and its interval row while it is on."""
        self._sync_options = sync
        if sync is None:
            self.sync_row.setVisible(False)
            self.sync_interval_row.setVisible(False)
            return
        self.sync_hint.setText(sync.description)
        blocked = self.sync_switch.blockSignals(True)
        self.sync_switch.setChecked(sync.enabled)
        self.sync_switch.blockSignals(blocked)
        blocked = self.sync_interval_combo.blockSignals(True)
        self.sync_interval_combo.clear()
        for choice in sync.choices:
            self.sync_interval_combo.addItem(choice.label, choice.key)
        self.sync_interval_combo.setCurrentIndex(max(0, self.sync_interval_combo.findData(sync.interval)))
        self.sync_interval_combo.blockSignals(blocked)
        self.sync_row.setVisible(True)
        self.sync_interval_row.setVisible(sync.enabled)

    def sync_options(self) -> Any:
        """The shown device's opt-in sync options (None when it has none)."""
        return self._sync_options

    def _sync_toggled(self, checked: bool) -> None:
        if self._index >= 0 and self._sync_options is not None:
            self.lighting_sync_enabled_changed.emit(self._index, bool(checked))

    def _sync_interval_selected(self, _index: int) -> None:
        key = self.sync_interval_combo.currentData()
        if self._index >= 0 and self._sync_options is not None and key:
            self.lighting_sync_interval_changed.emit(self._index, str(key))

    def _show_effect_options(self, effects: Any) -> None:
        """The effect rows: the effect list, then the rows the chosen effect takes."""
        self._effect_options = effects
        if effects is None:
            for row in self._effect_rows:
                row.setVisible(False)
            self._secondary_effect_has_color = True
            self._update_secondary_color_rows()
            self._update_option_dividers()
            return
        blocked = self.effect_combo.blockSignals(True)
        self.effect_combo.clear()
        for choice in effects.choices:
            self.effect_combo.addItem(choice.name, choice.key)
        self.effect_combo.setCurrentIndex(max(0, self.effect_combo.findData(effects.effect)))
        self.effect_combo.blockSignals(blocked)
        blocked = self.effect_speed_slider.blockSignals(True)
        self.effect_speed_slider.setRange(1, effects.speed_steps)
        self.effect_speed_slider.setValue(effects.speed)
        self.effect_speed_slider.blockSignals(blocked)
        self._describe_effect_speed(effects.speed)
        blocked = self.effect_direction_combo.blockSignals(True)
        self.effect_direction_combo.setCurrentIndex(1 if effects.reversed else 0)
        self.effect_direction_combo.blockSignals(blocked)
        blocked = self.effect_multicolor_switch.blockSignals(True)
        self.effect_multicolor_switch.setChecked(effects.multicolor)
        self.effect_multicolor_switch.blockSignals(blocked)
        self.effect_row.setVisible(True)
        self._show_effect_rows(effects.current)

    def _show_effect_rows(self, choice: Any) -> None:
        """Show speed, direction and multicolour only where the effect has them."""
        self.effect_speed_row.setVisible(choice.has_speed)
        self.effect_direction_row.setVisible(choice.has_direction)
        if choice.has_direction:
            for row, label in enumerate(choice.directions):
                self.effect_direction_combo.setItemText(row, label)
        self.effect_multicolor_row.setVisible(choice.multicolor)
        self._secondary_effect_has_color = bool(choice.has_color)
        self._update_secondary_color_rows()
        self._update_option_dividers()

    def _describe_effect_speed(self, value: int) -> None:
        steps = self.effect_speed_slider.maximum()
        self.effect_speed_slider.setAccessibleDescription(f"{value} of {steps}")
        self.effect_speed_slider.setToolTip(f"Speed {value} of {steps}")

    def _color_correction_toggled(self, checked: bool) -> None:
        if self._index >= 0:
            self.color_correction_changed.emit(self._index, bool(checked))

    def _layout_selected(self, _index: int) -> None:
        value = self.layout_combo.currentData()
        if self._index >= 0 and value:
            self.lighting_layout_changed.emit(self._index, str(value))

    def _effect_selected(self, _index: int) -> None:
        key = self.effect_combo.currentData()
        effects = self._effect_options
        if effects is None or not key:
            return
        choice = effects.choice(str(key))
        if choice is not None:
            self._show_effect_rows(choice)
        if self._index >= 0:
            self.lighting_effect_changed.emit(self._index, str(key))

    def _on_effect_speed(self, value: int) -> None:
        self._describe_effect_speed(value)
        self._effect_speed_timer.start()

    def _commit_effect_speed(self) -> None:
        if self._index >= 0 and self._effect_options is not None:
            self.lighting_effect_speed_changed.emit(self._index, self.effect_speed_slider.value())

    def _effect_direction_selected(self, _index: int) -> None:
        if self._index >= 0 and self._effect_options is not None:
            self.lighting_effect_reversed_changed.emit(self._index, bool(self.effect_direction_combo.currentData()))

    def _effect_multicolor_toggled(self, checked: bool) -> None:
        if self._index >= 0 and self._effect_options is not None:
            self.lighting_effect_multicolor_changed.emit(self._index, bool(checked))

    def _secondary_color_toggled(self, checked: bool) -> None:
        if self._secondary_color is None:
            return
        self._update_secondary_color_rows()
        self._update_option_dividers()
        if self._index >= 0:
            self.lighting_secondary_color_enabled_changed.emit(self._index, bool(checked))

    def _pick_secondary_color(self) -> None:
        option = self._secondary_color
        if self._index < 0 or option is None:
            return
        current = self._secondary_qcolor() or self._current_color or QColor("#FFFFFF")
        chosen = QColorDialog.getColor(current, self, f"Choose the {option.color_title.lower()}")
        if chosen.isValid() and self._index >= 0:
            remember_recent_color(chosen)
            self.secondary_color_button.set_color(chosen, None)
            self.secondary_color_value_hint.setText(swatch_tooltip(chosen, action=""))
            self.lighting_secondary_color_changed.emit(self._index, chosen)

    def _toggle_details(self, show: bool) -> None:
        self.details_panel.setVisible(show)
        self.details_toggle.setText("Hide device details" if show else "Device details")

    def _add_info_row(self, layout: QVBoxLayout, title: str, value: str) -> QLabel:
        section = QWidget()
        row = QHBoxLayout(section)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        title_label = QLabel(title)
        title_label.setProperty("role", "subtle")
        row.addWidget(title_label)
        value_label = QLabel(value)
        value_label.setWordWrap(True)
        value_label.setTextFormat(Qt.TextFormat.PlainText)
        value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        # The value takes the rest of the row (right-aligned), so a wrapping
        # label is only as narrow as it must be (long connection names and
        # identifiers stay on one line).
        row.addWidget(value_label, 1)
        layout.addWidget(section)
        self._info_rows[title] = section
        self._info_titles[title] = title_label
        return value_label

    def set_device(
        self,
        index: int,
        device: Dict[str, Any],
        state: Dict[str, Any],
        *,
        primary: bool,
    ) -> None:
        self._brightness_timer.stop()
        self._temperature_timer.stop()
        if (self._device.get("device_id"), self._device.get("mac")) != (device.get("device_id"), device.get("mac")):
            self.details_toggle.setChecked(False)
        self._index = index
        self._device = dict(device)
        self._cap = cap = capabilities_for_device(device)
        self.title_label.setText(str(device.get("name") or device.get("model") or "Unknown device"))
        type_name, icon = device_identity(device)
        self.type_label.setText(type_name)
        self.type_icon.setPixmap(tinted_pixmap(icon, qcolor("text_dim"), 30))
        self.type_icon.setAccessibleName(type_name)
        self._meters = tuple(meter_fields(device))

        transport = str(device.get("transport") or "lan").lower()
        self.connection_value.setText(connection_name(device))
        self.address_value.setText(
            str(
                device.get("ble_address")
                or device.get("ip")
                or device.get("mac")
                or UNKNOWN_VALUE
            )
        )
        port = device.get("port")
        if not port and transport == "lan":
            port = connection.get_device_port(device)
        self.port_value.setText(str(port) if port else UNKNOWN_VALUE)
        identifier = (
            device.get("device_id")
            or device.get("mac")
            or device.get("ble_address")
            or UNKNOWN_VALUE
        )
        self.identifier_value.setText(str(identifier))
        self._info_rows["Control port"].setVisible(bool(port))
        # USB lighting has no network address; its identifier is enough.
        self._info_rows["Address"].setVisible(not is_cloud(device) and transport != "usb")
        self._info_rows["Identifier"].setVisible(is_cloud(device) or self.identifier_value.text() != self.address_value.text())
        self._info_titles["Layout"].setText("Device type" if device.get("device_kind") in ("light_switch", "smart_plug") else "Layout")
        # USB lighting describes its layout only when its driver has one.
        options = lighting_options(device)
        self._info_rows["Layout"].setVisible(transport != "usb" or (options is not None and options.has_layout))
        self.readback_value.setText(
            "Live"
            if state.get("readback_supported", transport not in ("ble", "usb"))
            else "Last command only"
        )
        if device.get("device_kind") in ("light_switch", "smart_plug"):
            self.zones_value.setText(("Smart plug" if device["device_kind"] == "smart_plug" else "Light switch") + " · power control")
        elif transport == "ble":
            self.zones_value.setText(
                f"{device.get('matrix_size', '32x32')} matrix"
            )
        elif options is not None and options.has_layout:
            self.zones_value.setText(
                f"{cap.segment_count} zones · per zone" if cap.segment_count > 1
                else f"{options.whole_label} · one colour"
            )
        elif transport == "usb":
            # USB lighting without a layout option is one colour throughout.
            self.zones_value.setText("Whole device · one colour")
        elif transport == "tuya" or is_cloud(device):
            if cap.supports_segments and cap.segment_count > 1:
                source = "custom" if device.get("segment_count_override") else "default"
                self.zones_value.setText(f"{cap.segment_count} zones · {source}")
            else:
                self.zones_value.setText(
                    "Single ambient light" if transport == "tuya" else "Whole light · manual controls"
                )
        else:
            count = connection.get_segment_count(device)
            source = "custom" if device.get("segment_count_override") else "default"
            self.zones_value.setText(f"{count} zones · {source}")

        self.power_button.setVisible(cap.supports_power)
        self.power_button.setEnabled(cap.supports_power)
        self.brightness_slider.setEnabled(cap.supports_brightness)
        self.color_button.setEnabled(cap.supports_color)
        self.brightness_controls.setVisible(cap.supports_brightness)
        self.color_controls.setVisible(cap.supports_color)
        self.status_swatch.setVisible(cap.supports_color)
        supported = bool(cap.supports_white and cap.color_temp_max > cap.color_temp_min > 0)
        self.temperature_slider.setEnabled(supported)
        self.temperature_controls.setVisible(supported)
        self.color_divider.setVisible(cap.supports_brightness and cap.supports_color)
        self.temperature_divider.setVisible(supported and (cap.supports_brightness or cap.supports_color))
        lighting_controls = cap.supports_brightness or cap.supports_color or supported
        self.controls_label.setVisible(lighting_controls)
        self.controls_panel.setVisible(lighting_controls)
        self._show_usb_lighting_options(device)
        if supported:
            self._ct_min = int(cap.color_temp_min)
            self._ct_max = int(cap.color_temp_max)
            blocked = self.temperature_slider.blockSignals(True)
            self.temperature_slider.setRange(self._ct_min, self._ct_max)
            self.temperature_slider.blockSignals(blocked)
        for widget in self._temperature_widgets:
            widget.setVisible(supported)

        self.default_button.setVisible(cap.supports_streaming)
        self.default_button.setEnabled(not primary and cap.supports_streaming)
        self.default_button.setText(
            "Default Device" if primary else "Set as Default"
        )
        self.reset_zones_button.setEnabled(
            bool(device.get("segment_count_override"))
        )
        self.zones_button.setVisible(transport in ("", "lan") and cap.supports_segments)
        self.reset_zones_button.setVisible(transport in ("", "lan") and cap.supports_segments)
        self.panel_tools_button.setVisible(cap.is_matrix)
        blocked = self.connection_combo.blockSignals(True)
        self.connection_combo.clear()
        local = device.get("local_transport")
        cloud = device.get("cloud_transport")
        if local and device.get("ip"):
            local_cap = capabilities_for_device({**device, "transport": local})
            self.connection_combo.addItem("Local Wi-Fi · " + ("screen and music sync" if local_cap.supports_streaming else "manual controls"), local)
        if cloud and device.get("account_id"):
            self.connection_combo.addItem("Account · manual controls", cloud)
        selected = self.connection_combo.findData(transport)
        if selected >= 0:
            self.connection_combo.setCurrentIndex(selected)
        self.connection_combo.blockSignals(blocked)
        self.connection_combo.setVisible(self.connection_combo.count() > 1)
        # A reused inspector must not display the previous device's values.
        self.brightness_value.setText(UNKNOWN_VALUE)
        self.temperature_value.setText(UNKNOWN_VALUE)
        self._show_color(None, None)
        self._refresh_presets()
        self.set_state(state)
        self.energy.set_device(device, state)
        if not cap.supports_brightness:
            self.brightness_value.setText(UNKNOWN_VALUE)

    def set_available_size(self, width: int, height: int) -> None:
        """Reflow to the viewport, rather than our potentially taller content."""
        self._available_height = height
        compact = height < 720
        inset = 12 if compact else 18
        self.layout().setContentsMargins(inset, inset, inset, inset)
        self.layout().setSpacing(10 if compact else 14)
        self.energy.set_available_size(max(0, min(width, self.maximumWidth()) - inset * 2), height)

    def _connection_selected(self, _index: int) -> None:
        transport = self.connection_combo.currentData()
        if self._index >= 0 and transport:
            self.connection_changed.emit(self._index, transport)

    def set_state(self, state: Dict[str, Any]) -> None:
        if not state:
            return
        self.energy.set_state(state)
        self.readback_value.setText(
            "Live" if state.get("readback_supported", True)
            else "Last command only"
        )
        self._set_power_visual(power_visual_state(state), state.get("power_on"))

        cap = self._cap
        self.status = describe_device_status(state, cap, self._meters)
        apply_status(self.status_label, self.status_dot, self.status)

        brightness = state.get("brightness")
        if (cap.supports_brightness and isinstance(brightness, int)
                and not self.brightness_slider.isSliderDown() and not self._brightness_timer.isActive()):
            bounded = max(0, min(100, brightness))
            blocked = self.brightness_slider.blockSignals(True)
            self.brightness_slider.setValue(bounded)
            self.brightness_slider.blockSignals(blocked)
            self.brightness_value.setText(f"{bounded}%")

        if cap.supports_color:
            color, kelvin = state_display_color(state, cap)
            if color is not None:
                self._show_color(color, kelvin)

        color_temp = state.get("color_temp")
        if (cap.supports_white and color_temp and not self.temperature_slider.isSliderDown()
                and not self._temperature_timer.isActive()):
            bounded_temp = max(
                self._ct_min, min(self._ct_max, int(color_temp))
            )
            blocked = self.temperature_slider.blockSignals(True)
            self.temperature_slider.setValue(bounded_temp)
            self.temperature_slider.blockSignals(blocked)
            self.temperature_value.setText(f"{bounded_temp}K")

    def _set_power_visual(self, state: str, last_known: Optional[bool] = None) -> None:
        visual = (state, last_known if state == "offline" else None)
        if getattr(self, "_power_visual_state", None) == visual:
            return
        self._power_visual_state = visual
        set_power_button_state(self.power_button, state, last_known=last_known)

    def _on_brightness(self, value: int) -> None:
        self.brightness_value.setText(f"{value}%")
        self._brightness_timer.start()

    def _commit_brightness(self) -> None:
        if self._index >= 0 and self._cap.supports_brightness:
            self.brightness_changed.emit(
                self._index, self.brightness_slider.value()
            )

    def _on_temperature(self, value: int) -> None:
        self.temperature_value.setText(f"{value}K")
        self._temperature_timer.start()

    def _commit_temperature(self) -> None:
        if self._index >= 0 and self._cap.supports_white:
            self.color_temp_changed.emit(
                self._index, self.temperature_slider.value()
            )

    # ------------------------------------------------------------------ colour

    def current_color(self) -> Optional[QColor]:
        return QColor(self._current_color) if self._current_color is not None else None

    def _show_color(self, color: Optional[QColor], kelvin: Optional[int]) -> None:
        self._current_color = QColor(color) if color is not None else None
        self._current_kelvin = kelvin
        for swatch in (self.color_button, self.status_swatch):
            swatch.set_color(self._current_color, kelvin)
        description = swatch_tooltip(self._current_color, kelvin, action="")
        self.color_hint.setText(description)
        self.status_swatch.setToolTip(description)
        self.color_button.setToolTip(swatch_tooltip(self._current_color, kelvin, action="click for more colours"))
        self.color_presets.mark_current(self._current_color)
        if self._secondary_color is not None:
            self._show_secondary_color_value()  # It shows the main colour until one is chosen.

    def _refresh_presets(self) -> None:
        white = white_range(self._cap) if self._cap.supports_white else None
        self.color_presets.set_choices([*preset_choices(white), *recent_choices()], self._current_color)

    def _choose_preset(self, choice: SwatchChoice) -> None:
        if self._index < 0 or not self._cap.supports_color:
            return
        if choice.kelvin:
            self._show_color(QColor(choice.color), int(choice.kelvin))
            self.color_temp_changed.emit(self._index, int(choice.kelvin))
            return
        self._show_color(QColor(choice.color), None)
        self.color_picked.emit(self._index, QColor(choice.color))

    def _pick_color(self) -> None:
        if self._index < 0 or not self._cap.supports_color:
            return
        chosen = QColorDialog.getColor(
            self._current_color or QColor("#FFFFFF"), self, "Choose device colour"
        )
        if chosen.isValid() and self._index >= 0:
            remember_recent_color(chosen)
            self._show_color(chosen, None)
            self._refresh_presets()
            self.color_picked.emit(self._index, chosen)


__all__ = ["DeviceInspector"]
