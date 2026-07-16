"""Device capability catalog for Govee models.

Answers "how many zones does this strip have, and what does it support?" without
requiring the Govee app or an online account — which matters for Linux users and
anyone who does not want to install Govee Desktop.

Govee Desktop itself learns capabilities from an online service endpoint
``/bff-app/pc/v1/support-skus`` (after sign-in) and caches them locally. The LAN
scan only broadcasts ``sku``/``mac``/``ip`` — the device does not report its own
segment count. So the only offline-friendly options are a bundled table and an
optional import of an existing Govee Desktop cache. This module provides both.

Resolution precedence (highest first), applied by callers such as
``connection.get_segment_count``:

1. explicit per-device user override (``segment_count_override``)
2. an imported Govee Desktop cache (real data, Windows only) via
   :func:`import_govee_desktop_cache`
3. this bundled table (ships with LumiSync; community-updatable)
4. a generic default
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class SkuCapabilities:
    """What a given Govee SKU can do. ``segment_count == 0`` means unknown."""

    sku: str
    name: str = ""
    segment_count: int = 0
    supports_razer: bool = True
    supports_color: bool = True
    supports_music: bool = True
    color_temp_min: int = 0
    color_temp_max: int = 0


def _cap(sku, name, segments, ct_min=2000, ct_max=9000, **kw) -> SkuCapabilities:
    return SkuCapabilities(
        sku=sku, name=name, segment_count=segments,
        color_temp_min=ct_min, color_temp_max=ct_max, **kw,
    )


# Bundled table. "H619C" is confirmed from a Govee Desktop cache on a real
# device (10 UI segments, 2000-9000 K). The rest of the H619x line are the same
# 5 m/10 m "RGBIC Pro" strip family that the desktop app exposes with 10 UI
# segments. Unknown SKUs fall back to the caller's default and can always be
# corrected with a per-device override, so a missing or slightly-off entry never
# breaks a device. Add models here (PRs welcome) as they are confirmed.
BUILTIN: Dict[str, SkuCapabilities] = {
    "H619A": _cap("H619A", "RGBIC Pro Strip", 10),
    "H619B": _cap("H619B", "RGBIC Pro Strip", 10),
    "H619C": _cap("H619C", "10m RGBIC Pro Strip Lights", 10),
    "H619D": _cap("H619D", "RGBIC Pro Strip", 10),
    "H619E": _cap("H619E", "RGBIC Pro Strip", 10),
    "H619Z": _cap("H619Z", "RGBIC Pro Strip", 10),
    "H6199": _cap("H6199", "RGBIC TV Strip", 10),
    # Govee Home 7.5.20 resolves H6672's manual color pieces to min(ic=56, 14).
    # Govee Desktop 2.40.50 still reports segmentNums=0/supportRazer=false, so
    # 14 is a physical/manual mapping count, not proof of official B0 support.
    # Keep the user override authoritative while this LAN path is experimental.
    "H6672": _cap("H6672", "RGBWIC TV Backlight 2", 14),
}

# Live catalog = bundled table plus anything imported at runtime. Mutated by
# register()/import_govee_desktop_cache(); read by the lookup helpers. Kept in
# memory so hot-path callers never touch the filesystem.
_RUNTIME: Dict[str, SkuCapabilities] = dict(BUILTIN)


def _normalize(sku: Optional[str]) -> str:
    return str(sku).strip().upper() if sku else ""


def register(cap: SkuCapabilities) -> None:
    """Add or override a catalog entry at runtime."""
    key = _normalize(cap.sku)
    if key:
        _RUNTIME[key] = cap


def capabilities_for(sku: Optional[str]) -> Optional[SkuCapabilities]:
    """Return capabilities for a SKU, or None if unknown."""
    return _RUNTIME.get(_normalize(sku))


def segment_count_for(sku: Optional[str]) -> Optional[int]:
    """Return a known segment count for a SKU, or None if unknown."""
    cap = capabilities_for(sku)
    if cap and cap.segment_count > 0:
        return cap.segment_count
    return None


def default_govee_cache_path() -> str:
    """Path to Govee Desktop's device_info.ini for the current user (Windows)."""
    base = os.environ.get("LOCALAPPDATA", "")
    return os.path.join(base, "GoveeDesktop", "config", "device_info.ini")


def parse_govee_cache(text: str) -> Dict[str, SkuCapabilities]:
    """Parse the Sku section of a Govee Desktop device_info.ini into the catalog.

    The file is INI-like with large JSON values. We only read the ``Sku`` array,
    which carries ``sku``/``segmentNums``/capability flags/color-temp range.
    """
    result: Dict[str, SkuCapabilities] = {}
    # The value runs from "Sku=" to the next "[Section]" header (or EOF) and may
    # span multiple lines.
    match = re.search(r"^Sku=(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if not match:
        return result
    try:
        entries = json.loads(match.group(1).strip())
    except (ValueError, TypeError):
        return result

    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        sku = _normalize(entry.get("sku"))
        if not sku:
            continue
        try:
            segments = int(entry.get("segmentNums", 0) or 0)
        except (TypeError, ValueError):
            segments = 0
        result[sku] = SkuCapabilities(
            sku=sku,
            name=str(entry.get("name", "")),
            segment_count=max(0, segments),
            supports_razer=bool(entry.get("supportRazer", True)),
            supports_color=bool(entry.get("supportColor", 1)),
            supports_music=bool(entry.get("supportMusicalFeast", 1)),
            color_temp_min=int(entry.get("colorTemperatureStart", 0) or 0),
            color_temp_max=int(entry.get("colorTemperatureEnd", 0) or 0),
        )
    return result


def import_govee_desktop_cache(path: Optional[str] = None) -> int:
    """Merge a local Govee Desktop cache into the runtime catalog if present.

    Safe to call unconditionally at startup: returns 0 and changes nothing when
    the file is absent or unreadable. Imported entries take precedence over the
    bundled table because they are real per-device data.

    Returns the number of SKUs imported.
    """
    path = path or default_govee_cache_path()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return 0

    imported = parse_govee_cache(text)
    for cap in imported.values():
        if cap.segment_count > 0:  # only trust entries that actually carry data
            register(cap)
    return len(imported)
