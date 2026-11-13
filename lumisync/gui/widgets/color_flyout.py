"""Quick colour controls: a current-colour swatch, preset rows and a flyout.

The card's swatch shows what a light is (or was last told to be) showing; a
click opens :class:`ColorFlyout` with preset colours, the user's recent custom
colours and the full colour dialog. The inspector reuses :class:`SwatchRow`
inline. Everything is painted from the ``fl_*`` tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QConicalGradient, QGuiApplication, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QGridLayout,
    QLabel,
    QLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...utils.colors import kelvin_to_rgb
from ..app_settings import app_settings
from ..theme import TOKENS, qcolor
from ..utils.flow_layout import FlowLayout

RECENT_COLORS_KEY = "ui/recent_colors"
MAX_RECENT_COLORS = 5

WARM_WHITE_KELVIN = 2700
COOL_WHITE_KELVIN = 6500
WARM_WHITE_RGB = "#FFC58F"
COOL_WHITE_RGB = "#FFFFFF"

# Saturated values: they read clearly as swatches and drive LEDs cleanly.
PRESET_COLORS: Tuple[Tuple[str, str], ...] = (
    ("Red", "#FF1A1A"),
    ("Orange", "#FF7A00"),
    ("Yellow", "#FFD000"),
    ("Green", "#1ED760"),
    ("Cyan", "#00D2F0"),
    ("Blue", "#1F5BFF"),
    ("Purple", "#8A3DFF"),
    ("Pink", "#FF3D9A"),
)


@dataclass(frozen=True)
class SwatchChoice:
    """One selectable colour. ``kelvin`` set means "send a white temperature"."""

    name: str
    color: QColor
    kelvin: Optional[int] = None
    recent: bool = False


def hex_name(color: QColor) -> str:
    return QColor(color).name(QColor.NameFormat.HexRgb).upper()


def swatch_tooltip(color: Optional[QColor], kelvin: Optional[int] = None, *, action: str = "click to change") -> str:
    """``Colour #RRGGBB · click to change`` (or ``White 2700K · …``)."""
    suffix = f" · {action}" if action else ""
    if color is None:
        return "No colour reported yet" + (" · click to pick a colour" if action else "")
    if kelvin:
        return f"White {int(kelvin)}K{suffix}"
    return f"Colour {hex_name(color)}{suffix}"


def state_display_color(
    state: Dict[str, Any], cap: Any = None
) -> Tuple[Optional[QColor], Optional[int]]:
    """The colour a light shows: white temperature first, then RGB.

    ``state`` holds confirmed readback or, until a device answers, the last
    command LumiSync sent, so this is "current, else last sent".
    """
    supports_white = cap is None or bool(getattr(cap, "supports_white", False))
    supports_color = cap is None or bool(getattr(cap, "supports_color", False))
    kelvin = state.get("color_temp")
    if supports_white and isinstance(kelvin, (int, float)) and not isinstance(kelvin, bool) and kelvin > 0:
        return QColor(*kelvin_to_rgb(int(kelvin))), int(kelvin)
    color = state.get("color")
    if supports_color and isinstance(color, (tuple, list)) and len(color) >= 3:
        try:
            r, g, b = (max(0, min(255, int(c))) for c in color[:3])
        except (TypeError, ValueError):
            return None, None
        return QColor(r, g, b), None
    return None, None


def white_range(cap: Any) -> Optional[Tuple[int, int]]:
    """The device's tunable-white range, or None when it has no white channel."""
    if cap is None or not getattr(cap, "supports_white", False):
        return None
    low, high = int(getattr(cap, "color_temp_min", 0) or 0), int(getattr(cap, "color_temp_max", 0) or 0)
    return (low, high) if high > low > 0 else None


