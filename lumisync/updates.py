"""GitHub release update checks for LumiSync."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, Callable, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from packaging.version import InvalidVersion, Version

from . import __version__


logger = logging.getLogger(__name__)

GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/Minlor/LumiSync/releases/latest"
GITHUB_RELEASES_URL = "https://github.com/Minlor/LumiSync/releases"
GITHUB_API_VERSION = "2026-03-10"
USER_AGENT = f"LumiSync/{__version__}"

# Release signing: a detached Ed25519 signature over the checksum manifest,
# published as ``<manifest name>.sig`` (base64 of the 64-byte signature).
SIGNATURE_SUFFIX = ".sig"
SIGNED_MESSAGE_PREFIX = b"LumiSync update manifest v1\n"
MAX_SIGNATURE_BYTES = 1024
MAX_MANIFEST_BYTES = 256 * 1024
UNVERIFIED_UPDATE_MESSAGE = (
    "This update could not be verified as coming from LumiSync, so it was not "
    "installed."
)


class UpdateVerificationError(RuntimeError):
    """The update could not be proven to come from LumiSync's signing key."""


@dataclass(frozen=True)
class ReleaseAsset:
    """One downloadable file attached to a GitHub release."""

    name: str
    url: str
    size: int = 0
    digest: Optional[str] = None


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest_version: Optional[str] = None
    is_update_available: bool = False
    release_url: Optional[str] = None
    release_name: Optional[str] = None
    published_at: Optional[str] = None
    body: Optional[str] = None
    error: Optional[str] = None
    assets: tuple[ReleaseAsset, ...] = ()


def normalize_version_tag(tag: Any) -> str:
    """Return a comparable version string from a GitHub release tag."""

    if not isinstance(tag, str) or not tag.strip():
        raise InvalidVersion("Release tag is missing")
    normalized = tag.strip()
    if normalized[:1].lower() == "v":
        return normalized[1:]
    return normalized


def is_newer_version(current_version: str, release_tag: str) -> bool:
    """Compare the installed version with a GitHub release tag."""

    current = Version(normalize_version_tag(current_version))
    latest = Version(normalize_version_tag(release_tag))
    return latest > current


def _parse_assets(payload: dict[str, Any]) -> tuple[ReleaseAsset, ...]:
    """Read the release's downloadable assets without trusting their shape."""
    assets = []
    raw_assets = payload.get("assets")
    if not isinstance(raw_assets, list):
        return ()
    for entry in raw_assets:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        url = entry.get("browser_download_url")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(url, str) or not url.strip():
            continue
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        digest = entry.get("digest")
        assets.append(
            ReleaseAsset(
                name=name.strip(),
                url=url.strip(),
                size=max(0, size),
                digest=str(digest) if isinstance(digest, str) else None,
            )
        )
    return tuple(assets)


def select_update_asset(
    assets: Sequence[ReleaseAsset],
    *,
    platform: Optional[str] = None,
    prefer_installer: bool = True,
) -> Optional[ReleaseAsset]:
    """Choose the best release asset for this platform.

    Windows prefers the Inno Setup installer and falls back to the portable
    executable. Linux uses the AppImage. The selection is intentionally based
    on file names so it keeps working when asset ordering changes.
    """
    platform = (platform or sys.platform).lower()
    lowered = [(asset, asset.name.casefold()) for asset in assets]

    if platform.startswith("win"):
        installers = [
            asset
            for asset, name in lowered
            if name.endswith(".exe") and ("setup" in name or "installer" in name)
        ]
        if prefer_installer and installers:
            return installers[0]
        portables = [
            asset
            for asset, name in lowered
            if name.endswith(".exe") and "setup" not in name and "installer" not in name
        ]
        if portables:
            # Prefer an explicitly named portable build, then the historical
            # single-file name.
            portables.sort(key=lambda asset: ("portable" not in asset.name.casefold(),))
            return portables[0]
        return installers[0] if installers else None

    if platform.startswith("linux"):
        for asset, name in lowered:
            if name.endswith(".appimage"):
                return asset
        return None

    return None


def _is_checksum_manifest(name: str) -> bool:
    name = name.casefold()
    return (
        name in ("sha256sums.txt", "checksums.txt", "sha256sums")
        or name.endswith("sha256sums.txt")
        or name.endswith(".sha256")
    )


