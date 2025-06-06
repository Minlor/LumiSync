"""
Logging module for LumiSync.
This module provides logging functionality for the application.
"""

import os
import logging
import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler

# Dictionary to store loggers so we don't create duplicates
_loggers = {}

def get_logs_directory():
    """Get or create the logs directory."""
    # Get user's home directory
    home_dir = str(Path.home())

    # Create logs directory path
    logs_dir = os.path.join(home_dir, ".lumisync", "logs")

    # Create the directory if it doesn't exist
    os.makedirs(logs_dir, exist_ok=True)

    return logs_dir

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
