"""Background workers for LAN, Bluetooth and status discovery.

These used to live in ``device_controller.py`` alongside control, persistence
and account management. They are independent of the controller's command queue,
so keeping them in their own module makes the discovery lifecycle easier to
reason about.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Dict, List

from PySide6.QtCore import QObject, Signal, Slot, QThread

from ... import connection, devices, groups
from ...drivers import pool, tuya_presence
from ...drivers.registry import is_cloud

# Device fields that status sync learns from the device itself (Tuya devices
# self-describe their data points in status replies). When one is stamped
# during a refresh, the descriptor is saved so the knowledge survives restarts
# for every user, not just this session.
DISCOVERY_KEYS = ("tuya_paint_dp", "tuya_paint_zones", "colour_format", "dp_schema")


class DeviceDiscoveryWorker(QObject):
    """Worker object for device discovery in a separate thread."""

    finished = Signal(list, int, int)  # devices, selected_index, LAN replies
    error = Signal(str)  # error message

    def run(self):
        """Run device discovery in background thread.

        Falls back to a unicast subnet sweep when the multicast scan finds
        nothing, so devices are still discovered on multicast-hostile networks.
        """
        try:
            settings = devices.discover_lan_devices(preserve_existing=True, persist=False)

            if int(settings.get("lastDiscoveryCount", 0)) == 0:
                settings = devices.discover_lan_devices(
                    preserve_existing=True, deep=True, persist=False
                )

            self.finished.emit(
                settings.get("lastDiscoveryDevices", settings["devices"]),
                settings["selectedDevice"],
                int(settings.get("lastDiscoveryCount", 0)),
            )
        except Exception as e:
            self.error.emit(str(e))


class DeviceStatusWorker(QObject):
    """Query current LAN state for one or more devices off the GUI thread."""

    state_updated = Signal(int, dict)
    finished = Signal()
    error = Signal(str)

    def __init__(self, indexed_devices: List[tuple[int, Dict[str, Any]]]):
        super().__init__()
        # Keep the live descriptors (not copies): status discovery — paint DP,
        # zone grid, colour format — is stamped onto them by the adapter and
        # then persisted by the controller's _flush_discovery.
        self.indexed_devices = list(indexed_devices)

    @Slot()
    def run(self) -> None:
        server = None
        try:
            if any(
                device.get("ip") and str(device.get("transport", "lan")) in ("", "lan")
                for _, device in self.indexed_devices
            ):
                try:
                    server = connection.create_lan_socket(timeout=0.45)
                except OSError as exc:
                    server = None
                    self.error.emit(str(exc))

            for index, device in self.indexed_devices:
                if QThread.currentThread().isInterruptionRequested():
                    break
                try:
                    query_started_at = time.time()
                    status = None
                    transport = str(device.get("transport", "lan"))
                    if transport in ("", "lan") and server is not None and device.get("ip"):
                        status = connection.query_status(server, device, timeout=0.45)
                    elif transport not in ("", "lan", "ble"):
                        # LSC strips announce themselves on the LAN (UDP 6667).
                        # A fresh beat proves reachability without any I/O, and
                        # no beat for over ~25 s proves absence offline, both
                        # more reliable than a single status request while the
                        # strip streams paint.
                        identity = str(device.get("device_id") or device.get("devId") or "")
                        age = tuya_presence.presence_age(identity) if identity else None
                        if age is not None and age <= tuya_presence.PRESENCE_FRESH_AFTER:
                            self.state_updated.emit(
                                index,
                                {
                                    "online": True,
                                    "stale": False,
                                    "status_source": "confirmed",
                                    "readback_supported": True,
                                    "last_error": None,
                                    "last_seen": time.time(),
                                    "device_key": groups.device_key(device),
                                    "query_started_at": query_started_at,
                                },
                            )
                            continue
                        if age is not None:
                            self.state_updated.emit(
                                index,
                                {
                                    "online": False,
                                    "stale": True,
                                    "status_source": "offline",
                                    "readback_supported": True,
                                    "last_error": f"No LAN broadcast for {int(age)}s",
                                    "device_key": groups.device_key(device),
                                    "query_started_at": query_started_at,
                                },
                            )
                            continue
                        adapter = pool.acquire(device)
                        try:
                            status = adapter.query_status()
                            if status is not None and pool.is_pooled(device):
                                # Pooled adapters hold their own descriptor copy;
                                # mirror what the adapter discovered (paint DP,
                                # zone grid, formats) onto the live device so the
                                # controller persists it.
                                learned = getattr(adapter, "device", None)
                                if isinstance(learned, Mapping):
                                    for field in ("tuya_paint_dp", "tuya_paint_zones",
                                                  "colour_format", "dp_schema"):
                                        value = learned.get(field)
                                        if value is not None and device.get(field) != value:
                                            device[field] = value
                        finally:
                            if not pool.is_pooled(device):
                                adapter.close()
                    if status is None:
                        self.state_updated.emit(
                            index,
                            {
                                "online": False,
                                "stale": True,
                                "status_source": "offline",
                                "readback_supported": True,
                                "last_error": "No status reply",
                                "device_key": groups.device_key(device),
                                "query_started_at": query_started_at,
                            },
                        )
                    else:
                        status.update(
                            {
                                "online": status.get("online", True),
                                "stale": not status.get("online", True),
                                "status_source": "confirmed" if status.get("online", True) else "offline",
                                "readback_supported": True,
                                "last_error": None,
                                "last_seen": time.time(),
                                "device_key": groups.device_key(device),
                                "query_started_at": query_started_at,
                            }
                        )
                        self.state_updated.emit(index, status)
                except Exception as exc:
                    from ...accounts.errors import AccountError
                    message = str(exc) if isinstance(exc, AccountError) or not is_cloud(device) else "Could not read account device status. Reconnect the account and try again."
                    self.state_updated.emit(
                        index,
                        {
                            "online": False,
                            "stale": True,
                            "status_source": "offline",
                            "readback_supported": True,
                            "last_error": message,
                            "device_key": groups.device_key(device),
                            "query_started_at": query_started_at,
                        },
                    )
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            if server is not None:
                try:
                    server.close()
                except Exception:
                    pass
            self.finished.emit()


class BleScanWorker(QObject):
    """Run the (blocking, ~8s) BLE discovery scan off the GUI thread."""

    finished = Signal(list)  # raw discovery entries
    error = Signal(str)

    def run(self) -> None:
        try:
            from ...drivers.idotmatrix_ble import IDotMatrixBleAdapter

            self.finished.emit(IDotMatrixBleAdapter.discover(timeout=8.0))
        except Exception as e:
            self.error.emit(str(e))
