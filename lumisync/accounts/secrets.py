"""Credential references backed by the OS vault, with an explicit session mode."""

from __future__ import annotations

import json
import copy
import threading
from collections.abc import Mapping
from typing import Any

from ..device import as_device
from .errors import AccountError


def _logger():
    from ..utils.logging import get_logger

    return get_logger(__name__)


class CredentialVault:
    def __init__(self) -> None:
        self._session: dict[str, dict[str, Any]] = {}
        self._session_only: set[str] = set()
        self._lock = threading.RLock()

    @staticmethod
    def _backend():
        import keyring

        backend = keyring.get_keyring()
        # A chained backend may include keyrings.alt's plaintext file fallback.
        # Select an OS credential backend directly instead of trusting the chain.
        candidates = getattr(backend, "backends", (backend,))
        secure_modules = ("keyring.backends.Windows", "keyring.backends.macOS",
                          "keyring.backends.SecretService", "keyring.backends.kwallet",
                          "keyring.backends.libsecret")
        for candidate in candidates:
            if type(candidate).__module__.startswith(secure_modules) and candidate.priority > 0:
                return candidate
        raise AccountError(
            "Secure credential storage is unavailable. Disable Remember connection "
            "to use this session, or enable your system's credential service.",
            "credential_store",
        )

    def put(self, key: str, value: dict[str, Any], *, remember: bool = True) -> None:
        with self._lock:
            if remember:
                try:
                    self._backend().set_password("LumiSync", key, json.dumps(value))
                except AccountError:
                    raise
                except Exception:
                    raise AccountError("Could not save the connection in the system credential store.", "credential_store") from None
                self._session_only.discard(key)
            else:
                self._session_only.add(key)
            self._session[key] = copy.deepcopy(value)

    def get(self, key: str) -> dict[str, Any]:
        with self._lock:
            if key in self._session:
                return copy.deepcopy(self._session[key])
            try:
                raw = self._backend().get_password("LumiSync", key)
                value = json.loads(raw) if raw else {}
            except AccountError:
                raise
            except Exception:
                raise AccountError("Could not read the saved connection. Please connect the account again.", "credential_store") from None
            return value if isinstance(value, dict) else {}

    def delete(self, key: str) -> None:
        with self._lock:
            if key in self._session_only:
                self._session.pop(key, None)
                self._session_only.discard(key)
                return
            try:
                backend = self._backend()
            except AccountError:
                # Without an OS credential store nothing durable was ever saved
                # under this key (put() refuses remember=True then), so there is
                # nothing to remove. Disconnecting must still succeed.
                _logger().info("Credential store unavailable; no saved credential to remove")
                self._session.pop(key, None)
                return
            try:
                if backend.get_password("LumiSync", key) is not None:
                    backend.delete_password("LumiSync", key)
            except Exception:
                raise AccountError("Could not remove the saved credential from the system store.", "credential_store") from None
            self._session.pop(key, None)


vault = CredentialVault()


def local_key_for(device: Mapping[str, Any]) -> str:
    # Legacy plaintext descriptors still work; the next settings write migrates them.
    typed = as_device(device)
    key = typed.get("local_key")
    if key:
        return str(key)
    reference = typed.get("local_key_ref")
    return str(vault.get(str(reference)).get("local_key", "")) if reference else ""


def replace_local_key(device: Mapping[str, Any], key: str) -> bool:
    """Store a rotated local key under the device's existing vault reference.

    The reference keeps its storage mode (system vault, or this session only),
    so a refreshed key is never kept somewhere the original was not. Returns
    False when the descriptor has no reference to update (a legacy plaintext
    descriptor); raises :class:`AccountError` when the vault refuses the write.
    """
    reference = as_device(device).get("local_key_ref")
    if not reference or not key:
        return False
    reference = str(reference)
    with vault._lock:
        remember = reference not in vault._session_only
        try:
            stored = vault.get(reference)
        except AccountError:
            stored = {}
        stored["local_key"] = str(key)
        vault.put(reference, stored, remember=remember)
    return True


def protect_device(device: Mapping[str, Any], *, remember: bool = True) -> dict[str, Any]:
    result = as_device(device).to_dict()
    key = result.pop("local_key", None)
    if key:
        identity = result.get("device_id")
        if not identity:
            raise AccountError("A device ID is required before saving a local key.", "validation")
        reference = f"tuya-local/{identity}"
        vault.put(reference, {"local_key": str(key)}, remember=remember)
        result["local_key_ref"] = reference
    return result
