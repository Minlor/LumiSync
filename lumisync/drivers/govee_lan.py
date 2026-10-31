"""Govee LAN driver — wraps the existing UDP protocol behind TransportAdapter.

This adds no new protocol; it adapts ``lumisync.connection`` (the proven LAN
path) to the shared driver interface so the Govee strip and, later, a BLE
iDotMatrix look identical to the sync engine.
"""

from __future__ import annotations

import socket
from typing import Any, Dict, List, Optional

from .. import connection, sku_catalog, utils
from ..device import DeviceLike
from .base import RGB, DeviceCapabilities, TransportAdapter


class GoveeLanAdapter(TransportAdapter):
    """Control one Govee device over LAN UDP.

    A socket may be shared across devices (the sync engine already keeps one
    socket for all participants). Pass ``owns_socket=True`` to have the adapter
    create and close its own.
    """

    def __init__(
        self,
        device: DeviceLike,
        server=None,
        *,
        owns_socket: bool = False,
    ) -> None:
        super().__init__(device)
        self._owns_socket = owns_socket or server is None
        self.server: Optional[socket.socket] = (
            server if server is not None else connection.create_lan_socket()
        )

    def _socket(self) -> socket.socket:
        if self.server is None:
            raise RuntimeError("This Govee adapter has already been closed.")
        return self.server

    @classmethod
    def capabilities_for(cls, device: DeviceLike) -> DeviceCapabilities:
        """Compute capabilities without opening a socket."""
        sku = device.get("sku") or device.get("model")
        cap = sku_catalog.capabilities_for(sku)
        return DeviceCapabilities(
            transport="lan",
            segment_count=connection.get_segment_count(device, default=10),
            supports_power=True,
            supports_brightness=True,
            supports_color=cap.supports_color if cap else True,
            supports_segments=(cap.supports_razer if cap else True),
            supports_streaming=(cap.supports_razer if cap else True),
            supports_white=bool(cap and cap.color_temp_max > 0),
            color_temp_min=cap.color_temp_min if cap else 0,
            color_temp_max=cap.color_temp_max if cap else 0,
        )

    @property
    def capabilities(self) -> DeviceCapabilities:
        return self.capabilities_for(self.device)

    def set_power(self, on: bool) -> None:
        sku = self.device.get("sku") or self.device.get("model") or ""
        if connection.is_h6080_sku(sku):
            connection.send_h6080_switch(self._socket(), self.device, on)
        else:
            connection.switch(self._socket(), self.device, on)

    def set_brightness(self, percent: int) -> None:
        connection.set_brightness(self._socket(), self.device, int(percent))

    def set_color(self, r: int, g: int, b: int) -> None:
        sku = self.device.get("sku") or self.device.get("model") or ""
        if connection.is_h6080_sku(sku):
            connection.send_h6080_color(
                self._socket(), self.device, int(r), int(g), int(b)
            )
        elif connection.is_0x0b_sku(sku):
            connection.send_0x0b_color(self._socket(), self.device, int(r), int(g), int(b))
        else:
            connection.set_color(self._socket(), self.device, int(r), int(g), int(b))

    def set_segments(self, colors: List[RGB]) -> None:
        sku = self.device.get("sku") or self.device.get("model") or ""
        count = self.capabilities.segment_count
        fitted = utils.fit_colors_to_count(list(colors), count)
        mode = str(self.device.get("segment_mode", "standard")).lower()
        if connection.is_h6080_sku(sku):
            # H6080 devices use the binary segment frame (Phase 2)
            connection.send_segment_frame(self._socket(), self.device, fitted)
        elif mode == "multi_pack":
            connection.send_segment_frame_multi_pack(self._socket(), self.device, fitted)
        elif mode == "gradient":
            connection.send_segment_frame_gradient(self._socket(), self.device, fitted)
        else:
            connection.send_razer_data(
                self._socket(), self.device, utils.convert_colors(fitted)
            )

    def set_color_temperature(self, kelvin: int) -> None:
        sku = self.device.get("sku") or self.device.get("model") or ""
        if connection.is_h6080_sku(sku):
            connection.send_h6080_color(
                self._socket(), self.device, 0, 0, 0, kelvin=int(kelvin)
            )
        else:
            connection.set_color_temp(self._socket(), self.device, int(kelvin))

    def set_razer_enabled(self, enabled: bool) -> None:
        """Toggle the device's Razer/segment mode (needed before segment streams)."""
        connection.switch_razer(self._socket(), self.device, enabled)

    def begin_stream(self) -> None:
        # Segment streams require Razer mode on Govee devices.
        connection.switch_razer(self._socket(), self.device, True)

    def end_stream(self) -> None:
        connection.switch_razer(self._socket(), self.device, False)

    def query_status(self) -> Optional[Dict[str, Any]]:
        return connection.query_status(self._socket(), self.device)

    def close(self) -> None:
        if self._owns_socket and self.server is not None:
            try:
                self.server.close()
            finally:
                self.server = None