def preset_choices(white: Optional[Tuple[int, int]] = None) -> List[SwatchChoice]:
    """Warm white, cool white, then the colour presets.

    Whites use the colour-temperature path when the device has one (clamped
    to its range); otherwise they are sent as plain RGB.
    """
    choices: List[SwatchChoice] = []
    for name, kelvin, fallback in (
        ("Warm white", WARM_WHITE_KELVIN, WARM_WHITE_RGB),
        ("Cool white", COOL_WHITE_KELVIN, COOL_WHITE_RGB),
    ):
        if white is not None:
            bounded = max(white[0], min(white[1], kelvin))
            choices.append(SwatchChoice(name, QColor(*kelvin_to_rgb(bounded)), bounded))
        else:
            choices.append(SwatchChoice(name, QColor(fallback)))
    choices.extend(SwatchChoice(name, QColor(value)) for name, value in PRESET_COLORS)
    return choices


def recent_colors() -> List[QColor]:
    """Up to five custom colours, most recent first (never raises)."""
    try:
        raw = app_settings().value(RECENT_COLORS_KEY, [])
    except Exception:
        return []
    if isinstance(raw, str):
        raw = [part for part in raw.split(",") if part]
    if not isinstance(raw, (list, tuple)):
        return []
    colors: List[QColor] = []
    for value in raw:
        color = QColor(str(value).strip())
        if color.isValid() and all(hex_name(color) != hex_name(seen) for seen in colors):
            colors.append(color)
            if len(colors) >= MAX_RECENT_COLORS:
                break
    return colors


def remember_recent_color(color: QColor) -> List[QColor]:
    """Put a custom colour first in the recent list and save it."""
    color = QColor(color)
    if not color.isValid():
        return recent_colors()
    updated = [color, *(c for c in recent_colors() if hex_name(c) != hex_name(color))][:MAX_RECENT_COLORS]
    try:
        settings = app_settings()
        settings.setValue(RECENT_COLORS_KEY, [hex_name(c) for c in updated])
        settings.sync()
    except Exception:
        pass
    return updated


def recent_choices() -> List[SwatchChoice]:
    return [SwatchChoice(f"Recent colour {hex_name(color)}", color, recent=True) for color in recent_colors()]


class ColorSwatchButton(QAbstractButton):
    """A round colour swatch; a hue ring marks "no colour known yet"."""

    def __init__(
        self,
        color: Optional[QColor] = None,
        parent: Optional[QWidget] = None,
        *,
        diameter: int = 22,
        kelvin: Optional[int] = None,
    ):
        super().__init__(parent)
        self._diameter = int(diameter)
        self._color: Optional[QColor] = QColor(color) if color is not None else None
        self._kelvin = kelvin
        self._current = False
        self.setObjectName("ColorSwatch")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        side = self._diameter + 6
        self.setFixedSize(side, side)

    # ------------------------------------------------------------------ api

    def color(self) -> Optional[QColor]:
        return QColor(self._color) if self._color is not None else None

    def kelvin(self) -> Optional[int]:
        return self._kelvin

    def set_color(self, color: Optional[QColor], kelvin: Optional[int] = None) -> None:
        new = QColor(color) if color is not None else None
        if (new is None) == (self._color is None) and (new is None or new == self._color) and kelvin == self._kelvin:
            return
        self._color = new
        self._kelvin = kelvin
        self.update()

    def set_current(self, current: bool) -> None:
        """Mark the swatch that matches what the light shows now."""
        if self._current != bool(current):
            self._current = bool(current)
            self.update()

    def is_current(self) -> bool:
        return self._current

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        side = self._diameter + 6
        return QSize(side, side)

    # ------------------------------------------------------------------ events

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.isEnabled():
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        radius = self._diameter / 2
        if not self.isEnabled():
            painter.setOpacity(0.45)

        if self._color is None:
            # Neutral fill inside a soft hue ring: "pick a colour".
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(qcolor("fl_control"))
            painter.drawEllipse(center, radius, radius)
            gradient = QConicalGradient(center, 90)
            for stop, hue in ((0.0, 0), (1 / 6, 60), (2 / 6, 120), (3 / 6, 180), (4 / 6, 240), (5 / 6, 300), (1.0, 359)):
                gradient.setColorAt(stop, QColor.fromHsv(hue, 170, 235, 200))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(gradient, 2.2))
            painter.drawEllipse(center, radius - 1.1, radius - 1.1)
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._color)
            painter.drawEllipse(center, radius, radius)
            # A stronger ring keeps very dark colours visible on dark cards.
            stroke = qcolor("fl_control_stroke") if self._color.lightness() > 70 else QColor(255, 255, 255, 46)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(stroke, 1))
            painter.drawEllipse(center, radius - 0.5, radius - 0.5)

        outer = radius + 2.25
        if self.hasFocus():
            painter.setPen(QPen(qcolor("fl_text"), 1.5))
            painter.drawEllipse(center, outer, outer)
        elif self._current:
            painter.setPen(QPen(qcolor("fl_accent"), 1.5))
            painter.drawEllipse(center, outer, outer)
        elif self.isDown():
            painter.setPen(QPen(qcolor("fl_text_tertiary"), 1.2))
            painter.drawEllipse(center, outer, outer)
        elif self.underMouse() and self.isEnabled():
            painter.setPen(QPen(qcolor("fl_text_secondary"), 1.2))
            painter.drawEllipse(center, outer, outer)


