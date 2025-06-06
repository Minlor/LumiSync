"""
File operation utilities for LumiSync.
This module provides functions for reading and writing configuration files.
"""

import json
import os
from typing import Any, Dict

from colorama import Fore

from .logging import get_logger

# Set up logger for file operations
logger = get_logger('lumisync_file_ops')

def write_json(settings: Dict[str, Any], filename: str = "settings.json") -> None:
    """Writes data to a JSON file.

    Parameters
    ----------
    settings : Dict[str, Any]
        The data to write to the file.
    filename : str, optional
        The name of the file to write to. Default is "settings.json".
    """
    try:
        with open(filename, "w") as f:
            json.dump(settings, f, indent=2)

        msg = f"Data written to {filename}"
        print(f"{Fore.LIGHTGREEN_EX}{msg}")
        logger.info(msg)
    except Exception as e:
        error_msg = f"Error writing to {filename}: {str(e)}"
        print(f"{Fore.RED}{error_msg}")
        logger.error(error_msg, exc_info=True)

def read_json(filename: str = "settings.json") -> Dict[str, Any]:
    """Reads data from a JSON file.

    Parameters
    ----------
    filename : str, optional
        The name of the file to read from. Default is "settings.json".

    Returns
    -------
    Dict[str, Any]
        The data read from the file, or an empty dict if the file doesn't exist.
    """
    try:
        with open(filename, "r") as f:
            data = json.load(f)
        logger.info(f"Successfully read data from {filename}")
        return data
    except FileNotFoundError:
        logger.warning(f"File not found: {filename}")
        return {}
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON in {filename}", exc_info=True)
        return {}
    except Exception as e:
        logger.error(f"Error reading {filename}: {str(e)}", exc_info=True)
        return {}

# For backwards compatibility
writeJSON = write_json
