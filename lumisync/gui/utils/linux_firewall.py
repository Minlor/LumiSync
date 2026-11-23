"""Linux firewalls (ufw, firewalld): a hint, never a change.

LumiSync hears its lights through UDP traffic it did not ask for over an
existing flow: Govee discovery replies on the listen port and LSC/Tuya
presence broadcasts. An active ``ufw`` or ``firewalld`` with its default
policy drops both. The app cannot fix that without root, and it must not try:
it only detects, without root, whether one of them is active, and offers the
commands for the user to run.

Detection asks ``systemctl is-active`` with a short timeout and caches the
answer; without ``systemctl`` (or on any error) the answer is "unknown" and
nothing is shown. :func:`detect` blocks for up to the timeout, so callers run
it off the GUI thread.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from typing import Callable, Optional, Sequence

from ...config.options import CONNECTION
from ...drivers.tuya_presence import PRESENCE_PORT
from ...utils.logging import get_logger

logger = get_logger(__name__)

FIREWALL_UFW = "ufw"
FIREWALL_FIREWALLD = "firewalld"
# Checked in this order; the first active one is reported.
FIREWALLS = (FIREWALL_UFW, FIREWALL_FIREWALLD)
UFW_CONFIG = "/etc/ufw/ufw.conf"
SYSTEMCTL_TIMEOUT_SECONDS = 2.0


def listen_ports() -> tuple[int, ...]:
    """The UDP ports LumiSync must receive unsolicited traffic on, sorted."""
    return tuple(sorted({int(CONNECTION.default.listen_port), int(PRESENCE_PORT)}))


def allow_commands(name: str, ports: Optional[Sequence[int]] = None) -> list[str]:
    """Shell lines that open LumiSync's ports in firewall *name*."""
    ports = tuple(listen_ports() if ports is None else ports)
    if name == FIREWALL_UFW:
        return [f"sudo ufw allow {port}/udp comment 'LumiSync'" for port in ports]
    if name == FIREWALL_FIREWALLD:
        adds = " ".join(f"--add-port={port}/udp" for port in ports)
        return [f"sudo firewall-cmd --permanent {adds} && sudo firewall-cmd --reload"]
    return []


def allow_commands_text(name: str, ports: Optional[Sequence[int]] = None) -> str:
    lines = allow_commands(name, ports)
    return "\n".join(lines) + "\n" if lines else ""


Runner = Callable[[list[str], float], Optional[str]]


def _run_systemctl(arguments: list[str], timeout: float) -> Optional[str]:
    """``systemctl`` stdout, or None when it is missing or does not answer."""
    program = shutil.which("systemctl")
    if program is None:
        return None
    try:
        completed = subprocess.run(
            [program, *arguments],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # is-active exits non-zero when any unit is inactive; stdout still has
    # one state per unit.
    return completed.stdout


def _ufw_enabled_in_config(path: str = UFW_CONFIG) -> Optional[bool]:
    """``ENABLED=`` from ufw.conf (world-readable), or None if unreadable.

    Ubuntu's ufw.service is "active" even when ufw itself is disabled; the
    config file says whether it really filters.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                key, sep, value = line.strip().partition("=")
                if sep and key.strip() == "ENABLED":
                    return value.strip().strip("'\"").lower() == "yes"
    except OSError:
        return None
    return False


def detect_active_firewall(
    run: Optional[Runner] = None,
    *,
    platform: Optional[str] = None,
    ufw_enabled: Optional[Callable[[], Optional[bool]]] = None,
) -> Optional[str]:
    """``"ufw"``, ``"firewalld"`` or None (none active, or unknown). Never raises."""
    if not (platform or sys.platform).startswith("linux"):
        return None
    runner = run or _run_systemctl
    try:
        output = runner(["is-active", *FIREWALLS], SYSTEMCTL_TIMEOUT_SECONDS)
    except Exception:
        logger.debug("systemctl is-active failed", exc_info=True)
        return None
    if not output:
        return None
    states = [line.strip() for line in output.splitlines()]
    for name, state in zip(FIREWALLS, states):
        if state != "active":
            continue
        if name == FIREWALL_UFW:
            enabled = (ufw_enabled or _ufw_enabled_in_config)()
            if enabled is False:
                continue
        return name
    return None


_UNSET = object()
_cache: object = _UNSET
_cache_lock = threading.Lock()


def detect(run: Optional[Runner] = None) -> Optional[str]:
    """:func:`detect_active_firewall`, asked once per run and then cached."""
    global _cache
    with _cache_lock:
        if _cache is not _UNSET:
            return _cache  # type: ignore[return-value]
    result = detect_active_firewall(run)
    with _cache_lock:
        _cache = result
    if result:
        logger.info("Linux firewall: %s is active", result)
    return result


def cached() -> Optional[str]:
    """The cached answer without running anything (None until detected)."""
    with _cache_lock:
        return None if _cache is _UNSET else _cache  # type: ignore[return-value]


def is_detected() -> bool:
    with _cache_lock:
        return _cache is not _UNSET


def reset_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = _UNSET


__all__ = [
    "FIREWALLS",
    "FIREWALL_FIREWALLD",
    "FIREWALL_UFW",
    "allow_commands",
    "allow_commands_text",
    "cached",
    "detect",
    "detect_active_firewall",
    "is_detected",
    "listen_ports",
    "reset_cache",
]