class SwatchRow(QWidget):
    """Preset and recent colour swatches with arrow-key navigation.

    With ``columns`` the swatches form a fixed grid (the flyout); without it
    they flow to the available width (the inspector).
    """

    chosen = Signal(object)  # SwatchChoice

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        diameter: int = 24,
        spacing: int = 6,
        columns: Optional[int] = None,
    ):
        super().__init__(parent)
        self._diameter = diameter
        self._columns = columns
        self._choices: List[SwatchChoice] = []
        self._buttons: List[ColorSwatchButton] = []
        layout: QLayout
        if columns:
            grid = QGridLayout(self)
            grid.setHorizontalSpacing(spacing)
            grid.setVerticalSpacing(spacing)
            layout = grid
        else:
            layout = FlowLayout(self, h_spacing=spacing, v_spacing=spacing)
        layout.setContentsMargins(0, 0, 0, 0)
        self._layout = layout
        self.setAccessibleName("Colour presets")

    def choices(self) -> List[SwatchChoice]:
        return list(self._choices)

    def buttons(self) -> List[ColorSwatchButton]:
        return list(self._buttons)

    def set_choices(self, choices: Sequence[SwatchChoice], current: Optional[QColor] = None) -> None:
        for button in self._buttons:
            self._layout.removeWidget(button)
            button.deleteLater()
        self._buttons = []
        self._choices = list(choices)
        for position, choice in enumerate(self._choices):
            button = ColorSwatchButton(choice.color, self, diameter=self._diameter, kelvin=choice.kelvin)
            button.setToolTip(choice.name if not choice.recent else f"Recent · {hex_name(choice.color)}")
            button.setAccessibleName(choice.name)
            button.clicked.connect(lambda _checked=False, value=choice: self.chosen.emit(value))
            button.installEventFilter(self)
            if isinstance(self._layout, QGridLayout) and self._columns:
                self._layout.addWidget(button, position // self._columns, position % self._columns)
            else:
                self._layout.addWidget(button)
            self._buttons.append(button)
        if isinstance(self._layout, QGridLayout) and self._columns:
            # A short row (three recent colours) keeps the grid's spacing
            # instead of spreading across the full width.
            self._layout.setColumnStretch(self._columns, 1)
        self.mark_current(current)
        self.updateGeometry()

    def mark_current(self, color: Optional[QColor]) -> None:
        target = hex_name(color) if color is not None else None
        for button in self._buttons:
            swatch = button.color()
            button.set_current(target is not None and swatch is not None and hex_name(swatch) == target)

    def focus_first(self) -> None:
        current = next((button for button in self._buttons if button.is_current()), None)
        target = current or (self._buttons[0] if self._buttons else None)
        if target is not None:
            target.setFocus(Qt.FocusReason.TabFocusReason)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt API
        return self._layout.hasHeightForWidth()

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt API
        return self._layout.heightForWidth(width)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        if self._columns or not self._buttons:
            return super().sizeHint()
        side = self._buttons[0].sizeHint().width()
        spacing = 6
        width = len(self._buttons) * side + (len(self._buttons) - 1) * spacing
        return QSize(width, side)

    def _neighbour(self, button: ColorSwatchButton, key: int) -> Optional[ColorSwatchButton]:
        if button not in self._buttons:
            return None
        index = self._buttons.index(button)
        if key == Qt.Key.Key_Left:
            return self._buttons[index - 1] if index > 0 else None
        if key == Qt.Key.Key_Right:
            return self._buttons[index + 1] if index + 1 < len(self._buttons) else None
        if key in (Qt.Key.Key_Home, Qt.Key.Key_End):
            return self._buttons[0 if key == Qt.Key.Key_Home else -1]
        # Up/Down: the nearest swatch in the previous/next visual row.
        here = button.geometry().center()
        rows = [b for b in self._buttons if (b.geometry().center().y() < here.y() - 2) == (key == Qt.Key.Key_Up)
                and abs(b.geometry().center().y() - here.y()) > 2]
        if not rows:
            return None
        row_y = (max if key == Qt.Key.Key_Up else min)(b.geometry().center().y() for b in rows)
        same_row = [b for b in rows if b.geometry().center().y() == row_y]
        return min(same_row, key=lambda b: abs(b.geometry().center().x() - here.x()))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt API
        is_key = isinstance(event, QKeyEvent) and event.type() == QEvent.Type.KeyPress
        if isinstance(watched, ColorSwatchButton) and isinstance(event, QKeyEvent) and is_key:
            key = event.key()
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
                       Qt.Key.Key_Home, Qt.Key.Key_End):
                target = self._neighbour(watched, key)
                if target is not None:
                    target.setFocus(Qt.FocusReason.TabFocusReason)
                return True
        return super().eventFilter(watched, event)


