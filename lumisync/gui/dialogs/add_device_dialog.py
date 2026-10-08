"""
Add Device Dialog for the LumiSync GUI.
This module provides a dialog for manually adding devices.
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel,
    QLineEdit, QFormLayout, QDialogButtonBox, QCheckBox, QScrollArea, QWidget
)
from PySide6.QtCore import Qt

from ..utils.validators import IPAddressValidator, MACAddressValidator, PortValidator
from ...drivers.idotmatrix_ble import KNOWN_SIZES
from ..widgets.product_controls import ProductComboBox


class AddDeviceDialog(QDialog):
    """Dialog for manually adding a device."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Device")
        self.setObjectName("AddDeviceDialog")
        self.setModal(True)
        self.setMinimumWidth(400)

        # Set up the UI
        self.setup_ui()
        self.resize(560, min(620, max(400, self.screen().availableGeometry().height() - 72)))

        # Center on parent
        if parent:
            self.move(
                parent.window().geometry().center() - self.rect().center()
            )

    def setup_ui(self):
        """Set up the user interface."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        # Header
        header = QLabel("Add Device")
        header.setProperty("role", "title")
        layout.addWidget(header)

        # Description
        self.description_label = QLabel()
        self.description_label.setProperty("role", "pageDescription")
        self.description_label.setWordWrap(True)
        layout.addWidget(self.description_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        fields = QWidget()
        fields_layout = QVBoxLayout(fields)
        fields_layout.setContentsMargins(0, 0, 0, 0)
        fields_layout.setSpacing(12)
        scroll.setWidget(fields)
        layout.addWidget(scroll, 1)

        # Device type selector
        type_form = QFormLayout()
        self.type_combo = ProductComboBox()
        self.type_combo.addItem("Govee (LAN / Wi-Fi)", "lan")
        self.type_combo.addItem("iDotMatrix (Bluetooth)", "ble")
        self.type_combo.addItem("LSC / Tuya (Wi-Fi)", "tuya")
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        type_form.addRow("Device type:", self.type_combo)
        fields_layout.addLayout(type_form)

        # Form layout
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setSpacing(10)

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
        self.mac_row_label = QLabel("MAC Address:")
        form.addRow(self.mac_row_label, self.mac_entry)

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

        self.matrix_size_combo = ProductComboBox()
        for size in KNOWN_SIZES:
            self.matrix_size_combo.addItem(size, size)
        self.matrix_size_combo.setCurrentText("32x32")
        self.matrix_size_label = QLabel("Matrix size:")
        form.addRow(self.matrix_size_label, self.matrix_size_combo)

        # --- Tuya/LSC-only fields (hidden unless type == tuya) ---
        self.tuya_id_entry = QLineEdit()
        self.tuya_id_entry.setPlaceholderText("Device ID from your linked account")
        self.tuya_id_label = QLabel("Device ID *:")
        form.addRow(self.tuya_id_label, self.tuya_id_entry)

        self.tuya_key_entry = QLineEdit()
        self.tuya_key_entry.setEchoMode(QLineEdit.EchoMode.Password)
        self.tuya_key_entry.setPlaceholderText("16-char local key")
        self.tuya_key_label = QLabel("Local key *:")
        form.addRow(self.tuya_key_label, self.tuya_key_entry)

        self.tuya_version_combo = ProductComboBox()
        for ver in ("3.3", "3.1", "3.2", "3.4", "3.5"):
            self.tuya_version_combo.addItem(ver, ver)
        self.tuya_version_label = QLabel("Protocol version:")
        form.addRow(self.tuya_version_label, self.tuya_version_combo)
        self.tuya_schema_combo = ProductComboBox()
        self.tuya_schema_combo.addItem("Modern light (DP 20–24)", "v2")
        self.tuya_schema_combo.addItem("Legacy light (DP 1–5)", "v1")
        self.tuya_schema_label = QLabel("Light data points:")
        form.addRow(self.tuya_schema_label, self.tuya_schema_combo)
        self.remember_key = QCheckBox("Remember local key")
        self.remember_key.setToolTip("Save this local key in your system credential store.")
        self.remember_key.setChecked(True)
        form.addRow(self.remember_key)

        fields_layout.addLayout(form)
        fields_layout.addStretch(1)
        for control in (self.type_combo, self.ip_entry, self.model_entry, self.mac_entry, self.port_entry,
                        self.ble_address_entry, self.matrix_size_combo, self.tuya_id_entry, self.tuya_key_entry,
                        self.tuya_version_combo, self.tuya_schema_combo):
            label = form.labelForField(control) or type_form.labelForField(control)
            if isinstance(label, QLabel):
                label.setBuddy(control)
                control.setAccessibleName(label.text().rstrip(" *:"))
        self._on_type_changed()

        # Status label
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        layout.addWidget(self.status_label)

        layout.addSpacing(10)

        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.add_button = button_box.button(QDialogButtonBox.StandardButton.Ok)
        self.add_button.setText("Add Device")
        self.add_button.setObjectName("Primary")
        self.add_button.setDefault(True)
        button_box.button(QDialogButtonBox.StandardButton.Cancel).setAutoDefault(False)
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
        self.ble_address_entry.returnPressed.connect(self.validate_and_accept)
        self.tuya_id_entry.returnPressed.connect(self.validate_and_accept)
        self.tuya_key_entry.returnPressed.connect(self.validate_and_accept)

    def device_type(self) -> str:
        return self.type_combo.currentData() or "lan"

    def matrix_size(self) -> str:
        return self.matrix_size_combo.currentData() or "32x32"

    def tuya_device_id(self) -> str:
        return self.tuya_id_entry.text().strip()

    def tuya_local_key(self) -> str:
        return self.tuya_key_entry.text().strip()

    def tuya_version(self) -> str:
        return self.tuya_version_combo.currentData() or "3.3"

    def _on_type_changed(self, *_args) -> None:
        kind = self.device_type()
        is_ble = kind == "ble"
        is_tuya = kind == "tuya"
        is_lan = kind == "lan"

        # Govee LAN fields: IP + MAC + port.
        for widget in (self.ip_entry, self.ip_row_label):
            widget.setVisible(is_lan or is_tuya)  # IP is needed by Tuya too
        for widget in (
            self.mac_entry,
            self.mac_row_label,
            self.port_entry,
            self.port_row_label,
        ):
            widget.setVisible(is_lan)

        # iDotMatrix BLE fields.
        for widget in (self.ble_address_entry, self.ble_address_label,
                       self.matrix_size_combo, self.matrix_size_label):
            widget.setVisible(is_ble)

        # Tuya/LSC fields.
        for widget in (self.tuya_id_entry, self.tuya_id_label,
                       self.tuya_key_entry, self.tuya_key_label,
                       self.tuya_version_combo, self.tuya_version_label,
                       self.tuya_schema_combo, self.tuya_schema_label, self.remember_key):
            widget.setVisible(is_tuya)

        placeholder = {
            "ble": "e.g., iDotMatrix 32x32",
            "tuya": "e.g., LSC Smart Bulb",
        }.get(kind, "e.g., Govee H6199")
        self.model_entry.setPlaceholderText(placeholder)

        descriptions = {
            "ble": "Enter the Bluetooth address printed by or reported for your matrix panel.",
            "tuya": "Enter the local connection details for your LSC or Tuya light.",
            "lan": "Enter the light's local IP address. The name, MAC address, and port are optional.",
        }
        self.description_label.setText(descriptions[kind])

        if is_ble:
            self.ble_address_entry.setFocus()
        else:
            self.ip_entry.setFocus()

    def validate_and_accept(self):
        """Validate input and accept dialog if valid."""
        # Clear previous status
        self.status_label.setText("")
        self.status_label.hide()
        self.status_label.setProperty("state", "")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

        if self.device_type() == "ble":
            if not self.ble_address_entry.text().strip():
                self.show_error("Bluetooth address is required")
                self.ble_address_entry.setFocus()
                return
            self.accept()
            return

        if self.device_type() == "tuya":
            if not self.ip_entry.text().strip():
                self.show_error("IP address is required")
                self.ip_entry.setFocus()
                return
            if not self.ip_entry.hasAcceptableInput():
                self.show_error("Enter a valid IPv4 address")
                return
            if not self.tuya_device_id():
                self.show_error("Enter the device ID from your Tuya or LSC account.")
                self.tuya_id_entry.setFocus()
                return
            if not self.tuya_local_key():
                self.show_error("Enter this device's local key.")
                self.tuya_key_entry.setFocus()
                return
            if len(self.tuya_local_key().encode("utf-8")) != 16:
                self.show_error("The local key must contain exactly 16 bytes")
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
        self.status_label.setText(message)
        self.status_label.show()
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
