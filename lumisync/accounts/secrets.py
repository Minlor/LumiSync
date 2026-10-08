"""Credential references backed by the OS vault, with an explicit session mode."""

from __future__ import annotations

import json
import copy
import threading
from typing import Any

from .errors import AccountError


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
                if backend.get_password("LumiSync", key) is not None:
                    backend.delete_password("LumiSync", key)
            except AccountError:
                raise
            except Exception:
                raise AccountError("Could not remove the saved credential from the system store.", "credential_store") from None
            self._session.pop(key, None)


vault = CredentialVault()


def local_key_for(device: dict[str, Any]) -> str:
    # Legacy plaintext descriptors still work; the next settings write migrates them.
    key = device.get("local_key") or device.get("localKey")
    if key:
        return str(key)
    reference = device.get("local_key_ref")
    return str(vault.get(str(reference)).get("local_key", "")) if reference else ""


def protect_device(device: dict[str, Any], *, remember: bool = True) -> dict[str, Any]:
    result = dict(device)
    key = result.pop("local_key", None) or result.pop("localKey", None)
    result.pop("localKey", None)
    if key:
        identity = result.get("device_id") or result.get("devId")
        if not identity:
            raise AccountError("A device ID is required before saving a local key.", "validation")
        reference = f"tuya-local/{identity}"
        vault.put(reference, {"local_key": str(key)}, remember=remember)
        result["local_key_ref"] = reference
    return result
