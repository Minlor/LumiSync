"""Credential references backed by the OS vault, with an explicit session mode.

Values are stored as JSON under the service ``LumiSync``. A value longer than
:data:`CHUNK_SIZE` characters is split across numbered part entries
(``<key>#part<n>``) and the entry under ``<key>`` itself becomes a small
header with the part count, the total length and a SHA-256 of the whole text.
The header is written last and checked on every read, so a write that was cut
short never reads back as some other valid value; it reads as nothing saved.
Windows caps one credential at 2560 bytes (1280 characters, stored as UTF-16),
which an account's token alone can approach.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections.abc import Mapping
from typing import Any

from ..device import as_device
from .errors import AccountError

SERVICE = "LumiSync"
# Well under every backend's limit (Windows: 1280 characters).
CHUNK_SIZE = 1000
# Bounds a part count read from a header and the probe for leftover parts.
MAX_PARTS = 64
_HEADER_MARK = "__lumisync_vault__"
_KINDS = ("account", "vendor-app", "tuya-local")


def _logger():
    from ..utils.logging import get_logger

    return get_logger(__name__)


def _kind(key: str) -> str:
    """The key's category, safe to log; the rest of the key is not logged."""
    prefix = key.split("/", 1)[0]
    return prefix if prefix in _KINDS else "other"


def _part_key(key: str, index: int) -> str:
    return f"{key}#part{index}"


def _error_detail(exc: BaseException) -> str:
    """Type, numeric code and failing function of a store error; never its text."""
    detail = type(exc).__name__
    code = getattr(exc, "winerror", None)
    if not isinstance(code, int):
        code = getattr(exc, "errno", None)
    if isinstance(code, int):
        detail += f" code={code}"
    function = getattr(exc, "funcname", None)
    if isinstance(function, str) and function.isidentifier() and len(function) <= 64:
        detail += f" function={function}"
    return detail


def _log_failure(action: str, key: str, exc: BaseException, length: int | None = None) -> None:
    _logger().warning(
        "Credential store %s failed: kind=%s length=%s error=%s",
        action, _kind(key), "-" if length is None else length, _error_detail(exc),
    )


def _header_parts(raw: str | None) -> int:
    """Part count of a chunked header, 0 for a plain value or nothing."""
    if not isinstance(raw, str) or not raw:
        return 0
    try:
        value = json.loads(raw)
    except ValueError:
        return 0
    if not isinstance(value, dict) or value.get(_HEADER_MARK) != 1:
        return 0
    parts = value.get("parts")
    return parts if isinstance(parts, int) and 0 < parts <= MAX_PARTS else 0


