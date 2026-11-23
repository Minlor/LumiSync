"""Sign and verify LumiSync release checksum manifests with Ed25519.

The in-app updater only installs a release whose checksum manifest carries a
valid ``<manifest>.sig`` from a key pinned in ``lumisync/update_keys.py``.

    uv run python tools/update_signing.py keygen --private-key-out PATH [--write-public]
    uv run python tools/update_signing.py sign --manifest FILE --version VERSION [--out FILE.sig]
    uv run python tools/update_signing.py verify --manifest FILE --signature FILE.sig --version VERSION

``sign`` reads the private key (base64 of the 32-byte seed) only from the
``LUMISYNC_UPDATE_SIGNING_KEY`` environment variable, never from arguments.
"""

from __future__ import annotations

import argparse
import ast
import base64
import binascii
import os
from pathlib import Path
import re
import sys
from typing import Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)
from packaging.version import InvalidVersion  # noqa: E402

from lumisync.updates import (  # noqa: E402
    SIGNATURE_SUFFIX,
    release_signing_version,
    signed_message,
    verify_manifest_signature,
)

SIGNING_KEY_ENV = "LUMISYNC_UPDATE_SIGNING_KEY"
KEYS_FILE = REPO_ROOT / "lumisync" / "update_keys.py"
_KEYS_PATTERN = re.compile(
    r"UPDATE_PUBLIC_KEYS: tuple\[str, \.\.\.\] = (\(.*?\))", re.DOTALL
)


class SigningError(Exception):
    """A user-facing failure; the message is printed and the tool exits 1."""


