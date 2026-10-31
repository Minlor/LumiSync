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
        sku=sku,
        name=name,
        segment_count=segments,
        color_temp_min=ct_min,
        color_temp_max=ct_max,
        **kw,
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
    # H7020 is the S14 bulb string light; its manual lists 15 bulbs, so 15 is
    # the physical zone count. Govee Desktop support and LAN razer-stream
    # acceptance are unverified (issue #49: music sync silently does nothing
    # at the generic 10-zone default), so a per-device override stays
    # authoritative and this entry only fixes the stream frame size.
    "H7020": _cap("H7020", "S14 LED Bulb String Lights", 15, ct_min=0, ct_max=0),
    # H6080 family — Govee Desktop 2.40.60 references GetCtroSendJsonOfH6080,
    # BuildH6080LightSwitchCommand, IsSeedColor in Govee.Application.DeviceService.
    # Segment count and razer support are unverified via LAN; defaults are
    # conservative and overridable per-device.
    "H6080": _cap("H6080", "H6080 Light Strip", 10),
    "H6081": _cap("H6081", "H6080 Light Strip", 10),
    "H6082": _cap("H6082", "H6080 Light Strip", 10),
    "H6083": _cap("H6083", "H6080 Light Strip", 10),
    "H6084": _cap("H6084", "H6080 Light Strip", 10),
    "H6085": _cap("H6085", "H6080 Light Strip", 10),
    "H608A": _cap("H608A", "H6080 Light Strip", 10),
    "H608B": _cap("H608B", "H6080 Light Strip", 10),
    "H608C": _cap("H608C", "H6080 Light Strip", 10),
    # H60xx / H61xx / H66xx / H68xx — additional LED strip and lighting devices
    # referenced in Govee Desktop 2.40.60 strings. Segment counts are defaults;
    # real values should be confirmed via per-device override or Govee Desktop
    # cache import.
    "H6002": _cap("H6002", "H6002 LED Strip", 10),
    "H601E": _cap("H601E", "H601E LED Device", 10),
    "H601F": _cap("H601F", "H601F LED Device", 10),
    "H6020": _cap("H6020", "H6020 LED Device", 10),
    "H6022": _cap("H6022", "H6022 LED Device", 10),
    "H6038": _cap("H6038", "H6038 LED Device", 10),
    "H6043": _cap("H6043", "H6043 LED Device", 10),
    "H6046": _cap("H6046", "H6046 LED Device", 10),
    "H6047": _cap("H6047", "H6047 LED Device", 10),
    "H6048": _cap("H6048", "H6048 LED Device", 10),
    "H6049": _cap("H6049", "H6049 LED Device", 10),
    "H6054": _cap("H6054", "H6054 LED Device", 10),
    "H6056": _cap("H6056", "H6056 LED Device", 10),
    "H6057": _cap("H6057", "H6057 LED Device", 10),
    "H605C": _cap("H605C", "H605C LED Device", 10),
    "H605D": _cap("H605D", "H605D LED Device", 10),
    "H6061": _cap("H6061", "H6061 LED Device", 10),
    "H6063": _cap("H6063", "H6063 LED Device", 10),
    "H6065": _cap("H6065", "H6065 LED Device", 10),
    "H6066": _cap("H6066", "H6066 LED Device", 10),
    "H6067": _cap("H6067", "H6067 LED Device", 10),
    "H6069": _cap("H6069", "H6069 LED Device", 10),
    "H606A": _cap("H606A", "H606A LED Device", 10),
    "H6072": _cap("H6072", "H6072 LED Device", 10),
    "H6076": _cap("H6076", "H6076 LED Device", 10),
    "H6079": _cap("H6079", "H6079 LED Device", 10),
    "H607C": _cap("H607C", "H607C LED Device", 10),
    "H6092": _cap("H6092", "H6092 LED Device", 10),
    "H6093": _cap("H6093", "H6093 LED Device", 10),
    "H6094": _cap("H6094", "H6094 LED Device", 10),
    "H6095": _cap("H6095", "H6095 LED Device", 10),
    "H6098": _cap("H6098", "H6098 LED Device", 10),
    "H6099": _cap("H6099", "H6099 LED Device", 10),
    "H609D": _cap("H609D", "H609D LED Device", 10),
    "H610A": _cap("H610A", "H610A LED Device", 10),
    "H610B": _cap("H610B", "H610B LED Device", 10),
    "H6116": _cap("H6116", "H6116 LED Device", 10),
    "H6117": _cap("H6117", "H6117 LED Device", 10),
    "H6125": _cap("H6125", "H6125 LED Device", 10),
    "H6126": _cap("H6126", "H6126 LED Device", 10),
    "H6127": _cap("H6127", "H6127 LED Device", 10),
    "H6143": _cap("H6143", "H6143 LED Device", 10),
    "H6144": _cap("H6144", "H6144 LED Device", 10),
    "H6145": _cap("H6145", "H6145 LED Device", 10),
    "H6146": _cap("H6146", "H6146 LED Device", 10),
    "H6161": _cap("H6161", "H6161 LED Device", 10),
    "H6163": _cap("H6163", "H6163 LED Device", 10),
    "H6172": _cap("H6172", "H6172 LED Device", 10),
    "H617A": _cap("H617A", "H617A LED Device", 10),
    "H617C": _cap("H617C", "H617C LED Device", 10),
    "H617E": _cap("H617E", "H617E LED Device", 10),
    "H618A": _cap("H618A", "H618A LED Device", 10),
    "H618C": _cap("H618C", "H618C LED Device", 10),
    "H618E": _cap("H618E", "H618E LED Device", 10),
    "H6198": _cap("H6198", "RGBIC Pro Strip", 10),
    "H6608": _cap("H6608", "H6608 LED Device", 10),
    "H6609": _cap("H6609", "H6609 LED Device", 10),
    "H6630": _cap("H6630", "H6630 LED Device", 10),
    "H6631": _cap("H6631", "H6631 LED Device", 10),
    "H6810": _cap("H6810", "H6810 LED Device", 10),
    "H6811": _cap("H6811", "H6811 LED Device", 10),
    # H7xxx — bulb and string light family. H7020 confirmed as S14 bulbs
    # (15 zones, no color temp). Other H7xxx models default to single-zone
    # white bulb behavior (no color, no razer, no music).
    "H7010": _cap(
        "H7010",
        "H7010 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7011": _cap(
        "H7011",
        "H7011 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7012": _cap(
        "H7012",
        "H7012 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7013": _cap(
        "H7013",
        "H7013 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7019": _cap(
        "H7019",
        "H7019 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7031": _cap(
        "H7031",
        "H7031 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7032": _cap(
        "H7032",
        "H7032 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7045": _cap(
        "H7045",
        "H7045 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7046": _cap(
        "H7046",
        "H7046 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H705A": _cap(
        "H705A",
        "H705A Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H705B": _cap(
        "H705B",
        "H705B Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H705C": _cap(
        "H705C",
        "H705C Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7060": _cap(
        "H7060",
        "H7060 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7061": _cap(
        "H7061",
        "H7061 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7065": _cap(
        "H7065",
        "H7065 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H706A": _cap(
        "H706A",
        "H706A Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H706B": _cap(
        "H706B",
        "H706B Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H706C": _cap(
        "H706C",
        "H706C Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7078": _cap(
        "H7078",
        "H7078 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H707A": _cap(
        "H707A",
        "H707A Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H707B": _cap(
        "H707B",
        "H707B Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H707C": _cap(
        "H707C",
        "H707C Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7101": _cap(
        "H7101",
        "H7101 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7102": _cap(
        "H7102",
        "H7102 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7105": _cap(
        "H7105",
        "H7105 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7107": _cap(
        "H7107",
        "H7107 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7122": _cap(
        "H7122",
        "H7122 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7126": _cap(
        "H7126",
        "H7126 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7128": _cap(
        "H7128",
        "H7128 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H712C": _cap(
        "H712C",
        "H712C Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7140": _cap(
        "H7140",
        "H7140 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7143": _cap(
        "H7143",
        "H7143 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    "H7145": _cap(
        "H7145",
        "H7145 Bulb",
        1,
        ct_min=2000,
        ct_max=6500,
        supports_color=False,
        supports_razer=False,
        supports_music=False,
    ),
    # H1xxx / H2xxx / H3xxx — older or specialty Govee devices referenced in
    # Govee Desktop 2.40.60. Defaults are conservative; confirm per device.
    "H1232": _cap("H1232", "H1232 Device", 10),
    "H1250": _cap("H1250", "H1250 Device", 10),
    "H1252": _cap("H1252", "H1252 Device", 10),
    "H1270": _cap("H1270", "H1270 Device", 10),
    "H1310": _cap("H1310", "H1310 Device", 10),
    "H1370": _cap("H1370", "H1370 Device", 10),
    "H1630": _cap("H1630", "H1630 Device", 10),
    "H1771": _cap("H1771", "H1771 Device", 10),
    "H2800": _cap("H2800", "H2800 Device", 10),
    "H3751": _cap("H3751", "H3751 Device", 10),
    # H5xxx — additional Govee devices referenced in Govee Desktop 2.40.60.
    "H5051": _cap("H5051", "H5051 Device", 10),
    "H5071": _cap("H5071", "H5071 Device", 10),
    "H5074": _cap("H5074", "H5074 Device", 10),
    "H5075": _cap("H5075", "H5075 Device", 10),
    "H5089": _cap("H5089", "H5089 Device", 10),
    "H5100": _cap("H5100", "H5100 Device", 10),
    "H5101": _cap("H5101", "H5101 Device", 10),
    "H5102": _cap("H5102", "H5102 Device", 10),
    "H5103": _cap("H5103", "H5103 Device", 10),
    "H5104": _cap("H5104", "H5104 Device", 10),
    "H5105": _cap("H5105", "H5105 Device", 10),
    "H5106": _cap("H5106", "H5106 Device", 10),
    "H5107": _cap("H5107", "H5107 Device", 10),
    "H5108": _cap("H5108", "H5108 Device", 10),
    "H5109": _cap("H5109", "H5109 Device", 10),
    "H5110": _cap("H5110", "H5110 Device", 10),
    "H5111": _cap("H5111", "H5111 Device", 10),
    "H5112": _cap("H5112", "H5112 Device", 10),
    "H5140": _cap("H5140", "H5140 Device", 10),
    "H5160": _cap("H5160", "H5160 Device", 10),
    "H5171": _cap("H5171", "H5171 Device", 10),
    "H5174": _cap("H5174", "H5174 Device", 10),
    "H5177": _cap("H5177", "H5177 Device", 10),
    "H5179": _cap("H5179", "H5179 Device", 10),
    "H5220": _cap("H5220", "H5220 Device", 10),
    "H5310": _cap("H5310", "H5310 Device", 10),
    # H8xxx — newer Govee devices referenced in Govee Desktop 2.40.60.
    "H8015": _cap("H8015", "H8015 Device", 10),
    "H8022": _cap("H8022", "H8022 Device", 10),
    "H8048": _cap("H8048", "H8048 Device", 10),
    "H8069": _cap("H8069", "H8069 Device", 10),
    "H8072": _cap("H8072", "H8072 Device", 10),
    "H807C": _cap("H807C", "H807C Device", 10),
    "H8120": _cap("H8120", "H8120 Device", 10),
    "H8121": _cap("H8121", "H8121 Device", 10),
    "H8122": _cap("H8122", "H8122 Device", 10),
    "H8131": _cap("H8131", "H8131 Device", 10),
    "H8630": _cap("H8630", "H8630 Device", 10),
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
