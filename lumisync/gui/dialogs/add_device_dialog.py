"""
Add Device Dialog for the LumiSync GUI.
This module provides a dialog for manually adding devices.
"""

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QFormLayout, QDialogButtonBox
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from ..utils.validators import IPAddressValidator, MACAddressValidator, PortValidator


class AddDeviceDialog(QDialog):
    """Dialog for manually adding a device."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Device Manually")
        self.setModal(True)
        self.setMinimumWidth(400)

        # Set up the UI
        self.setup_ui()

        # Center on parent
        if parent:
            self.move(
                parent.window().geometry().center() - self.rect().center()
            )

    def setup_ui(self):
        """Set up the user interface."""
        layout = QVBoxLayout(self)

        # Header
        header = QLabel("Add Device Manually")
        header_font = QFont()
        header_font.setPointSize(14)
        header_font.setBold(True)
        header.setFont(header_font)
        layout.addWidget(header)

        # Description
        desc = QLabel(
            "Enter the device information below. IP address is required.\n"
            "Other fields are optional and will be auto-generated if not provided."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addSpacing(10)

        # Form layout
        form = QFormLayout()

        # IP Address (required)
        self.ip_entry = QLineEdit()
        self.ip_entry.setPlaceholderText("e.g., 192.168.1.100")
        self.ip_entry.setValidator(IPAddressValidator())
        form.addRow("IP Address *:", self.ip_entry)

        # Device Name (optional)
        self.model_entry = QLineEdit()
        self.model_entry.setPlaceholderText("e.g., Govee H6199")
        form.addRow("Device Name:", self.model_entry)

        # MAC Address (optional)
        self.mac_entry = QLineEdit()
        self.mac_entry.setPlaceholderText("e.g., 00:11:22:33:44:55")
        self.mac_entry.setValidator(MACAddressValidator())
        form.addRow("MAC Address:", self.mac_entry)

        # Port (optional)
        self.port_entry = QLineEdit()
        self.port_entry.setPlaceholderText("Default: 4003")
        self.port_entry.setValidator(PortValidator())
        form.addRow("Port:", self.port_entry)

        layout.addLayout(form)

        # Status label
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        layout.addSpacing(10)

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.validate_and_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # Set focus to IP entry
        self.ip_entry.setFocus()

        # Connect Enter key to accept
        self.ip_entry.returnPressed.connect(self.validate_and_accept)
        self.model_entry.returnPressed.connect(self.validate_and_accept)
        self.mac_entry.returnPressed.connect(self.validate_and_accept)
        self.port_entry.returnPressed.connect(self.validate_and_accept)

    def validate_and_accept(self):
        """Validate input and accept dialog if valid."""
        # Clear previous status
        self.status_label.setText("")
        self.status_label.setStyleSheet("")

        # Check IP address (required)
        ip = self.ip_entry.text().strip()
        if not ip:
            self.show_error("IP address is required")
            self.ip_entry.setFocus()
            return

        # Validate IP format
        validator = self.ip_entry.validator()
        state, _, _ = validator.validate(ip, 0)
        if state != validator.State.Acceptable:
            self.show_error("Invalid IP address format")
            self.ip_entry.setFocus()
            return

        # Validate MAC if provided
        mac = self.mac_entry.text().strip()
        if mac:
            validator = self.mac_entry.validator()
            state, _, _ = validator.validate(mac, 0)
            if state != validator.State.Acceptable:
                self.show_error("Invalid MAC address format")
                self.mac_entry.setFocus()
                return

        # Validate port if provided
        port = self.port_entry.text().strip()
        if port:
            validator = self.port_entry.validator()
            state, _, _ = validator.validate(port, 0)
            if state != validator.State.Acceptable:
                self.show_error("Invalid port number (must be 1-65535)")
                self.port_entry.setFocus()
                return

        # All validation passed
        self.accept()

    def show_error(self, message: str):
        """Show error message in status label.

        Args:
            message: Error message to display
        """
        self.status_label.setText(f"❌ {message}")
        self.status_label.setStyleSheet("color: #E74C3C;")  # Red color

    def keyPressEvent(self, event):
        """Handle key press events.

        Args:
            event: Key event
        """
        # Handle Escape key to close dialog
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(event)
