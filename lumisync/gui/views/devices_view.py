"""
Devices view for the LumiSync GUI.
This module provides the device management interface.
"""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QListWidget, QGroupBox, QGridLayout,
    QMessageBox, QSizePolicy, QColorDialog
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont

from ..resources import ResourceManager
from ..controllers.device_controller import DeviceController
from ..dialogs.add_device_dialog import AddDeviceDialog


class DevicesView(QWidget):
    """View for managing devices."""

    def __init__(self, device_controller: DeviceController):
        super().__init__()
        self.controller = device_controller

        # Set up the UI
        self.setup_ui()

        # Connect controller signals to view updates
        self.connect_signals()

        # Initial update
        self.update_device_list()

    def setup_ui(self):
        """Set up the user interface."""
        # Main layout
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # Header
        header = QLabel("Device Management")
        header_font = QFont()
        header_font.setPointSize(16)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)

        # Action buttons
        button_layout = QHBoxLayout()

        self.discover_button = QPushButton("Discover Devices")
        self.discover_button.setIcon(ResourceManager.get_icon("refresh.svg"))
        self.discover_button.setIconSize(QSize(20, 20))
        self.discover_button.clicked.connect(self.on_discover_clicked)
        button_layout.addWidget(self.discover_button)

        self.add_button = QPushButton("Add Manual")
        self.add_button.setIcon(ResourceManager.get_icon("network.svg"))
        self.add_button.setIconSize(QSize(20, 20))
        self.add_button.clicked.connect(self.on_add_manual_clicked)
        button_layout.addWidget(self.add_button)

        self.remove_button = QPushButton("Remove")
        self.remove_button.setEnabled(False)
        self.remove_button.clicked.connect(self.on_remove_clicked)
        button_layout.addWidget(self.remove_button)

        self.turn_on_button = QPushButton("Turn On")
        self.turn_on_button.setIcon(ResourceManager.get_icon("power.svg"))
        self.turn_on_button.setIconSize(QSize(20, 20))
        self.turn_on_button.setEnabled(False)
        self.turn_on_button.clicked.connect(lambda: self.controller.turn_on_off(True))
        button_layout.addWidget(self.turn_on_button)

        self.turn_off_button = QPushButton("Turn Off")
        self.turn_off_button.setIcon(ResourceManager.get_icon("power.svg"))
        self.turn_off_button.setIconSize(QSize(20, 20))
        self.turn_off_button.setEnabled(False)
        self.turn_off_button.clicked.connect(lambda: self.controller.turn_on_off(False))
        button_layout.addWidget(self.turn_off_button)

        self.set_color_button = QPushButton("Set Color")
        self.set_color_button.setIcon(ResourceManager.get_icon("lightbulb-on.svg"))
        self.set_color_button.setIconSize(QSize(20, 20))
        self.set_color_button.setEnabled(False)
        self.set_color_button.clicked.connect(self.on_set_color_clicked)
        button_layout.addWidget(self.set_color_button)

        button_layout.addStretch()
        layout.addLayout(button_layout)

        # Content area with device list and details
        content_layout = QHBoxLayout()

        # Device list
        list_group = QGroupBox("Devices")
        list_layout = QVBoxLayout(list_group)

        self.device_list = QListWidget()
        self.device_list.currentItemChanged.connect(self.on_device_selection_changed)
        list_layout.addWidget(self.device_list)

        content_layout.addWidget(list_group, 1)

        # Device details
        details_group = QGroupBox("Device Details")
        details_layout = QGridLayout(details_group)

        # Detail labels
        details_layout.addWidget(QLabel("Model:"), 0, 0)
        self.model_label = QLabel("N/A")
        details_layout.addWidget(self.model_label, 0, 1)

        details_layout.addWidget(QLabel("MAC:"), 1, 0)
        self.mac_label = QLabel("N/A")
        details_layout.addWidget(self.mac_label, 1, 1)

        details_layout.addWidget(QLabel("IP:"), 2, 0)
        self.ip_label = QLabel("N/A")
        details_layout.addWidget(self.ip_label, 2, 1)

        details_layout.addWidget(QLabel("Port:"), 3, 0)
        self.port_label = QLabel("N/A")
        details_layout.addWidget(self.port_label, 3, 1)

        details_layout.addWidget(QLabel("Source:"), 4, 0)
        self.source_label = QLabel("N/A")
        details_layout.addWidget(self.source_label, 4, 1)

        details_layout.setRowStretch(5, 1)  # Add stretch at bottom

        content_layout.addWidget(details_group, 1)

        layout.addLayout(content_layout, 1)

    def connect_signals(self):
        """Connect controller signals to view updates."""
        self.controller.devices_discovered.connect(self.on_devices_discovered)
        self.controller.device_selected.connect(self.on_device_selected)
        self.controller.discovery_started.connect(self.on_discovery_started)
        self.controller.discovery_finished.connect(self.on_discovery_finished)

    def on_discover_clicked(self):
        """Handle discover button click."""
        self.controller.discover_devices()

    def on_add_manual_clicked(self):
        """Handle add manual button click."""
        dialog = AddDeviceDialog(self)
        if dialog.exec():
            # Dialog was accepted, get values
            ip = dialog.ip_entry.text()
            model = dialog.model_entry.text() or "Manual Device"
            mac = dialog.mac_entry.text() or None
            port = int(dialog.port_entry.text()) if dialog.port_entry.text() else 4003

            # Add device
            self.controller.add_device_manually(ip, model, mac, port)

    def on_remove_clicked(self):
        """Handle remove button click."""
        current_row = self.device_list.currentRow()
        if current_row < 0:
            return

        # Get device info for confirmation
        device = self.controller.devices[current_row]
        reply = QMessageBox.question(
            self,
            "Confirm Removal",
            f"Are you sure you want to remove '{device.get('model', 'Unknown')}' ({device.get('ip', 'Unknown')})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.controller.remove_device(current_row)

    def on_set_color_clicked(self):
        """Handle set color button click."""
        color = QColorDialog.getColor()
        if color.isValid():
            self.controller.set_device_color(color.red(), color.green(), color.blue())

    def on_device_selection_changed(self, current, previous):
        """Handle device list selection change."""
        if current:
            index = self.device_list.row(current)
            self.controller.select_device(index)

            # Enable/disable buttons
            self.remove_button.setEnabled(True)
            self.turn_on_button.setEnabled(True)
            self.turn_off_button.setEnabled(True)
            self.set_color_button.setEnabled(True)
        else:
            self.remove_button.setEnabled(False)
            self.turn_on_button.setEnabled(False)
            self.turn_off_button.setEnabled(False)
            self.set_color_button.setEnabled(False)

    def on_devices_discovered(self, devices):
        """Handle devices discovered signal."""
        self.update_device_list()

    def on_device_selected(self, device):
        """Handle device selected signal."""
        self.update_device_details(device)

    def on_discovery_started(self):
        """Handle discovery started signal."""
        self.discover_button.setEnabled(False)
        self.discover_button.setText("Discovering...")

    def on_discovery_finished(self):
        """Handle discovery finished signal."""
        self.discover_button.setEnabled(True)
        self.discover_button.setText("Discover Devices")

    def update_device_list(self):
        """Update the device list widget."""
        self.device_list.clear()

        for device in self.controller.devices:
            model = device.get('model', 'Unknown')
            ip = device.get('ip', 'Unknown')
            item_text = f"{model} ({ip})"
            self.device_list.addItem(item_text)

        # Select the current device if any
        if self.controller.devices and 0 <= self.controller.selected_device_index < len(self.controller.devices):
            self.device_list.setCurrentRow(self.controller.selected_device_index)

    def update_device_details(self, device):
        """Update the device details panel.

        Args:
            device: Device dictionary
        """
        if device:
            self.model_label.setText(device.get('model', 'Unknown'))
            self.mac_label.setText(device.get('mac', 'Unknown'))
            self.ip_label.setText(device.get('ip', 'Unknown'))
            self.port_label.setText(str(device.get('port', 'Unknown')))

            source = "Manual" if device.get('manual', False) else "Discovered"
            self.source_label.setText(source)
        else:
            self.model_label.setText("N/A")
            self.mac_label.setText("N/A")
            self.ip_label.setText("N/A")
            self.port_label.setText("N/A")
            self.source_label.setText("N/A")
