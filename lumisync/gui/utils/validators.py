"""
Input validators for the LumiSync GUI.
This module provides QValidator subclasses for validating user input.
"""

import re
from PyQt6.QtGui import QValidator


class IPAddressValidator(QValidator):
    """Validator for IP addresses in the format XXX.XXX.XXX.XXX"""

    def validate(self, text: str, pos: int) -> tuple:
        """Validate IP address input.

        Args:
            text: Input text to validate
            pos: Cursor position

        Returns:
            Tuple of (State, text, pos)
        """
        if not text:
            return (QValidator.State.Intermediate, text, pos)

        # Allow incomplete input during typing
        if re.match(r'^(\d{0,3}\.?){0,4}$', text):
            # Check if complete and valid
            parts = text.split('.')
            if len(parts) == 4 and all(parts):
                try:
                    if all(0 <= int(p) <= 255 for p in parts):
                        return (QValidator.State.Acceptable, text, pos)
                except ValueError:
                    pass
            return (QValidator.State.Intermediate, text, pos)

        return (QValidator.State.Invalid, text, pos)


class MACAddressValidator(QValidator):
    """Validator for MAC addresses in the format XX:XX:XX:XX:XX:XX or XX-XX-XX-XX-XX-XX"""

    def validate(self, text: str, pos: int) -> tuple:
        """Validate MAC address input.

        Args:
            text: Input text to validate
            pos: Cursor position

        Returns:
            Tuple of (State, text, pos)
        """
        if not text:
            return (QValidator.State.Intermediate, text, pos)

        # Allow formats: XX:XX:XX:XX:XX:XX or XX-XX-XX-XX-XX-XX
        # Allow incomplete input during typing
        pattern = r'^([0-9A-Fa-f]{0,2}[:-]?){0,6}$'
        if re.match(pattern, text):
            # Check if complete and valid
            complete_pattern = r'^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$'
            if re.match(complete_pattern, text):
                return (QValidator.State.Acceptable, text, pos)
            return (QValidator.State.Intermediate, text, pos)

        return (QValidator.State.Invalid, text, pos)


class PortValidator(QValidator):
    """Validator for port numbers (1-65535)"""

    def validate(self, text: str, pos: int) -> tuple:
        """Validate port number input.

        Args:
            text: Input text to validate
            pos: Cursor position

        Returns:
            Tuple of (State, text, pos)
        """
        if not text:
            return (QValidator.State.Intermediate, text, pos)

        try:
            port = int(text)
            if 1 <= port <= 65535:
                return (QValidator.State.Acceptable, text, pos)
            # Allow intermediate state for partial input like "1" which could become "1000"
            if 0 <= port < 65536 and len(text) < 5:
                return (QValidator.State.Intermediate, text, pos)
            return (QValidator.State.Invalid, text, pos)
        except ValueError:
            return (QValidator.State.Invalid, text, pos)


__all__ = ['IPAddressValidator', 'MACAddressValidator', 'PortValidator']