def select_checksum_assets(
    assets: Sequence[ReleaseAsset],
    target: Optional[ReleaseAsset] = None,
) -> list[ReleaseAsset]:
    """Return every SHA-256 manifest in the release, most specific first.

    Windows and Linux builds publish separate manifests, so the updater tries
    each until one lists the file it downloaded. A ``<asset>.sha256`` sidecar
    for ``target`` is tried before the shared manifests.
    """
    manifests = [asset for asset in assets if _is_checksum_manifest(asset.name)]
    if target is None:
        return manifests
    sidecar = f"{target.name}.sha256".casefold()
    platform_word = "windows" if target.name.casefold().endswith(".exe") else "linux"

    def rank(asset: ReleaseAsset) -> tuple[bool, bool]:
        name = asset.name.casefold()
        return (name != sidecar, platform_word not in name)

    return sorted(manifests, key=rank)


def select_checksum_asset(
    assets: Sequence[ReleaseAsset],
) -> Optional[ReleaseAsset]:
    """Find the release's SHA-256 manifest, if it published one."""
    manifests = select_checksum_assets(assets)
    return manifests[0] if manifests else None


def _is_sha256_hex(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def expected_sha256(
    asset: ReleaseAsset,
    assets: Sequence[ReleaseAsset],
    *,
    fetch: Optional[Callable[[str], str]] = None,
) -> Optional[str]:
    """Find the published SHA-256 for ``asset``.

    GitHub's own asset digest wins; otherwise each published manifest is read
    until one lists the asset. Returns ``None`` when nothing vouches for it.
    """
    digest = asset_sha256(asset)
    if digest:
        return digest
    reader = fetch or fetch_text
    wanted = asset.name.casefold()
    for manifest in select_checksum_assets(assets, asset):
        try:
            text = reader(manifest.url)
        except (OSError, ValueError):
            continue
        checksums = parse_checksums(text)
        if wanted in checksums:
            return checksums[wanted]
        # A bare ``<hash>`` sidecar names no file.
        if manifest.name.casefold() == f"{wanted}.sha256":
            parts = text.split()
            token = parts[0].casefold() if parts else ""
            if _is_sha256_hex(token):
                return token
    return None


def is_installed_build() -> bool:
    """Whether this process is the packaged desktop app.

    Only the frozen build may replace itself with the installer; a source
    checkout or pip install would end up with a second, separate copy.
    """
    return bool(getattr(sys, "frozen", False))


def parse_checksums(text: str) -> dict[str, str]:
    """Parse ``sha256  filename`` lines into a lower-case name -> hash map."""
    checksums: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts
        digest = digest.strip().casefold()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            continue
        checksums[name.strip().lstrip("*").casefold()] = digest
    return checksums


def asset_sha256(asset: ReleaseAsset) -> Optional[str]:
    """Return the asset's GitHub-provided SHA-256 digest, when present."""
    if not asset.digest:
        return None
    value = asset.digest.strip().casefold()
    if value.startswith("sha256:"):
        value = value.split(":", 1)[1]
    if len(value) == 64 and all(ch in "0123456789abcdef" for ch in value):
        return value
    return None


def fetch_text(url: str, timeout: float = 10.0) -> str:
    """Fetch a small text asset such as ``SHA256SUMS.txt``."""
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/plain"},
    )
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def fetch_asset_bytes(url: str, max_bytes: int, timeout: float = 10.0) -> bytes:
    """Fetch a small release asset, refusing anything over ``max_bytes``."""
    request = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
    )
    with urlopen(request, timeout=timeout) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"Release asset is larger than {max_bytes} bytes")
    return data


def pinned_update_keys() -> tuple[str, ...]:
    """The public keys this build accepts, read when called (not at import)."""
    from . import update_keys

    return tuple(update_keys.UPDATE_PUBLIC_KEYS)


def release_signing_version(version: str) -> str:
    """Normalise a release tag or version the way the signature binds it."""
    return str(Version(normalize_version_tag(version)))


