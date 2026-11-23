"""Background workers for LAN, Bluetooth and status discovery.

These used to live in ``device_controller.py`` alongside control, persistence
and account management. They are independent of the controller's command queue,
so keeping them in their own module makes the discovery lifecycle easier to
reason about.
"""

from __future__ import annotations

import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any, Dict, List

from PySide6.QtCore import QObject, Signal, Slot, QThread

from ... import connection, devices, groups
from ...device import as_device
from ...drivers import pool, tuya_presence
from ...drivers.registry import create_adapter, is_cloud

# Device fields that status sync learns from the device itself (Tuya devices
# self-describe their data points in status replies). When one is stamped
# during a refresh, the descriptor is saved so the knowledge survives restarts
# for every user, not just this session.
DISCOVERY_KEYS = ("tuya_paint_dp", "tuya_paint_zones", "colour_format", "dp_schema")

ACCOUNT_STATUS_ERROR = "Could not read account device status. Reconnect the account and try again."

# AccountError codes that mean the account itself needs the user (expired
# sign-in, unreadable saved credentials), as opposed to one device or the
# network being unavailable.
ACCOUNT_BROKEN_CODES = frozenset({"authentication", "credential_store"})


def account_error_code(exc: BaseException | None) -> str | None:
    """The AccountError code of *exc*, if it is one."""
    from ...accounts.errors import AccountError

    return exc.code if isinstance(exc, AccountError) else None


@dataclass(frozen=True)
class AccountFailure:
    """A failed account read, returned (not raised) by background actions.

    ``start_task`` turns raised errors into plain text; returning this keeps
    the AccountError code, so only a broken sign-in becomes an account issue.
    """

    message: str
    code: str | None = None

    @property
    def breaks_account(self) -> bool:
        return self.code in ACCOUNT_BROKEN_CODES

    @classmethod
    def from_error(cls, exc: BaseException) -> "AccountFailure":
        return cls(account_error_text(exc), account_error_code(exc))


def account_error_text(exc: BaseException) -> str:
    """User-facing text for a failed account read.

    Account errors are written for the user; anything else (HTTP/MQTT library
    exceptions) can carry hostnames, payloads or tokens and is never shown.
    """
    from ...accounts.errors import AccountError

    return str(exc) if isinstance(exc, AccountError) else ACCOUNT_STATUS_ERROR


def _learned_fields(adapter: Any, device: Mapping[str, Any]) -> Dict[str, Any]:
    """Discovery fields a pooled adapter learned that the descriptor lacks."""
    learned = getattr(adapter, "device", None)
    if not isinstance(learned, Mapping):
        return {}
    result: Dict[str, Any] = {}
    for field in DISCOVERY_KEYS:
        value = learned.get(field)
        if value is not None and device.get(field) != value:
            result[field] = value
    return result


def cloud_status_device(device: Mapping[str, Any]) -> Dict[str, Any] | None:
    """Return a cloud-transport copy for a device that has a linked account."""
    cloud_transport = str(device.get("cloud_transport") or "").lower()
    if cloud_transport not in ("tuya_cloud", "govee_cloud", "govee_account"):
        return None
    if not device.get("account_id"):
        return None
    fallback = dict(device)
    fallback["transport"] = cloud_transport
    return fallback


def has_account_fallback(device: Mapping[str, Any]) -> bool:
    """Whether a local device can also be read through its linked account."""
    return cloud_status_device(device) is not None


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

            # Lighting on this PC's USB is merged like any discovered device,
            # but the third value still counts LAN replies only.
            found = list(settings.get("lastDiscoveryDevices", settings["devices"]))
            found.extend(devices.discover_local_hardware())
            self.finished.emit(
                found,
                settings["selectedDevice"],
                int(settings.get("lastDiscoveryCount", 0)),
            )
        except Exception as e:
            self.error.emit(str(e))


