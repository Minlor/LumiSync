"""
Modes tab for the LumiSync GUI.
This module contains the UI for synchronization modes (monitor sync and music sync).
"""

import tkinter as tk
import customtkinter as ctk
from typing import List, Dict, Any, Callable
import os
import sys
from PIL import Image

from ..base import BaseFrame
from ..styles import MEDIUM_PAD, LARGE_PAD, MEDIUM_BUTTON, LARGE_BUTTON
from ...gui.controllers.sync_controller import SyncController
from ...gui.resources import get_resource_path
from ...config.options import BRIGHTNESS


class ModesTab(BaseFrame):
    """Tab for synchronization modes."""
    
    def __init__(self, master, app, sync_controller=None):
        super().__init__(master)
        self.app = app
        # Use provided sync_controller if available, otherwise create a new one
        self.sync_controller = sync_controller if sync_controller else SyncController(status_callback=self.app.set_status)

        # Load icons
        self.load_icons()

        # Configure grid for responsive layout
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        
        # Create widgets
        self.create_header()
        self.create_sync_modes()
        self.create_sync_controls()

        # Bind configure event to handle window resize
        self.bind("<Configure>", self.on_resize)

    def on_resize(self, event):
        """Handle window resize events to adjust layout."""
        # Only handle events from this widget, not children
        if event.widget == self:
            # Update any width-dependent widgets
            self.update_wraplengths()
            # Update height-based layouts
            self.adjust_height_distribution()

    def update_wraplengths(self):
        """Update text wrapping based on current window width."""
        # Get current width and adjust wraplengths dynamically
        width = self.winfo_width()
        if width > 10:  # Avoid division by zero or negative values
            # Calculate appropriate wraplength based on window width
            panel_width = max(200, (width - 60) // 2)  # Account for padding

            # Update any labels with wraplength
            if hasattr(self, "monitor_desc"):
                self.monitor_desc.configure(wraplength=panel_width)
            if hasattr(self, "music_desc"):
                self.music_desc.configure(wraplength=panel_width)

    def adjust_height_distribution(self):
        """Adjust height distribution based on window height."""
        height = self.winfo_height()
        if height > 10:  # Avoid division by zero or negative values
            # Set dynamic row weights based on available height
            # Give more space to controls section when window is taller
            if height > 600:
                self.grid_rowconfigure(2, weight=3)  # More space for controls
                self.grid_rowconfigure(0, weight=1)  # Header
                self.grid_rowconfigure(1, weight=2)  # Sync modes
            elif height > 400:
                self.grid_rowconfigure(2, weight=2)  # Controls
                self.grid_rowconfigure(0, weight=1)  # Header
                self.grid_rowconfigure(1, weight=2)  # Sync modes
            else:
                # For smaller heights, distribute more evenly
                self.grid_rowconfigure(0, weight=1)  # Header
                self.grid_rowconfigure(1, weight=1)  # Sync modes
                self.grid_rowconfigure(2, weight=1)  # Controls

    def load_icons(self):
        """Load icon images for buttons."""
        try:
            # Load the new icons with updated filenames
            music_icon_path = get_resource_path("music.png")
            play_icon_path = get_resource_path("play.png")
            stop_icon_path = get_resource_path("stop.png")
            screen_icon_path = get_resource_path("screen.png")
            settings_icon_path = get_resource_path("settings.png")

            # Music icon for music sync
            if music_icon_path and os.path.exists(music_icon_path):
                self.music_icon = ctk.CTkImage(
                    light_image=Image.open(music_icon_path),
                    dark_image=Image.open(music_icon_path),
                    size=(24, 24)
                )
                self.app.set_status(f"Loaded music icon")
            else:
                self.music_icon = None

            # Play icon for starting sync
            if play_icon_path and os.path.exists(play_icon_path):
                self.play_icon = ctk.CTkImage(
                    light_image=Image.open(play_icon_path),
                    dark_image=Image.open(play_icon_path),
                    size=(24, 24)
                )
                self.app.set_status(f"Loaded play icon")
            else:
                self.play_icon = None

            # Stop icon for stopping sync
            if stop_icon_path and os.path.exists(stop_icon_path):
                self.stop_icon = ctk.CTkImage(
                    light_image=Image.open(stop_icon_path),
                    dark_image=Image.open(stop_icon_path),
                    size=(20, 20)
                )
                self.app.set_status(f"Loaded stop icon")
            else:
                self.stop_icon = None

            # Settings icon for settings
            if settings_icon_path and os.path.exists(settings_icon_path):
                self.settings_icon = ctk.CTkImage(
                    light_image=Image.open(settings_icon_path),
                    dark_image=Image.open(settings_icon_path),
                    size=(20, 20)
                )
                self.app.set_status(f"Loaded settings icon")
            else:
                self.settings_icon = None

            # Screen icon for monitor sync (renamed from sitemap to screen)
            if screen_icon_path and os.path.exists(screen_icon_path):
                self.monitor_icon = ctk.CTkImage(
                    light_image=Image.open(screen_icon_path),
                    dark_image=Image.open(screen_icon_path),
                    size=(24, 24)
                )
                self.app.set_status(f"Loaded screen icon")
            else:
                self.monitor_icon = None

        except Exception as e:
            self.app.set_status(f"Failed to load icons: {str(e)}")
            self.music_icon = None
            self.play_icon = None
            self.stop_icon = None
            self.monitor_icon = None
            self.settings_icon = None

    def create_header(self):
        """Create the header section."""
        header_frame = ctk.CTkFrame(self)
        header_frame.grid(row=0, column=0, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="ew")
        
        header_label = ctk.CTkLabel(
            header_frame, 
            text="Synchronization Modes", 
            font=("Segoe UI", 16, "bold")
        )
        header_label.pack(padx=MEDIUM_PAD, pady=MEDIUM_PAD)
    
    def create_sync_modes(self):
        """Create the synchronization modes section."""
        modes_frame = ctk.CTkFrame(self)
        modes_frame.grid(row=1, column=0, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="ew")
        
        # Configure grid for equal columns with responsive width
        modes_frame.grid_columnconfigure((0, 1), weight=1, uniform="column")

        # Monitor Sync
        monitor_frame = ctk.CTkFrame(modes_frame)
        monitor_frame.grid(row=0, column=0, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="nsew")
        
        # Configure monitor frame grid
        monitor_frame.grid_columnconfigure(0, weight=1)

        monitor_label = ctk.CTkLabel(
            monitor_frame, 
            text="Monitor Sync", 
            font=("Segoe UI", 14, "bold")
        )
        monitor_label.grid(row=0, column=0, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="ew")

        self.monitor_desc = ctk.CTkLabel(
            monitor_frame,
            text="Synchronize your lights with your monitor content.",
            wraplength=250
        )
        self.monitor_desc.grid(row=1, column=0, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="ew")

        # Add brightness slider for monitor sync
        brightness_frame = ctk.CTkFrame(monitor_frame)
        brightness_frame.grid(row=2, column=0, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="ew")
        brightness_frame.grid_columnconfigure(1, weight=1)

        brightness_label = ctk.CTkLabel(brightness_frame, text="Brightness:")
        brightness_label.grid(row=0, column=0, padx=(MEDIUM_PAD, 0), sticky="w")

        # Initialize slider with current brightness value
        self.monitor_brightness_slider = ctk.CTkSlider(
            brightness_frame,
            from_=0.1,
            to=1.0,
            number_of_steps=90,
            command=self.update_monitor_brightness
        )
        self.monitor_brightness_slider.set(self.sync_controller.get_monitor_brightness())
        self.monitor_brightness_slider.grid(row=0, column=1, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="ew")

        # Add brightness percentage label
        self.monitor_brightness_value = ctk.CTkLabel(
            brightness_frame,
            text=f"{int(self.sync_controller.get_monitor_brightness() * 100)}%",
            width=40
        )
        self.monitor_brightness_value.grid(row=0, column=2, padx=(0, MEDIUM_PAD), sticky="e")

        monitor_button = ctk.CTkButton(
            monitor_frame,
            text="Start Monitor Sync",
            command=self.start_monitor_sync,
            width=LARGE_BUTTON[0],
            height=LARGE_BUTTON[1],
            image=self.monitor_icon if hasattr(self, 'monitor_icon') and self.monitor_icon else None,
            compound="left"
        )
        monitor_button.grid(row=3, column=0, padx=MEDIUM_PAD, pady=MEDIUM_PAD)

        # Music Sync
        music_frame = ctk.CTkFrame(modes_frame)
        music_frame.grid(row=0, column=1, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="nsew")
        
        # Configure music frame grid
        music_frame.grid_columnconfigure(0, weight=1)

        music_label = ctk.CTkLabel(
            music_frame, 
            text="Music Sync", 
            font=("Segoe UI", 14, "bold")
        )
        music_label.grid(row=0, column=0, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="ew")

        self.music_desc = ctk.CTkLabel(
            music_frame,
            text="Synchronize your lights with your music and audio.",
            wraplength=250
        )
        self.music_desc.grid(row=1, column=0, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="ew")

        # Add brightness slider for music sync
        music_brightness_frame = ctk.CTkFrame(music_frame)
        music_brightness_frame.grid(row=2, column=0, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="ew")
        music_brightness_frame.grid_columnconfigure(1, weight=1)

        music_brightness_label = ctk.CTkLabel(music_brightness_frame, text="Brightness:")
        music_brightness_label.grid(row=0, column=0, padx=(MEDIUM_PAD, 0), sticky="w")

        # Initialize slider with current brightness value
        self.music_brightness_slider = ctk.CTkSlider(
            music_brightness_frame,
            from_=0.1,
            to=1.0,
            number_of_steps=90,
            command=self.update_music_brightness
        )
        self.music_brightness_slider.set(self.sync_controller.get_music_brightness())
        self.music_brightness_slider.grid(row=0, column=1, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="ew")

        # Add brightness percentage label
        self.music_brightness_value = ctk.CTkLabel(
            music_brightness_frame,
            text=f"{int(self.sync_controller.get_music_brightness() * 100)}%",
            width=40
        )
        self.music_brightness_value.grid(row=0, column=2, padx=(0, MEDIUM_PAD), sticky="e")

        music_button = ctk.CTkButton(
            music_frame,
            text="Start Music Sync",
            command=self.start_music_sync,
            width=LARGE_BUTTON[0],
            height=LARGE_BUTTON[1],
            image=self.music_icon if hasattr(self, 'music_icon') and self.music_icon else None,
            compound="left"
        )
        music_button.grid(row=3, column=0, padx=MEDIUM_PAD, pady=MEDIUM_PAD)

    def update_monitor_brightness(self, value):
        """Update monitor brightness setting."""
        # Update the controller (without status callback)
        self.sync_controller.set_monitor_brightness(value)

        # Directly update the label ourselves
        self.monitor_brightness_value.configure(text=f"{int(value * 100)}%")

    def update_music_brightness(self, value):
        """Update music brightness setting."""
        # Update the controller (without status callback)
        self.sync_controller.set_music_brightness(value)

        # Directly update the label ourselves
        self.music_brightness_value.configure(text=f"{int(value * 100)}%")

    def create_sync_controls(self):
        """Create the synchronization controls section."""
        controls_frame = ctk.CTkFrame(self)
        controls_frame.grid(row=2, column=0, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="nsew")
        
        # Configure grid
        controls_frame.grid_columnconfigure(0, weight=1)
        controls_frame.grid_rowconfigure(1, weight=1)
        
        # Header
        controls_label = ctk.CTkLabel(
            controls_frame, 
            text="Sync Controls", 
            font=("Segoe UI", 12, "bold")
        )
        controls_label.grid(row=0, column=0, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="w")
        
        # Status and controls
        status_frame = ctk.CTkFrame(controls_frame)
        status_frame.grid(row=1, column=0, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="nsew")
        
        # Configure grid
        status_frame.grid_columnconfigure(1, weight=1)
        
        # Current mode
        mode_label = ctk.CTkLabel(status_frame, text="Current Mode:")
        mode_label.grid(row=0, column=0, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="w")
        
        self.mode_value = ctk.CTkLabel(status_frame, text="None")
        self.mode_value.grid(row=0, column=1, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="w")
        
        # Status
        status_label = ctk.CTkLabel(status_frame, text="Status:")
        status_label.grid(row=1, column=0, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="w")
        
        self.status_value = ctk.CTkLabel(status_frame, text="Idle")
        self.status_value.grid(row=1, column=1, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="w")
        
        # Stop button
        stop_button = ctk.CTkButton(
            status_frame,
            text="Stop Sync",
            command=self.stop_sync,
            width=MEDIUM_BUTTON[0],
            height=MEDIUM_BUTTON[1],
            fg_color="#E74C3C",
            hover_color="#C0392B",
            image=self.stop_icon if hasattr(self, 'stop_icon') and self.stop_icon else None,
            compound="left"
        )
        stop_button.grid(row=2, column=0, columnspan=2, padx=MEDIUM_PAD, pady=MEDIUM_PAD)

        # Update status periodically
        self.update_status()
    
    def start_monitor_sync(self):
        """Start monitor synchronization."""
        self.sync_controller.start_monitor_sync()
        self.update_status()
    
    def start_music_sync(self):
        """Start music synchronization."""
        self.sync_controller.start_music_sync()
        self.update_status()
    
    def stop_sync(self):
        """Stop synchronization."""
        self.sync_controller.stop_sync()
        self.update_status()
    
    def update_status(self):
        """Update the status display."""
        current_mode = self.sync_controller.get_current_sync_mode()
        is_syncing = self.sync_controller.is_syncing()
        
        if current_mode:
            self.mode_value.configure(text=current_mode.capitalize())
            self.status_value.configure(text="Active")
        else:
            self.mode_value.configure(text="None")
            self.status_value.configure(text="Idle")
        
        # Schedule the next update
        self.after(1000, self.update_status)