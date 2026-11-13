"""
Device controller for the LumiSync GUI.
This module handles device discovery and management with PySide6 signals.
"""

import socket
import time
import weakref
from typing import Any, Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import QObject, Signal, Slot, QThread, QTimer, Qt

from ... import connection, devices, groups
from ...device import Device, DeviceLike, as_device
from ...utils.logging import get_logger
from ...drivers import pool, tuya_presence
from ...drivers.registry import create_adapter, is_cloud, capabilities_for_device
from .background import start_task
from .command_queue import CoalescingCommandQueue
from .device_admin import DeviceAdminMixin
from .device_store import DeviceStore, SettingsReloaded
from .discovery import (
    ACCOUNT_BROKEN_CODES,
    DISCOVERY_KEYS,
    AccountFailure,
    BleScanWorker,
    DeviceDiscoveryWorker,
    DeviceStatusWorker,
    has_account_fallback,
)

logger = get_logger(__name__)

SETTINGS_UNREADABLE_STARTUP = (
    "LumiSync can't read its settings file right now (another program may be "
    "using it). Saved devices appear once it can; nothing is saved until then."
)
SETTINGS_UNREADABLE = (
    "LumiSync can't read its settings file right now (another program may be "
    "using it), so the change was not saved. Try again in a moment."
)
SETTINGS_UNWRITABLE = (
    "Check that the settings folder is writable and not locked by another "
    "program, then try again."
)

# Notice kinds (the toast styles the main window shows).
NOTICE_INFO = "info"
NOTICE_SUCCESS = "success"
NOTICE_ERROR = "error"

PROVIDER_DISPLAY_NAMES = {
    "govee_account": "Govee Home", "govee_api": "Govee API", "tuya_account": "Tuya Smart",
    "lsc_account": "LSC Smart Connect", "tuya_qr": "Tuya / Smart Life", "tuya_project": "Tuya cloud project",
}
_PROVIDER_SHORT_NAMES = {"govee_account": "Govee", "govee_api": "Govee", "tuya_account": "Tuya",
                         "lsc_account": "LSC", "tuya_qr": "Tuya / Smart Life", "tuya_project": "Tuya project"}

CONTROL_UNREACHABLE = "The device didn't respond. Check that it has power and is on the same network."


def _control_error_text(exc: BaseException, device: Dict[str, Any]) -> str:
    """User-facing text for a failed manual control (never raw socket text)."""
    from ...accounts.errors import AccountError

    if isinstance(exc, AccountError):
        return str(exc)
    if is_cloud(device):
        return "The account could not complete this command. Reconnect the account and try again."
    if isinstance(exc, (OSError, TimeoutError, ConnectionError)):
        return CONTROL_UNREACHABLE
    text = str(exc).strip()
    return text if text and len(text) <= 160 else CONTROL_UNREACHABLE


