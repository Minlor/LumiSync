"""
Devices tab for the LumiSync GUI.
This module contains the UI for device discovery and management.
"""

import tkinter as tk
import customtkinter as ctk
from typing import List, Dict, Any, Callable

from ..base import BaseFrame
from ..styles import MEDIUM_PAD, LARGE_PAD, MEDIUM_BUTTON
from ...gui.controllers.device_controller import DeviceController


class DevicesTab(BaseFrame):
    """Tab for device discovery and management."""
    
    def __init__(self, master, app, device_controller=None):
        super().__init__(master)
        self.app = app
        # Use provided device_controller if available, otherwise create a new one
        self.device_controller = device_controller if device_controller else DeviceController(status_callback=self.app.set_status)

        # Configure grid
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # Create widgets
        self.create_header()
        self.create_actions()
        self.create_device_list()
        self.create_device_details()

        # Load devices
        self.load_devices()

    def create_header(self):
        """Create the header section."""
        header_frame = ctk.CTkFrame(self)
        header_frame.grid(row=0, column=0, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="ew")
        
        header_label = ctk.CTkLabel(
            header_frame, 
            text="Device Management", 
            font=("Segoe UI", 16, "bold")
        )
        header_label.pack(padx=MEDIUM_PAD, pady=MEDIUM_PAD)

    def create_actions(self):
        """Create the actions section."""
        actions_frame = ctk.CTkFrame(self)
        actions_frame.grid(row=1, column=0, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="ew")
        
        # Configure grid
        actions_frame.grid_columnconfigure((0, 1, 2), weight=1)
        
        # Create buttons
        discover_button = ctk.CTkButton(
            actions_frame,
            text="Discover Devices",
            command=self.discover_devices,
            width=MEDIUM_BUTTON[0],
            height=MEDIUM_BUTTON[1]
        )
        discover_button.grid(row=0, column=0, padx=MEDIUM_PAD, pady=MEDIUM_PAD)

        turn_on_button = ctk.CTkButton(
            actions_frame,
            text="Turn On",
            command=lambda: self.device_controller.turn_on_off(True),
            width=MEDIUM_BUTTON[0],
            height=MEDIUM_BUTTON[1]
        )
        turn_on_button.grid(row=0, column=1, padx=MEDIUM_PAD, pady=MEDIUM_PAD)
        
        turn_off_button = ctk.CTkButton(
            actions_frame,
            text="Turn Off",
            command=lambda: self.device_controller.turn_on_off(False),
            width=MEDIUM_BUTTON[0],
            height=MEDIUM_BUTTON[1]
        )
        turn_off_button.grid(row=0, column=2, padx=MEDIUM_PAD, pady=MEDIUM_PAD)
    
    def create_device_list(self):
        """Create the device list section."""
        list_frame = ctk.CTkFrame(self)
        list_frame.grid(row=2, column=0, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="ew")
        
        # Header
        list_label = ctk.CTkLabel(
            list_frame, 
            text="Available Devices", 
            font=("Segoe UI", 12, "bold")
        )
        list_label.pack(padx=MEDIUM_PAD, pady=MEDIUM_PAD, anchor="w")
        
        # Device list (using a Listbox with a scrollbar)
        self.device_listbox = tk.Listbox(
            list_frame,
            height=5,
            selectmode=tk.SINGLE,
            exportselection=0,
            bg="#2b2b2b",
            fg="white",
            font=("Segoe UI", 10)
        )
        self.device_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD))
        
        scrollbar = tk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.device_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=(0, MEDIUM_PAD))
        
        self.device_listbox.config(yscrollcommand=scrollbar.set)
        self.device_listbox.bind("<<ListboxSelect>>", self.on_device_select)
    
    def create_device_details(self):
        """Create the device details section."""
        details_frame = ctk.CTkFrame(self)
        details_frame.grid(row=3, column=0, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="nsew")
        
        # Configure grid
        details_frame.grid_columnconfigure(1, weight=1)
        
        # Header
        details_label = ctk.CTkLabel(
            details_frame, 
            text="Device Details", 
            font=("Segoe UI", 12, "bold")
        )
        details_label.grid(row=0, column=0, columnspan=2, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="w")
        
        # Device details
        model_label = ctk.CTkLabel(details_frame, text="Model:")
        model_label.grid(row=1, column=0, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="w")
        
        self.model_value = ctk.CTkLabel(details_frame, text="-")
        self.model_value.grid(row=1, column=1, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="w")
        
        mac_label = ctk.CTkLabel(details_frame, text="MAC Address:")
        mac_label.grid(row=2, column=0, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="w")
        
        self.mac_value = ctk.CTkLabel(details_frame, text="-")
        self.mac_value.grid(row=2, column=1, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="w")
        
        ip_label = ctk.CTkLabel(details_frame, text="IP Address:")
        ip_label.grid(row=3, column=0, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="w")
        
        self.ip_value = ctk.CTkLabel(details_frame, text="-")
        self.ip_value.grid(row=3, column=1, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="w")
        
        port_label = ctk.CTkLabel(details_frame, text="Port:")
        port_label.grid(row=4, column=0, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="w")
        
        self.port_value = ctk.CTkLabel(details_frame, text="-")
        self.port_value.grid(row=4, column=1, padx=MEDIUM_PAD, pady=(0, MEDIUM_PAD), sticky="w")

    def load_devices(self):
        """Load devices from settings."""
        devices = self.device_controller.get_devices()
        self.update_device_list(devices)
    
    def discover_devices(self):
        """Discover devices on the network."""
        self.device_controller.discover_devices(callback=self.update_device_list)

    def update_device_list(self, devices: List[Dict[str, Any]]):
        """Update the device list."""
        self.device_listbox.delete(0, tk.END)
        
        for i, device in enumerate(devices):
            # Use lowercase key names that match our connection.py implementation
            self.device_listbox.insert(tk.END, f"{device.get('model', '-')} ({device.get('ip', '-')})")

            # Select the currently selected device
            if i == self.device_controller.selected_device_index:
                self.device_listbox.selection_set(i)
                self.update_device_details(device)
    
    def on_device_select(self, event):
        """Handle device selection."""
        selection = self.device_listbox.curselection()
        if selection:
            index = selection[0]
            self.device_controller.select_device(index)
            device = self.device_controller.get_selected_device()
            if device:
                self.update_device_details(device)
    
    def update_device_details(self, device: Dict[str, Any]):
        """Update the device details display."""
        # Use lowercase key names that match our connection.py implementation
        self.model_value.configure(text=device.get("model", "-"))
        self.mac_value.configure(text=device.get("mac", "-"))
        self.ip_value.configure(text=device.get("ip", "-"))
        self.port_value.configure(text=str(device.get("port", "-")))
