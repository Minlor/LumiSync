"""Pluggable device drivers for LumiSync.

The sync engine produces one list of per-segment RGB colors per frame. A driver
turns that generic color stream into whatever a specific device speaks:

* :class:`~lumisync.drivers.govee_lan.GoveeLanAdapter` — Govee strips over LAN
  UDP (the Razer segment stream LumiSync already uses).
* :class:`~lumisync.drivers.idotmatrix_ble.IDotMatrixBleAdapter` — iDotMatrix
  pixel displays over Bluetooth LE (skeleton; see the module docstring).

Keeping this behind one interface (:class:`~lumisync.drivers.base.TransportAdapter`)
is what lets LumiSync grow beyond Govee without rewriting the sync loops.
"""

from .base import DeviceCapabilities, TransportAdapter

__all__ = ["DeviceCapabilities", "TransportAdapter"]