class DeviceController(DeviceAdminMixin, QObject):
    """Controller for device management with PySide6 signals."""

    # Consecutive silent status polls tolerated before a device that has
    # answered before is shown as Offline. LSC strips push change-only echoes
    # during paint streaming, so one empty poll is not proof of absence.
    STATUS_MISS_TOLERANCE = 3

    # Account status reads go to vendor servers that rate-limit sessions. The
    # bulk account pass runs at most this often however many times the views
    # ask for a refresh; reads for one device the user is acting on (a command
    # confirmation, the energy panel, a refresh button) are not held back.
    ACCOUNT_STATUS_INTERVAL = 60.0
    # Upper bound for one status pass, so neither pass can hold its thread
    # (and app shutdown) for long. Devices it did not reach go first next time.
    STATUS_PASS_BUDGET = 20.0

    # Signals
    # Status bar + log only. Never shown as a toast.
    status_updated = Signal(str)  # Status message
    # Something the user should see now (a toast): message, kind
    # (NOTICE_INFO / NOTICE_SUCCESS / NOTICE_ERROR). Also sent as a status.
    notice = Signal(str, str)
    # Account id -> "<account label>: <reason>" for accounts that need the
    # user (sign-in expired, refresh failed). Emitted whenever the set changes.
    account_issues_changed = Signal(dict)
    devices_discovered = Signal(list)  # List of Device descriptors
    device_selected = Signal(object)  # Selected Device descriptor
    device_added = Signal(object)  # Newly added Device descriptor
    device_removed = Signal(int)  # Index of removed device
    device_updated = Signal(int, object)  # Index, updated Device descriptor
    device_state_updated = Signal(int, dict)  # Index, cached state
    discovery_started = Signal()  # Discovery process started
    discovery_finished = Signal()  # Discovery process finished
    ble_scan_started = Signal()  # Bluetooth scan started
    ble_scan_finished = Signal()  # Bluetooth scan finished
    device_search_started = Signal()  # Combined LAN + Bluetooth search started
    device_search_finished = Signal(dict)  # Per-transport availability/results
    groups_changed = Signal(list)  # Updated list of sync groups
    tuya_device_seen = Signal(str, str)  # Device ID, announced address (presence thread)

    @property
    def devices(self) -> List[Device]:
        return self._store.devices

    @devices.setter
    def devices(self, value) -> None:
        self._store.replace(value)

    @property
    def selected_device_index(self) -> int:
        return self._store.selected_index

    @selected_device_index.setter
    def selected_device_index(self, value: int) -> None:
        self._store.selected_index = int(value)

    def __init__(self):
        """Initialize the device controller."""
        super().__init__()

        self._store = DeviceStore()
        # Weak: a store -> controller reference cycle would keep a discarded
        # controller (and its presence subscription) alive until the next GC.
        owner = weakref.ref(self)

        def on_reloaded() -> None:
            controller = owner()
            if controller is not None:
                controller._on_settings_reloaded()

        def on_notice(message: str) -> None:
            controller = owner()
            if controller is not None:
                controller._notify(message, NOTICE_INFO)

        self._store.on_reloaded = on_reloaded
        self._store.on_notice = on_notice
        self.discovery_thread: Optional[QThread] = None
        self.discovery_worker: Optional[DeviceDiscoveryWorker] = None
        # Local pass (LAN / Tuya LAN). Never waits on a vendor server.
        self.status_thread: Optional[QThread] = None
        self.status_worker: Optional[DeviceStatusWorker] = None
        # Account pass (vendor servers), independent of the local pass.
        self.account_status_thread: Optional[QThread] = None
        self.account_status_worker: Optional[DeviceStatusWorker] = None
        # Device key -> ask the account first (True) or use the device's own
        # transport (False), for the next account pass.
        self._account_status_queue: dict[str, bool] = {}
        # Devices with a read requested for them specifically; their account
        # read is not held back by ACCOUNT_STATUS_INTERVAL.
        self._status_targets: set[str] = set()
        self._last_account_pass = float("-inf")
        self._account_pass_timer = QTimer(self)
        self._account_pass_timer.setSingleShot(True)
        self._account_pass_timer.timeout.connect(self._start_account_pass)
        # Devices a pass skipped when its budget ran out; read first next time.
        self._unvisited_status_keys: list[str] = []
        self._ble_scan_thread: Optional[QThread] = None
        self._ble_scan_worker: Optional[BleScanWorker] = None
        self._combined_search_active = False
        self._search_pending: set[str] = set()
        self._search_results: Dict[str, Dict[str, Any]] = {}
        # Identity keys of every device known when the current search began,
        # so the outcome can say how many devices are actually new.
        self._search_known_keys: Optional[set[str]] = None
        self._search_has_network = True
        self._account_refresh_tasks = {}
        # Account id -> (account label, user-facing reason).
        self._account_issues: Dict[str, tuple[str, str]] = {}
        self._tuya_scan_results = []
        self._refresh_all_pending = False
        self._refresh_keys_pending: dict[str, None] = {}
        self._discovery_before: Dict[str, Dict[str, Any]] = {}
        self.server = None
        self.device_states: Dict[str, Dict[str, Any]] = {}
        self._commands = CoalescingCommandQueue()
        # Public-ish views kept for existing tests and integrations.
        self._command_tasks = self._commands.tasks
        self._command_queue = self._commands.pending
        self._requested_power = self._commands.requested_power
        self._confirmation_timers = {}
        self._confirmation_delays = {}
        # Devices whose LAN path recently failed but whose cloud path works.
        # Manual controls skip the local timeout for this short cooldown; a
        # fresh UDP presence beat or local status clears it immediately.
        self._cloud_preferred_until: dict[str, float] = {}
        # Devices the local pass handed to the account pass because their LAN
        # read failed. Only such a read (or an inline fallback) may start the
        # cooldown above; an account-first poll never tried the LAN at all.
        self._lan_failed_keys: set[str] = set()
        self._tuya_task = None
        self._closing = False
        # LSC/Tuya lights announce their current address on UDP 6667; follow
        # DHCP moves so a stale saved IP never strands commands and sync.
        self.tuya_device_seen.connect(self._on_tuya_device_seen, Qt.ConnectionType.QueuedConnection)
        self._presence_callback = self.tuya_device_seen.emit
        tuya_presence.subscribe(self._presence_callback)

        # Initialize devices on startup
        self._init_devices()

    def _init_devices(self):
        """Initialize with existing devices from settings."""
        try:
            settings = self._store.load(refresh=False)
        except devices.SettingsUnavailable:
            # Nothing is saved until a later read succeeds (DeviceStore).
            self._notify(SETTINGS_UNREADABLE_STARTUP, NOTICE_ERROR)
            return
        except Exception:
            logger.warning("Saved devices could not be loaded", exc_info=True)
            self._notify("LumiSync couldn't load your saved devices. Check the log for details.", NOTICE_ERROR)
            return
        self._prune_session_accounts(settings.get("accounts"))
        try:
            if self.devices:
                self.status_updated.emit(f"Found {len(self.devices)} device(s)")
                self.devices_discovered.emit(self.devices)

                device = self.get_selected_device()
                if device:
                    self.device_selected.emit(device)
                    self.status_updated.emit(
                        f"Selected device: {device.get('model', 'Unknown')}"
                    )
        except Exception:
            logger.warning("Saved devices could not be shown", exc_info=True)
            self._notify("LumiSync couldn't load your saved devices. Check the log for details.", NOTICE_ERROR)

    # ------------------------------------------------------------------ notices

    def _notify(self, message: str, kind: str = NOTICE_INFO) -> None:
        """Show *message* now (toast) and keep it in the status bar/log."""
        self.status_updated.emit(message)
        self.notice.emit(message, kind)

    def _device_name(self, device: Optional[Dict[str, Any]]) -> str:
        if not device:
            return "Device"
        return str(device.get("name") or device.get("model") or "Device")

    # ------------------------------------------------------------------ account issues

    @property
    def account_issues(self) -> Dict[str, str]:
        """Account id -> ``"<label>: <reason>"`` for accounts needing attention."""
        return {account_id: f"{label}: {reason}" for account_id, (label, reason) in self._account_issues.items()}

    def account_issue(self, account_id: str) -> Optional[tuple[str, str]]:
        """``(account label, reason)`` for one account, or None when it is fine."""
        return self._account_issues.get(str(account_id))

    def account_label(self, account: Any) -> str:
        """The label the Accounts dialog shows for an account (email masked)."""
        from ...accounts.privacy import mask_account_label

        metadata = account if isinstance(account, dict) else None
        if metadata is None:
            metadata = next((saved for saved in self.get_accounts() if isinstance(saved, dict)
                             and saved.get("id") == account), {})
        provider = str(metadata.get("provider") or "")
        label = metadata.get("label") or PROVIDER_DISPLAY_NAMES.get(provider) or "Linked account"
        return mask_account_label(str(label))

    def set_account_issue(self, account: Any, reason: str) -> None:
        """Record that an account needs the user (refresh or sign-in failed)."""
        metadata = account if isinstance(account, dict) else {"id": account}
        account_id = str(metadata.get("id") or "")
        if not account_id:
            return
        label = self.account_label(metadata if metadata.get("provider") or metadata.get("label") else account_id)
        reason = " ".join(str(reason or "").split()) or "The account could not be refreshed. Reconnect it in Accounts."
        entry = (label, reason)
        if self._account_issues.get(account_id) == entry:
            return
        self._account_issues[account_id] = entry
        logger.info("Account needs attention (%s)", metadata.get("provider") or "account")
        self.account_issues_changed.emit(self.account_issues)

    def record_account_failure(self, account: Any, failure: AccountFailure, *,
                               network: Optional[bool] = None) -> bool:
        """Record an account issue only when the sign-in itself is broken.

        Rate limits, server errors, timeouts and offline devices are logged
        once and otherwise left alone: they fix themselves and must not ask
        the user to reconnect. Nothing is recorded without a local network
        (every account looks broken then). Returns whether an issue was set.
        """
        if not failure.breaks_account:
            logger.info("Account refresh failed (%s); not treated as an account problem",
                        failure.code or "transient")
            return False
        if network is None:
            network = self._local_network_available()
        if not network:
            logger.info("Account refresh failed without a local network; not recorded")
            return False
        self.set_account_issue(account, failure.message)
        return True

    def clear_account_issue(self, account_id: Any) -> None:
        if self._account_issues.pop(str(account_id or ""), None) is not None:
            self.account_issues_changed.emit(self.account_issues)

    def _discovery_running(self) -> bool:
        """Safe check that handles a stale Python ref to a deleted QThread."""
        if self.discovery_thread is None:
            return False
        try:
            self.discovery_thread.isRunning()
            return True  # Retain ownership through native thread cleanup.
        except RuntimeError:
            self.discovery_thread = None
            self.discovery_worker = None
            return False

    @Slot()
    def _clear_discovery_refs(self) -> None:
        self._release_worker_thread("discovery_thread", "discovery_worker", self._clear_discovery_refs)

    def _release_worker_thread(self, thread_field, worker_field, retry) -> None:
        thread = getattr(self, thread_field)
        sender = self.sender()
        if thread is None or (isinstance(sender, QThread) and sender is not thread):
            return
        if not thread.wait(0):
            QTimer.singleShot(1, retry)
            return
        setattr(self, worker_field, None)
        setattr(self, thread_field, None)
        thread.deleteLater()

    def _status_running(self) -> bool:
        if self.status_thread is None:
            return False
        try:
            self.status_thread.isRunning()
            # Keep ownership until the finished callback has cleared the refs.
            # Starting a replacement earlier lets the old callback erase it.
            return True
        except RuntimeError:
            self.status_thread = None
            self.status_worker = None
            return False

    @Slot()
    def _clear_status_refs(self) -> None:
        sender = self.sender()
        if isinstance(sender, QThread) and sender is not self.status_thread:
            return
        thread = self.status_thread
        if thread is not None and not thread.wait(0):
            # isRunning() becomes false before native/deferred QObject cleanup
            # finishes. Releasing the worker then can race its deleteLater and
            # corrupt the Windows heap. wait(0) verifies the native thread has
            # actually joined without blocking the GUI.
            QTimer.singleShot(1, self._clear_status_refs)
            return
        self.status_worker = None
        self.status_thread = None
        if thread is not None:
            thread.deleteLater()
        if self._closing:
            self._refresh_keys_pending.clear()
            return
        if self._refresh_keys_pending:
            QTimer.singleShot(0, self._drain_status_refresh)
        elif self._refresh_all_pending:
            self._refresh_all_pending = False
            QTimer.singleShot(0, self.refresh_device_states)

    def _account_status_running(self) -> bool:
        if self.account_status_thread is None:
            return False
        try:
            self.account_status_thread.isRunning()
            return True  # Keep ownership until the native thread has joined.
        except RuntimeError:
            self.account_status_thread = None
            self.account_status_worker = None
            return False

    @Slot()
    def _clear_account_status_refs(self) -> None:
        sender = self.sender()
        thread = self.account_status_thread
        if isinstance(sender, QThread) and sender is not thread:
            return
        if thread is not None and not thread.wait(0):
            # Same native-join guard as _clear_status_refs.
            QTimer.singleShot(1, self._clear_account_status_refs)
            return
        self.account_status_worker = None
        self.account_status_thread = None
        if thread is not None:
            thread.deleteLater()
        if self._closing:
            self._account_status_queue.clear()
            return
        if self._account_status_queue:
            QTimer.singleShot(0, self._start_account_pass)

    @Slot(list)
    def _on_status_unvisited(self, keys: List[str]) -> None:
        if self._closing or not keys:
            return
        logger.info("Status pass reached its time budget; %d device(s) go first next time", len(keys))
        self._unvisited_status_keys = list(dict.fromkeys([*keys, *self._unvisited_status_keys]))

    @Slot(list)
    def _on_account_status_needed(self, keys: List[str]) -> None:
        """Queue devices whose local read failed for the account pass."""
        if self._closing:
            return
        for key in keys:
            if self._index_for_key(key) >= 0:
                self._lan_failed_keys.add(key)
                self._queue_account_status(key, prefer_account=True)
        self._start_account_pass()

    def _queue_account_status(self, key: str, *, prefer_account: bool) -> None:
        self._account_status_queue[key] = (
            self._account_status_queue.get(key, False) or prefer_account
        )

    @Slot()
    def _start_account_pass(self) -> None:
        """Start the account pass, at most once per ACCOUNT_STATUS_INTERVAL.

        Devices in :attr:`_status_targets` (a read the user is waiting on)
        are read at once; everything else waits for the next window.
        """
        if self._closing or not self._account_status_queue or self._account_status_running():
            return
        positions = {self._device_key(device): index for index, device in enumerate(self.devices)}
        queue = {key: prefer for key, prefer in self._account_status_queue.items() if key in positions}
        self._account_status_queue = queue
        if not queue:
            return
        now = time.monotonic()
        wait = self._last_account_pass + self.ACCOUNT_STATUS_INTERVAL - now
        if wait > 0:
            keys = [key for key in queue if key in self._status_targets]
            if len(keys) < len(queue) and not self._account_pass_timer.isActive():
                self._account_pass_timer.start(max(1, int(wait * 1000) + 1))
            if not keys:
                return
        else:
            keys = list(queue)
            self._last_account_pass = now
            self._account_pass_timer.stop()
        prefer_keys = {key for key in keys if queue[key]}
        for key in keys:
            queue.pop(key, None)
            self._status_targets.discard(key)
        indexed = [(positions[key], self.devices[positions[key]]) for key in keys]
        self._launch_status_worker(indexed, account=True, prefer_keys=prefer_keys)

    def _flush_discovery(self) -> None:
        """Persist fields the devices taught the last status pass.

        Tuya devices self-describe in their status replies (data-point layout,
        colour format, RGBIC paint DP and its zone count). That knowledge is a
        one-time upgrade per device, so it is written to the saved descriptor
        instead of being re-derived every session.
        """
        sender = self.sender()
        if isinstance(sender, QThread) and sender is not self.status_thread:
            return
        before = getattr(self, "_discovery_before", None) or {}
        self._discovery_before = None
        if self._closing or self.devices is None:
            return
        learned = [
            device for device in self.devices
            if self._device_key(device) in before
            and {k: device.get(k) for k in DISCOVERY_KEYS} != before[self._device_key(device)]
        ]
        if not learned:
            return
        try:
            self._store.save()
        except Exception:
            return
        self.devices_discovered.emit(self.devices)
        self.status_updated.emit(
            f"Saved learned device data points: {', '.join(d.get('model', 'Device') for d in learned[:3])}"
        )

    @Slot()
    def _drain_status_refresh(self) -> None:
        if self._closing or self._status_running():
            return
        # Resolve identities now: discovery can reorder or remove saved rows
        # while another query is in flight. One queued read per device is enough.
        keys = list(self._refresh_keys_pending)
        self._refresh_keys_pending.clear()
        by_key = {self._device_key(device): index for index, device in enumerate(self.devices)}
        indexes = [by_key[key] for key in keys if key in by_key]
        if self._refresh_all_pending:
            self._refresh_all_pending = False
            indexes.extend(index for index in range(len(self.devices)) if index not in indexes)
        self._start_status_refresh(indexes)

    @Slot(str)
    def _on_status_error(self, message: str) -> None:
        # Status passes run every few seconds; a failure is for the log.
        logger.info("Status refresh error: %s", message)

    def _ble_scan_running(self) -> bool:
        if self._ble_scan_thread is None:
            return False
        try:
            self._ble_scan_thread.isRunning()
            return True
        except RuntimeError:
            self._ble_scan_thread = None
            self._ble_scan_worker = None
            return False

    # Route probes for _local_network_available: the Govee discovery group
    # (multicast routes exist even on LANs without a gateway) and TEST-NET-1
    # (RFC 5737; reached through the default route). Nothing is sent.
    NETWORK_PROBE_TARGETS = (("239.255.255.250", 4001), ("192.0.2.1", 9))

    @classmethod
    def _local_network_available(cls) -> bool:
        """Return whether a non-loopback IPv4 route exists, without DNS.

        This runs on the GUI thread. ``getaddrinfo(gethostname())`` can block
        for seconds on Linux when the resolver is slow; connecting a UDP socket
        only asks the routing table which local address would be used.
        """
        for target in cls.NETWORK_PROBE_TARGETS:
            probe = None
            try:
                probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                probe.connect(target)
                address = str(probe.getsockname()[0])
            except OSError:
                continue
            finally:
                if probe is not None:
                    probe.close()
            if address and not address.startswith("127.") and address != "0.0.0.0":
                return True
        return False

    def find_devices(self) -> None:
        """Refresh linked accounts, scan locally, then update all saved states."""
        if self._closing:
            return
        if (
            self._combined_search_active
            or self._discovery_running()
            or self._ble_scan_running()
        ):
            self.status_updated.emit("A device search is already in progress.")
            return

        has_network = self._local_network_available()
        self._search_has_network = has_network
        self._search_known_keys = self._identity_keys(self.devices)
        accounts = list({account["id"]: dict(account) for account in self.get_accounts()
                         if account.get("id") and account.get("provider")}.values())
        self._combined_search_active = True
        self._tuya_scan_results = []
        self._search_pending = {"bluetooth"}
        self._search_results = {
            "lan": {
                "available": has_network,
                "found": 0,
                "error": None if has_network else "No active local network connection",
            },
            "bluetooth": {
                "available": None,
                "found": 0,
                "added": 0,
                "seen": 0,
                "error": None,
            },
            "accounts": {"available": not accounts, "total": len(accounts), "refreshed": 0,
                         "found": 0, "errors": []},
        }
        self._search_pending.update("account:" + account["id"] for account in accounts)
        if has_network:
            self._search_pending.add("lan")
            self._search_pending.add("tuya")
            self._search_results["tuya"] = {"available": None, "found": 0, "error": None}

        self.device_search_started.emit()
        self.status_updated.emit("Searching for devices…")

        for metadata in accounts:
            def callback(value, error, account=metadata):
                self._on_account_refresh_finished(account, value, error)
            try:
                self._account_refresh_tasks[metadata["id"]] = start_task(
                    lambda account=metadata: self._list_account_devices(account), callback)
            except Exception:
                callback(None, "The account refresh could not start. Try Find Devices again.")
        if has_network:
            self._tuya_task = start_task(self._scan_tuya_lan, self._on_tuya_scan_finished)
            if not self._start_lan_discovery(announce=False):
                self._search_results["lan"].update(
                    {"available": False, "error": "Search could not be started"}
                )
                self._finish_search_transport("lan")
        if not self._start_ble_scan(announce=False):
            self._search_results["bluetooth"].update(
                {"available": False, "error": "Search could not be started"}
            )
            self._finish_search_transport("bluetooth")

    @staticmethod
    def _list_account_devices(metadata: dict) -> list[dict] | AccountFailure:
        from ...accounts.errors import AccountError
        from ...accounts.manager import account_manager

        try:
            client = account_manager.client_for({"account_id": metadata["id"]})
            return account_manager.descriptors(metadata, client.list_devices())
        except AccountError as exc:
            # Returned, not raised: start_task would reduce it to text and
            # lose the code that says whether the sign-in itself is broken.
            return AccountFailure.from_error(exc)

    def _on_account_refresh_finished(self, metadata, descriptors, error) -> None:
        self._account_refresh_tasks.pop(metadata["id"], None)
        if self._closing or not self._combined_search_active:
            return
        failure: AccountFailure | None = None
        if isinstance(descriptors, AccountFailure):
            failure, descriptors = descriptors, None
        elif error:
            failure = AccountFailure(str(error))
        result = self._search_results["accounts"]
        current = next((account for account in self.get_accounts() if account.get("id") == metadata["id"]), None)
        if current is not None and failure is None:
            try:
                saved = bool(self.import_account_devices(current, descriptors or []))
                if saved and self._tuya_scan_results:
                    self._on_tuya_scan_finished(self._tuya_scan_results, None)
            except Exception:
                logger.warning("Saving refreshed account devices failed", exc_info=True)
                saved = False
            if saved:
                result["refreshed"] += 1
                result["found"] += len(descriptors or [])
            else:
                # The save failure was already reported once (settings
                # notice); it says nothing about the account itself.
                failure = AccountFailure("Could not save refreshed account devices. Try Find devices again.",
                                         "storage")
        if failure is not None and current is not None:
            name = _PROVIDER_SHORT_NAMES.get(metadata["provider"], "Account")
            result["errors"].append(f"{name}: {failure.message}")
            if failure.code != "storage":
                self.record_account_failure(current, failure, network=self._search_has_network)
        result["available"] = result["refreshed"] > 0 or not result["errors"]
        self._finish_search_transport("account:" + metadata["id"])

    def discover_devices(self):
        """Start device discovery in background thread."""
        self._start_lan_discovery(announce=True)

    def _start_lan_discovery(self, *, announce: bool) -> bool:
        if self._discovery_running():
            if announce:
                self.status_updated.emit("Local-network discovery is already in progress.")
            return False

        self.discovery_started.emit()
        if announce:
            self.status_updated.emit("Searching the local network for devices...")

        thread = QThread(self)
        worker = DeviceDiscoveryWorker()
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_discovery_finished)
        worker.error.connect(self._on_discovery_error)

        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._clear_discovery_refs, Qt.ConnectionType.QueuedConnection)

        self.discovery_thread = thread
        self.discovery_worker = worker
        thread.start()
        return True

    def _on_discovery_finished(
        self,
        discovered_devices: List[Dict],
        selected_index: int,
        last_discovery_count: int,
    ):
        """Handle discovery completion.

        Args:
            discovered_devices: List of discovered devices
            selected_index: Index of the selected device
        """
        if self._closing:
            return
        selected_key = None
        selected = self.get_selected_device()
        if selected:
            selected_key = self._device_key(selected)

        # LAN and Bluetooth search concurrently. Preserve a Bluetooth device
        # that may have been added while the LAN worker was still running.
        merged_devices = devices.merge_discovered_devices(self.devices, discovered_devices)
        for device in merged_devices:
            if device.get("account_id") and device.get("ip") and str(device.get("transport", "lan")) in ("", "lan", "govee_cloud", "govee_account"):
                if is_cloud(device) and device.get("connection_preference") != "cloud":
                    device["cloud_transport"] = device["transport"]
                    device["transport"] = "lan"
                device["local_transport"] = "lan"

        self.devices = merged_devices
        if selected_key:
            self.selected_device_index = next(
                (
                    index
                    for index, device in enumerate(self.devices)
                    if self._device_key(device) == selected_key
                ),
                min(selected_index, max(0, len(self.devices) - 1)),
            )
        else:
            self.selected_device_index = min(
                selected_index, max(0, len(self.devices) - 1)
            )

        try:
            self._store.save()
        except Exception as exc:
            self._report_settings_failure(exc, "Could not save the device search results.")

        if self._combined_search_active:
            self._search_results["lan"].update(
                {"available": True, "found": last_discovery_count, "error": None}
            )
        elif last_discovery_count == 0 and self.devices:
            self.status_updated.emit(
                f"No new LAN devices found; kept {len(self.devices)} saved device(s)"
            )
        elif not self._combined_search_active:
            self.status_updated.emit(f"Found {len(self.devices)} device(s)")
        self.devices_discovered.emit(self.devices)
        self.discovery_finished.emit()
        if self._combined_search_active:
            self._finish_search_transport("lan")
        else:
            self.refresh_device_states()

        # Emit selected device
        device = self.get_selected_device()
        if device:
            self.device_selected.emit(device)

    def _on_discovery_error(self, error_message: str):
        """Handle discovery error.

        Args:
            error_message: Error message from discovery
        """
        if self._combined_search_active:
            self._search_results["lan"].update(
                {"available": False, "found": 0, "error": error_message}
            )
        else:
            self.status_updated.emit(f"Local-network search unavailable: {error_message}")
        self.discovery_finished.emit()
        if self._combined_search_active:
            self._finish_search_transport("lan")

    def get_devices(self) -> List[Dict[str, Any]]:
        """Get the list of discovered devices.

        Returns:
            List of device dictionaries
        """
        try:
            self._store.load(refresh=False)
            return self.devices
        except Exception as e:
            self.status_updated.emit(f"Error getting devices: {str(e)}")
            return []

    def _device_key(self, device: Dict[str, Any]) -> str:
        return groups.device_key(device) or "?"

    def _default_state(self, device: Dict[str, Any]) -> Dict[str, Any]:
        is_ble = self._is_ble(device)
        return {
            "power_on": None,
            "brightness": None,
            "color": None,
            "color_temp": None,
            "online": False,
            "stale": not is_ble,
            "status_source": "unknown" if is_ble else "pending",
            "readback_supported": not is_ble,
            "readback_seen": False,
            "last_seen": None,
            "last_command_at": None,
            "last_output": None,
            "active_output": None,
            "last_error": None,
            "status_misses": 0,
            "device_key": self._device_key(device),
        }

    def get_device_state_at(self, index: int) -> Dict[str, Any]:
        device = self._device_at(index)
        if not device:
            return {}
        key = self._device_key(device)
        if key not in self.device_states:
            self.device_states[key] = self._default_state(device)
        return dict(self.device_states[key])

    def is_device_busy(self, device: Dict[str, Any], *, allowed_outputs=()) -> bool:
        """Whether another output or queued command currently owns this light."""
        key = self._device_key(device)
        state = self.device_states.get(key, {})
        output = state.get("active_output")
        return key in self._command_tasks or bool(output and output not in allowed_outputs)

    def _index_for_key(self, key: str) -> int:
        return next(
            (
                index
                for index, device in enumerate(self.devices)
                if self._device_key(device) == key
            ),
            -1,
        )

    @Slot(int, dict)
    def _merge_device_state(self, index: int, updates: Dict[str, Any]) -> None:
        if self._closing:
            return
        device = self._device_at(index) if index >= 0 else None
        key = str(updates.get("device_key") or (self._device_key(device) if device else ""))
        if not key:
            return
        # Resolve by identity, not list position: a device can be removed or
        # reordered while a status poll is in flight. Never paint the reply onto
        # whichever card now happens to sit at the old index.
        resolved = self._index_for_key(key)
        if resolved < 0:
            return
        index = resolved
        device = self.devices[index]
        updates = dict(updates)
        self._note_account_health(device, updates)

        learned = updates.pop("learned", None)
        if isinstance(learned, dict):
            for field in DISCOVERY_KEYS:
                value = learned.get(field)
                if value is not None and device.get(field) != value:
                    device[field] = value

        state = self.device_states.get(key, self._default_state(device))
        if (updates.get("query_started_at") is not None
                and updates["query_started_at"] < (state.get("last_command_at") or 0)):
            return  # A read begun before the last write cannot confirm it.
        updates = dict(updates)
        self._remember_status_origin(key, device, updates)
        if updates.get("status_origin") == "local" and updates.get("status_source") == "confirmed":
            # The read someone was waiting for arrived; a later fallback for
            # this device is ordinary bulk work again.
            self._status_targets.discard(key)
            # The device answers locally again: drop the "via account" note.
            updates.setdefault("command_route", None)
        if updates.get("status_source") == "confirmed":
            state["readback_seen"] = True
            state["status_misses"] = 0
        elif updates.get("status_source") in ("commanded", "seen"):
            state["status_misses"] = 0
        elif updates.get("status_source") == "offline":
            misses = int(state.get("status_misses") or 0) + 1
            state["status_misses"] = misses
            if state.get("readback_seen") and misses < self.STATUS_MISS_TOLERANCE:
                # A single silent poll is normal while an LSC strip streams
                # paint or its MCU is busy — it pushes change-only echoes that
                # do not always include the switch DP. Do not flash Offline
                # for a transient miss; the next poll usually confirms again.
                updates["status_source"] = "pending"
                updates["stale"] = True
                updates["online"] = True
                updates["last_error"] = None
        if (updates.get("status_source") == "offline"
                and updates.get("last_error")
                and not state.get("readback_seen")):
            # A failed status poll is not proof of absence: Govee LAN commands
            # are fire-and-forget, and some models (e.g. the H7020 string
            # lights) simply never answer status queries. Until one reply has
            # been confirmed, report "unknown" instead of "offline" so the
            # card does not flap to Offline after every command (issue #49).
            updates["status_source"] = "unknown"
        for field, value in updates.items():
            if field == "color" and value is None and state.get("color") is not None:
                continue
            state[field] = value
        state["device_key"] = key
        self.device_states[key] = state
        self.device_state_updated.emit(index, dict(state))

    def _note_account_health(self, device: Dict[str, Any], updates: Dict[str, Any]) -> None:
        """Turn account status reads into account issues (and clear them).

        Only errors that mean the account itself is broken (sign-in expired,
        credentials unreadable) are recorded; an unreachable device is not an
        account problem.
        """
        code = updates.pop("account_error_code", None)
        account_id = device.get("account_id")
        if not account_id:
            return
        if code in ACCOUNT_BROKEN_CODES:
            self.record_account_failure(str(account_id), AccountFailure(str(updates.get("last_error") or ""), code))
        elif updates.get("status_origin") == "cloud" and updates.get("status_source") == "confirmed":
            self.clear_account_issue(str(account_id))

    def _record_command_state(
        self,
        index: int,
        updates: Dict[str, Any],
        *,
        output: Optional[str] = None,
    ) -> None:
        """Cache a successful command without pretending BLE has readback."""
        device = self._device_at(index)
        if not device:
            return
        is_ble = self._is_ble(device)
        payload = {
            "online": True,
            "stale": not is_ble,
            "status_source": "commanded" if is_ble else "pending",
            "readback_supported": not is_ble,
            "last_seen": time.time(),
            "last_command_at": time.time(),
            "last_error": None,
        }
        payload.update(updates)
        if output:
            payload["last_output"] = output
        self._merge_device_state(index, payload)

    def mark_sync_active(
        self, mode: str, active_devices: Sequence[DeviceLike]
    ) -> None:
        """Expose active monitor/music output on matching device cards."""
        active_keys = {self._device_key(device) for device in active_devices}
        label = "Monitor sync" if mode == "monitor" else "Music sync"
        for index, device in enumerate(self.devices):
            if self._device_key(device) in active_keys:
                self._merge_device_state(
                    index,
                    {
                        "active_output": label,
                        "online": True,
                        "last_seen": time.time(),
                        "last_error": None,
                    },
                )

    def _adopt_pooled_discovery(self) -> bool:
        """Persist fields a pooled adapter learned during the last sync."""
        changed = False
        for device in self.devices:
            learned = pool.learned_fields(device, DISCOVERY_KEYS)
            if not learned:
                continue
            for field, value in learned.items():
                if device.get(field) != value:
                    device[field] = value
                    changed = True
        if changed:
            try:
                self._store.save()
            except Exception:
                logger.warning("Could not save learned device data", exc_info=True)
            self.devices_discovered.emit(self.devices)
        return changed

    def clear_sync_activity(self) -> None:
        # Sync may have taught a pooled Tuya adapter a paint DP / zone grid.
        # Save it now so quitting immediately afterwards keeps that knowledge.
        self._adopt_pooled_discovery()
        for index in range(len(self.devices)):
            state = self.get_device_state_at(index)
            if state.get("active_output") in {"Monitor sync", "Music sync"}:
                self._merge_device_state(index, {"active_output": None})

    def mark_device_output(
        self,
        device: Dict[str, Any],
        label: str,
        *,
        active: bool = False,
    ) -> None:
        """Record output produced outside the standard color controls."""
        key = self._device_key(device)
        index = next(
            (
                candidate
                for candidate, saved in enumerate(self.devices)
                if self._device_key(saved) == key
            ),
            -1,
        )
        if index < 0:
            return
        updates: Dict[str, Any] = {
            "active_output": label if active else None,
            "last_output": None if active else label,
        }
        self._record_command_state(index, updates, output=None)

    def clear_device_activity(self, device: Dict[str, Any]) -> None:
        key = self._device_key(device)
        for index, saved in enumerate(self.devices):
            if self._device_key(saved) == key:
                self._merge_device_state(index, {"active_output": None})
                return

    def refresh_device_states(self) -> None:
        """Refresh current state for all known devices.

        Local devices are read at once. Account devices join the account
        pass, which runs at most once per ``ACCOUNT_STATUS_INTERVAL`` however
        often this is called (the Devices page calls it on every show).
        """
        if self._closing:
            return
        self._retry_settings_load()
        if self._status_running():
            self._refresh_all_pending = True
            return
        indexes = list(range(len(self.devices)))
        self._start_status_refresh(indexes)

    def refresh_device_state_at(self, index: int) -> None:
        """Refresh current state for one device, without waiting for a window."""
        device = self._device_at(index)
        if self._closing or not device or self._is_ble(device):
            return
        key = self._device_key(device)
        self._status_targets.add(key)
        if self._status_running():
            self._refresh_keys_pending[key] = None
            return
        self._start_status_refresh([index])

    def refresh_local_device_states(self) -> None:
        """Poll local-network devices so their online indicator stays current.

        Called periodically by the Devices page. Account devices are skipped:
        their status costs a vendor round-trip and the sessions are
        rate-limited, while LAN/Tuya/BLE devices are cheap to probe. A running
        account pass never delays this.
        """
        if self._closing:
            return
        self._retry_settings_load()
        if self._status_running():
            return
        indexes = [
            index
            for index, device in enumerate(self.devices)
            if str(device.get("transport") or "lan").lower() in ("", "lan", "tuya")
            and not self._is_ble(device)
            and not is_cloud(device)
            and not self._cloud_preferred(device)
        ]
        if indexes:
            self._start_status_refresh(indexes)

    def refresh_account_device_states(self) -> None:
        """Poll account-linked devices on a slower cadence.

        The Devices page polls cheap LAN devices every 15 s. Account status
        is also available for imported devices and for local Tuya devices
        whose LAN path is unreachable, so a slower pass keeps power/colour
        state in sync without hammering the vendor session. It runs on its
        own thread, rate-limited by ``ACCOUNT_STATUS_INTERVAL``.

        A local Tuya device whose LAN works stays on the local poll: reading
        it through the account here would only add vendor traffic, and used
        to keep it marked cloud-only for good (each read renewed the mark,
        and marked devices skip the local poll that could clear it).
        """
        if self._closing:
            return
        indexes = []
        for index, device in enumerate(self.devices):
            if not device.get("account_id") or self._is_ble(device):
                continue
            if is_cloud(device):
                # Imported account-only devices, with or without a recorded
                # cloud_transport: the account is their only status source.
                indexes.append(index)
            elif (
                str(device.get("transport") or "").lower() == "tuya"
                and has_account_fallback(device)
                and self._cloud_preferred(device)
            ):
                indexes.append(index)
        if indexes:
            self._start_status_refresh(indexes, prefer_cloud=True)

    def _schedule_status_refresh(self, index: int) -> None:
        """Restart one identity-bound confirmation sequence after each write."""
        device = self._device_at(index)
        if self._closing or not device or self._is_ble(device):
            return
        key = self._device_key(device)
        timer = self._confirmation_timers.get(key)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setProperty("deviceKey", key)
            timer.timeout.connect(self._on_confirmation_timeout)
            self._confirmation_timers[key] = timer
        self._confirmation_delays[key] = [] if is_cloud(device) else [450, 600]
        timer.start(1800 if is_cloud(device) else 250)

    @Slot()
    def _on_confirmation_timeout(self) -> None:
        timer = self.sender()
        if isinstance(timer, QTimer):
            self._confirm_device_state(timer.property("deviceKey"))

    def _confirm_device_state(self, key: str) -> None:
        timer = self._confirmation_timers.get(key)
        if timer is None:
            return
        index = next((i for i, device in enumerate(self.devices) if self._device_key(device) == key), -1)
        if index >= 0 and not self._closing:
            self.refresh_device_state_at(index)
        delays = self._confirmation_delays.get(key, [])
        if delays and index >= 0 and not self._closing:
            timer.start(delays.pop(0))
        else:
            timer.stop()
            self._confirmation_delays.pop(key, None)
            self._confirmation_timers.pop(key, None)
            timer.deleteLater()

    def _start_status_refresh(
        self, indexes: List[int], *, prefer_cloud: bool = False
    ) -> None:
        """Read the given devices: local ones now, account ones via the queue.

        ``prefer_cloud`` sends every device to the account pass and asks the
        account first. Otherwise devices on an account transport, and local
        devices currently reached through their account, go to the account
        pass; everything else is read by the local pass.
        """
        if self._closing:
            return

        indexed_devices = []
        for idx in indexes:
            if not (0 <= idx < len(self.devices)):
                continue
            device = self.devices[idx]
            if self._is_ble(device):
                state = self.get_device_state_at(idx)
                if state.get("status_source") == "pending":
                    self._merge_device_state(
                        idx,
                        {
                            "stale": False,
                            "status_source": "unknown",
                            "readback_supported": False,
                            "last_error": None,
                        },
                    )
                continue
            key = self._device_key(device)
            if prefer_cloud:
                self._queue_account_status(key, prefer_account=True)
            elif is_cloud(device):
                self._queue_account_status(key, prefer_account=False)
            elif self._cloud_preferred(device) and has_account_fallback(device):
                # Its LAN path just failed; skip the doomed local timeout.
                self._queue_account_status(key, prefer_account=True)
            else:
                indexed_devices.append((idx, device))
        self._start_account_pass()
        if not indexed_devices or self._status_running():
            return

        # Capture the discovery fields before the pass; anything the devices
        # teach us here is persisted when the pass ends (see _flush_discovery).
        self._discovery_before = {
            self._device_key(device): {k: device.get(k) for k in DISCOVERY_KEYS}
            for _, device in indexed_devices
        }
        self._launch_status_worker(indexed_devices, account=False)

    def _launch_status_worker(
        self,
        indexed_devices: List[tuple[int, Dict[str, Any]]],
        *,
        account: bool,
        prefer_keys: Sequence[str] | set[str] = (),
    ) -> None:
        """Start one status pass on its own thread (local or account)."""
        if self._unvisited_status_keys:
            # Devices a previous pass ran out of time for go first, so a few
            # slow devices at the top of the list cannot starve the rest.
            order = {key: rank for rank, key in enumerate(self._unvisited_status_keys)}
            indexed_devices = sorted(
                indexed_devices,
                key=lambda item: order.get(self._device_key(item[1]), len(order)),
            )
            launched = {self._device_key(device) for _, device in indexed_devices}
            self._unvisited_status_keys = [
                key for key in self._unvisited_status_keys if key not in launched
            ]

        thread = QThread(self)
        worker = DeviceStatusWorker(
            indexed_devices,
            prefer_account_keys=prefer_keys,
            defer_account_fallback=not account,
            budget=self.STATUS_PASS_BUDGET,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run, Qt.ConnectionType.QueuedConnection)
        worker.state_updated.connect(self._merge_device_state, Qt.ConnectionType.QueuedConnection)
        worker.error.connect(self._on_status_error, Qt.ConnectionType.QueuedConnection)
        worker.unvisited.connect(self._on_status_unvisited, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        thread.finished.connect(worker.deleteLater)
        if account:
            thread.finished.connect(self._clear_account_status_refs, Qt.ConnectionType.QueuedConnection)
            self.account_status_thread = thread
            self.account_status_worker = worker
        else:
            worker.account_needed.connect(
                self._on_account_status_needed, Qt.ConnectionType.QueuedConnection
            )
            # Before _clear_status_refs: queued slots run in connection order, and
            # the flush needs self.status_thread still set for its sender guard.
            thread.finished.connect(self._flush_discovery, Qt.ConnectionType.QueuedConnection)
            thread.finished.connect(self._clear_status_refs, Qt.ConnectionType.QueuedConnection)
            self.status_thread = thread
            self.status_worker = worker
        thread.start()

    def select_device(self, index: int):
        """Select a device by index.

        Args:
            index: Index of the device to select
        """
        if not (0 <= index < len(self.devices)):
            return

        previous = self.selected_device_index
        self.selected_device_index = index

        if previous != index:
            # Clicking the same card repeatedly (e.g. to refresh) used to
            # rewrite the whole settings file every time — a needless
            # micro-stutter on every click.
            try:
                self._store.save(selected=index)
            except Exception as exc:
                # The selection still applies for this session.
                self._report_settings_failure(exc, "Could not remember the selected device.")

        try:
            device = self.get_selected_device()
            if device is None:
                return
            self.device_selected.emit(device)
            self.status_updated.emit(
                f"Selected device: {device.get('model', 'Unknown')}"
            )
            self.refresh_device_state_at(index)
        except Exception as e:
            self.status_updated.emit(f"Error selecting device: {str(e)}")

    def get_selected_device(self) -> Optional[Dict[str, Any]]:
        """Get the currently selected device.

        Returns:
            Selected device dictionary or None if no device selected
        """
        if self.devices and 0 <= self.selected_device_index < len(self.devices):
            return self.devices[self.selected_device_index]
        return None

    def _can_try_lan(self, device: Dict[str, Any]) -> bool:
        return bool(device.get("ip"))

    def _is_ble(self, device: Dict[str, Any]) -> bool:
        return str(device.get("transport", "")).lower() == "ble"

    @staticmethod
    def _cloud_fallback_device(device: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Return a cloud-transport copy for a local Tuya device, if available."""
        if str(device.get("cloud_transport", "")).lower() != "tuya_cloud":
            return None
        if not device.get("account_id"):
            return None
        fallback = dict(device)
        fallback["transport"] = "tuya_cloud"
        return fallback

    def _cloud_preferred(self, device: Dict[str, Any]) -> bool:
        until = self._cloud_preferred_until.get(self._device_key(device), 0.0)
        if until <= time.time():
            self._cloud_preferred_until.pop(self._device_key(device), None)
            return False
        return True

    def is_cloud_preferred(self, device: Dict[str, Any]) -> bool:
        """Whether a local device is temporarily using its cloud connection."""
        return self._cloud_preferred(device)

    def _remember_status_origin(
        self, key: str, device: Dict[str, Any], updates: Dict[str, Any]
    ) -> None:
        # The worker's marker is bookkeeping, never device state.
        lan_failed = bool(updates.pop("lan_failed", False))
        # Only local Tuya devices have the expensive LAN timeout this cache
        # avoids. Govee LAN status is cheap and stays on its 15 s local poll.
        if str(device.get("transport") or "").lower() != "tuya":
            self._lan_failed_keys.discard(key)
            return
        origin = updates.get("status_origin")
        if origin == "cloud":
            lan_failed = lan_failed or key in self._lan_failed_keys
            self._lan_failed_keys.discard(key)
            if lan_failed and updates.get("online"):
                # The LAN read just failed and the account answered: give the
                # cloud path a two-minute head start so the 15 s local poll
                # and manual controls don't keep paying the LAN timeout. A
                # read that went to the account first proves nothing about
                # the LAN, so it neither starts nor renews this.
                self._cloud_preferred_until[key] = time.time() + 120.0
        elif origin == "local" and updates.get("status_source") in (
            "confirmed",
            "commanded",
            "seen",
        ):
            self._cloud_preferred_until.pop(key, None)
            self._lan_failed_keys.discard(key)

    def _run_adapter(self, device: Dict[str, Any], action) -> str:
        """Run a control action through the device's transport adapter.

        BLE devices use a shared, persistent connection from the pool (no
        reconnect per action). Govee devices open a short-lived LAN socket per
        action, matching the previous behavior.
        """
        if pool.is_pooled(device):
            fallback = self._cloud_fallback_device(device)
            if fallback is not None and self._cloud_preferred(device):
                adapter = create_adapter(fallback)
                try:
                    action(adapter)
                finally:
                    try:
                        adapter.close()
                    except Exception:
                        pass
                return "Wi-Fi cloud"
            try:
                action(pool.acquire(device))
                self._cloud_preferred_until.pop(self._device_key(device), None)
                return "BLE" if self._is_ble(device) else "LAN"
            except Exception as local_error:
                if fallback is None:
                    raise
                logger.info(
                    "Local control failed for %s; trying the cloud path: %s",
                    device.get("name") or device.get("device_id"),
                    local_error,
                )
                adapter = create_adapter(fallback)
                try:
                    action(adapter)
                finally:
                    try:
                        adapter.close()
                    except Exception:
                        pass
                self._cloud_preferred_until[self._device_key(device)] = time.time() + 120.0
                return "Wi-Fi cloud"

        if not self._can_try_lan(device) and not is_cloud(device):
            raise RuntimeError(
                "Device has no LAN IP. Discover it on the same network or add it manually."
            )
        adapter = create_adapter(device)
        try:
            action(adapter)
        finally:
            try:
                adapter.close()
            except Exception:
                pass
        return "Wi-Fi cloud" if is_cloud(device) else "LAN"

    def _queue_control(self, index: int, method: str, args: tuple, updates: dict, output=None) -> None:
        device = self._device_at(index)
        if not device or self._closing:
            return
        flag = {"set_power": "supports_power", "set_brightness": "supports_brightness",
                "set_color": "supports_color", "set_color_temperature": "supports_white"}.get(method)
        if flag and not getattr(capabilities_for_device(device), flag):
            self.status_updated.emit("This device does not support that control.")
            return
        key = self._device_key(device)
        request = (as_device(device).copy(), method, args, updates, output)
        if method == "set_power":
            self._commands.requested_power[key] = bool(args[0])
        if not self._commands.enqueue(key, method, request):
            return
        self._start_control(key, request)

    def _start_control(self, key: str, request) -> None:
        device, method, args, updates, output = request

        def action():
            try:
                transport = self._run_adapter(
                    device, lambda adapter: getattr(adapter, method)(*args)
                )
                return {"key": key, "transport": transport, "updates": updates,
                        "output": output, "error": None}
            except Exception as exc:
                from ...accounts.errors import AccountError
                logger.info("Control %s failed for %s (%s)", method, self._device_name(device), type(exc).__name__)
                return {"key": key, "error": _control_error_text(exc, device),
                        "account_error_code": exc.code if isinstance(exc, AccountError) else None}

        self._commands.tasks[key] = start_task(action, self._on_control_finished)

    @Slot(object, object)
    def _on_control_finished(self, result, error) -> None:
        if result is None:
            self._notify(error or "Device control failed.", NOTICE_ERROR)
            return
        key = result["key"]
        self._commands.tasks.pop(key, None)
        index = next((i for i, d in enumerate(self.devices) if self._device_key(d) == key), -1)
        if index >= 0 and not self._closing:
            device = self.devices[index]
            if result.get("error"):
                self._merge_device_state(index, {"last_error": result["error"], "stale": True, "status_source": "error"})
                if result.get("account_error_code") in ACCOUNT_BROKEN_CODES and device.get("account_id"):
                    self.record_account_failure(str(device["account_id"]),
                                                AccountFailure(str(result["error"]), result["account_error_code"]))
                # Failures name the device; successes stay silent (the card
                # and inspector already show the new state).
                self._notify(f"{self._device_name(device)}: {result['error']}", NOTICE_ERROR)
            else:
                updates = dict(result.get("updates") or {})
                via_account = result.get("transport") == "Wi-Fi cloud"
                if not is_cloud(device):
                    updates["command_route"] = "account" if via_account else "local"
                self._record_command_state(index, updates, output=result.get("output"))
                if via_account and device.get("account_id"):
                    self.clear_account_issue(str(device["account_id"]))
                self._schedule_status_refresh(index)
        pending = self._commands.pop_next(key)
        if pending is not None and index >= 0 and not self._closing:
            self._start_control(key, pending)
        else:
            self._commands.complete(key)

    def _send_turn(self, device: Dict[str, Any], on: bool) -> str:
        return self._run_adapter(device, lambda adapter: adapter.set_power(on))

    def _send_brightness(self, device: Dict[str, Any], brightness: int) -> str:
        return self._run_adapter(
            device, lambda adapter: adapter.set_brightness(brightness)
        )

    def _send_color(self, device: Dict[str, Any], r: int, g: int, b: int) -> str:
        return self._run_adapter(device, lambda adapter: adapter.set_color(r, g, b))

    def _send_color_temp(self, device: Dict[str, Any], kelvin: int) -> str:
        return self._run_adapter(
            device, lambda adapter: adapter.set_color_temperature(kelvin)
        )

    def get_capabilities_at(self, index: int):
        """Return the SKU capabilities for a device, or None."""
        device = self._device_at(index)
        if not device:
            return None
        return capabilities_for_device(device)

    def supports_color_temp_at(self, index: int) -> bool:
        cap = self.get_capabilities_at(index)
        return bool(cap and cap.supports_white and cap.color_temp_max > cap.color_temp_min > 0)

    def set_color_temperature_at(self, index: int, kelvin: int) -> None:
        """Set a device's tunable-white color temperature (Kelvin)."""
        device = self._device_at(index)
        if not device:
            return
        cap = self.get_capabilities_at(index)
        if cap and cap.color_temp_max > 0:
            kelvin = max(cap.color_temp_min, min(cap.color_temp_max, int(kelvin)))
        from ...utils.colors import kelvin_to_rgb
        self._queue_control(index, "set_color_temperature", (int(kelvin),),
                            {"color": kelvin_to_rgb(int(kelvin)), "color_temp": int(kelvin)}, f"White {int(kelvin)}K")

    def scan_ble_devices(self) -> None:
        """Scan for iDotMatrix devices over BLE and add likely matches.

        The scan itself blocks for ~8 s, so it runs in a worker thread;
        results are processed back on the GUI thread. Auto-adds devices whose
        advertisement looks like an iDotMatrix panel; if none match, reports
        what was seen so the user can add by address instead.
        """
        self._start_ble_scan(announce=True)

    def _start_ble_scan(self, *, announce: bool) -> bool:
        if self._ble_scan_running():
            if announce:
                self.status_updated.emit("Bluetooth search is already in progress.")
            return False

        self.ble_scan_started.emit()
        if announce:
            self.status_updated.emit("Searching for nearby Bluetooth devices...")

        thread = QThread(self)
        worker = BleScanWorker()
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_ble_scan_finished)
        worker.error.connect(self._on_ble_scan_error)

        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._clear_ble_scan_refs, Qt.ConnectionType.QueuedConnection)

        self._ble_scan_thread = thread
        self._ble_scan_worker = worker
        thread.start()
        return True

    @Slot()
    def _clear_ble_scan_refs(self) -> None:
        self._release_worker_thread("_ble_scan_thread", "_ble_scan_worker", self._clear_ble_scan_refs)

    def _on_ble_scan_error(self, message: str) -> None:
        if self._combined_search_active:
            self._search_results["bluetooth"].update(
                {"available": False, "found": 0, "error": message}
            )
        else:
            self.status_updated.emit("Bluetooth search unavailable. Check that Bluetooth is on.")
        self.ble_scan_finished.emit()
        if self._combined_search_active:
            self._finish_search_transport("bluetooth")

    def _on_ble_scan_finished(self, found: List[Dict[str, Any]]) -> None:
        if self._closing:
            return
        likely = [d for d in found if d.get("likely")]
        added = 0
        for entry in likely:
            if self.add_ble_device_manually(
                entry.get("ble_address", ""),
                entry.get("model") or "iDotMatrix",
                announce=not self._combined_search_active,
            ):
                added += 1

        visible_addresses = {
            str(entry.get("ble_address") or "").casefold()
            for entry in found
            if entry.get("ble_address")
        }
        for index, device in enumerate(self.devices):
            if not self._is_ble(device):
                continue
            address = str(
                device.get("ble_address") or device.get("mac") or ""
            ).casefold()
            if address and address in visible_addresses:
                self._merge_device_state(
                    index,
                    {
                        "online": True,
                        "stale": False,
                        "status_source": "seen",
                        "readback_supported": False,
                        "last_seen": time.time(),
                        "last_error": None,
                    },
                )
                continue

            state = self.get_device_state_at(index)
            # A connected BLE panel often stops advertising. Keep a successful
            # command or active stream as the stronger signal; otherwise say
            # only that the latest search could not see it.
            if state.get("status_source") != "commanded" and not state.get(
                "active_output"
            ):
                self._merge_device_state(
                    index,
                    {
                        "online": False,
                        "stale": False,
                        "status_source": "not_seen",
                        "readback_supported": False,
                        "last_error": None,
                    },
                )

        if self._combined_search_active:
            self._search_results["bluetooth"].update(
                {
                    "available": True,
                    "found": len(likely),
                    "added": added,
                    "seen": len(found),
                    "error": None,
                }
            )
        elif added:
            self.status_updated.emit(
                f"Bluetooth scan added {added} iDotMatrix device(s)."
            )
        elif found:
            names = sorted(
                {
                    d["model"]
                    for d in found
                    if d.get("model") and d["model"] != "Unknown"
                }
            )
            seen = ", ".join(names[:8]) if names else "unnamed devices"
            self.status_updated.emit(
                f"No iDotMatrix panel matched among {len(found)} Bluetooth device(s). "
                f"Seen: {seen}. Disconnect the phone app from the panel and rescan, "
                f"or use 'Add Manually' with its Bluetooth address."
            )
        else:
            self.status_updated.emit(
                "No Bluetooth devices found. Turn Bluetooth on and make sure the "
                "panel is advertising (disconnect it from the phone app first)."
            )
        self.ble_scan_finished.emit()
        if self._combined_search_active:
            self._finish_search_transport("bluetooth")

    def _finish_search_transport(self, transport: str) -> None:
        self._search_pending.discard(transport)
        if self._search_pending or not self._combined_search_active:
            return

        known = self._search_known_keys
        self._search_known_keys = None
        new_devices = self._count_new_devices(known)
        summary = {
            name: dict(result) for name, result in self._search_results.items()
        }
        summary["new_devices"] = new_devices
        self._combined_search_active = False
        self.device_search_finished.emit(summary)

        lan = summary.get("lan", {})
        bluetooth = summary.get("bluetooth", {})
        tuya = summary.get("tuya", {})
        accounts = summary.get("accounts", {})
        issues = []
        if not lan.get("available"):
            issues.append("no local network")
        if not bluetooth.get("available"):
            issues.append("Bluetooth unavailable")
        if tuya and not tuya.get("available"):
            issues.append("Tuya LAN search unavailable")
        if accounts.get("errors"):
            issues.append(f"{len(accounts['errors'])} account(s) couldn't refresh")

        # One outcome only. New devices get a single success notice; problems
        # are shown by the Devices page as InfoBars; a quiet search says
        # nothing beyond the status bar.
        if new_devices:
            self.notice.emit(
                f"Found {new_devices} new device{'s' if new_devices != 1 else ''}", NOTICE_SUCCESS
            )
        if issues:
            self.status_updated.emit("Device search finished: " + "; ".join(issues) + ".")
        elif not new_devices:
            self.status_updated.emit("Device search finished. No new devices.")
        else:
            self.status_updated.emit(
                f"Device search finished. Found {new_devices} new device{'s' if new_devices != 1 else ''}."
            )
        self.refresh_device_states()

    @staticmethod
    def _identity_keys(device_list) -> set[str]:
        keys: set[str] = set()
        for device in device_list or ():
            keys.update(devices._device_keys(device))
            keys.add(groups.device_key(device))
        keys.discard("")
        return keys

    def _count_new_devices(self, known: Optional[set[str]]) -> int:
        """Devices that share no identity with anything known before the search."""
        if known is None:
            return 0
        return sum(1 for device in self.devices if not (self._identity_keys([device]) & known))

    def set_zone_count_at(self, index: int, zone_count: Optional[int]) -> bool:
        """Set or clear a per-device LED/zone count override."""
        device = self._device_at(index)
        if not device:
            return False

        if str(device.get("transport", "lan")).lower() not in ("lan", "tuya") or not self.get_capabilities_at(index).supports_segments:
            self._notify("This device does not support configurable LED zones.", NOTICE_ERROR)
            return False
        if self.is_device_busy(device):
            self._notify("Finish the current output before changing this device's zone count.", NOTICE_ERROR)
            return False

        try:
            device = dict(device)
            if zone_count is None:
                device.pop("segment_count_override", None)
                message = f"{device.get('model', 'Device')}: using default zone count"
            else:
                count = int(zone_count)
                if not 1 <= count <= 255:
                    raise ValueError("Zone count must be between 1 and 255")
                device["segment_count_override"] = count
                message = f"{device.get('model', 'Device')}: zone count set to {count}"

            updated = list(self.devices)
            updated[index] = device
            if not self._write_settings(
                {"devices": updated, "selectedDevice": self.selected_device_index},
                failure="Could not save the zone count.",
            ):
                return False
            self.devices = updated

            self.device_updated.emit(index, dict(device))
            self.devices_discovered.emit(self.devices)
            selected = self.get_selected_device()
            if selected and index == self.selected_device_index:
                self.device_selected.emit(selected)
            self.status_updated.emit(message)
            return True
        except ValueError as e:
            self._notify(str(e), NOTICE_ERROR)
            return False
        except Exception:
            logger.warning("Zone count change failed", exc_info=True)
            self._notify("Could not change the zone count.", NOTICE_ERROR)
            return False

    def turn_on_off(self, on: bool = True):
        """Turn the selected device on or off.

        Args:
            on: True to turn on, False to turn off
        """
        self.turn_on_off_at(self.selected_device_index, on)

    def set_razer_mode(self, on: bool = True):
        """Set the device to Razer mode.

        Args:
            on: True to enable Razer mode, False to disable
        """
        try:
            device = self.get_selected_device()
            if not device:
                self.status_updated.emit("No device selected")
                return

            self._ensure_server()
            try:
                connection.switch_razer(self.server, device, on)
            finally:
                self._close_server()
            self._record_command_state(
                self.selected_device_index,
                {},
                output="Razer mode" if on else "Standard mode",
            )
            self.status_updated.emit(f"Razer mode {'enabled' if on else 'disabled'}")
            self._schedule_status_refresh(self.selected_device_index)
        except Exception as e:
            self.status_updated.emit(f"Error: {str(e)}")

    def set_device_color(self, r: int, g: int, b: int):
        """Set the color of the selected device.

        Args:
            r: Red component (0-255)
            g: Green component (0-255)
            b: Blue component (0-255)
        """
        self.set_color_at(self.selected_device_index, r, g, b)

    def set_device_brightness(self, brightness: int):
        """Set the brightness of the selected device.

        Args:
            brightness: Brightness value (0-100)
        """
        self.set_brightness_at(self.selected_device_index, brightness)

    # --- Per-index variants (used by multi-device card UI) ------------------
    # These act on a specific device without changing the "primary" selection.

    def _device_at(self, index: int) -> Optional[Dict[str, Any]]:
        if 0 <= index < len(self.devices):
            return self.devices[index]
        return None

    def turn_on_off_at(self, index: int, on: bool = True) -> None:
        self._queue_control(index, "set_power", (bool(on),), {"power_on": bool(on)}, "On" if on else "Off")

    def toggle_power_at(self, index: int) -> None:
        state = self.get_device_state_at(index)
        current = self._requested_power.get(state.get("device_key"), state.get("power_on"))
        self.turn_on_off_at(index, True if current is None else not bool(current))

    def set_color_at(self, index: int, r: int, g: int, b: int) -> None:
        r, g, b = (max(0, min(255, int(c))) for c in (r, g, b))
        self._queue_control(index, "set_color", (r, g, b), {"color": (r, g, b), "color_temp": 0}, f"#{r:02X}{g:02X}{b:02X}")

    def set_brightness_at(self, index: int, brightness: int) -> None:
        bounded = max(0, min(100, int(brightness)))
        self._queue_control(index, "set_brightness", (bounded,), {"brightness": bounded})

    # --- Vendor account imports / connection preference -------------------

    @staticmethod
    def _scan_tuya_lan() -> list[dict]:
        import tinytuya
        with tuya_presence.released_port():
            found = tinytuya.deviceScan(verbose=False, maxretry=3, poll=False)
        results = [{"device_id": d.get("gwId") or d.get("id"), "ip": d.get("ip"),
                    "protocol_version": str(d.get("version", "3.3"))} for d in found.values()
                   if isinstance(d, dict) and (d.get("gwId") or d.get("id")) and d.get("ip")]
        for result in results:
            tuya_presence.registry.note_seen(result["device_id"], address=result["ip"])
        return results

    @Slot(str, str)
    def _on_tuya_device_seen(self, device_id: str, address: str) -> None:
        """Save the address an LSC/Tuya light now announces from.

        Adapters already follow the beat on their own; saving it keeps the
        device list and the next launch on the right IP. A light that is
        streaming or running a command is updated on a later beat, once idle.
        """
        if self._closing or not device_id or not address:
            return
        updated = None
        moved = []
        for index, device in enumerate(self.devices):
            typed = as_device(device)
            if typed.transport != "tuya" or typed.device_id != device_id:
                continue
            # A live LAN beat means the local path is healthy again; stop
            # preferring cloud for this device immediately.
            self._cloud_preferred_until.pop(self._device_key(device), None)
            if typed.ip == address:
                continue
            if self.is_device_busy(device):
                continue
            if updated is None:
                updated = [as_device(d).copy() for d in self.devices]
            pool.close(updated[index])
            updated[index]["ip"] = address
            moved.append(typed.get("name") or typed.get("model") or device_id)
        if updated is None:
            return
        try:
            self._store.write({"devices": updated})
        except Exception as exc:
            # The adapters already follow the beat; the next beat retries.
            logger.warning("Could not save a moved device address (%s)", type(exc).__name__)
            return
        self.devices = updated
        self.devices_discovered.emit(self.devices)
        self.status_updated.emit(f"{', '.join(moved)}: now at {address}")

    @Slot(object, object)
    def _on_tuya_scan_finished(self, found, error) -> None:
        if self._closing:
            return
        found = found or []
        if self._combined_search_active:
            self._tuya_scan_results = list(found)
        updated = [as_device(d).copy() for d in self.devices]
        matched = 0
        for raw in found:
            for index, device in enumerate(updated):
                if device.get("device_id") != raw["device_id"] or not device.get("local_supported", True):
                    continue  # Broadcasts contain no key; only enrich known devices.
                matched += 1
                if self.get_device_state_at(index).get("active_output") or self._device_key(device) in self._command_tasks:
                    continue
                pool.close(device)
                device.update(raw)
                if device.get("local_key_ref") or device.get("local_key"):
                    device["local_transport"] = "tuya"
                    if is_cloud(device) and device.get("connection_preference") != "cloud":
                        device["cloud_transport"] = device["transport"]
                        device["transport"] = "tuya"
        try:
            if matched:
                self._store.write({"devices": updated})
                self.devices = updated
                self.devices_discovered.emit(self.devices)
        except Exception as exc:
            logger.warning("Could not save local Tuya connections (%s)", type(exc).__name__)
            error = "Could not save the local Tuya connections."
        if self._combined_search_active:
            self._search_results["tuya"] = {"available": not bool(error), "found": matched, "seen": len(found), "error": error}
            self._finish_search_transport("tuya")

    def shutdown(self) -> bool:
        """Request cancellation, retaining running QThreads until they finish."""
        self._closing = True
        tuya_presence.unsubscribe(self._presence_callback)
        self._adopt_pooled_discovery()
        self._close_server()
        self._command_queue.clear()
        for timer in self._confirmation_timers.values():
            timer.stop()
            timer.deleteLater()
        self._confirmation_timers.clear()
        self._confirmation_delays.clear()
        self._account_pass_timer.stop()
        self._account_status_queue.clear()
        running = False
        for thread in (
            self.discovery_thread,
            self.status_thread,
            self.account_status_thread,
            self._ble_scan_thread,
        ):
            if thread is not None:
                try:
                    thread.requestInterruption()
                    thread.quit()
                    running = not thread.wait(0) or running
                except RuntimeError:
                    pass
        return not running

    # --- Sync groups -------------------------------------------------------

    # --- Settings persistence ----------------------------------------------

    def _load_settings_safe(self) -> Dict[str, Any]:
        """Settings for display only (accounts, groups); never saved back."""
        return self._store.load_safe()

    @staticmethod
    def _settings_failure_text(exc: BaseException, failure: str) -> str:
        if isinstance(exc, SettingsReloaded):
            return str(exc)
        if isinstance(exc, devices.SettingsUnavailable):
            return SETTINGS_UNREADABLE
        return f"{failure} {SETTINGS_UNWRITABLE}"

    def _report_settings_failure(self, exc: BaseException, failure: str) -> None:
        # Exception type only: the GUI shows a friendly message instead of
        # raw OS text, and the log never needs more than the kind of failure.
        logger.warning("Settings were not saved (%s): %s", type(exc).__name__, failure)
        self._notify(self._settings_failure_text(exc, failure), NOTICE_ERROR)

    def _write_settings(self, updates, *, failure: str) -> bool:
        """Save *updates* (top-level keys, or a function of the fresh file).

        Returns False after reporting a friendly status message instead of
        letting a write error reach a button slot.
        """
        try:
            self._store.write(updates)
        except Exception as exc:
            self._report_settings_failure(exc, failure)
            return False
        return True

    def _retry_settings_load(self) -> None:
        """Retry a startup load that failed because settings.json was locked."""
        if self._store.needs_reload:
            self._store.retry_load()

    def _on_settings_reloaded(self) -> None:
        # Called from inside a store read; announce from the event loop so
        # views never rebuild in the middle of the operation that triggered it.
        QTimer.singleShot(0, self._announce_reloaded_settings)

    def _announce_reloaded_settings(self) -> None:
        if self._closing:
            return
        logger.info("Settings are readable again; loaded %d saved device(s)", len(self.devices))
        self.devices_discovered.emit(self.devices)
        device = self.get_selected_device()
        if device:
            self.device_selected.emit(device)
        self.groups_changed.emit(self.get_groups())
        self._notify(
            f"Settings are readable again. Loaded {len(self.devices)} saved device(s).", NOTICE_INFO
        )
        self._prune_session_accounts(self._load_settings_safe().get("accounts"))

    def get_groups(self) -> List[Dict[str, Any]]:
        """Return the saved sync groups."""
        return groups.list_groups(self._load_settings_safe())

    def _persist_groups(
        self, change: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]
    ) -> bool:
        """Apply *change* to the groups as saved on disk right now and save.

        Returns False (after a status message) when settings could not be
        read or written; nothing else in settings.json is touched.
        """
        result: Dict[str, List[Dict[str, Any]]] = {}

        def update(settings: Dict[str, Any]) -> Dict[str, Any]:
            result["groups"] = change(groups.list_groups(settings))
            return {"groups": result["groups"]}

        if not self._write_settings(update, failure="Could not save the sync groups."):
            return False
        self.groups_changed.emit(result["groups"])
        return True

    def save_group(self, name: str, indices: List[int]) -> bool:
        """Create or replace a group from the given device indices."""
        name = (name or "").strip()
        members = [self.devices[i] for i in indices if 0 <= i < len(self.devices)
                   and capabilities_for_device(self.devices[i]).supports_streaming]
        if not name or not members:
            self._notify("Group needs a name and at least one device.", NOTICE_ERROR)
            return False
        group = groups.make_group(name, members)
        if not self._persist_groups(lambda existing: groups.upsert_group(existing, group)):
            return False
        self.status_updated.emit(f"Saved group '{name}' ({len(members)} device(s))")
        return True

    def delete_group(self, name: str) -> bool:
        if not self._persist_groups(lambda existing: groups.remove_group(existing, name)):
            return False
        self.status_updated.emit(f"Deleted group '{name}'")
        return True

    def get_group_devices(self, name: str) -> List[Dict[str, Any]]:
        """Resolve a group name to the current device dicts it contains."""
        group = groups.find_group(self.get_groups(), name)
        if not group:
            return []
        return groups.resolve_devices(group, self.devices)

    def get_group_indices(self, name: str) -> List[int]:
        """Return the current device indices belonging to a group."""
        group = groups.find_group(self.get_groups(), name)
        if not group:
            return []
        keys = set(group.get("devices", []))
        return [
            i for i, device in enumerate(self.devices)
            if groups.device_key(device) in keys
        ]

    def _ensure_server(self):
        """Ensure server socket exists."""
        if self.server is None:
            self.server = connection.create_lan_socket()

    def _close_server(self) -> None:
        if self.server is not None:
            try:
                self.server.close()
            finally:
                self.server = None
