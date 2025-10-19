"""
Device handling for LumiSync.
This module handles device discovery and management.
"""

import json
import socket
import time
from typing import Dict, List, Any

from colorama import Fore

from .connection import connect, listen as connection_listen, parse
from .utils import write_json, get_logger
from .connection import switch

# Set up logger for devices module
logger = get_logger('lumisync_devices')

# Store a reusable server socket inside a module-level dict to avoid using
# the `global` statement which is flagged by linters.
_global_state = {"server": None}

def request() -> socket.socket:
    """Request device data from the network."""

    # If we have an existing server, close it properly first
    if _global_state["server"] is not None:
        try:
            _global_state["server"].close()
        except OSError:
            # Ignore socket close errors
            pass

    logger.info("Requesting device data from network")
    server, _ = connect()
    _global_state["server"] = server
    return server

def listen(server: socket.socket) -> List[str]:
    """Listen for device responses."""
    try:
        logger.info("Listening for device responses")
        messages = connection_listen(server)
        logger.info("Received %d device response(s)", len(messages))
        return messages
    except OSError as e:
        error_msg = "Error listening for devices: %s" % str(e)
        print(f"{Fore.RED}{error_msg}")
        logger.error(error_msg, exc_info=True)
        return []

def parse_messages(messages: List[str]) -> Dict[str, Any]:
    """Parse messages from devices."""
    logger.info("Parsing %d device message(s)", len(messages))
    devices_list = parse(messages)

    settings = {
        "devices": devices_list,
        "selectedDevice": 0,
        "time": time.time(),
    }

    logger.info("Found %d device(s)", len(devices_list))
    return settings

# Backwards-compatible alias for callers using the original CamelCase name
parseMessages = parse_messages

def write_json_file(settings: Dict[str, Any]) -> None:
    """Write settings to a JSON file."""
    logger.info("Writing settings to JSON file")
    write_json(settings)

# Preserve original name for compatibility
writeJSON = write_json_file

def get_data() -> Dict[str, Any]:
    """Get device data from settings file or by requesting new data."""
    try:
        logger.info("Attempting to load device data from settings.json")
        with open("settings.json", "r", encoding="utf-8") as f:
            data = json.load(f)

        if time.time() - data.get("time", 0) > 86400:
            logger.info("Device data is older than 24 hours, requesting new data...")
            print("Device data is older than 24 hours, requesting new data...")
            server = request()
            messages = listen(server)
            settings = parseMessages(messages)
            server.close()
            writeJSON(settings)
            return settings
        logger.info("Loaded data with %d device(s) from settings.json", len(data.get("devices", [])))
        return data

    except (FileNotFoundError, json.JSONDecodeError) as e:
        error_msg = "Settings.json not found or invalid, requesting new data... (%s)" % str(e)
        print(error_msg)
        logger.info(error_msg)
        server = request()
        messages = listen(server)
        settings = parseMessages(messages)
        server.close()
        writeJSON(settings)
        return settings
    except OSError as e:
        error_msg = "Error getting device data: %s" % str(e)
        print(f"{Fore.RED}{error_msg}")
        logger.error(error_msg, exc_info=True)
        # Return empty data as fallback
        return {"devices": [], "selectedDevice": 0, "time": time.time()}
    
def power_on(device: Dict[str, Any]) -> None:
    """Turn on the LED device (true power state)."""
    try:
        server, _ = connect()
        switch(server, device, on=True)
        print(Fore.GREEN + "Powered ON: %s" % device.get("mac"))
        server.close()
    except (OSError, socket.error) as e:
        print(Fore.RED + "Failed to power ON device: %s" % str(e))

def power_off(device: Dict[str, Any]) -> None:
    """Turn off the LED device (true power state)."""
    try:
        server, _ = connect()
        switch(server, device, on=False)
        print(Fore.YELLOW + "Powered OFF: %s" % device.get("mac"))
        server.close()
    except (OSError, socket.error) as e:
        print(Fore.RED + "Failed to power OFF device: %s" % str(e))
