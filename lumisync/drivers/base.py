"""The transport-agnostic device interface every driver implements."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

RGB = Tuple[int, int, int]


@dataclass(frozen=True)
class DeviceCapabilities:
    """What a device can do and how the sync engine should target it.

    ``segment_count`` is the number of independently colored zones. For a linear
    strip that is its zone count; for a matrix it is ``rows * cols`` and
    ``matrix_size`` is set so drivers/UX can lay pixels out in 2D.
    """

    transport: str = "lan"              # "lan" | "ble"
    segment_count: int = 10
    supports_power: bool = True
    supports_brightness: bool = True
    supports_color: bool = True
    supports_segments: bool = True      # per-zone / per-pixel color stream
    supports_white: bool = False        # tunable color temperature
    color_temp_min: int = 0
    color_temp_max: int = 0
    matrix_size: Optional[Tuple[int, int]] = None  # (cols, rows) for pixel grids

    @property
    def is_matrix(self) -> bool:
        return self.matrix_size is not None


class TransportAdapter(abc.ABC):
    """Uniform control surface for a single physical device.

    Implementations wrap a transport (LAN UDP, BLE GATT, …). The sync engine and
    GUI controllers should depend only on this interface, never on a concrete
    transport, so new device families slot in without touching sync logic.
    """

    def __init__(self, device: Dict[str, Any]) -> None:
        self.device = device

    @property
    @abc.abstractmethod
    def capabilities(self) -> DeviceCapabilities:
        """Return the device's capabilities."""

    @abc.abstractmethod
    def set_power(self, on: bool) -> None:
        """Turn the device on or off."""

    @abc.abstractmethod
    def set_brightness(self, percent: int) -> None:
        """Set overall brightness (0-100)."""

    @abc.abstractmethod
    def set_color(self, r: int, g: int, b: int) -> None:
        """Set a single solid color for the whole device."""

    @abc.abstractmethod
    def set_segments(self, colors: List[RGB]) -> None:
        """Push one per-segment (or per-pixel) color frame.

        This is the hot path used by monitor/music sync. ``colors`` should hold
        ``capabilities.segment_count`` entries; drivers resize defensively.
        """

    def set_color_temperature(self, kelvin: int) -> None:
        """Set a tunable-white color temperature (Kelvin).

        Default implementation approximates it with an RGB color; transports with
        a native white channel should override.
        """
        from ..utils.colors import kelvin_to_rgb

        r, g, b = kelvin_to_rgb(kelvin)
        self.set_color(r, g, b)

    def begin_stream(self) -> None:
        """Prepare the device for a run of :meth:`set_segments` frames.

        Called once when a sync mode starts. Govee enables Razer mode here; a
        matrix display may switch to its DIY/pixel mode. Default is a no-op.
        """

    def end_stream(self) -> None:
        """Tear down after a segment stream. Default is a no-op."""

    def query_status(self) -> Optional[Dict[str, Any]]:
        """Return current device status, or None if unsupported/unreachable."""
        return None

    def close(self) -> None:
        """Release any transport resources. Safe to call more than once."""

    # Context-manager sugar so callers can scope a connection.
    def __enter__(self) -> "TransportAdapter":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()
