"""
Utility functions for the LumiSync application.
This package provides various utilities for color handling, file operations, and logging.
"""

# Re-export logging functions
from .logging import setup_logger, get_logger

# Re-export color utilities
from .colors import (
    clamp_channel,
    convert_colors,
    fit_colors_to_count,
    get_color,
    lerp,
    normalize_rgb,
)

# Re-export file operation utilities
from .file_operations import write_json, writeJSON

__all__ = [
    # Logging
    "setup_logger",
    "get_logger",

    # Colors
    "clamp_channel",
    "convert_colors",
    "fit_colors_to_count",
    "lerp",
    "get_color",
    "normalize_rgb",

    # File operations
    "write_json",
    "writeJSON"
]
