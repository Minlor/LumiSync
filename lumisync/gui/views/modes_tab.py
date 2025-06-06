"""
Modes tab for the LumiSync GUI.
This module contains the UI for synchronization modes (monitor sync and music sync).
"""

import tkinter as tk
import customtkinter as ctk
from typing import List, Dict, Any, Callable

from ..base import BaseFrame
from ..styles import MEDIUM_PAD, LARGE_PAD, MEDIUM_BUTTON, LARGE_BUTTON
from ...gui.controllers.sync_controller import SyncController


class ModesTab(BaseFrame):
    """Tab for synchronization modes."""
    
    def __init__(self, master, app, sync_controller=None):
        super().__init__(master)
        self.app = app
        # Use provided sync_controller if available, otherwise create a new one
        self.sync_controller = sync_controller if sync_controller else SyncController(status_callback=self.app.set_status)

        # Configure grid
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        
        # Create widgets
        self.create_header()
        self.create_sync_modes()
        self.create_sync_controls()
    
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
        
        # Configure grid
        modes_frame.grid_columnconfigure((0, 1), weight=1)
        
        # Monitor Sync
        monitor_frame = ctk.CTkFrame(modes_frame)
        monitor_frame.grid(row=0, column=0, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="nsew")
        
        monitor_label = ctk.CTkLabel(
            monitor_frame, 
            text="Monitor Sync", 
            font=("Segoe UI", 14, "bold")
        )
        monitor_label.pack(padx=MEDIUM_PAD, pady=MEDIUM_PAD)
        
        monitor_desc = ctk.CTkLabel(
            monitor_frame, 
            text="Synchronize your lights with your monitor content.",
            wraplength=250
        )
        monitor_desc.pack(padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD))
        
        monitor_button = ctk.CTkButton(
            monitor_frame,
            text="Start Monitor Sync",
            command=self.start_monitor_sync,
            width=LARGE_BUTTON[0],
            height=LARGE_BUTTON[1]
        )
        monitor_button.pack(padx=MEDIUM_PAD, pady=MEDIUM_PAD)
        
        # Music Sync
        music_frame = ctk.CTkFrame(modes_frame)
        music_frame.grid(row=0, column=1, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="nsew")
        
        music_label = ctk.CTkLabel(
            music_frame, 
            text="Music Sync", 
            font=("Segoe UI", 14, "bold")
        )
        music_label.pack(padx=MEDIUM_PAD, pady=MEDIUM_PAD)
        
        music_desc = ctk.CTkLabel(
            music_frame, 
            text="Synchronize your lights with your music and audio.",
            wraplength=250
        )
        music_desc.pack(padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD))
        
        music_button = ctk.CTkButton(
            music_frame,
            text="Start Music Sync",
            command=self.start_music_sync,
            width=LARGE_BUTTON[0],
            height=LARGE_BUTTON[1]
        )
        music_button.pack(padx=MEDIUM_PAD, pady=MEDIUM_PAD)
    
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
            hover_color="#C0392B"
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