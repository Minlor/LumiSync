"""
Resources module for the LumiSync GUI.
This module provides access to resource files like icons.
"""

import importlib.resources
import os
import sys
from pathlib import Path


def get_resource_path(resource_name):
    """
    Get the absolute path to a resource file.
    Works in development, installed package and frozen executable contexts.

    Args:
        resource_name (str): Name of the resource file

    Returns:
        str: Absolute path to the resource file
    """
    # Try different strategies to locate the resource

    # 1. Try using importlib.resources (works for installed packages)
    try:
        # First check if the file exists in the package resources
        try:
            with importlib.resources.path(
                "lumisync.gui.resources", resource_name
            ) as path:
                if path.exists():
                    return str(path)
        except (ImportError, ModuleNotFoundError):
            pass  # Fall through to other methods
    except Exception:
        pass  # Fall through to other methods

    # 2. Try PyInstaller's _MEIPASS path for frozen executables
    if getattr(sys, "frozen", False):
        frozen_path = os.path.join(sys._MEIPASS, "resources", resource_name)
        if os.path.exists(frozen_path):
            return frozen_path

    # 3. Try relative to this file (development mode)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    dev_path = os.path.join(current_dir, resource_name)
    if os.path.exists(dev_path):
        return dev_path

    # 4. If all else fails, look in some common directories
    common_paths = [
        # Current working directory resources folder
        os.path.join(os.getcwd(), "resources", resource_name),
        # One level up from current directory
        os.path.join(os.path.dirname(os.getcwd()), "resources", resource_name),
        # User's config directory
        os.path.join(str(Path.home()), ".lumisync", "resources", resource_name),
    ]

    for path in common_paths:
        if os.path.exists(path):
            return path

    # Return None if resource couldn't be found
    return None


def get_all_resources():
    """
    Get a list of all available resources.

    Returns:
        list: List of resource filenames
    """
    try:
        # Try to use the directory of this file
        current_dir = os.path.dirname(os.path.abspath(__file__))
        if os.path.exists(current_dir):
            return [
                f
                for f in os.listdir(current_dir)
                if os.path.isfile(os.path.join(current_dir, f))
                and f != "__init__.py"
                and not f.endswith(".pyc")
            ]
    except:
        # Return an empty list if we can't access the resources
        return []
