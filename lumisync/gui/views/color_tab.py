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

from ..base import BaseFrame
from ..styles import MEDIUM_BUTTON, MEDIUM_PAD


class ColorWheelWidget(ctk.CTkFrame):
    """Custom color wheel widget for color selection."""
    
    def __init__(self, master, size=200, callback=None):
        super().__init__(master)
        self.size = size
        self.callback = callback
        self.current_color = (255, 0, 0)  # Default to red
        
        # Create canvas for color wheel
        self.canvas = tk.Canvas(
            self, 
            width=size, 
            height=size, 
            bg='#2b2b2b',
            highlightthickness=0
        )
        self.canvas.pack(padx=10, pady=10)
        
        # Bind mouse events
        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        
        # Create the color wheel
        self.create_color_wheel()
        
    def create_color_wheel(self):
        """Create the color wheel image."""
        # Create PIL image for color wheel
        img = Image.new('RGB', (self.size, self.size), 'white')
        draw = ImageDraw.Draw(img)
        
        center = self.size // 2
        radius = center - 10
        
        # Draw color wheel
        for angle in range(360):
            for r in range(radius):
                # Convert polar to cartesian coordinates
                x = center + int(r * math.cos(math.radians(angle)))
                y = center + int(r * math.sin(math.radians(angle)))
                
                # Calculate hue and saturation
                hue = angle / 360.0
                saturation = r / radius
                value = 1.0
                
                # Convert HSV to RGB
                rgb = colorsys.hsv_to_rgb(hue, saturation, value)
                color = tuple(int(c * 255) for c in rgb)
                
                try:
                    draw.point((x, y), fill=color)
                except:
                    pass
        
        # Convert to PhotoImage and display
        self.wheel_image = ImageTk.PhotoImage(img)
        self.canvas.create_image(center, center, image=self.wheel_image)
        
        # Draw center circle for brightness
        self.canvas.create_oval(
            center-20, center-20, center+20, center+20,
            fill='white', outline='black', width=2
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
        
        if distance <= center - 10:  # Within wheel bounds
            # Calculate angle and distance
            angle = math.atan2(dy, dx)
            if angle < 0:
                angle += 2 * math.pi
                
            hue = angle / (2 * math.pi)
            saturation = min(distance / (center - 10), 1.0)
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
        
        # Current color preview
        self.color_preview = ctk.CTkFrame(wheel_frame, height=40)
        self.color_preview.pack(
            padx=MEDIUM_PAD, pady=MEDIUM_PAD, fill="x"
        )
        
        self.color_preview_label = ctk.CTkLabel(
            self.color_preview, text="Current Color", 
            font=("Segoe UI", 10)
        )
        self.color_preview_label.pack(pady=10)
        
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
            command=self.on_rgb_change
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
            command=self.on_rgb_change
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
            command=self.on_rgb_change
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
                command=lambda c=color: self.set_color(c)
            )
            button.grid(
                row=row, col=col, 
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
            command=self.on_brightness_change
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
            font=("Segoe UI", 12, "bold")
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
