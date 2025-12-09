"""
Dialog for manually adding a device.
This dialog allows users to add devices by IP address when automatic discovery fails.
"""

import re
import tkinter as tk
from typing import Callable, Dict, Any, Optional

import customtkinter as ctk

from ..styles import (
    DEFAULT_FONT,
    HEADER_FONT,
    MEDIUM_PAD,
    SMALL_PAD,
    ERROR_COLOR,
    SUCCESS_COLOR,
)


class AddDeviceDialog(ctk.CTkToplevel):
    """Dialog for manually adding a device."""

    def __init__(
        self,
        parent,
        on_add_callback: Callable[[str, str, Optional[str], int], bool],
        **kwargs
    ):
        """
        Initialize the add device dialog.

        Args:
            parent: Parent window
            on_add_callback: Callback function to add the device.
                            Takes (ip, model, mac, port) and returns True if successful.
        """
        super().__init__(parent, **kwargs)

        self.on_add_callback = on_add_callback
        self.result = None

        # Configure dialog
        self.title("Add Device Manually")
        self.geometry("450x380")
        self.resizable(False, False)

        # Make dialog modal
        self.transient(parent)
        self.grab_set()

        # Center the dialog on parent
        self.update_idletasks()
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_width = parent.winfo_width()
        parent_height = parent.winfo_height()
        dialog_width = self.winfo_width()
        dialog_height = self.winfo_height()
        x = parent_x + (parent_width - dialog_width) // 2
        y = parent_y + (parent_height - dialog_height) // 2
        self.geometry(f"+{x}+{y}")

        self._create_widgets()

        # Focus on IP entry
        self.ip_entry.focus_set()

        # Bind Enter key to add button
        self.bind("<Return>", lambda e: self._on_add())
        self.bind("<Escape>", lambda e: self._on_cancel())

    def _create_widgets(self):
        """Create the dialog widgets."""
        # Main container
        main_frame = ctk.CTkFrame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=MEDIUM_PAD, pady=MEDIUM_PAD)

        # Header
        header_label = ctk.CTkLabel(
            main_frame,
            text="Add Device Manually",
            font=HEADER_FONT,
        )
        header_label.pack(pady=(SMALL_PAD, MEDIUM_PAD))

        # Description
        desc_label = ctk.CTkLabel(
            main_frame,
            text="If automatic discovery doesn't find your device,\nyou can add it manually using its IP address.",
            font=DEFAULT_FONT,
            text_color="gray",
        )
        desc_label.pack(pady=(0, MEDIUM_PAD))

        # Form frame
        form_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        form_frame.pack(fill=tk.X, padx=MEDIUM_PAD, pady=SMALL_PAD)

        # IP Address (required)
        ip_frame = ctk.CTkFrame(form_frame, fg_color="transparent")
        ip_frame.pack(fill=tk.X, pady=SMALL_PAD)

        ip_label = ctk.CTkLabel(
            ip_frame,
            text="IP Address *",
            font=DEFAULT_FONT,
            anchor="w",
            width=120,
        )
        ip_label.pack(side=tk.LEFT)

        self.ip_entry = ctk.CTkEntry(
            ip_frame,
            placeholder_text="e.g., 192.168.1.100",
            width=250,
        )
        self.ip_entry.pack(side=tk.LEFT, padx=(SMALL_PAD, 0))

        # Model Name (optional)
        model_frame = ctk.CTkFrame(form_frame, fg_color="transparent")
        model_frame.pack(fill=tk.X, pady=SMALL_PAD)

        model_label = ctk.CTkLabel(
            model_frame,
            text="Device Name",
            font=DEFAULT_FONT,
            anchor="w",
            width=120,
        )
        model_label.pack(side=tk.LEFT)

        self.model_entry = ctk.CTkEntry(
            model_frame,
            placeholder_text="e.g., Living Room Light",
            width=250,
        )
        self.model_entry.pack(side=tk.LEFT, padx=(SMALL_PAD, 0))

        # MAC Address (optional)
        mac_frame = ctk.CTkFrame(form_frame, fg_color="transparent")
        mac_frame.pack(fill=tk.X, pady=SMALL_PAD)

        mac_label = ctk.CTkLabel(
            mac_frame,
            text="MAC Address",
            font=DEFAULT_FONT,
            anchor="w",
            width=120,
        )
        mac_label.pack(side=tk.LEFT)

        self.mac_entry = ctk.CTkEntry(
            mac_frame,
            placeholder_text="Optional (auto-generated)",
            width=250,
        )
        self.mac_entry.pack(side=tk.LEFT, padx=(SMALL_PAD, 0))

        # Port (optional)
        port_frame = ctk.CTkFrame(form_frame, fg_color="transparent")
        port_frame.pack(fill=tk.X, pady=SMALL_PAD)

        port_label = ctk.CTkLabel(
            port_frame,
            text="Port",
            font=DEFAULT_FONT,
            anchor="w",
            width=120,
        )
        port_label.pack(side=tk.LEFT)

        self.port_entry = ctk.CTkEntry(
            port_frame,
            placeholder_text="Default: 4003",
            width=250,
        )
        self.port_entry.pack(side=tk.LEFT, padx=(SMALL_PAD, 0))

        # Error/Status label
        self.status_label = ctk.CTkLabel(
            main_frame,
            text="",
            font=DEFAULT_FONT,
            text_color=ERROR_COLOR,
        )
        self.status_label.pack(pady=SMALL_PAD)

        # Button frame
        button_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        button_frame.pack(fill=tk.X, pady=MEDIUM_PAD)

        # Cancel button
        cancel_btn = ctk.CTkButton(
            button_frame,
            text="Cancel",
            command=self._on_cancel,
            width=120,
            fg_color="gray",
            hover_color="darkgray",
        )
        cancel_btn.pack(side=tk.RIGHT, padx=SMALL_PAD)

        # Add button
        add_btn = ctk.CTkButton(
            button_frame,
            text="Add Device",
            command=self._on_add,
            width=120,
        )
        add_btn.pack(side=tk.RIGHT, padx=SMALL_PAD)

    def _validate_ip(self, ip: str) -> bool:
        """Validate IP address format."""
        pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        if not re.match(pattern, ip):
            return False
        parts = ip.split('.')
        return all(0 <= int(part) <= 255 for part in parts)

    def _validate_mac(self, mac: str) -> bool:
        """Validate MAC address format (if provided)."""
        if not mac:
            return True  # Optional field
        # Accept formats: XX:XX:XX:XX:XX:XX or XX-XX-XX-XX-XX-XX
        pattern = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$'
        return bool(re.match(pattern, mac))

    def _validate_port(self, port_str: str) -> tuple[bool, int]:
        """Validate port number."""
        if not port_str:
            return True, 4003  # Default port
        try:
            port = int(port_str)
            if 1 <= port <= 65535:
                return True, port
            return False, 0
        except ValueError:
            return False, 0

    def _on_add(self):
        """Handle add button click."""
        ip = self.ip_entry.get().strip()
        model = self.model_entry.get().strip() or "Manual Device"
        mac = self.mac_entry.get().strip() or None
        port_str = self.port_entry.get().strip()

        # Validate IP
        if not ip:
            self.status_label.configure(text="IP address is required", text_color=ERROR_COLOR)
            self.ip_entry.focus_set()
            return

        if not self._validate_ip(ip):
            self.status_label.configure(text="Invalid IP address format", text_color=ERROR_COLOR)
            self.ip_entry.focus_set()
            return

        # Validate MAC if provided
        if mac and not self._validate_mac(mac):
            self.status_label.configure(
                text="Invalid MAC address format (use XX:XX:XX:XX:XX:XX)",
                text_color=ERROR_COLOR
            )
            self.mac_entry.focus_set()
            return

        # Validate port
        port_valid, port = self._validate_port(port_str)
        if not port_valid:
            self.status_label.configure(
                text="Invalid port number (1-65535)",
                text_color=ERROR_COLOR
            )
            self.port_entry.focus_set()
            return

        # Try to add the device
        self.status_label.configure(text="Adding device...", text_color="gray")
        self.update()

        success = self.on_add_callback(ip, model, mac, port)

        if success:
            self.status_label.configure(text="Device added successfully!", text_color=SUCCESS_COLOR)
            self.result = {"ip": ip, "model": model, "mac": mac, "port": port}
            self.after(500, self.destroy)  # Close after brief delay
        else:
            self.status_label.configure(
                text="Failed to add device (may already exist)",
                text_color=ERROR_COLOR
            )

    def _on_cancel(self):
        """Handle cancel button click."""
        self.result = None
        self.destroy()

