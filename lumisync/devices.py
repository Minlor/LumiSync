"""
Device handling for LumiSync.
This module handles device discovery and management.
"""

import copy
import datetime
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence

from . import connection
from .connection import connect, listen as connection_listen, parse
from .config.options import CONNECTION
from .device import Device, DeviceLike, as_device
from .utils import write_json, get_logger
from .utils.file_operations import replace_with_retry

# Set up logger for devices module
logger = get_logger('lumisync_devices')

# Store a global server socket that can be reused
_global_server = None

# Points the default settings.json somewhere else (tests, self-checks).
SETTINGS_FILE_ENV = "LUMISYNC_SETTINGS_FILE"

# One-shot warning for the GUI/CLI after a settings file was recovered.
_settings_warning: Optional[str] = None

# One-shot notice for the GUI after a write had to keep a device's saved key
# form because the OS credential store is unavailable.
_save_notice: Optional[str] = None
_key_store_notice_given = False


class SettingsUnavailable(OSError):
    """settings.json exists but cannot be read right now.

    Raised instead of returning an empty inventory: anything saved on top of
    an empty read would erase the user's devices, accounts and groups. The
    file is usually only locked for a moment (antivirus, backup, sync tools).
    """


def _empty_settings() -> Dict[str, Any]:
    return {"devices": [], "selectedDevice": 0, "time": time.time()}


def _remember_settings_warning(message: str) -> None:
    global _settings_warning
    _settings_warning = message
    logger.error(message)


def consume_settings_warning() -> Optional[str]:
    """Return (and clear) a warning produced while loading settings."""
    global _settings_warning
    message, _settings_warning = _settings_warning, None
    return message


def consume_save_notice() -> Optional[str]:
    """Return (and clear) a one-time notice produced while saving settings."""
    global _save_notice
    message, _save_notice = _save_notice, None
    return message


def _settings_unavailable(path: Path, exc: OSError) -> SettingsUnavailable:
    reason = exc.strerror or type(exc).__name__
    message = (
        f"Could not read settings from {path}: {reason}. LumiSync will not "
        "save changes until it can read the file again."
    )
    _remember_settings_warning(message)
    return SettingsUnavailable(message)


def _read_settings_file(path: Path) -> Dict[str, Any]:
    """Read a settings object, rejecting invalid UTF-8 and non-object JSON."""
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("settings root is not a JSON object")
    return data


def _quarantine_settings(path: Path) -> Optional[Path]:
    """Move a damaged settings file aside instead of letting a save erase it."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.corrupt-{stamp}")
    counter = 1
    while target.exists():
        target = path.with_name(f"{path.name}.corrupt-{stamp}-{counter}")
        counter += 1
    try:
        path.replace(target)
        return target
    except OSError as exc:
        logger.warning("Could not set damaged settings aside: %s", exc)
        return None


def _recover_saved_settings(path: Path, reason: str) -> Dict[str, Any]:
    """Restore the last good backup after finding a damaged settings file."""
    damaged = _quarantine_settings(path)
    backup_path = path.with_name(path.name + ".bak")
    restored = None
    if backup_path.is_file():
        try:
            restored = _read_settings_file(backup_path)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            restored = None

    location = f" The damaged file was kept as {damaged.name}." if damaged else ""
    if restored is not None:
        _remember_settings_warning(
            f"Your settings file was damaged ({reason}). LumiSync restored the "
            f"last good backup.{location}"
        )
        return restored

    _remember_settings_warning(
        f"Your settings file was damaged ({reason}) and no usable backup was "
        f"found.{location} Saved devices will need to be added or discovered again."
    )
    return _empty_settings()


def _backup_good_settings(path: Path) -> None:
    """Keep a backup copy of the last settings file that parsed successfully."""
    if not path.is_file():
        return
    try:
        with path.open("r", encoding="utf-8") as handle:
            json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        # Never overwrite a good backup with a damaged file.
        return

    backup = path.with_name(path.name + ".bak")
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(
            prefix=backup.name + ".", suffix=".tmp", dir=path.parent
        )
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            with path.open("r", encoding="utf-8") as source:
                shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
        replace_with_retry(temporary, backup)
        temporary = None
    except OSError as exc:
        logger.warning("Could not back up settings to %s: %s", backup, exc)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _looks_like_lumisync_settings(path: Path) -> bool:
    """Return whether *path* is a plausible legacy LumiSync settings file."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return False
    return isinstance(data, dict) and isinstance(data.get("devices", []), list)


