"""
Resource management for LumiSync GUI.
Handles loading of icons and other resources for PyQt6.
"""

import os
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtCore import QSize, Qt


class ResourceManager:
    """Manages loading of application resources (icons, images, etc.)."""

    _resource_dir: Optional[Path] = None

    @classmethod
    def get_resource_dir(cls) -> Path:
        """Get the resources directory path.

        Supports multiple deployment contexts:
        - Development mode
        - Installed package
        - Frozen executable (PyInstaller)
        """
        if cls._resource_dir is not None:
            return cls._resource_dir

        # Check if running as frozen executable
        if getattr(sys, 'frozen', False):
            # PyInstaller sets sys._MEIPASS
            if hasattr(sys, '_MEIPASS'):
                base_path = Path(sys._MEIPASS) / 'lumisync' / 'gui' / 'resources'
            else:
                base_path = Path(sys.executable).parent / 'lumisync' / 'gui' / 'resources'
        else:
            # Development or installed package
            base_path = Path(__file__).parent

        cls._resource_dir = base_path
        return cls._resource_dir

    @classmethod
    def get_icon_path(cls, name: str) -> Optional[Path]:
        """Get the full path to an icon file.

        Args:
            name: Icon filename (e.g., 'refresh.png')

        Returns:
            Path to the icon file if it exists, None otherwise
        """
        icon_path = cls.get_resource_dir() / 'icons' / name
        if icon_path.exists():
            return icon_path

        # Try without icons subdirectory (fallback)
        fallback_path = cls.get_resource_dir() / name
        if fallback_path.exists():
            return fallback_path

        return None

    @classmethod
    def get_icon(cls, name: str, size: Optional[QSize] = None) -> QIcon:
        """Load an icon from the resources directory.

        Args:
            name: Icon filename (e.g., 'refresh.png')
            size: Optional QSize for the icon

        Returns:
            QIcon object, or empty QIcon if file not found
        """
        icon_path = cls.get_icon_path(name)
        if icon_path is None:
            return QIcon()  # Return empty icon

        icon = QIcon(str(icon_path))
        return icon

    @classmethod
    def get_pixmap(cls, name: str, width: Optional[int] = None,
                   height: Optional[int] = None,
                   smooth: bool = True) -> QPixmap:
        """Load a pixmap from the resources directory.

        Args:
            name: Image filename (e.g., 'lightbulb-on.png')
            width: Optional width for scaling
            height: Optional height for scaling
            smooth: Use smooth transformation when scaling

        Returns:
            QPixmap object, or null pixmap if file not found
        """
        icon_path = cls.get_icon_path(name)
        if icon_path is None:
            return QPixmap()  # Return null pixmap

        pixmap = QPixmap(str(icon_path))

        if width and height:
            transform_mode = Qt.TransformationMode.SmoothTransformation if smooth else Qt.TransformationMode.FastTransformation
            pixmap = pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio, transform_mode)

        return pixmap

    @classmethod
    def get_all_icons(cls) -> list[str]:
        """Get a list of all available icon files.

        Returns:
            List of icon filenames
        """
        icons_dir = cls.get_resource_dir() / 'icons'
        if not icons_dir.exists():
            return []

        return [f.name for f in icons_dir.glob('*.png')]


def get_resource_path(filename: str) -> Optional[str]:
    """Get the path to a resource file (compatibility function).

    Args:
        filename: Name of the resource file

    Returns:
        String path to the resource, or None if not found
    """
    path = ResourceManager.get_icon_path(filename)
    return str(path) if path else None


def get_all_resources() -> list[str]:
    """Get all available resource files (compatibility function).

    Returns:
        List of resource filenames
    """
    return ResourceManager.get_all_icons()


__all__ = ['ResourceManager', 'get_resource_path', 'get_all_resources']
