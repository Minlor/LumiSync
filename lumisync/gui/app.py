"""
Main application for the LumiSync GUI.
This module contains the main application window and entry point for the GUI.
"""

import tkinter as tk
import customtkinter as ctk
import sys
import os

from .base import BaseApp
from .views.devices_tab import DevicesTab
from .views.modes_tab import ModesTab
from .controllers.device_controller import DeviceController
from .controllers.sync_controller import SyncController
from ..utils.logging import setup_logger, get_logger

# Set up logger for GUI application
logger = setup_logger('lumisync_gui')


class LumiSyncApp(BaseApp):
    """Main application class for LumiSync."""
    
    def __init__(self):
        logger.info("Initializing LumiSync GUI application")
        super().__init__()

        # Create controllers first so they can be properly linked
        self.device_controller = DeviceController(status_callback=self.set_status)
        self.sync_controller = SyncController(status_callback=self.set_status)

        # Link controllers together for device selection coordination
        self.device_controller.set_sync_controller(self.sync_controller)
        logger.info("Controllers initialized and linked")

        # Create a tabview for the main content
        self.tabview = ctk.CTkTabview(self.container)
        self.tabview.pack(fill=tk.BOTH, expand=True)

        # Add tabs
        self.tabview.add("Devices")
        self.tabview.add("Modes")
        logger.debug("Added tabs to main window")

        # Create tab contents, passing the controllers
        self.devices_tab = DevicesTab(self.tabview.tab("Devices"), self, self.device_controller)
        self.devices_tab.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.modes_tab = ModesTab(self.tabview.tab("Modes"), self, self.sync_controller)
        self.modes_tab.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        logger.debug("Tab contents created")

        # Set default tab
        self.tabview.set("Devices")

        # Set window icon if available
        self.set_icon()

        # Ensure bottom of app is visible initially
        self.after(500, self.ensure_status_bar_visible)

        logger.info("LumiSync GUI application initialized successfully")

    def ensure_status_bar_visible(self):
        """Ensure the status bar is visible."""
        current_height = self.winfo_height()
        min_height = self.status_bar.winfo_reqheight() + 100  # Add some margin

        if current_height < min_height:
            self.geometry(f"{self.winfo_width()}x{min_height}")
            logger.debug(f"Adjusted window size to ensure status bar visibility: {self.winfo_width()}x{min_height}")

        # Check again after a delay for resize events
        self.after(2000, self.ensure_status_bar_visible)

    def set_icon(self):
        """Set the application icon if available."""
        try:
            # Check if running as a bundled executable
            if getattr(sys, 'frozen', False):
                application_path = sys._MEIPASS
            else:
                application_path = os.path.dirname(os.path.abspath(__file__))
            
            # Path to icon file (you would need to add an icon file to the project)
            icon_path = os.path.join(application_path, "resources", "icon.ico")
            
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
                logger.debug(f"Set application icon from {icon_path}")
            else:
                logger.debug(f"Icon file not found at {icon_path}")
        except Exception as e:
            # If icon setting fails, just continue without an icon
            logger.warning(f"Failed to set application icon: {str(e)}")

    def set_status(self, message):
        """Set the status bar message and log it."""
        super().set_status(message)
        logger.info(f"Status: {message}")


def main():
    """Main entry point for the GUI application."""
    logger.info("Starting LumiSync GUI application")
    try:
        app = LumiSyncApp()
        app.run()
        logger.info("LumiSync GUI application closed normally")
    except Exception as e:
        logger.critical(f"Uncaught exception in GUI application: {str(e)}", exc_info=True)
        raise


if __name__ == "__main__":
    main()