"""GitHub release update checks for LumiSync."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from packaging.version import InvalidVersion, Version

from . import __version__


GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/Minlor/LumiSync/releases/latest"
GITHUB_RELEASES_URL = "https://github.com/Minlor/LumiSync/releases"
GITHUB_API_VERSION = "2026-03-10"
USER_AGENT = f"LumiSync/{__version__}"


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest_version: Optional[str] = None
    is_update_available: bool = False
    release_url: Optional[str] = None
    release_name: Optional[str] = None
    published_at: Optional[str] = None
    error: Optional[str] = None


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


def parse_release_payload(
    payload: dict[str, Any],
    current_version: str = __version__,
) -> UpdateCheckResult:
    """Convert GitHub's latest-release response into an update result."""

    tag = payload.get("tag_name")
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
            error=f"Invalid release version: {exc}",
        )

    return UpdateCheckResult(
        current_version=current_version,
        latest_version=latest_version,
        is_update_available=update_available,
        release_url=payload.get("html_url") or GITHUB_RELEASES_URL,
        release_name=payload.get("name") or str(tag),
        published_at=payload.get("published_at"),
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
