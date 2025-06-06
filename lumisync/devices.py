"""
Device handling for LumiSync.
This module handles device discovery and management.
"""

import json
import socket
import sys
import time
from typing import Dict, List, Any

from colorama import Fore

from .connection import connect, listen as connection_listen, parse
from .utils import writeJSON as write_json_util

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

    server, _ = connect()
    _global_server = server
    return server

def listen(server: socket.socket) -> List[str]:
    """Listen for device responses."""
    try:
        return connection_listen(server)
    except Exception as e:
        print(f"{Fore.RED}Error listening for devices: {str(e)}")
        return []

def parseMessages(messages: List[str]) -> Dict[str, Any]:
    """Parse messages from devices."""
    devices = parse(messages)

    settings = {
        "devices": devices,
        "selectedDevice": 0,
        "time": time.time()
    }

    return settings

def writeJSON(settings: Dict[str, Any]) -> None:
    """Write settings to a JSON file."""
    write_json_util(settings)

def get_data() -> Dict[str, Any]:
    """Get device data from settings file or by requesting new data."""
    try:
        with open("settings.json", "r") as f:
            data = json.load(f)

        if time.time() - data.get("time", 0) > 86400:
            print("Device data is older than 24 hours, requesting new data...")
            server = request()
            messages = listen(server)
            settings = parseMessages(messages)
            server.close()
            writeJSON(settings)
            return settings
        return data

    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Settings.json not found or invalid, requesting new data... ({str(e)})")
        server = request()
        messages = listen(server)
        settings = parseMessages(messages)
        server.close()
        writeJSON(settings)
        return settings
    except Exception as e:
        print(f"{Fore.RED}Error getting device data: {str(e)}")
        # Return empty data as fallback
        return {"devices": [], "selectedDevice": 0, "time": time.time()}