def signed_message(version: str, manifest_bytes: bytes) -> bytes:
    """The exact bytes a release signature covers.

    The version is part of the message so an old signed manifest and its
    installer cannot be replayed under a newer release tag.
    """
    normalized = release_signing_version(version)
    return SIGNED_MESSAGE_PREFIX + normalized.encode("ascii") + b"\n" + bytes(manifest_bytes)


def _b64decode_exact(value: str | bytes, length: int) -> Optional[bytes]:
    try:
        if isinstance(value, bytes):
            value = value.decode("ascii")
        decoded = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError, UnicodeDecodeError, AttributeError, TypeError):
        return None
    return decoded if len(decoded) == length else None


def verify_manifest_signature(
    manifest_bytes: bytes,
    signature_b64: str | bytes,
    version: str,
    public_keys: Sequence[str],
) -> bool:
    """Whether any of ``public_keys`` signed this manifest for ``version``.

    Malformed signatures, keys or versions return False; this never raises.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    signature = _b64decode_exact(signature_b64, 64)
    if signature is None:
        return False
    try:
        message = signed_message(version, manifest_bytes)
    except (InvalidVersion, UnicodeEncodeError, TypeError, ValueError):
        return False
    for encoded_key in public_keys:
        raw_key = _b64decode_exact(encoded_key, 32)
        if raw_key is None:
            continue
        try:
            Ed25519PublicKey.from_public_bytes(raw_key).verify(signature, message)
        except (InvalidSignature, ValueError, TypeError):
            continue
        return True
    return False


def select_signature_asset(
    manifest_asset: ReleaseAsset,
    assets: Sequence[ReleaseAsset],
) -> Optional[ReleaseAsset]:
    """Find the ``<manifest>.sig`` published next to ``manifest_asset``."""
    wanted = f"{manifest_asset.name}{SIGNATURE_SUFFIX}".casefold()
    for asset in assets:
        if asset.name.casefold() == wanted:
            return asset
    return None


def signed_expected_sha256(
    asset: ReleaseAsset,
    assets: Sequence[ReleaseAsset],
    version: Optional[str],
    public_keys: Sequence[str],
    *,
    fetch: Optional[Callable[[str, int], bytes]] = None,
) -> Optional[str]:
    """Find ``asset``'s SHA-256 in a manifest signed by a pinned key.

    Only manifests whose ``.sig`` verifies for ``version`` count; GitHub's
    asset digest and unsigned manifests are ignored. Returns ``None`` when no
    signed manifest lists the asset.
    """
    if not version or not public_keys:
        return None
    reader = fetch or fetch_asset_bytes
    wanted = asset.name.casefold()
    for manifest in select_checksum_assets(assets, asset):
        signature_asset = select_signature_asset(manifest, assets)
        if signature_asset is None:
            continue
        if manifest.size > MAX_MANIFEST_BYTES or signature_asset.size > MAX_SIGNATURE_BYTES:
            continue
        try:
            manifest_bytes = reader(manifest.url, MAX_MANIFEST_BYTES)
            signature = reader(signature_asset.url, MAX_SIGNATURE_BYTES)
        except (OSError, ValueError):
            continue
        if not verify_manifest_signature(manifest_bytes, signature, version, public_keys):
            logger.warning("Update manifest %s has an invalid signature", manifest.name)
            continue
        text = manifest_bytes.decode("utf-8", errors="replace")
        checksums = parse_checksums(text)
        if wanted in checksums:
            return checksums[wanted]
        if manifest.name.casefold() == f"{wanted}.sha256":
            parts = text.split()
            token = parts[0].casefold() if parts else ""
            if _is_sha256_hex(token):
                return token
    return None


def verify_file_sha256(path: str | os.PathLike[str], expected: str) -> bool:
    """Return whether ``path`` matches the expected SHA-256 hex digest."""
    expected = expected.strip().casefold()
    if len(expected) != 64:
        return False
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest().casefold() == expected


def download_update_asset(
    asset: ReleaseAsset,
    destination: str | os.PathLike[str],
    *,
    progress: Optional[Callable[[int, int], None]] = None,
    timeout: float = 30.0,
) -> Path:
    """Download one release asset to ``destination``.

    ``progress`` receives ``(bytes_written, total_bytes)``; total is the
    asset's advertised size when GitHub provided one.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(
        asset.url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"},
    )
    written = 0
    with urlopen(request, timeout=timeout) as response, open(destination, "wb") as handle:
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            written += len(chunk)
            if progress is not None:
                progress(written, asset.size)
    if asset.size and written != asset.size:
        raise ValueError(
            f"Downloaded {written} bytes but the release listed {asset.size}"
        )
    return destination