def _raw_public_key(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def _raw_seed(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def read_pinned_keys(keys_file: Path = KEYS_FILE) -> tuple[str, ...]:
    """Read ``UPDATE_PUBLIC_KEYS`` from the keys module without importing it."""
    text = keys_file.read_text(encoding="utf-8")
    match = _KEYS_PATTERN.search(text)
    if match is None:
        raise SigningError(f"Could not find UPDATE_PUBLIC_KEYS in {keys_file}")
    try:
        value = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError) as exc:
        raise SigningError(f"UPDATE_PUBLIC_KEYS in {keys_file} is not a literal tuple") from exc
    if not isinstance(value, tuple) or not all(isinstance(key, str) for key in value):
        raise SigningError(f"UPDATE_PUBLIC_KEYS in {keys_file} must be a tuple of strings")
    return value


def write_pinned_keys(keys: Sequence[str], keys_file: Path = KEYS_FILE) -> None:
    """Replace ``UPDATE_PUBLIC_KEYS`` in the keys module with ``keys``."""
    text = keys_file.read_text(encoding="utf-8")
    if _KEYS_PATTERN.search(text) is None:
        raise SigningError(f"Could not find UPDATE_PUBLIC_KEYS in {keys_file}")
    body = "".join(f'    "{key}",\n' for key in keys)
    replacement = f"UPDATE_PUBLIC_KEYS: tuple[str, ...] = (\n{body})" if keys else (
        "UPDATE_PUBLIC_KEYS: tuple[str, ...] = ()"
    )
    keys_file.write_text(
        _KEYS_PATTERN.sub(lambda _match: replacement, text, count=1),
        encoding="utf-8",
    )


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _write_private_key(path: Path, data: bytes) -> None:
    # O_EXCL refuses an existing file; 0o600 is owner-only where the OS
    # honours POSIX modes (on Windows the file inherits the folder's ACL).
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


def keygen(
    private_key_out: Path,
    *,
    write_public: bool = False,
    keys_file: Path = KEYS_FILE,
    repo_root: Path = REPO_ROOT,
) -> str:
    """Create a key pair; returns the base64 public key."""
    target = Path(os.path.abspath(private_key_out))
    if _is_inside(target.resolve(), repo_root.resolve()):
        raise SigningError(
            f"Refusing to write the private key inside the git work tree ({repo_root}). "
            "Choose a path outside the repository."
        )
    if target.exists():
        raise SigningError(f"Refusing to overwrite existing file {target}")
    if not target.parent.is_dir():
        raise SigningError(f"Folder does not exist: {target.parent}")

    private_key = Ed25519PrivateKey.generate()
    public_b64 = base64.b64encode(_raw_public_key(private_key)).decode("ascii")
    seed_b64 = base64.b64encode(_raw_seed(private_key)).decode("ascii")
    try:
        _write_private_key(target, (seed_b64 + "\n").encode("ascii"))
    except FileExistsError as exc:
        raise SigningError(f"Refusing to overwrite existing file {target}") from exc

    if write_public:
        keys = read_pinned_keys(keys_file)
        if public_b64 not in keys:
            write_pinned_keys((*keys, public_b64), keys_file)
    return public_b64


def load_signing_key(environ: Optional[dict[str, str]] = None) -> Ed25519PrivateKey:
    env = os.environ if environ is None else environ
    encoded = (env.get(SIGNING_KEY_ENV) or "").strip()
    if not encoded:
        raise SigningError(
            f"{SIGNING_KEY_ENV} is not set. Put the base64 private key in that "
            "environment variable (never on the command line)."
        )
    try:
        seed = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SigningError(f"{SIGNING_KEY_ENV} is not valid base64.") from exc
    if len(seed) != 32:
        raise SigningError(
            f"{SIGNING_KEY_ENV} must hold a 32-byte Ed25519 seed in base64."
        )
    return Ed25519PrivateKey.from_private_bytes(seed)


def _signing_version(version: str) -> str:
    try:
        return release_signing_version(version)
    except InvalidVersion as exc:
        raise SigningError(f"Not a valid release version: {version!r}") from exc


def sign(
    manifest: Path,
    version: str,
    out: Optional[Path] = None,
    *,
    environ: Optional[dict[str, str]] = None,
) -> Path:
    private_key = load_signing_key(environ)
    normalized = _signing_version(version)
    try:
        manifest_bytes = manifest.read_bytes()
    except OSError as exc:
        raise SigningError(f"Could not read manifest {manifest}: {exc}") from exc
    signature = private_key.sign(signed_message(normalized, manifest_bytes))
    target = out or manifest.with_name(manifest.name + SIGNATURE_SUFFIX)
    target.write_bytes(base64.b64encode(signature) + b"\n")
    return target


def verify(
    manifest: Path,
    signature: Path,
    version: str,
    *,
    keys_file: Path = KEYS_FILE,
) -> bool:
    keys = read_pinned_keys(keys_file)
    if not keys:
        raise SigningError(f"No public key is pinned in {keys_file}.")
    normalized = _signing_version(version)
    try:
        manifest_bytes = manifest.read_bytes()
        signature_bytes = signature.read_bytes()
    except OSError as exc:
        raise SigningError(f"Could not read file: {exc}") from exc
    return verify_manifest_signature(manifest_bytes, signature_bytes, normalized, keys)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sign and verify LumiSync release checksum manifests."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    keygen_cmd = commands.add_parser("keygen", help="Create a new signing key pair.")
    keygen_cmd.add_argument("--private-key-out", required=True, type=Path)
    keygen_cmd.add_argument(
        "--write-public",
        action="store_true",
        help="Append the public key to UPDATE_PUBLIC_KEYS in lumisync/update_keys.py.",
    )
    keygen_cmd.add_argument("--keys-file", type=Path, default=KEYS_FILE, help=argparse.SUPPRESS)

    sign_cmd = commands.add_parser(
        "sign", help=f"Sign a manifest with the key in ${SIGNING_KEY_ENV}."
    )
    sign_cmd.add_argument("--manifest", required=True, type=Path)
    sign_cmd.add_argument("--version", required=True)
    sign_cmd.add_argument("--out", type=Path)

    verify_cmd = commands.add_parser("verify", help="Verify a manifest against the pinned keys.")
    verify_cmd.add_argument("--manifest", required=True, type=Path)
    verify_cmd.add_argument("--signature", required=True, type=Path)
    verify_cmd.add_argument("--version", required=True)
    verify_cmd.add_argument("--keys-file", type=Path, default=KEYS_FILE, help=argparse.SUPPRESS)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "keygen":
            path = args.private_key_out
            public_b64 = keygen(path, write_public=args.write_public, keys_file=args.keys_file)
            print(f"Public key: {public_b64}")
            if args.write_public:
                print(f"Added the public key to {args.keys_file}.")
            else:
                print("Add it to UPDATE_PUBLIC_KEYS in lumisync/update_keys.py (or rerun with --write-public).")
            print(f"Private key written to {Path(os.path.abspath(path))}. Next steps:")
            print(f"  1. gh secret set UPDATE_SIGNING_KEY < {path}")
            print("  2. Keep an offline copy of the private key somewhere safe.")
            print(f"  3. Delete {path} from this computer.")
            return 0
        if args.command == "sign":
            target = sign(args.manifest, args.version, args.out)
            print(f"Signed {args.manifest} for version {_signing_version(args.version)}: {target}")
            return 0
        if args.command == "verify":
            if verify(args.manifest, args.signature, args.version, keys_file=args.keys_file):
                print(f"Signature OK for {args.manifest} (version {_signing_version(args.version)}).")
                return 0
            print(
                f"Signature check FAILED for {args.manifest}: it was not signed by a "
                "pinned key for this version.",
                file=sys.stderr,
            )
            return 1
    except SigningError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
