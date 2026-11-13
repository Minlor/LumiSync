"""
File operation utilities for LumiSync.
This module provides functions for reading and writing configuration files.
"""

import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Dict

from .logging import get_logger

# Set up logger for file operations
logger = get_logger('lumisync_file_ops')

# Windows Defender, the search indexer and backup tools briefly open freshly
# written files without delete sharing; ``os.replace`` then fails with a
# PermissionError for a few milliseconds. Retry before giving up.
REPLACE_RETRIES = 5
REPLACE_RETRY_DELAY_S = 0.075


def replace_with_retry(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
    """``os.replace`` that tolerates a short-lived sharing violation."""
    for attempt in range(REPLACE_RETRIES + 1):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt >= REPLACE_RETRIES:
                raise
            time.sleep(REPLACE_RETRY_DELAY_S)


def write_json(settings: Dict[str, Any], filename: str = "settings.json") -> None:
    """Writes data to a JSON file.

    Parameters
    ----------
    settings : Dict[str, Any]
        The data to write to the file.
    filename : str, optional
        The name of the file to write to. Default is "settings.json".
    """
    temporary = None
    try:
        destination = Path(filename)
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        replace_with_retry(temporary, destination)
        temporary = None

        logger.info("Data written to %s", filename)
    except Exception as e:
        logger.error("Error writing to %s: %s", filename, e, exc_info=True)
        raise
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                # Never mask the original write error with a cleanup failure.
                logger.warning("Could not remove temporary file %s", temporary)

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
