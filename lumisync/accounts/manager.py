"""Session ownership; settings contain metadata and references, never credentials."""

from __future__ import annotations

import threading
import uuid

from .errors import AccountError
from .secrets import protect_device, vault


def make_client(provider: str, credentials: dict):
    if provider == "govee_api":
        from .govee import GoveePlatformClient
        return GoveePlatformClient(credentials)
    if provider == "govee_account":
        from .govee import GoveeAccountClient
        return GoveeAccountClient(credentials)
    if provider == "tuya_project":
        from .tuya import TuyaOpenApiClient
        return TuyaOpenApiClient(credentials)
    if provider == "tuya_qr":
        from .tuya import TuyaSharingClient
        return TuyaSharingClient(credentials)
    if provider in ("tuya_account", "lsc_account"):
        from .tuya_mobile import TuyaMobileClient
        return TuyaMobileClient(provider.removesuffix("_account"), credentials)
    raise AccountError("Unknown account provider.", "unsupported")


class AccountManager:
    def __init__(self) -> None:
        self._clients: dict[str, object] = {}
        self._lock = threading.RLock()

    def register(self, provider: str, client, *, remember: bool = True, identity: str | None = None) -> dict:
        identity = identity or uuid.uuid4().hex
        reference = "account/" + identity
        vault.put(reference, {"provider": provider, "credentials": client.credentials, "remember": remember}, remember=remember)
        with self._lock:
            previous = self._clients.get(identity)
            if previous is not None and previous is not client:
                previous.close()
            self._clients[identity] = client
        client.credentials_updated = lambda credentials: vault.put(reference, {"provider": provider, "credentials": credentials, "remember": remember}, remember=remember)
        return {"id": identity, "provider": provider, "credential_ref": reference, "remember": remember}

    def client_for(self, device: dict):
        identity = device.get("account_id")
        if not identity:
            raise AccountError("Connect the device's vendor account from Devices → Accounts.", "authentication")
        with self._lock:
            if identity not in self._clients:
                saved = vault.get("account/" + identity)
                if not saved.get("credentials"):
                    raise AccountError("This account is disconnected. Sign in again from Devices → Accounts.", "authentication")
                client = make_client(saved["provider"], saved["credentials"])
                self.register(saved["provider"], client, identity=identity, remember=saved.get("remember", True))
            client = self._clients[identity]
            if device.get("device_id") and hasattr(client, "bind_device"):
                client.bind_device(device)
            return client

    def descriptors(self, metadata: dict, raw_devices: list[dict]) -> list[dict]:
        result = []
        for raw in raw_devices:
            device = protect_device(raw, remember=metadata["remember"])
            device["account_id"] = metadata["id"]
            result.append(device)
        return result

    def disconnect(self, identity: str) -> None:
        # Remove the durable token first so a failed vault operation is visible.
        vault.delete("account/" + identity)
        with self._lock:
            client = self._clients.pop(identity, None)
            if client is not None:
                client.close()

    def close_all(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except Exception:
                # Other accounts must still release their resources on shutdown.
                continue


account_manager = AccountManager()
