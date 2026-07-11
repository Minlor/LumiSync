"""
Add Device Dialog for the LumiSync GUI.
This module provides a dialog for manually adding devices.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QComboBox,
    QLineEdit, QFormLayout, QDialogButtonBox
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from ..utils.validators import IPAddressValidator, MACAddressValidator, PortValidator
from ...drivers.idotmatrix_ble import KNOWN_SIZES


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

        # Device type selector
        type_form = QFormLayout()
        self.type_combo = QComboBox()
        self.type_combo.addItem("Govee (LAN / Wi-Fi)", "lan")
        self.type_combo.addItem("iDotMatrix (Bluetooth)", "ble")
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        type_form.addRow("Device type:", self.type_combo)
        layout.addLayout(type_form)

        # Form layout
        form = QFormLayout()

        # IP Address (required for LAN)
        self.ip_entry = QLineEdit()
        self.ip_entry.setPlaceholderText("e.g., 192.168.1.100")
        self.ip_entry.setValidator(IPAddressValidator())
        self.ip_row_label = QLabel("IP Address *:")
        form.addRow(self.ip_row_label, self.ip_entry)

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
        self.port_row_label = QLabel("Port:")
        form.addRow(self.port_row_label, self.port_entry)

        # --- BLE-only fields (hidden unless type == ble) ---
        self.ble_address_entry = QLineEdit()
        self.ble_address_entry.setPlaceholderText("e.g., AA:BB:CC:DD:EE:FF")
        self.ble_address_label = QLabel("Bluetooth address *:")
        form.addRow(self.ble_address_label, self.ble_address_entry)

        self.matrix_size_combo = QComboBox()
        for size in KNOWN_SIZES:
            self.matrix_size_combo.addItem(size, size)
        self.matrix_size_combo.setCurrentText("32x32")
        self.matrix_size_label = QLabel("Matrix size:")
        form.addRow(self.matrix_size_label, self.matrix_size_combo)

        layout.addLayout(form)
        self._on_type_changed()

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

    def device_type(self) -> str:
        return self.type_combo.currentData() or "lan"

    def matrix_size(self) -> str:
        return self.matrix_size_combo.currentData() or "32x32"

    def _on_type_changed(self, *_args) -> None:
        is_ble = self.device_type() == "ble"
        for widget in (self.ip_entry, self.ip_row_label, self.mac_entry,
                       self.port_entry, self.port_row_label):
            widget.setVisible(not is_ble)
        # MAC row label is managed by the form; toggle the field only.
        self.mac_entry.setVisible(not is_ble)
        for widget in (self.ble_address_entry, self.ble_address_label,
                       self.matrix_size_combo, self.matrix_size_label):
            widget.setVisible(is_ble)
        self.model_entry.setPlaceholderText(
            "e.g., iDotMatrix 32x32" if is_ble else "e.g., Govee H6199"
        )

    def validate_and_accept(self):
        """Validate input and accept dialog if valid."""
        # Clear previous status
        self.status_label.setText("")
        self.status_label.setStyleSheet("")

        if self.device_type() == "ble":
            if not self.ble_address_entry.text().strip():
                self.show_error("Bluetooth address is required")
                self.ble_address_entry.setFocus()
                return
            self.accept()
            return

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
        """Show error message in status label."""
        self.status_label.setText(f"⚠ {message}")
        self.status_label.setProperty("state", "error")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

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
