"""
Utility functions for the LumiSync application.
This package provides various utilities for color handling, file operations, and logging.
"""

# Re-export logging functions
from .logging import setup_logger, get_logger

# Re-export color utilities
from .colors import lerp, get_color, convert_colors

# Re-export file operation utilities
from .file_operations import write_json, writeJSON

__all__ = [
    # Logging
    "setup_logger",
    "get_logger",

    # Colors
    "convert_colors",
    "lerp",
    "get_color",

    # File operations
    "write_json",
    "writeJSON"
]