class DeviceStatusWorker(QObject):
    """Query current state for one or more devices off the GUI thread.

    The controller runs two of these independently: a local pass (LAN, Tuya
    LAN) that must stay fast, and an account pass that talks to vendor
    servers. A local pass started with ``defer_account_fallback`` reports
    devices whose local read failed through :attr:`account_needed` instead of
    blocking on a vendor round-trip itself. ``budget`` bounds a pass in
    seconds; devices it did not reach are reported through :attr:`unvisited`.
    """

    state_updated = Signal(int, dict)
    account_needed = Signal(list)  # device keys to read through the account pass
    unvisited = Signal(list)  # device keys skipped (budget spent or quitting)
    finished = Signal()
    error = Signal(str)

    def __init__(
        self,
        indexed_devices: List[tuple[int, Dict[str, Any]]],
        *,
        prefer_cloud: bool = False,
        prefer_account_keys: Collection[str] = (),
        defer_account_fallback: bool = False,
        budget: float | None = None,
    ):
        super().__init__()
        # Work on copies. The worker runs on its own thread while the GUI may
        # save, edit or remove the live descriptors; learned fields are sent
        # back with the state update and applied by the controller on the GUI
        # thread instead of being written here.
        self.indexed_devices = [
            (index, as_device(device).copy()) for index, device in indexed_devices
        ]
        self.prefer_cloud = prefer_cloud
        self.prefer_account_keys = frozenset(prefer_account_keys)
        self.defer_account_fallback = defer_account_fallback
        self.budget = budget

    def _prefers_account(self, device: Mapping[str, Any]) -> bool:
        return self.prefer_cloud or groups.device_key(device) in self.prefer_account_keys

    @staticmethod
    def _should_stop(deadline: float | None) -> bool:
        if QThread.currentThread().isInterruptionRequested():
            return True
        return deadline is not None and time.monotonic() >= deadline

    def _remaining_keys(self, position: int) -> List[str]:
        return [groups.device_key(device) for _, device in self.indexed_devices[position:]]

    @Slot()
    def run(self) -> None:
        server = None
        deferred: List[str] = []
        unvisited: List[str] = []
        deadline = None if self.budget is None else time.monotonic() + self.budget
        try:
            if any(
                device.get("ip")
                and str(device.get("transport", "lan")) in ("", "lan")
                and not self._prefers_account(device)
                for _, device in self.indexed_devices
            ):
                try:
                    server = connection.create_lan_socket(timeout=0.45)
                except OSError as exc:
                    server = None
                    self.error.emit(str(exc))

            for position, (index, device) in enumerate(self.indexed_devices):
                if self._should_stop(deadline):
                    unvisited.extend(self._remaining_keys(position))
                    break
                query_started_at = time.time()
                learned_fields: Dict[str, Any] = {}
                via_account = False
                # Set when the account is read because the LAN read failed,
                # as opposed to a pass that asks the account first.
                lan_failed = False
                try:
                    status = None
                    status_origin = "local"
                    cloud_error = None
                    cloud_attempted = False
                    transport = str(device.get("transport", "lan"))
                    cloud_device = cloud_status_device(device)

                    if self._prefers_account(device) and cloud_device is not None:
                        cloud_attempted = via_account = True
                        status_origin = "cloud"
                        adapter = create_adapter(cloud_device)
                        try:
                            status = adapter.query_status()
                        except Exception as exc:
                            cloud_error = exc
                        finally:
                            try:
                                adapter.close()
                            except Exception:
                                pass
                    elif transport in ("", "lan") and server is not None and device.get("ip"):
                        status = connection.query_status(server, device, timeout=0.45)
                    elif transport == "tuya":
                        # LSC/Tuya local strips announce on UDP 6667. A fresh
                        # beat proves reachability without any I/O. A missing
                        # beat is not proof of absence — a strip can be online
                        # through the cloud on another VLAN — so fall through to
                        # a real query and then the cloud fallback.
                        identity = str(device.get("device_id") or device.get("devId") or "")
                        age = tuya_presence.presence_age(identity) if identity else None
                        if age is not None and age <= tuya_presence.PRESENCE_FRESH_AFTER:
                            self.state_updated.emit(
                                index,
                                {
                                    "online": True,
                                    "stale": False,
                                    "status_source": "confirmed",
                                    "status_origin": "local",
                                    "readback_supported": True,
                                    "last_error": None,
                                    "last_seen": time.time(),
                                    "device_key": groups.device_key(device),
                                    "query_started_at": query_started_at,
                                },
                            )
                            continue
                        adapter = pool.acquire(device)
                        try:
                            status = adapter.query_status()
                            if pool.is_pooled(device):
                                # Pooled adapters hold their own descriptor copy;
                                # return what the adapter discovered (paint DP,
                                # zone grid, formats) with the state update so the
                                # controller can apply it on the GUI thread.
                                learned_fields.update(_learned_fields(adapter, device))
                        finally:
                            if not pool.is_pooled(device):
                                adapter.close()
                    elif transport == "ble":
                        # BLE reachability comes from the scan, not a TCP poll.
                        status = None
                    elif transport == "usb":
                        # USB lighting (RGB Fusion 2 boards, Logitech mice, Lorgar keyboards):
                        # reachable while the device enumerates, asked of its
                        # own driver. No report is exchanged and there is no
                        # readback, so power/colour stay as sent.
                        from ...drivers.registry import local_hardware_presence

                        present, missing_message = local_hardware_presence(device)
                        usb_update: Dict[str, Any] = {
                            "online": present,
                            "stale": False,
                            "status_source": "attached" if present else "offline",
                            "status_origin": "local",
                            "readback_supported": False,
                            "last_error": None if present else missing_message,
                            "device_key": groups.device_key(device),
                            "query_started_at": query_started_at,
                        }
                        if present:
                            usb_update["last_seen"] = time.time()
                        self.state_updated.emit(index, usb_update)
                        continue
                    else:
                        # Cloud transports query the vendor account directly.
                        cloud_attempted = cloud_device is not None
                        via_account = True
                        # An account-only device (no cloud_transport fallback
                        # field) is still read through the vendor account.
                        status_origin = "cloud" if cloud_device is not None or is_cloud(device) else "local"
                        adapter = pool.acquire(device)
                        try:
                            status = adapter.query_status()
                            if pool.is_pooled(device):
                                learned_fields.update(_learned_fields(adapter, device))
                        finally:
                            if not pool.is_pooled(device):
                                adapter.close()

                    if status is None and not cloud_attempted and cloud_device is not None:
                        # A local LAN failure must not leave an account-linked
                        # device showing "State unknown"; the cloud usually
                        # knows the real power/colour state.
                        if self.defer_account_fallback:
                            # Never block the local pass on a vendor
                            # round-trip; the account pass reads it instead.
                            deferred.append(groups.device_key(device))
                            if learned_fields:
                                self.state_updated.emit(
                                    index,
                                    {"device_key": groups.device_key(device),
                                     "learned": learned_fields},
                                )
                            continue
                        if self._should_stop(deadline):
                            unvisited.extend(self._remaining_keys(position))
                            break
                        cloud_attempted = via_account = lan_failed = True
                        status_origin = "cloud"
                        adapter = create_adapter(cloud_device)
                        try:
                            status = adapter.query_status()
                        except Exception as exc:
                            cloud_error = exc
                        finally:
                            try:
                                adapter.close()
                            except Exception:
                                pass

                    if status is None:
                        update = {
                            "online": False,
                            "stale": True,
                            "status_source": "offline",
                            "status_origin": status_origin,
                            "readback_supported": True,
                            "last_error": (
                                account_error_text(cloud_error)
                                if cloud_error is not None
                                else "No status reply"
                            ),
                            "device_key": groups.device_key(device),
                            "query_started_at": query_started_at,
                        }
                        code = account_error_code(cloud_error)
                        if code:
                            update["account_error_code"] = code
                        if lan_failed:
                            update["lan_failed"] = True
                        if learned_fields:
                            update["learned"] = learned_fields
                        self.state_updated.emit(index, update)
                    else:
                        status.update(
                            {
                                "online": status.get("online", True),
                                "stale": not status.get("online", True),
                                "status_source": "confirmed" if status.get("online", True) else "offline",
                                "status_origin": status_origin,
                                "readback_supported": True,
                                "last_error": None,
                                "last_seen": time.time(),
                                "device_key": groups.device_key(device),
                                "query_started_at": query_started_at,
                            }
                        )
                        if lan_failed:
                            status["lan_failed"] = True
                        if learned_fields:
                            status["learned"] = learned_fields
                        self.state_updated.emit(index, status)
                except Exception as exc:
                    on_account = via_account or is_cloud(device)
                    update = {
                        "online": False,
                        "stale": True,
                        "status_source": "offline",
                        "status_origin": "cloud" if on_account else "local",
                        "readback_supported": True,
                        "last_error": account_error_text(exc) if on_account else str(exc),
                        "device_key": groups.device_key(device),
                        "query_started_at": query_started_at,
                    }
                    code = account_error_code(exc) if on_account else None
                    if code:
                        update["account_error_code"] = code
                    if learned_fields:
                        update["learned"] = learned_fields
                    self.state_updated.emit(index, update)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            if server is not None:
                try:
                    server.close()
                except Exception:
                    pass
            if deferred:
                self.account_needed.emit(deferred)
            if unvisited:
                self.unvisited.emit(unvisited)
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
