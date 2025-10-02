"""
Color controls tab for the LumiSync GUI.
This module contains the UI for color management and controls.
"""

import tkinter as tk
from typing import Any, Dict, List, Optional, Tuple, Callable
import colorsys
import math

import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk

from ..base import BaseFrame
from ..styles import MEDIUM_BUTTON, MEDIUM_PAD


class ColorWheelWidget(ctk.CTkFrame):
    """Custom color wheel widget for color selection."""
    
    def __init__(
        self, 
        master: ctk.CTkFrame, 
        size: int = 200, 
        callback: Optional[Callable[[Tuple[int, int, int]], None]] = None
    ) -> None:
        """
        Initialize the color wheel widget.
        
        Args:
            master: Parent widget
            size: Size of the color wheel in pixels
            callback: Function to call when color changes
        """
        super().__init__(master)
        self.size = size
        self.callback = callback
        self.current_color: Tuple[int, int, int] = (255, 0, 0)  # Default to red
        self.wheel_image: Optional[ImageTk.PhotoImage] = None
        
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
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        
        # Create the color wheel
        self._create_color_wheel()
        
    def _create_color_wheel(self) -> None:
        """Create the color wheel image with high quality and proper background."""
        # Create PIL image for color wheel with higher resolution for better quality
        scale_factor = 2  # Render at 2x resolution for better quality
        high_res_size = self.size * scale_factor
        img = Image.new('RGB', (high_res_size, high_res_size), (33, 33, 33))  # Dark background
        draw = ImageDraw.Draw(img)
        
        center = high_res_size // 2
        radius = center - 20 * scale_factor  # Adjust padding for scale
        
        # Draw color wheel with improved algorithm
        for angle_deg in range(0, 360, 2):  # Step by 2 for performance
            for r in range(0, radius, 2):  # Step by 2 for performance
                # Convert polar to cartesian coordinates
                angle_rad = math.radians(angle_deg)
                x = center + int(r * math.cos(angle_rad))
                y = center + int(r * math.sin(angle_rad))
                
                # Calculate hue and saturation
                hue = angle_deg / 360.0
                saturation = r / radius
                value = 1.0
                
                # Convert HSV to RGB
                rgb = colorsys.hsv_to_rgb(hue, saturation, value)
                color = tuple(int(c * 255) for c in rgb)
                
                # Draw multiple pixels for smoother appearance
                try:
                    for dx in range(2):
                        for dy in range(2):
                            if 0 <= x + dx < high_res_size and 0 <= y + dy < high_res_size:
                                draw.point((x + dx, y + dy), fill=color)
                except (IndexError, ValueError):
                    continue  # Skip invalid coordinates
        
        # Resize back to original size with high-quality resampling
        img = img.resize((self.size, self.size), Image.Resampling.LANCZOS)
        
        # Convert to PhotoImage and display
        self.wheel_image = ImageTk.PhotoImage(img)
        self.canvas.create_image(self.size//2, self.size//2, image=self.wheel_image)
        
        # Draw center circle for brightness with proper dark theme colors
        center_display = self.size // 2
        self.canvas.create_oval(
            center_display - 20, center_display - 20, 
            center_display + 20, center_display + 20,
            fill='#404040', outline='#606060', width=2  # Dark theme colors
        )
        
    def _on_click(self, event: tk.Event) -> None:
        """Handle mouse click on color wheel."""
        self._update_color_from_position(event.x, event.y)
        
    def _on_drag(self, event: tk.Event) -> None:
        """Handle mouse drag on color wheel."""
        self._update_color_from_position(event.x, event.y)
        
    def _update_color_from_position(self, x: int, y: int) -> None:
        """Update color based on mouse position."""
        center = self.size // 2
        dx = x - center
        dy = y - center
        distance = math.sqrt(dx * dx + dy * dy)
        
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
    
    def __init__(
        self, 
        master: ctk.CTkFrame, 
        app: Any, 
        device_controller: Optional[Any] = None
    ) -> None:
        """
        Initialize the color tab.
        
        Args:
            master: Parent widget
            app: Main application instance
            device_controller: Device controller for managing devices
        """
        super().__init__(master)
        self.app = app
        self.device_controller = device_controller
        
        # Current color values
        self.current_rgb: List[int] = [255, 0, 0]
        self.current_brightness: int = 100
        
        # Widget references
        self.color_wheel: Optional[ColorWheelWidget] = None
        self.color_preview: Optional[ctk.CTkFrame] = None
        self.color_preview_label: Optional[ctk.CTkLabel] = None
        self.red_slider: Optional[ctk.CTkSlider] = None
        self.green_slider: Optional[ctk.CTkSlider] = None
        self.blue_slider: Optional[ctk.CTkSlider] = None
        self.red_value_label: Optional[ctk.CTkLabel] = None
        self.green_value_label: Optional[ctk.CTkLabel] = None
        self.blue_value_label: Optional[ctk.CTkLabel] = None
        self.hex_entry: Optional[ctk.CTkEntry] = None
        self.brightness_slider: Optional[ctk.CTkSlider] = None
        self.brightness_value_label: Optional[ctk.CTkLabel] = None
        
        # Predefined colors
        self.preset_colors: List[Tuple[str, Tuple[int, int, int]]] = [
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
        self._create_header()
        self._create_color_wheel_section()
        self._create_rgb_controls()
        self._create_preset_colors()
        self._create_brightness_control()
        
    def _create_header(self) -> None:
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
        
    def _create_color_wheel_section(self) -> None:
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
            wheel_frame, size=200, callback=self._on_color_wheel_change
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
        
    def _create_rgb_controls(self) -> None:
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
        
        # Create RGB sliders
        self._create_rgb_slider(rgb_frame, "Red", "#ff4444", 255)
        self._create_rgb_slider(rgb_frame, "Green", "#44ff44", 0)
        self._create_rgb_slider(rgb_frame, "Blue", "#4444ff", 0)
        
        # Hex color input
        self._create_hex_input(rgb_frame)
        
    def _create_rgb_slider(
        self, 
        parent: ctk.CTkFrame, 
        color_name: str, 
        color_code: str, 
        initial_value: int
    ) -> None:
        """Create an RGB slider with label and value display."""
        slider_frame = ctk.CTkFrame(parent)
        slider_frame.pack(padx=MEDIUM_PAD, pady=5, fill="x")
        
        label = ctk.CTkLabel(slider_frame, text=f"{color_name}:", width=50)
        label.pack(side="left", padx=5)
        
        slider = ctk.CTkSlider(
            slider_frame, from_=0, to=255, 
            command=self._on_rgb_change,
            progress_color=color_code
        )
        slider.pack(side="left", fill="x", expand=True, padx=5)
        slider.set(initial_value)
        
        value_label = ctk.CTkLabel(slider_frame, text=str(initial_value), width=30)
        value_label.pack(side="right", padx=5)
        
        # Store references
        if color_name == "Red":
            self.red_slider = slider
            self.red_value_label = value_label
        elif color_name == "Green":
            self.green_slider = slider
            self.green_value_label = value_label
        elif color_name == "Blue":
            self.blue_slider = slider
            self.blue_value_label = value_label
            
    def _create_hex_input(self, parent: ctk.CTkFrame) -> None:
        """Create hex color input field."""
        hex_frame = ctk.CTkFrame(parent)
        hex_frame.pack(padx=MEDIUM_PAD, pady=10, fill="x")
        
        hex_label = ctk.CTkLabel(hex_frame, text="Hex:")
        hex_label.pack(side="left", padx=5)
        
        self.hex_entry = ctk.CTkEntry(hex_frame, placeholder_text="#FF0000")
        self.hex_entry.pack(side="left", fill="x", expand=True, padx=5)
        self.hex_entry.bind("<Return>", self._on_hex_change)
        
        hex_button = ctk.CTkButton(
            hex_frame, text="Apply", width=60,
            command=self._on_hex_change
        )
        hex_button.pack(side="right", padx=5)
        
    def _create_preset_colors(self) -> None:
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
            self._create_preset_button(buttons_frame, i, name, color)
            
    def _create_preset_button(
        self, 
        parent: ctk.CTkFrame, 
        index: int, 
        name: str, 
        color: Tuple[int, int, int]
    ) -> None:
        """Create a single preset color button."""
        row = index // 5
        col = index % 5
        
        hex_color = f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"
        
        button = ctk.CTkButton(
            parent,
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
            
    def _create_brightness_control(self) -> None:
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
        
        brightness_control_label = ctk.CTkLabel(
            brightness_controls, text="Brightness:", width=80
        )
        brightness_control_label.pack(side="left", padx=5)
        
        self.brightness_slider = ctk.CTkSlider(
            brightness_controls, from_=1, to=100,
            command=self._on_brightness_change,
            progress_color="#ffaa00"  # Orange color for brightness
        )
        self.brightness_slider.pack(side="left", fill="x", expand=True, padx=5)
        self.brightness_slider.set(100)
        
        self.brightness_value_label = ctk.CTkLabel(
            brightness_controls, text="100%", width=40
        )
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
        
    def _on_color_wheel_change(self, color: Tuple[int, int, int]) -> None:
        """Handle color wheel color change."""
        self.current_rgb = list(color)
        self._update_rgb_sliders()
        self._update_color_preview()
        self._update_hex_display()
        
    def _on_rgb_change(self, value: Optional[float] = None) -> None:
        """Handle RGB slider changes."""
        if not all([self.red_slider, self.green_slider, self.blue_slider]):
            return
            
        self.current_rgb = [
            int(self.red_slider.get()),
            int(self.green_slider.get()),
            int(self.blue_slider.get())
        ]
        
        # Update value labels
        if self.red_value_label:
            self.red_value_label.configure(text=str(self.current_rgb[0]))
        if self.green_value_label:
            self.green_value_label.configure(text=str(self.current_rgb[1]))
        if self.blue_value_label:
            self.blue_value_label.configure(text=str(self.current_rgb[2]))
        
        self._update_color_preview()
        self._update_hex_display()
        
    def _on_hex_change(self, event: Optional[tk.Event] = None) -> None:
        """Handle hex color input change."""
        if not self.hex_entry:
            return
            
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
            
    def _on_brightness_change(self, value: float) -> None:
        """Handle brightness slider change."""
        self.current_brightness = int(value)
        if self.brightness_value_label:
            self.brightness_value_label.configure(text=f"{self.current_brightness}%")
        
    def set_color(self, color: Tuple[int, int, int]) -> None:
        """Set the current color."""
        self.current_rgb = list(color)
        self._update_rgb_sliders()
        self._update_color_preview()
        self._update_hex_display()
        
    def _update_rgb_sliders(self) -> None:
        """Update RGB sliders to match current color."""
        if not all([self.red_slider, self.green_slider, self.blue_slider]):
            return
            
        self.red_slider.set(self.current_rgb[0])
        self.green_slider.set(self.current_rgb[1])
        self.blue_slider.set(self.current_rgb[2])
        
        if self.red_value_label:
            self.red_value_label.configure(text=str(self.current_rgb[0]))
        if self.green_value_label:
            self.green_value_label.configure(text=str(self.current_rgb[1]))
        if self.blue_value_label:
            self.blue_value_label.configure(text=str(self.current_rgb[2]))
        
    def _update_color_preview(self) -> None:
        """Update the color preview."""
        if not self.color_preview or not self.color_preview_label:
            return
            
        hex_color = f"#{self.current_rgb[0]:02x}{self.current_rgb[1]:02x}{self.current_rgb[2]:02x}"
        self.color_preview.configure(fg_color=hex_color)
        
        # Update text color based on brightness for better readability
        brightness = sum(self.current_rgb) / 3
        text_color = "white" if brightness < 127 else "black"
        self.color_preview_label.configure(text_color=text_color)
        
    def _update_hex_display(self) -> None:
        """Update the hex color display."""
        if not self.hex_entry:
            return
            
        hex_color = f"#{self.current_rgb[0]:02x}{self.current_rgb[1]:02x}{self.current_rgb[2]:02x}"
        self.hex_entry.delete(0, tk.END)
        self.hex_entry.insert(0, hex_color.upper())
        
    def apply_color(self) -> None:
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
                self.app.set_status(
                    f"Applied color {hex_color} at {self.current_brightness}% brightness"
                )
                
                # TODO: Implement actual device color control
                # self.device_controller.set_color(adjusted_rgb)
                
            except Exception as e:
                self.app.set_status(f"Failed to apply color: {str(e)}")
        else:
            self.app.set_status("No device selected. Please select a device first.")
