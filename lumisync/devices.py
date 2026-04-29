"""
Device handling for LumiSync.
This module handles device discovery and management.
"""

import copy
import json
import socket
import time
from typing import Dict, List, Any

from colorama import Fore

from .connection import connect, listen as connection_listen, parse
from .config.options import CONNECTION
from .utils import write_json, get_logger

# Set up logger for devices module
logger = get_logger('lumisync_devices')

# Store a global server socket that can be reused
_global_server = None


def _empty_settings() -> Dict[str, Any]:
    return {"devices": [], "selectedDevice": 0, "time": time.time()}


def _load_saved_settings(filename: str = "settings.json") -> Dict[str, Any]:
    """Load persisted devices without triggering a network discovery."""
    try:
        with open(filename, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return _empty_settings()

    if not isinstance(data, dict):
        return _empty_settings()

    devices = data.get("devices")
    if not isinstance(devices, list):
        devices = []

    try:
        selected = int(data.get("selectedDevice", 0))
    except (TypeError, ValueError):
        selected = 0

    copied = dict(data)
    copied["devices"] = [dict(device) for device in devices if isinstance(device, dict)]
    copied["selectedDevice"] = selected
    copied["time"] = float(data.get("time", 0) or 0)
    return copied


def _device_keys(device: Dict[str, Any]) -> List[str]:
    """Return stable match keys, preferring MAC over weaker IP/model fallback."""
    keys: List[str] = []
    mac = device.get("mac")
    if mac:
        keys.append(f"mac:{str(mac).lower()}")

    ip = device.get("ip")
    model = device.get("model")
    if ip and model:
        keys.append(f"ip-model:{str(ip).lower()}|{str(model).lower()}")
    elif ip:
        keys.append(f"ip:{str(ip).lower()}")

    return keys


def _selected_index(settings: Dict[str, Any], device_count: int) -> int:
    try:
        selected = int(settings.get("selectedDevice", 0))
    except (TypeError, ValueError):
        selected = 0
    if device_count <= 0:
        return 0
    return max(0, min(selected, device_count - 1))


def merge_discovered_devices(
    existing_devices: List[Dict[str, Any]],
    discovered_devices: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge LAN scan results into saved devices without dropping stale ones."""
    merged = [dict(device) for device in existing_devices]
    index_by_key: Dict[str, int] = {}

    for index, device in enumerate(merged):
        for key in _device_keys(device):
            index_by_key.setdefault(key, index)

    for discovered in discovered_devices:
        copied = dict(discovered)
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
            for key, value in copied.items():
                if value is not None:
                    existing[key] = value

        for key in _device_keys(merged[matching_index]):
            index_by_key.setdefault(key, matching_index)

    return merged


def discover_lan_devices(preserve_existing: bool = True) -> Dict[str, Any]:
    """Discover LAN devices once, merge with saved devices, and persist safely."""
    saved_settings = _load_saved_settings() if preserve_existing else _empty_settings()
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

    merged_devices = merge_discovered_devices(
        [dict(device) for device in existing_devices if isinstance(device, dict)],
        discovered_devices,
    )
    settings = copy.deepcopy(saved_settings)
    settings["devices"] = merged_devices
    settings["selectedDevice"] = _selected_index(settings, len(merged_devices))
    settings["time"] = time.time()
    settings["lastDiscoveryCount"] = len(discovered_devices)

    CONNECTION.devices = [dict(device) for device in merged_devices]

    logger.info(
        "Discovery found %s responding device(s); keeping %s total device(s)",
        len(discovered_devices),
        len(merged_devices),
    )
    writeJSON(settings)
    return settings

def request() -> socket.socket:
    """Request device data from the network."""
    global _global_server

    # If we have an existing server, close it properly first
    if _global_server is not None:
        try:
            _global_server.close()
        except:
            pass

    logger.info("Requesting device data from network")
    server, _ = connect()
    _global_server = server
    return server

def listen(server: socket.socket) -> List[str]:
    """Listen for device responses."""
    try:
        logger.info("Listening for device responses")
        messages = connection_listen(server)
        logger.info(f"Received {len(messages)} device response(s)")
        return messages
    except Exception as e:
        error_msg = f"Error listening for devices: {str(e)}"
        print(f"{Fore.RED}{error_msg}")
        logger.error(error_msg, exc_info=True)
        return []

def parseMessages(messages: List[str]) -> Dict[str, Any]:
    """Parse messages from devices."""
    logger.info(f"Parsing {len(messages)} device message(s)")
    devices = parse(messages)

    settings = {
        "devices": devices,
        "selectedDevice": 0,
        "time": time.time()
    }

    logger.info(f"Found {len(devices)} device(s)")
    return settings

def writeJSON(settings: Dict[str, Any]) -> None:
    """Write settings to a JSON file."""
    logger.info("Writing settings to JSON file")
    write_json(settings)

def get_data() -> Dict[str, Any]:
    """Get device data from settings file or by requesting new data."""
    try:
        logger.info("Attempting to load device data from settings.json")
        with open("settings.json", "r") as f:
            data = json.load(f)

        if time.time() - data.get("time", 0) > 86400:
            logger.info("Device data is older than 24 hours, requesting new data...")
            print("Device data is older than 24 hours, requesting new data...")
            try:
                return discover_lan_devices(preserve_existing=True)
            except Exception as e:
                logger.error(f"Discovery refresh failed: {str(e)}", exc_info=True)
                return data
        logger.info(f"Loaded data with {len(data.get('devices', []))} device(s) from settings.json")
        return data

    except (FileNotFoundError, json.JSONDecodeError) as e:
        error_msg = f"Settings.json not found or invalid, requesting new data... ({str(e)})"
        print(error_msg)
        logger.info(error_msg)
        try:
            return discover_lan_devices(preserve_existing=False)
        except Exception as exc:
            logger.error(f"Initial discovery failed: {str(exc)}", exc_info=True)
            return _empty_settings()
    except Exception as e:
        error_msg = f"Error getting device data: {str(e)}"
        print(f"{Fore.RED}{error_msg}")
        logger.error(error_msg, exc_info=True)
        # Return empty data as fallback
        return {"devices": [], "selectedDevice": 0, "time": time.time()}