def _legacy_settings_candidates() -> List[Path]:
    """Find old launch-directory settings files used by packaged builds."""
    candidates = [Path.cwd() / "settings.json"]
    executable = Path(sys.executable).resolve()
    candidates.append(executable.parent / "settings.json")

    # Developer preview builds live below the repository. This also makes the
    # migration deterministic when validating a packaged build locally.
    for parent in executable.parents:
        if (parent / "pyproject.toml").is_file():
            candidates.append(parent / "settings.json")
            break

    unique: List[Path] = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _is_packaged_app() -> bool:
    """Detect supported PyInstaller layouts without relying on one flag."""
    if getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None):
        return True
    executable = Path(sys.executable).resolve()
    return (
        executable.stem.casefold() == "lumisync"
        and (executable.parent / "_internal").is_dir()
    )


def _is_source_checkout() -> bool:
    """Return whether this module is running directly from a project checkout."""
    project_root = Path(__file__).resolve().parent.parent
    return (project_root / "pyproject.toml").is_file()


def settings_path(filename: str | os.PathLike[str] = "settings.json") -> Path:
    """Return a stable settings path, migrating old launch-directory data once.

    Source checkouts keep the historical relative path so scripts and tests are
    unchanged. Frozen builds and installed wheels use the per-user application-
    data directory because their working directory is not stable or writable.
    ``LUMISYNC_SETTINGS_FILE`` overrides the default file outright (the test
    suite and self-checks use it so they never touch the user's settings).
    """
    requested = Path(filename)
    if requested == Path("settings.json"):
        override = os.environ.get(SETTINGS_FILE_ENV, "").strip()
        if override:
            target = Path(override)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning("Could not create the settings folder %s: %s", target.parent, exc)
            return target
    uses_user_data = _is_packaged_app() or not _is_source_checkout()
    if requested != Path("settings.json") or not uses_user_data:
        return requested

    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    config_dir = Path(base) / "LumiSync" if base else Path.home() / ".lumisync"
    destination = config_dir / "settings.json"
    if destination.exists():
        return destination

    for legacy in _legacy_settings_candidates():
        if legacy == destination or not _looks_like_lumisync_settings(legacy):
            continue
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, destination)
            logger.info("Migrated settings from %s to %s", legacy, destination)
            break
        except OSError as exc:
            logger.warning("Could not migrate settings from %s: %s", legacy, exc)
    return destination


