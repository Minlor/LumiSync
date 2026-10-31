"""
Logging module for LumiSync.
This module provides logging functionality for the application.
"""

import os
import logging
import datetime
import sys
import tempfile
import time
from pathlib import Path
from logging.handlers import RotatingFileHandler

# Dictionary to store loggers so we don't create duplicates
_loggers = {}
_logs_directory = None
_pruned = False

_LOG_MAX_AGE_DAYS = 14
_LOG_MAX_FILES = 40
_LOG_MAX_BYTES = 50 * 1024 * 1024


def _running_under_tests() -> bool:
    if os.environ.get("LUMISYNC_TESTING"):
        return True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    executable = str(sys.argv[0] if sys.argv else "").lower()
    return any(token in executable for token in ("pytest", "py.test", "unittest"))


def get_logs_directory():
    """Get or create the logs directory."""
    global _logs_directory
    if _logs_directory is not None:
        return _logs_directory

    override = os.environ.get("LUMISYNC_LOG_DIR")
    if override:
        logs_dir = Path(override)
    elif _running_under_tests():
        # Keep test runs out of the developer's real ~/.lumisync/logs folder.
        logs_dir = (
            Path(tempfile.gettempdir())
            / "lumisync-tests"
            / f"pid-{os.getpid()}"
        )
    else:
        logs_dir = Path.home() / ".lumisync" / "logs"

    logs_dir.mkdir(parents=True, exist_ok=True)
    _logs_directory = str(logs_dir)
    return _logs_directory


def _prune_logs(logs_dir: str) -> None:
    """Keep the log directory bounded without touching unrelated files."""
    global _pruned
    if _pruned:
        return
    _pruned = True
    directory = Path(logs_dir)
    try:
        files = [
            path
            for path in directory.iterdir()
            if path.is_file()
            and (
                path.name.startswith("lumisync")
                or path.name.startswith("settings")
            )
            and (path.suffix == ".log" or ".log." in path.name)
        ]
    except OSError:
        return

    cutoff = time.time() - _LOG_MAX_AGE_DAYS * 86400
    kept = []
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < cutoff:
            try:
                path.unlink()
                continue
            except OSError:
                pass
        kept.append((stat.st_mtime, stat.st_size, path))

    kept.sort(reverse=True)
    total = 0
    for index, (_mtime, size, path) in enumerate(kept):
        total += size
        if index < _LOG_MAX_FILES and total <= _LOG_MAX_BYTES:
            continue
        try:
            path.unlink()
        except OSError:
            pass

def setup_logger(name, level=logging.INFO, max_size=10485760, backup_count=5):
    """
    Set up and configure a logger.

    Args:
        name: Name of the logger
        level: Logging level (default: INFO)
        max_size: Maximum size of log file before rotation in bytes (default: 10MB)
        backup_count: Number of backup files to keep (default: 5)

    Returns:
        Logger: Configured logger
    """
    # If logger with this name already exists, return it
    if name in _loggers:
        return _loggers[name]

    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Avoid adding handlers if they already exist
    if logger.handlers:
        return logger

    # Get logs directory
    logs_dir = get_logs_directory()
    _prune_logs(logs_dir)

    # Create a formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Create console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Create rotating file handler
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    log_file = os.path.join(logs_dir, f"{name}_{today}.log")

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_size,
        backupCount=backup_count
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Store logger in dictionary for future use
    _loggers[name] = logger

    return logger

def get_logger(name):
    """
    Get an existing logger or create a new one.

    Args:
        name: Name of the logger

    Returns:
        Logger: The requested logger
    """
    if name in _loggers:
        return _loggers[name]

    # If logger doesn't exist, create a new one
    return setup_logger(name)