class CredentialVault:
    def __init__(self) -> None:
        self._session: dict[str, dict[str, Any]] = {}
        self._session_only: set[str] = set()
        # Asked to be remembered, but the system store refused: kept for this
        # session, and an older saved copy may still exist under the key.
        self._unsaved: set[str] = set()
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
                text = json.dumps(value)
                try:
                    backend = self._backend()
                except AccountError as exc:
                    _log_failure("save", key, exc, len(text))
                    raise
                try:
                    self._write(backend, key, text)
                except Exception as exc:
                    _log_failure("save", key, exc, len(text))
                    raise AccountError("Could not save the connection in the system credential store.", "credential_store") from None
                self._session_only.discard(key)
                self._unsaved.discard(key)
            else:
                self._session_only.add(key)
            self._session[key] = copy.deepcopy(value)

    def put_or_keep(self, key: str, value: dict[str, Any]) -> bool:
        """Save *value* durably; if the system store refuses, keep it for this session.

        Returns True when it was saved, False when it is held in memory only
        (the failure is logged). A sign-in the vendor already accepted is then
        still usable until LumiSync closes.
        """
        with self._lock:
            try:
                self.put(key, value, remember=True)
                return True
            except AccountError as exc:
                if exc.code != "credential_store":
                    raise
            self._session[key] = copy.deepcopy(value)
            self._session_only.add(key)
            self._unsaved.add(key)
            _logger().warning("Kept a %s credential for this session only; the system store did not save it", _kind(key))
            return False

    def is_session_only(self, key: str) -> bool:
        """Whether *key* is held in memory only (not in the system store)."""
        with self._lock:
            return key in self._session_only

    def get(self, key: str) -> dict[str, Any]:
        with self._lock:
            if key in self._session:
                return copy.deepcopy(self._session[key])
            try:
                backend = self._backend()
            except AccountError as exc:
                _log_failure("read", key, exc)
                raise
            try:
                value = self._read(backend, key)
            except Exception as exc:
                _log_failure("read", key, exc)
                raise AccountError("Could not read the saved connection. Please connect the account again.", "credential_store") from None
            return value if isinstance(value, dict) else {}

    def delete(self, key: str) -> None:
        with self._lock:
            if key in self._session_only:
                self._session.pop(key, None)
                self._session_only.discard(key)
                if key in self._unsaved:
                    # A copy saved before the store started refusing may remain.
                    self._unsaved.discard(key)
                    self.discard_durable(key)
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
                self._remove(backend, key)
            except Exception as exc:
                _log_failure("delete", key, exc)
                raise AccountError("Could not remove the saved credential from the system store.", "credential_store") from None
            self._session.pop(key, None)

    def discard_durable(self, key: str) -> None:
        """Best effort: remove any copy of *key* in the system store; never raises."""
        with self._lock:
            try:
                backend = self._backend()
            except AccountError:
                return
            try:
                self._remove(backend, key)
            except Exception as exc:
                _log_failure("delete", key, exc)

    # Storage format -----------------------------------------------------

    def _write(self, backend, key: str, text: str) -> None:
        stale = _header_parts(backend.get_password(SERVICE, key))
        if len(text) <= CHUNK_SIZE:
            backend.set_password(SERVICE, key, text)
            parts = 0
        else:
            pieces = [text[start:start + CHUNK_SIZE] for start in range(0, len(text), CHUNK_SIZE)]
            if len(pieces) > MAX_PARTS:
                raise ValueError("credential value too large")
            for index, piece in enumerate(pieces):
                backend.set_password(SERVICE, _part_key(key, index), piece)
            # Written last: until now the old header (if any) no longer
            # matches the parts, so a cut-short write reads as nothing saved.
            backend.set_password(SERVICE, key, json.dumps({
                _HEADER_MARK: 1,
                "parts": len(pieces),
                "length": len(text),
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }))
            parts = len(pieces)
        try:
            self._remove_parts(backend, key, parts, stale)
        except Exception as exc:
            # The new value is saved; a leftover part is harmless and the next
            # save or delete of this key removes it.
            _log_failure("cleanup", key, exc, len(text))

    def _read(self, backend, key: str) -> Any:
        raw = backend.get_password(SERVICE, key)
        if not raw:
            return {}
        value = json.loads(raw)
        if not (isinstance(value, dict) and value.get(_HEADER_MARK) == 1):
            return value
        parts = _header_parts(raw)
        pieces = [backend.get_password(SERVICE, _part_key(key, index)) for index in range(parts)]
        text = "".join(piece for piece in pieces if isinstance(piece, str))
        complete = (
            parts > 0
            and all(isinstance(piece, str) for piece in pieces)
            and len(text) == value.get("length")
            and hashlib.sha256(text.encode("utf-8")).hexdigest() == value.get("sha256")
        )
        if not complete:
            _logger().warning("Saved %s credential is incomplete (parts=%d); treating it as not saved", _kind(key), parts)
            return {}
        return json.loads(text)

    def _remove(self, backend, key: str) -> None:
        raw = backend.get_password(SERVICE, key)
        if raw is not None:
            # The header goes first: from here on the value reads as nothing.
            backend.delete_password(SERVICE, key)
        self._remove_parts(backend, key, 0, _header_parts(raw))

    @staticmethod
    def _remove_parts(backend, key: str, start: int, known: int) -> None:
        """Delete parts from *start*: all *known* ones, then any left after them."""
        for index in range(start, MAX_PARTS):
            part = _part_key(key, index)
            if backend.get_password(SERVICE, part) is not None:
                backend.delete_password(SERVICE, part)
            elif index >= known:
                break


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


def protect_device(device: Mapping[str, Any], *, remember: bool = True,
                   keep_for_session: bool = False) -> dict[str, Any]:
    """Move a plaintext local key into the vault and return the descriptor.

    With *keep_for_session*, a key the system store refuses is held for this
    session instead of failing (an account sign-in that already succeeded).
    Without it the error is raised, so a migration keeps the plaintext key.
    """
    result = as_device(device).to_dict()
    key = result.pop("local_key", None)
    if key:
        identity = result.get("device_id")
        if not identity:
            raise AccountError("A device ID is required before saving a local key.", "validation")
        reference = f"tuya-local/{identity}"
        if remember and keep_for_session:
            vault.put_or_keep(reference, {"local_key": str(key)})
        else:
            vault.put(reference, {"local_key": str(key)}, remember=remember)
        result["local_key_ref"] = reference
    return result
