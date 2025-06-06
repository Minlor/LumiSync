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

# Set up logger for devices module
logger = get_logger('lumisync_devices')

# Store a global server socket that can be reused
_global_server = None

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
            server = request()
            messages = listen(server)
            settings = parseMessages(messages)
            server.close()
            writeJSON(settings)
            return settings
        logger.info(f"Loaded data with {len(data.get('devices', []))} device(s) from settings.json")
        return data

    except (FileNotFoundError, json.JSONDecodeError) as e:
        error_msg = f"Settings.json not found or invalid, requesting new data... ({str(e)})"
        print(error_msg)
        logger.info(error_msg)
        server = request()
        messages = listen(server)
        settings = parseMessages(messages)
        server.close()
        writeJSON(settings)
        return settings
    except Exception as e:
        error_msg = f"Error getting device data: {str(e)}"
        print(f"{Fore.RED}{error_msg}")
        logger.error(error_msg, exc_info=True)
        # Return empty data as fallback
        return {"devices": [], "selectedDevice": 0, "time": time.time()}
