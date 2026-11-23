"""Public keys that LumiSync releases are signed with.

Each entry is the base64 of a raw 32-byte Ed25519 public key. The in-app
updater only installs a release whose checksum manifest is signed by one of
these keys; while the tuple is empty, signatures are not checked.

Fill it with ``uv run python tools/update_signing.py keygen --private-key-out
PATH --write-public``. More than one key may be listed so a new key can be
added one release before releases are signed with it (key rotation).
"""

from __future__ import annotations

UPDATE_PUBLIC_KEYS: tuple[str, ...] = (
    "AFRDnGJ9+4TzY0tW+0rDRA/IVDgDjPg69NcWyqnqh7c=",
)
