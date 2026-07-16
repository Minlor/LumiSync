"""Govee LAN driver — wraps the existing UDP protocol behind TransportAdapter.

This adds no new protocol; it adapts ``lumisync.connection`` (the proven LAN
path) to the shared driver interface so the Govee strip and, later, a BLE
iDotMatrix look identical to the sync engine.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .. import connection, sku_catalog, utils
from .base import RGB, DeviceCapabilities, TransportAdapter


class GoveeLanAdapter(TransportAdapter):
    """Control one Govee device over LAN UDP.

    A socket may be shared across devices (the sync engine already keeps one
    socket for all participants). Pass ``owns_socket=True`` to have the adapter
    create and close its own.
    """

    def __init__(
        self,
        device: Dict[str, Any],
        server=None,
        *,
        owns_socket: bool = False,
    ) -> None:
        super().__init__(device)
        self._owns_socket = owns_socket or server is None
        self.server = server if server is not None else connection.create_lan_socket()

    @property
    def capabilities(self) -> DeviceCapabilities:
        sku = self.device.get("model") or self.device.get("sku")
        cap = sku_catalog.capabilities_for(sku)
        return DeviceCapabilities(
            transport="lan",
            segment_count=connection.get_segment_count(self.device, default=10),
            supports_power=True,
            supports_brightness=True,
            supports_color=True,
            supports_segments=(cap.supports_razer if cap else True),
            supports_white=bool(cap and cap.color_temp_max > 0),
            color_temp_min=cap.color_temp_min if cap else 0,
            color_temp_max=cap.color_temp_max if cap else 0,
        )

    def set_power(self, on: bool) -> None:
        connection.switch(self.server, self.device, on)

    def set_brightness(self, percent: int) -> None:
        connection.set_brightness(self.server, self.device, int(percent))

    def set_color(self, r: int, g: int, b: int) -> None:
        connection.set_color(self.server, self.device, int(r), int(g), int(b))

    def set_segments(self, colors: List[RGB]) -> None:
        count = self.capabilities.segment_count
        fitted = utils.fit_colors_to_count(list(colors), count)
        connection.send_razer_data(
            self.server, self.device, utils.convert_colors(fitted)
        )

    def set_color_temperature(self, kelvin: int) -> None:
        connection.set_color_temp(self.server, self.device, int(kelvin))

    def set_razer_enabled(self, enabled: bool) -> None:
        """Toggle the device's Razer/segment mode (needed before segment streams)."""
        connection.switch_razer(self.server, self.device, enabled)

    def begin_stream(self) -> None:
        # Segment streams require Razer mode on Govee devices.
        connection.switch_razer(self.server, self.device, True)

    def end_stream(self) -> None:
        connection.switch_razer(self.server, self.device, False)

    def query_status(self) -> Optional[Dict[str, Any]]:
        return connection.query_status(self.server, self.device)

    def close(self) -> None:
        if self._owns_socket and self.server is not None:
            try:
                self.server.close()
            finally:
                self.server = None