def download_verified_update(
    result: UpdateCheckResult,
    directory: str | os.PathLike[str],
    *,
    progress: Optional[Callable[[int, int], None]] = None,
    public_keys: Optional[Sequence[str]] = None,
) -> Path:
    """Download this platform's update into ``directory`` and verify it.

    With pinned keys (``public_keys`` defaults to :func:`pinned_update_keys`)
    the expected hash must come from a checksum manifest whose signature
    verifies for the release version, and that is checked before the
    installer is downloaded. Without keys the release's own checksum is
    trusted, as before, and a warning is logged. A file that fails
    verification is deleted; errors are raised with a user-facing message.
    """
    asset = select_update_asset(result.assets)
    if asset is None:
        raise RuntimeError(
            "This release has no compatible update asset for this platform."
        )

    keys = tuple(pinned_update_keys() if public_keys is None else public_keys)
    if keys:
        expected = signed_expected_sha256(
            asset, result.assets, result.latest_version, keys
        )
        if not expected:
            raise UpdateVerificationError(UNVERIFIED_UPDATE_MESSAGE)
    else:
        logger.warning(
            "Update signatures are not being checked: this build has no "
            "update signing key."
        )
        expected = expected_sha256(asset, result.assets)
        if not expected:
            raise RuntimeError(
                "The release does not publish a SHA-256 checksum, so the "
                "download cannot be verified. Use Open Release instead."
            )

    destination = Path(directory) / asset.name
    try:
        download_update_asset(asset, destination, progress=progress)
        if not verify_file_sha256(destination, expected):
            if keys:
                raise UpdateVerificationError(UNVERIFIED_UPDATE_MESSAGE)
            raise RuntimeError(
                "The downloaded file failed SHA-256 verification and was not run."
            )
    except BaseException:
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not delete the rejected update %s", destination)
        raise
    return destination


def parse_release_payload(
    payload: dict[str, Any],
    current_version: str = __version__,
) -> UpdateCheckResult:
    """Convert GitHub's latest-release response into an update result."""

    tag = payload.get("tag_name")
    assets = _parse_assets(payload)
    try:
        latest_version = normalize_version_tag(tag)
        update_available = is_newer_version(current_version, latest_version)
    except InvalidVersion as exc:
        return UpdateCheckResult(
            current_version=current_version,
            latest_version=str(tag) if tag else None,
            release_url=payload.get("html_url") or GITHUB_RELEASES_URL,
            release_name=payload.get("name") or str(tag or ""),
            published_at=payload.get("published_at"),
            body=payload.get("body") if isinstance(payload.get("body"), str) else None,
            error=f"Invalid release version: {exc}",
            assets=assets,
        )

    return UpdateCheckResult(
        current_version=current_version,
        latest_version=latest_version,
        is_update_available=update_available,
        release_url=payload.get("html_url") or GITHUB_RELEASES_URL,
        release_name=payload.get("name") or str(tag),
        published_at=payload.get("published_at"),
        body=payload.get("body") if isinstance(payload.get("body"), str) else None,
        assets=assets,
    )


def check_for_update(
    current_version: str = __version__,
    url: str = GITHUB_LATEST_RELEASE_URL,
    timeout: float = 6.0,
) -> UpdateCheckResult:
    """Fetch the latest stable GitHub release and compare it to this install."""

    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        },
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("GitHub returned an unexpected response")
        return parse_release_payload(payload, current_version=current_version)
    except HTTPError as exc:
        return UpdateCheckResult(
            current_version=current_version,
            error=f"GitHub update check failed with HTTP {exc.code}",
        )
    except (URLError, TimeoutError, OSError) as exc:
        return UpdateCheckResult(
            current_version=current_version,
            error=f"Could not reach GitHub: {exc}",
        )
    except (json.JSONDecodeError, ValueError) as exc:
        return UpdateCheckResult(
            current_version=current_version,
            error=f"Could not read GitHub release data: {exc}",
        )
