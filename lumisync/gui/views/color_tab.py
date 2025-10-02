"""
Color controls tab for the LumiSync GUI.
This module contains the UI for color management and controls.
"""

import tkinter as tk
from typing import Any, Dict, Optional
import colorsys
import math

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk
import numpy as np

from ..base import BaseFrame
from ..styles import MEDIUM_BUTTON, MEDIUM_PAD


class ColorWheelWidget(ctk.CTkFrame):
    """Custom color wheel widget for color selection."""
    
    def __init__(self, master, size=200, callback=None):
        super().__init__(master)
        self.size = size
        self.callback = callback
        self.current_color = (255, 0, 0)  # Default to red
        
        # Create canvas for color wheel with proper dark theme background
        self.canvas = tk.Canvas(
            self, 
            width=size, 
            height=size, 
            bg='#212121',  # Match CustomTkinter dark theme
            highlightthickness=0,
            relief='flat',
            bd=0
        )
        self.canvas.pack(padx=10, pady=10)
        
        # Bind mouse events
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        
        # Create the color wheel
        self.create_color_wheel()
        
    def create_color_wheel(self):
        """Create the color wheel image with high quality and proper background."""
        # Create PIL image for color wheel with higher resolution for better quality
        scale_factor = 2  # Render at 2x resolution for better quality
        high_res_size = self.size * scale_factor
        img = Image.new('RGBA', (high_res_size, high_res_size), (33, 33, 33, 255))  # Dark background
        
        # Use numpy for faster pixel manipulation
        img_array = np.array(img)
        
        center = high_res_size // 2
        radius = center - 20 * scale_factor  # Adjust padding for scale
        
        # Create coordinate arrays
        y, x = np.ogrid[:high_res_size, :high_res_size]
        x_centered = x - center
        y_centered = y - center
        
        # Calculate distance and angle for all pixels at once
        distances = np.sqrt(x_centered**2 + y_centered**2)
        angles = np.arctan2(y_centered, x_centered)
        
        # Convert negative angles to positive
        angles = np.where(angles < 0, angles + 2 * np.pi, angles)
        
        # Create mask for pixels within the wheel
        mask = distances <= radius
        
        # Calculate HSV values
        hue = angles[mask] / (2 * np.pi)
        saturation = np.minimum(distances[mask] / radius, 1.0)
        value = np.ones_like(hue)
        
        # Convert HSV to RGB using vectorized operations
        c = value * saturation
        x_val = c * (1 - np.abs((hue * 6) % 2 - 1))
        m = value - c
        
        # RGB calculation based on hue sector
        hue_sector = (hue * 6).astype(int)
        
        r = np.zeros_like(hue)
        g = np.zeros_like(hue)
        b = np.zeros_like(hue)
        
        # Sector 0: red to yellow
        sector_0 = hue_sector == 0
        r[sector_0] = c[sector_0]
        g[sector_0] = x_val[sector_0]
        
        # Sector 1: yellow to green
        sector_1 = hue_sector == 1
        r[sector_1] = x_val[sector_1]
        g[sector_1] = c[sector_1]
        
        # Sector 2: green to cyan
        sector_2 = hue_sector == 2
        g[sector_2] = c[sector_2]
        b[sector_2] = x_val[sector_2]
        
        # Sector 3: cyan to blue
        sector_3 = hue_sector == 3
        g[sector_3] = x_val[sector_3]
        b[sector_3] = c[sector_3]
        
        # Sector 4: blue to magenta
        sector_4 = hue_sector == 4
        r[sector_4] = x_val[sector_4]
        b[sector_4] = c[sector_4]
        
        # Sector 5: magenta to red
        sector_5 = hue_sector == 5
        r[sector_5] = c[sector_5]
        b[sector_5] = x_val[sector_5]
        
        # Add the minimum value and convert to 0-255 range
        r = ((r + m) * 255).astype(np.uint8)
        g = ((g + m) * 255).astype(np.uint8)
        b = ((b + m) * 255).astype(np.uint8)
        
        # Set the RGB values in the image array
        y_coords, x_coords = np.where(mask)
        img_array[y_coords, x_coords, 0] = r
        img_array[y_coords, x_coords, 1] = g
        img_array[y_coords, x_coords, 2] = b
        img_array[y_coords, x_coords, 3] = 255  # Full alpha
        
        # Convert back to PIL Image
        img = Image.fromarray(img_array, 'RGBA')
        
        # Resize back to original size with high-quality resampling
        img = img.resize((self.size, self.size), Image.Resampling.LANCZOS)
        
        # Convert to PhotoImage and display
        self.wheel_image = ImageTk.PhotoImage(img)
        self.canvas.create_image(self.size//2, self.size//2, image=self.wheel_image)
        
        # Draw center circle for brightness with proper dark theme colors
        center_display = self.size // 2
        self.canvas.create_oval(
            center_display-20, center_display-20, center_display+20, center_display+20,
            fill='#404040', outline='#606060', width=2  # Dark theme colors
        )
        
    def on_click(self, event):
        """Handle mouse click on color wheel."""
        self.update_color_from_position(event.x, event.y)
        
    def on_drag(self, event):
        """Handle mouse drag on color wheel."""
        self.update_color_from_position(event.x, event.y)
        
    def update_color_from_position(self, x, y):
        """Update color based on mouse position."""
        center = self.size // 2
        dx = x - center
        dy = y - center
        distance = math.sqrt(dx*dx + dy*dy)
        
        if distance <= center - 20:  # Within wheel bounds
            # Calculate angle and distance
            angle = math.atan2(dy, dx)
            if angle < 0:
                angle += 2 * math.pi
                
            hue = angle / (2 * math.pi)
            saturation = min(distance / (center - 20), 1.0)
            value = 1.0
            
            # Convert to RGB
            rgb = colorsys.hsv_to_rgb(hue, saturation, value)
            self.current_color = tuple(int(c * 255) for c in rgb)
            
            if self.callback:
                self.callback(self.current_color)


class ColorTab(BaseFrame):
    """Tab for color controls and management."""
    
    def __init__(self, master, app, device_controller=None):
        super().__init__(master)
        self.app = app
        self.device_controller = device_controller
        
        # Current color values
        self.current_rgb = [255, 0, 0]
        self.current_brightness = 100
        
        # Predefined colors
        self.preset_colors = [
            ("Red", (255, 0, 0)),
            ("Green", (0, 255, 0)),
            ("Blue", (0, 0, 255)),
            ("Yellow", (255, 255, 0)),
            ("Purple", (128, 0, 128)),
            ("Orange", (255, 165, 0)),
            ("Pink", (255, 192, 203)),
            ("Cyan", (0, 255, 255)),
            ("White", (255, 255, 255)),
            ("Warm White", (255, 223, 186))
        ]
        
        # Configure grid
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(2, weight=1)
        
        # Create widgets
        self.create_header()
        self.create_color_wheel_section()
        self.create_rgb_controls()
        self.create_preset_colors()
        self.create_brightness_control()
        
    def create_header(self):
        """Create the header section."""
        header_frame = ctk.CTkFrame(self)
        header_frame.grid(
            row=0, column=0, columnspan=2, 
            padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="ew"
        )
        
        header_label = ctk.CTkLabel(
            header_frame, text="Color Controls", font=("Segoe UI", 16, "bold")
        )
        header_label.pack(padx=MEDIUM_PAD, pady=MEDIUM_PAD)
        
    def create_color_wheel_section(self):
        """Create the color wheel section."""
        wheel_frame = ctk.CTkFrame(self)
        wheel_frame.grid(
            row=1, column=0, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="nsew"
        )
        
        # Color wheel label
        wheel_label = ctk.CTkLabel(
            wheel_frame, text="Color Wheel", font=("Segoe UI", 12, "bold")
        )
        wheel_label.pack(padx=MEDIUM_PAD, pady=(MEDIUM_PAD, 5))
        
        # Color wheel widget
        self.color_wheel = ColorWheelWidget(
            wheel_frame, size=200, callback=self.on_color_wheel_change
        )
        self.color_wheel.pack(padx=MEDIUM_PAD, pady=MEDIUM_PAD)
        
        # Current color preview with improved styling
        self.color_preview = ctk.CTkFrame(wheel_frame, height=50)
        self.color_preview.pack(
            padx=MEDIUM_PAD, pady=MEDIUM_PAD, fill="x"
        )
        
        self.color_preview_label = ctk.CTkLabel(
            self.color_preview, text="Current Color", 
            font=("Segoe UI", 10, "bold")
        )
        self.color_preview_label.pack(pady=15)
        
    def create_rgb_controls(self):
        """Create RGB slider controls."""
        rgb_frame = ctk.CTkFrame(self)
        rgb_frame.grid(
            row=1, column=1, padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="nsew"
        )
        
        # RGB controls label
        rgb_label = ctk.CTkLabel(
            rgb_frame, text="RGB Controls", font=("Segoe UI", 12, "bold")
        )
        rgb_label.pack(padx=MEDIUM_PAD, pady=(MEDIUM_PAD, 10))
        
        # Red slider
        red_frame = ctk.CTkFrame(rgb_frame)
        red_frame.pack(padx=MEDIUM_PAD, pady=5, fill="x")
        
        ctk.CTkLabel(red_frame, text="Red:", width=50).pack(side="left", padx=5)
        self.red_slider = ctk.CTkSlider(
            red_frame, from_=0, to=255, 
            command=self.on_rgb_change,
            progress_color="#ff4444"  # Red color for red slider
        )
        self.red_slider.pack(side="left", fill="x", expand=True, padx=5)
        self.red_slider.set(255)
        
        self.red_value_label = ctk.CTkLabel(red_frame, text="255", width=30)
        self.red_value_label.pack(side="right", padx=5)
        
        # Green slider
        green_frame = ctk.CTkFrame(rgb_frame)
        green_frame.pack(padx=MEDIUM_PAD, pady=5, fill="x")
        
        ctk.CTkLabel(green_frame, text="Green:", width=50).pack(side="left", padx=5)
        self.green_slider = ctk.CTkSlider(
            green_frame, from_=0, to=255,
            command=self.on_rgb_change,
            progress_color="#44ff44"  # Green color for green slider
        )
        self.green_slider.pack(side="left", fill="x", expand=True, padx=5)
        self.green_slider.set(0)
        
        self.green_value_label = ctk.CTkLabel(green_frame, text="0", width=30)
        self.green_value_label.pack(side="right", padx=5)
        
        # Blue slider
        blue_frame = ctk.CTkFrame(rgb_frame)
        blue_frame.pack(padx=MEDIUM_PAD, pady=5, fill="x")
        
        ctk.CTkLabel(blue_frame, text="Blue:", width=50).pack(side="left", padx=5)
        self.blue_slider = ctk.CTkSlider(
            blue_frame, from_=0, to=255,
            command=self.on_rgb_change,
            progress_color="#4444ff"  # Blue color for blue slider
        )
        self.blue_slider.pack(side="left", fill="x", expand=True, padx=5)
        self.blue_slider.set(0)
        
        self.blue_value_label = ctk.CTkLabel(blue_frame, text="0", width=30)
        self.blue_value_label.pack(side="right", padx=5)
        
        # Hex color input
        hex_frame = ctk.CTkFrame(rgb_frame)
        hex_frame.pack(padx=MEDIUM_PAD, pady=10, fill="x")
        
        ctk.CTkLabel(hex_frame, text="Hex:").pack(side="left", padx=5)
        self.hex_entry = ctk.CTkEntry(hex_frame, placeholder_text="#FF0000")
        self.hex_entry.pack(side="left", fill="x", expand=True, padx=5)
        self.hex_entry.bind("<Return>", self.on_hex_change)
        
        hex_button = ctk.CTkButton(
            hex_frame, text="Apply", width=60,
            command=self.on_hex_change
        )
        hex_button.pack(side="right", padx=5)
        
    def create_preset_colors(self):
        """Create preset color buttons."""
        preset_frame = ctk.CTkFrame(self)
        preset_frame.grid(
            row=2, column=0, columnspan=2,
            padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="ew"
        )
        
        # Preset colors label
        preset_label = ctk.CTkLabel(
            preset_frame, text="Preset Colors", font=("Segoe UI", 12, "bold")
        )
        preset_label.pack(padx=MEDIUM_PAD, pady=(MEDIUM_PAD, 10))
        
        # Create grid for preset buttons
        buttons_frame = ctk.CTkFrame(preset_frame)
        buttons_frame.pack(padx=MEDIUM_PAD, pady=MEDIUM_PAD, fill="both", expand=True)
        
        # Configure grid
        for i in range(5):
            buttons_frame.grid_columnconfigure(i, weight=1)
        
        # Create preset color buttons
        for i, (name, color) in enumerate(self.preset_colors):
            row = i // 5
            col = i % 5
            
            hex_color = f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"
            
            button = ctk.CTkButton(
                buttons_frame,
                text=name,
                fg_color=hex_color,
                hover_color=hex_color,
                text_color="white" if sum(color) < 400 else "black",
                command=lambda c=color: self.set_color(c),
                font=("Segoe UI", 10, "bold")
            )
            button.grid(
                row=row, column=col, 
                padx=5, pady=5, sticky="ew"
            )
            
    def create_brightness_control(self):
        """Create brightness control."""
        brightness_frame = ctk.CTkFrame(self)
        brightness_frame.grid(
            row=3, column=0, columnspan=2,
            padx=MEDIUM_PAD, pady=MEDIUM_PAD, sticky="ew"
        )
        
        # Brightness label
        brightness_label = ctk.CTkLabel(
            brightness_frame, text="Brightness", font=("Segoe UI", 12, "bold")
        )
        brightness_label.pack(padx=MEDIUM_PAD, pady=(MEDIUM_PAD, 5))
        
        # Brightness controls
        brightness_controls = ctk.CTkFrame(brightness_frame)
        brightness_controls.pack(padx=MEDIUM_PAD, pady=MEDIUM_PAD, fill="x")
        
        ctk.CTkLabel(brightness_controls, text="Brightness:", width=80).pack(side="left", padx=5)
        
        self.brightness_slider = ctk.CTkSlider(
            brightness_controls, from_=1, to=100,
            command=self.on_brightness_change,
            progress_color="#ffaa00"  # Orange color for brightness
        )
        self.brightness_slider.pack(side="left", fill="x", expand=True, padx=5)
        self.brightness_slider.set(100)
        
        self.brightness_value_label = ctk.CTkLabel(brightness_controls, text="100%", width=40)
        self.brightness_value_label.pack(side="right", padx=5)
        
        # Apply button
        apply_frame = ctk.CTkFrame(brightness_frame)
        apply_frame.pack(pady=MEDIUM_PAD)
        
        apply_button = ctk.CTkButton(
            apply_frame,
            text="Apply Color to Device",
            command=self.apply_color,
            width=200,
            height=40,
            font=("Segoe UI", 12, "bold"),
            fg_color="#2196F3",
            hover_color="#1976D2"
        )
        apply_button.pack(padx=MEDIUM_PAD, pady=MEDIUM_PAD)
        
    def on_color_wheel_change(self, color):
        """Handle color wheel color change."""
        self.current_rgb = list(color)
        self.update_rgb_sliders()
        self.update_color_preview()
        self.update_hex_display()
        
    def on_rgb_change(self, value=None):
        """Handle RGB slider changes."""
        self.current_rgb = [
            int(self.red_slider.get()),
            int(self.green_slider.get()),
            int(self.blue_slider.get())
        ]
        
        # Update value labels
        self.red_value_label.configure(text=str(self.current_rgb[0]))
        self.green_value_label.configure(text=str(self.current_rgb[1]))
        self.blue_value_label.configure(text=str(self.current_rgb[2]))
        
        self.update_color_preview()
        self.update_hex_display()
        
    def on_hex_change(self, event=None):
        """Handle hex color input change."""
        hex_value = self.hex_entry.get().strip()
        if hex_value.startswith('#'):
            hex_value = hex_value[1:]
            
        if len(hex_value) == 6:
            try:
                r = int(hex_value[0:2], 16)
                g = int(hex_value[2:4], 16)
                b = int(hex_value[4:6], 16)
                self.set_color((r, g, b))
            except ValueError:
                self.app.set_status("Invalid hex color format")
        else:
            self.app.set_status("Hex color must be 6 characters")
            
    def on_brightness_change(self, value):
        """Handle brightness slider change."""
        self.current_brightness = int(value)
        self.brightness_value_label.configure(text=f"{self.current_brightness}%")
        
    def set_color(self, color):
        """Set the current color."""
        self.current_rgb = list(color)
        self.update_rgb_sliders()
        self.update_color_preview()
        self.update_hex_display()
        
    def update_rgb_sliders(self):
        """Update RGB sliders to match current color."""
        self.red_slider.set(self.current_rgb[0])
        self.green_slider.set(self.current_rgb[1])
        self.blue_slider.set(self.current_rgb[2])
        
        self.red_value_label.configure(text=str(self.current_rgb[0]))
        self.green_value_label.configure(text=str(self.current_rgb[1]))
        self.blue_value_label.configure(text=str(self.current_rgb[2]))
        
    def update_color_preview(self):
        """Update the color preview."""
        hex_color = f"#{self.current_rgb[0]:02x}{self.current_rgb[1]:02x}{self.current_rgb[2]:02x}"
        self.color_preview.configure(fg_color=hex_color)
        
        # Update text color based on brightness for better readability
        brightness = sum(self.current_rgb) / 3
        text_color = "white" if brightness < 127 else "black"
        self.color_preview_label.configure(text_color=text_color)
        
    def update_hex_display(self):
        """Update the hex color display."""
        hex_color = f"#{self.current_rgb[0]:02x}{self.current_rgb[1]:02x}{self.current_rgb[2]:02x}"
        self.hex_entry.delete(0, tk.END)
        self.hex_entry.insert(0, hex_color.upper())
        
    def apply_color(self):
        """Apply the current color to the device."""
        if self.device_controller and self.device_controller.get_selected_device():
            try:
                # Apply color with brightness
                adjusted_rgb = [
                    int(c * self.current_brightness / 100) 
                    for c in self.current_rgb
                ]
                
                # Here you would implement the actual device color control
                # For now, we'll just show a status message
                hex_color = f"#{adjusted_rgb[0]:02x}{adjusted_rgb[1]:02x}{adjusted_rgb[2]:02x}"
                self.app.set_status(f"Applied color {hex_color} at {self.current_brightness}% brightness")
                
                # TODO: Implement actual device color control
                # self.device_controller.set_color(adjusted_rgb)
                
            except Exception as e:
                self.app.set_status(f"Failed to apply color: {str(e)}")
        else:
            self.app.set_status("No device selected. Please select a device first.")
