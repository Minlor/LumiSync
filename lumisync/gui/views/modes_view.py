"""
Modes view for the LumiSync GUI.
This module provides the synchronization modes interface.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSlider, QGroupBox, QGridLayout
)
from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtGui import QFont

from ..resources import ResourceManager
from ..controllers.sync_controller import SyncController


class ModesView(QWidget):
    """View for managing sync modes."""

    def __init__(self, sync_controller: SyncController):
        super().__init__()
        self.controller = sync_controller

        # Set up the UI
        self.setup_ui()

        # Connect controller signals
        self.connect_signals()

        # Set up status update timer
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.update_sync_status)
        self.status_timer.start(1000)  # Update every second

    def setup_ui(self):
        """Set up the user interface."""
        # Main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Header
        header = QLabel("Synchronization Modes")
        header_font = QFont()
        header_font.setPointSize(16)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)

        # Two-column layout for modes
        modes_layout = QHBoxLayout()

        # Monitor Sync
        monitor_group = self.create_monitor_sync_group()
        modes_layout.addWidget(monitor_group)

        # Music Sync
        music_group = self.create_music_sync_group()
        modes_layout.addWidget(music_group)

        layout.addLayout(modes_layout)

        # Sync controls
        controls_group = self.create_sync_controls()
        layout.addWidget(controls_group)

        layout.addStretch()

    def create_monitor_sync_group(self):
        """Create the monitor sync control group."""
        group = QGroupBox("Monitor Sync")
        layout = QVBoxLayout(group)

        # Description
        desc = QLabel(
            "Synchronize lights with your screen content. "
            "Colors are sampled from different screen regions."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(10)

        # Brightness slider
        brightness_layout = QHBoxLayout()
        brightness_layout.addWidget(QLabel("Brightness:"))

        self.monitor_brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self.monitor_brightness_slider.setRange(10, 100)
        self.monitor_brightness_slider.setValue(
            int(self.controller.get_monitor_brightness() * 100)
        )
        self.monitor_brightness_slider.valueChanged.connect(
            self.on_monitor_brightness_changed
        )
        brightness_layout.addWidget(self.monitor_brightness_slider)

        self.monitor_brightness_label = QLabel(
            f"{int(self.controller.get_monitor_brightness() * 100)}%"
        )
        self.monitor_brightness_label.setMinimumWidth(40)
        brightness_layout.addWidget(self.monitor_brightness_label)

        layout.addLayout(brightness_layout)

        # Start button
        self.monitor_start_button = QPushButton("Start Monitor Sync")
        self.monitor_start_button.setIcon(ResourceManager.get_icon("screen.png"))
        self.monitor_start_button.setIconSize(QSize(20, 20))
        self.monitor_start_button.clicked.connect(self.on_monitor_start_clicked)
        layout.addWidget(self.monitor_start_button)

        return group

    def create_music_sync_group(self):
        """Create the music sync control group."""
        group = QGroupBox("Music Sync")
        layout = QVBoxLayout(group)

        # Description
        desc = QLabel(
            "Synchronize lights with your audio output. "
            "Colors change based on sound amplitude."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(10)

        # Brightness slider
        brightness_layout = QHBoxLayout()
        brightness_layout.addWidget(QLabel("Brightness:"))

        self.music_brightness_slider = QSlider(Qt.Orientation.Horizontal)
        self.music_brightness_slider.setRange(10, 100)
        self.music_brightness_slider.setValue(
            int(self.controller.get_music_brightness() * 100)
        )
        self.music_brightness_slider.valueChanged.connect(
            self.on_music_brightness_changed
        )
        brightness_layout.addWidget(self.music_brightness_slider)

        self.music_brightness_label = QLabel(
            f"{int(self.controller.get_music_brightness() * 100)}%"
        )
        self.music_brightness_label.setMinimumWidth(40)
        brightness_layout.addWidget(self.music_brightness_label)

        layout.addLayout(brightness_layout)

        # Start button
        self.music_start_button = QPushButton("Start Music Sync")
        self.music_start_button.setIcon(ResourceManager.get_icon("music.png"))
        self.music_start_button.setIconSize(QSize(20, 20))
        self.music_start_button.clicked.connect(self.on_music_start_clicked)
        layout.addWidget(self.music_start_button)

        return group

    def create_sync_controls(self):
        """Create the sync controls group."""
        group = QGroupBox("Sync Control")
        layout = QGridLayout(group)

        # Current mode
        layout.addWidget(QLabel("Current Mode:"), 0, 0)
        self.mode_label = QLabel("None")
        layout.addWidget(self.mode_label, 0, 1)

        # Status
        layout.addWidget(QLabel("Status:"), 1, 0)
        self.sync_status_label = QLabel("Idle")
        layout.addWidget(self.sync_status_label, 1, 1)

        # Stop button
        self.stop_button = QPushButton("Stop Sync")
        self.stop_button.setIcon(ResourceManager.get_icon("stop.png"))
        self.stop_button.setIconSize(QSize(20, 20))
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.on_stop_clicked)
        # Make the button red-ish
        self.stop_button.setStyleSheet(
            "QPushButton:enabled { background-color: #E74C3C; color: white; }"
        )
        layout.addWidget(self.stop_button, 2, 0, 1, 2)

        return group

    def connect_signals(self):
        """Connect controller signals to view updates."""
        self.controller.sync_started.connect(self.on_sync_started)
        self.controller.sync_stopped.connect(self.on_sync_stopped)
        self.controller.brightness_changed.connect(self.on_brightness_changed)

    def on_monitor_brightness_changed(self, value):
        """Handle monitor brightness slider change."""
        brightness = value / 100.0
        self.controller.set_monitor_brightness(brightness)
        self.monitor_brightness_label.setText(f"{value}%")

    def on_music_brightness_changed(self, value):
        """Handle music brightness slider change."""
        brightness = value / 100.0
        self.controller.set_music_brightness(brightness)
        self.music_brightness_label.setText(f"{value}%")

    def on_monitor_start_clicked(self):
        """Handle monitor sync start button click."""
        self.controller.start_monitor_sync()

    def on_music_start_clicked(self):
        """Handle music sync start button click."""
        self.controller.start_music_sync()

    def on_stop_clicked(self):
        """Handle stop button click."""
        self.controller.stop_sync()

    def on_sync_started(self, mode):
        """Handle sync started signal."""
        self.update_button_states(syncing=True)

    def on_sync_stopped(self):
        """Handle sync stopped signal."""
        self.update_button_states(syncing=False)

    def on_brightness_changed(self, mode, value):
        """Handle brightness changed signal."""
        # Update the slider if it was changed programmatically
        if mode == "monitor":
            self.monitor_brightness_slider.setValue(int(value * 100))
        elif mode == "music":
            self.music_brightness_slider.setValue(int(value * 100))

    def update_button_states(self, syncing):
        """Update button enable/disable states based on sync status."""
        if syncing:
            self.monitor_start_button.setEnabled(False)
            self.music_start_button.setEnabled(False)
            self.stop_button.setEnabled(True)
        else:
            self.monitor_start_button.setEnabled(True)
            self.music_start_button.setEnabled(True)
            self.stop_button.setEnabled(False)

    def update_sync_status(self):
        """Update the sync status display (called periodically)."""
        mode = self.controller.get_current_sync_mode()
        is_syncing = self.controller.is_syncing()

        if mode:
            self.mode_label.setText(mode.capitalize())
        else:
            self.mode_label.setText("None")

        if is_syncing:
            self.sync_status_label.setText("Active")
            self.sync_status_label.setStyleSheet("color: #2ECC71;")  # Green
        else:
            self.sync_status_label.setText("Idle")
            self.sync_status_label.setStyleSheet("color: #95A5A6;")  # Gray