def _load_saved_settings(filename: str | os.PathLike[str] = "settings.json") -> Dict[str, Any]:
    """Load persisted devices without triggering a network discovery.

    A damaged file is moved aside and the last good ``.bak`` is restored rather
    than silently returning an empty inventory that the next save would make
    permanent. A file that exists but cannot be read (locked, permissions)
    raises :class:`SettingsUnavailable` for the same reason.
    """
    path = settings_path(filename)
    try:
        data = _read_settings_file(path)
    except FileNotFoundError:
        # A missing settings.json may follow a quarantine or an accidental
        # delete. Recover the last good backup instead of inventing an empty
        # inventory that the next save would make permanent.
        backup_path = path.with_name(path.name + ".bak")
        if not backup_path.is_file():
            return _empty_settings()
        try:
            data = _read_settings_file(backup_path)
        except FileNotFoundError:
            return _empty_settings()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return _empty_settings()
        except OSError as exc:
            # The backup exists but is locked: the next save would start a
            # fresh file and later rotate it over this good backup.
            raise _settings_unavailable(backup_path, exc) from exc
        _remember_settings_warning(
            f"Your settings file was missing. LumiSync restored "
            f"{backup_path.name}."
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
        data = _recover_saved_settings(path, str(exc))
    except OSError as exc:
        raise _settings_unavailable(path, exc) from exc

    devices = data.get("devices")
    if not isinstance(devices, list):
        devices = []

    try:
        selected = int(data.get("selectedDevice", 0))
    except (TypeError, ValueError):
        selected = 0

    copied = dict(data)
    copied["devices"] = [
        as_device(device)
        for device in devices
        if isinstance(device, (dict, Device)) and _has_driver(device)
    ]
    copied["selectedDevice"] = selected
    try:
        copied["time"] = float(data.get("time", 0) or 0)
    except (ValueError, TypeError):
        copied["time"] = 0.0
    copied["selectedDevice"] = _selected_index(copied, len(copied["devices"]))
    return copied


def load_settings(filename: str | os.PathLike[str] = "settings.json") -> Dict[str, Any]:
    """Read persisted settings from disk without triggering network discovery."""
    return _load_saved_settings(filename)


def _device_keys(raw_device: DeviceLike) -> List[str]:
    """Return stable match keys, preferring MAC over weaker IP/model fallback."""
    device = as_device(raw_device)
    keys: List[str] = []
    channel = device.get("tuya_channel")
    if device.device_id:
        keys.append(
            f"id:{device.device_id.lower()}" + (f":{channel}" if channel else "")
        )
    if device.mac:
        keys.append(f"mac:{device.mac.lower()}")

    # Gangs share an IP and physical ID, but must never merge into one another.
    if channel:
        return keys
    if device.ip and device.model:
        keys.append(f"ip-model:{device.ip.lower()}|{device.model.lower()}")
    elif device.ip:
        keys.append(f"ip:{device.ip.lower()}")

    return keys


def _has_driver(device: Any) -> bool:
    """False for a saved device whose connection type this version cannot drive.

    Such an entry (written by a newer or experimental build) is left out of
    the loaded list instead of crashing every view that asks for its driver.
    """
    from .drivers.registry import is_supported_device

    if is_supported_device(as_device(device)):
        return True
    try:
        label = device.get("name") or device.get("device_id") or "a saved device"
        transport = device.get("transport")
    except AttributeError:
        label, transport = "a saved device", None
    logger.warning(
        "Leaving out %s: this version has no driver for connection type %r.", label, transport
    )
    return False


def _normalize_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize every device once, at the settings boundary."""
    copied = dict(data)
    devices = copied.get("devices")
    if not isinstance(devices, list):
        devices = []
    copied["devices"] = [
        as_device(device)
        for device in devices
        if isinstance(device, (dict, Device)) and _has_driver(device)
    ]
    copied["selectedDevice"] = _selected_index(copied, len(copied["devices"]))
    return copied


def _selected_index(settings: Dict[str, Any], device_count: int) -> int:
    try:
        selected = int(settings.get("selectedDevice", 0))
    except (TypeError, ValueError):
        selected = 0
    if device_count <= 0:
        return 0
    return max(0, min(selected, device_count - 1))


def merge_discovered_devices(
    existing_devices: Sequence[DeviceLike],
    discovered_devices: Sequence[DeviceLike],
) -> List[Device]:
    """Merge LAN scan results into saved devices without dropping stale ones."""
    merged = [as_device(device).copy() for device in existing_devices]
    index_by_key: Dict[str, int] = {}

    for index, device in enumerate(merged):
        for key in _device_keys(device):
            index_by_key.setdefault(key, index)

    for raw_discovered in discovered_devices:
        copied = as_device(raw_discovered).copy()
        matching_index = next(
            (
                index_by_key[key]
                for key in _device_keys(copied)
                if key in index_by_key
            ),
            None,
        )

        if matching_index is None:
            matching_index = len(merged)
            merged.append(copied)
        else:
            existing = merged[matching_index]
            for key, value in copied.to_dict().items():
                if key == "mac" and str(existing.get("mac", "")).casefold() == str(value).casefold():
                    continue  # Preserve saved group identities across case changes.
                if value is not None:
                    existing[key] = value

        for key in _device_keys(merged[matching_index]):
            index_by_key.setdefault(key, matching_index)

    return merged


# Driver modules (under lumisync.drivers) whose discover() lists lighting on
# this PC. Each returns descriptors and never raises; adding a driver is one
# entry here.
LOCAL_HARDWARE_DRIVERS = ("gigabyte_fusion", "logitech_hidpp", "lorgar_keyboard", "asus_laptop")


def discover_local_hardware() -> List[Dict[str, Any]]:
    """Descriptors of lighting attached to this PC, never raising.

    Each driver in :data:`LOCAL_HARDWARE_DRIVERS` is asked separately, so
    one failing never hides another's devices. These are not LAN replies:
    callers merge them into the inventory but keep them out of
    ``lastDiscoveryCount``, which only counts devices that answered on the
    network (firewall and no-device hints key off it).
    """
    found: List[Dict[str, Any]] = []
    for name in LOCAL_HARDWARE_DRIVERS:
        try:
            driver = importlib.import_module(f".drivers.{name}", __package__)
            found.extend(dict(device) for device in driver.discover())
        except Exception:
            logger.warning("Local hardware discovery failed (%s)", name, exc_info=True)
    return found


def discover_lan_devices(
    preserve_existing: bool = True, deep: bool = False, *, persist: bool = True
) -> Dict[str, Any]:
    """Discover LAN devices once, merge with saved devices, and persist safely.

    When ``deep`` is set, a unicast subnet sweep runs alongside the multicast
    scan so devices are still found on networks that filter multicast.
    """
    saved_settings = _empty_settings()
    if preserve_existing:
        try:
            saved_settings = _load_saved_settings()
        except SettingsUnavailable:
            if persist:
                raise
            # Nothing is written in this mode; the caller merges the results
            # into its own inventory, so a locked file must not stop the scan.
            saved_settings = _empty_settings()
    existing_devices = saved_settings.get("devices", [])
    if not isinstance(existing_devices, list):
        existing_devices = []

    server = None
    previous_devices = [dict(device) for device in CONNECTION.devices]
    try:
        CONNECTION.devices = []
        logger.info("Requesting device data from network")
        server, discovered_devices = connect()
        discovered_devices = [dict(device) for device in discovered_devices]
    except Exception:
        CONNECTION.devices = previous_devices
        raise
    finally:
        if server is not None:
            try:
                server.close()
            except Exception:
                pass

    if deep:
        sweep_server = None
        try:
            logger.info("Running deep subnet sweep for devices")
            sweep_server, swept = connection.sweep_scan()
            discovered_devices = merge_discovered_devices(
                discovered_devices, [dict(device) for device in swept]
            )
        except Exception as exc:
            logger.warning("Deep sweep failed: %s", exc)
        finally:
            if sweep_server is not None:
                try:
                    sweep_server.close()
                except Exception:
                    pass

    merged_devices = merge_discovered_devices(
        [device for device in existing_devices if isinstance(device, (dict, Device))],
        discovered_devices,
    )
    settings = copy.deepcopy(saved_settings)
    settings["devices"] = merged_devices
    settings["selectedDevice"] = _selected_index(settings, len(merged_devices))
    settings["time"] = time.time()
    settings["lastDiscoveryCount"] = len(discovered_devices)

    CONNECTION.devices = [as_device(device) for device in merged_devices]

    logger.info(
        "Discovery found %s responding device(s); keeping %s total device(s)",
        len(discovered_devices),
        len(merged_devices),
    )
    if persist:
        writeJSON(settings)
    else:
        settings["lastDiscoveryDevices"] = discovered_devices
    return settings

def request() -> connection.LanSocket:
    """Request device data from the network."""
    global _global_server

    # If we have an existing server, close it properly first
    if _global_server is not None:
        try:
            _global_server.close()
        except Exception:
            pass

    logger.info("Requesting device data from network")
    server, _ = connect()
    _global_server = server
    return server

def listen(server: connection.LanSocket) -> List[str]:
    """Listen for device responses."""
    try:
        logger.info("Listening for device responses")
        messages = connection_listen(server)
        logger.info(f"Received {len(messages)} device response(s)")
        return messages
    except Exception as e:
        error_msg = f"Error listening for devices: {str(e)}"
        logger.error(error_msg, exc_info=True)
        return []

def parseMessages(messages: List[str]) -> Dict[str, Any]:
    """Parse messages from devices."""
    logger.info(f"Parsing {len(messages)} device message(s)")
    devices = parse(messages)

    settings = {
        "devices": [as_device(device) for device in devices],
        "selectedDevice": 0,
        "time": time.time()
    }

    logger.info(f"Found {len(devices)} device(s)")
    return settings

def _saved_device_entries(path: Path) -> List[Dict[str, Any]]:
    """Return the device entries currently on disk, or [] when unreadable."""
    try:
        data = _read_settings_file(path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return []
    saved = data.get("devices")
    if not isinstance(saved, list):
        return []
    return [entry for entry in saved if isinstance(entry, dict)]


def _keep_saved_key_form(
    plain: Dict[str, Any], saved_entries: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Write *plain* with the key material its saved entry already has.

    Used when the credential store is unavailable: a key that is on disk in
    plaintext stays exactly as it was, but a key LumiSync has not saved
    before is never newly written in plaintext.
    """
    result = dict(plain)
    result.pop("local_key", None)
    identities = set(_device_keys(plain))
    saved = next(
        (
            entry
            for entry in saved_entries
            if identities & set(_device_keys(entry))
        ),
        None,
    )
    if saved is not None:
        saved_form = as_device(saved).to_dict()
        for field in ("local_key", "local_key_ref"):
            if saved_form.get(field):
                result[field] = saved_form[field]
    return result


def _note_key_store_unavailable(count: int) -> None:
    global _save_notice, _key_store_notice_given
    if _key_store_notice_given:
        logger.debug("Kept the saved key form for %d device(s)", count)
        return
    _key_store_notice_given = True
    logger.warning(
        "Secure credential storage is unavailable; kept the saved key form "
        "for %d device(s)",
        count,
    )
    _save_notice = (
        "Secure credential storage is unavailable, so local device keys were "
        "left as they were saved before. Enable your system's credential "
        "service to protect them."
    )


def _protect_for_disk(device_list: Any, path: Path) -> List[Dict[str, Any]]:
    """Return descriptors with plaintext local keys moved into the OS vault."""
    from .accounts.errors import AccountError
    from .accounts.secrets import protect_device

    saved_entries: Optional[List[Dict[str, Any]]] = None
    kept = 0
    result: List[Dict[str, Any]] = []
    for device in device_list or []:
        plain = as_device(device).to_dict()
        try:
            protected = protect_device(plain)
        except AccountError as exc:
            if getattr(exc, "code", None) != "credential_store":
                raise
            # One unprotectable legacy key must not make every save fail.
            if saved_entries is None:
                saved_entries = _saved_device_entries(path)
            protected = _keep_saved_key_form(plain, saved_entries)
            kept += 1
        result.append(protected)
    if kept:
        _note_key_store_unavailable(kept)
    return result


def writeJSON(settings: Dict[str, Any]) -> None:
    """Write settings to a JSON file, keeping the last good copy as ``.bak``.

    Plaintext Tuya local keys are moved into the OS credential vault; the
    input is not modified. When the credential store is unavailable a device
    keeps the key form it already has on disk instead of failing the write.
    """
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Writing settings to %s", path)

    protected = dict(settings)
    protected["devices"] = _protect_for_disk(settings.get("devices", []), path)
    backup = path.with_name(path.name + ".bak")
    _backup_good_settings(path)
    write_json(protected, str(path))
    if not backup.exists():
        # First save: keep a recovery copy from the very beginning.
        _backup_good_settings(path)

def update_settings(
    updates: Dict[str, Any],
    filename: str | os.PathLike[str] = "settings.json",
) -> Dict[str, Any]:
    """Merge *updates* into the saved settings and persist them atomically."""
    settings = _load_saved_settings(filename)
    settings.update(updates)
    writeJSON(settings)
    return settings


def get_data(*, refresh: bool = True) -> Dict[str, Any]:
    """Get device data from settings file or by requesting new data."""
    path = settings_path()
    if not refresh:
        return _load_saved_settings(path)

    logger.info("Attempting to load device data from %s", path)
    had_file = path.exists()
    data = _load_saved_settings(path)
    try:
        saved_at = float(data.get("time", 0) or 0)
    except (TypeError, ValueError):
        saved_at = 0.0

    # A missing (or quarantined) file with no recovered devices must trigger the
    # first discovery, exactly like the old FileNotFoundError path did.
    if (not had_file or not path.exists()) and not data.get("devices"):
        should_discover = True
    else:
        should_discover = time.time() - saved_at > 86400

    if should_discover:
        logger.info("Device data is older than 24 hours, requesting new data...")
        print("Device data is older than 24 hours, requesting new data...")
        try:
            return discover_lan_devices(preserve_existing=True)
        except Exception as e:
            logger.error(f"Discovery refresh failed: {str(e)}", exc_info=True)

    normalized = _normalize_settings(data)
    logger.info(
        "Loaded data with %s device(s) from %s",
        len(normalized["devices"]),
        path,
    )
    return normalized