def _caption(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("FlyoutCaption")
    label.setStyleSheet(
        f"QLabel#FlyoutCaption {{ color: {TOKENS['fl_text_secondary']}; background: transparent;"
        " font-size: 9pt; font-weight: 600; }}"
    )
    return label


class ColorFlyout(QWidget):
    """Frameless popup anchored to a swatch: presets, recents, full picker.

    Escape or a click outside closes it; choosing a colour applies it and
    closes. Arrow keys move between swatches and Enter chooses.
    """

    color_chosen = Signal(QColor)
    temperature_chosen = Signal(int)
    more_requested = Signal()

    SHADOW = 14
    RADIUS = 8

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        white: Optional[Tuple[int, int]] = None,
        current: Optional[QColor] = None,
    ):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setObjectName("ColorFlyout")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAccessibleName("Quick colours")

        margin = self.SHADOW
        root = QVBoxLayout(self)
        root.setContentsMargins(margin + 12, margin + 10, margin + 12, margin + 8)
        root.setSpacing(8)

        root.addWidget(_caption("Colours", self))
        self.presets = SwatchRow(self, diameter=26, spacing=8, columns=5)
        self.presets.set_choices(preset_choices(white), current)
        self.presets.chosen.connect(self._choose)
        root.addWidget(self.presets)

        recents = recent_choices()
        self.recent_caption = _caption("Recent", self)
        self.recents = SwatchRow(self, diameter=26, spacing=8, columns=5)
        self.recents.set_choices(recents, current)
        self.recents.chosen.connect(self._choose)
        self.recent_caption.setVisible(bool(recents))
        self.recents.setVisible(bool(recents))
        root.addSpacing(2)
        root.addWidget(self.recent_caption)
        root.addWidget(self.recents)

        divider = QWidget(self)
        divider.setFixedHeight(1)
        divider.setObjectName("FlyoutDivider")
        divider.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        divider.setStyleSheet(f"QWidget#FlyoutDivider {{ background: {TOKENS['fl_divider']}; }}")
        root.addSpacing(2)
        root.addWidget(divider)

        self.more_button = QPushButton("More colours…", self)
        self.more_button.setObjectName("FlyoutMore")
        self.more_button.setAutoDefault(False)
        self.more_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.more_button.setStyleSheet(
            "QPushButton#FlyoutMore {"
            f" background: transparent; color: {TOKENS['fl_text']}; border: 1px solid transparent;"
            " border-radius: 4px; padding: 6px 8px; text-align: left; min-height: 20px; }"
            f"QPushButton#FlyoutMore:hover {{ background: {TOKENS['fl_subtle_hover']}; }}"
            f"QPushButton#FlyoutMore:pressed {{ background: {TOKENS['fl_control_pressed']}; }}"
            f"QPushButton#FlyoutMore:focus {{ border: 1px solid {TOKENS['fl_text']}; }}"
        )
        self.more_button.clicked.connect(self._more)
        root.addWidget(self.more_button)

        self.setTabOrder(self.presets.buttons()[-1] if self.presets.buttons() else self, self.more_button)

    # ------------------------------------------------------------------ api

    def popup_below(self, anchor: QWidget) -> None:
        """Show under *anchor* (above it when the screen has no room)."""
        self.adjustSize()
        size = self.sizeHint().expandedTo(self.minimumSizeHint())
        self.resize(size)
        top_left = anchor.mapToGlobal(QPoint(0, anchor.height()))
        x = top_left.x() + anchor.width() // 2 - size.width() // 2
        y = top_left.y() + 4 - self.SHADOW + 2
        # The screen under the anchor's centre: anchor.screen() is the
        # window's screen and is wrong when the window spans monitors.
        screen = QGuiApplication.screenAt(anchor.mapToGlobal(anchor.rect().center())) or anchor.screen()
        if screen is not None:
            area: QRect = screen.availableGeometry()
            x = max(area.left() - self.SHADOW, min(x, area.right() - size.width() + self.SHADOW))
            if y + size.height() - self.SHADOW > area.bottom():
                above = anchor.mapToGlobal(QPoint(0, 0)).y() - size.height() - 4 + self.SHADOW
                y = max(area.top() - self.SHADOW, above)
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self.presets.focus_first()

    def surface_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(self.SHADOW, self.SHADOW, -self.SHADOW, -self.SHADOW)

    # ------------------------------------------------------------------ internals

    def _choose(self, choice: SwatchChoice) -> None:
        if choice.kelvin:
            self.temperature_chosen.emit(int(choice.kelvin))
        else:
            self.color_chosen.emit(QColor(choice.color))
        self.close()

    def _more(self) -> None:
        self.close()
        self.more_requested.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt API
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        surface = self.surface_rect()
        # Soft layered shadow (WinUI flyouts float on a 32 px elevation).
        painter.setPen(Qt.PenStyle.NoPen)
        steps = self.SHADOW
        for step in range(steps, 0, -1):
            alpha = int(30 * (1 - step / (steps + 1)) ** 2)
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(surface.adjusted(-step, -step + 3, step, step + 3),
                                    self.RADIUS + step, self.RADIUS + step)
        painter.setBrush(qcolor("fl_flyout"))
        painter.setPen(QPen(qcolor("fl_flyout_stroke"), 1))
        painter.drawRoundedRect(surface.adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.RADIUS)


__all__ = [
    "COOL_WHITE_KELVIN",
    "ColorFlyout",
    "ColorSwatchButton",
    "PRESET_COLORS",
    "RECENT_COLORS_KEY",
    "SwatchChoice",
    "SwatchRow",
    "WARM_WHITE_KELVIN",
    "WARM_WHITE_RGB",
    "hex_name",
    "preset_choices",
    "recent_choices",
    "recent_colors",
    "remember_recent_color",
    "state_display_color",
    "swatch_tooltip",
    "white_range",
]
