"""Windows Firewall: can LumiSync hear its lights on the local network?

LumiSync listens for traffic it did not ask for over an existing flow: LSC
and Tuya presence broadcasts on UDP 6667, and Govee LAN discovery replies on
UDP 4002 (they answer a multicast scan, so stateful filtering does not match
them). Without an inbound rule for the program, Windows Firewall drops both.

The decision lives in :func:`evaluate`, which takes plain values and runs
anywhere. :func:`check` reads the live policy through the ``HNetCfg.FwPolicy2``
COM object, and :func:`allow` adds the rule through one elevated ``netsh``
call. Both block, so callers run them off the GUI thread.
"""

from __future__ import annotations

import ctypes
import ntpath
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Optional

from ...utils.logging import get_logger

logger = get_logger(__name__)

RULE_NAME = "LumiSync - local devices"
RULE_DESCRIPTION = (
    "Lets LumiSync hear lights on your network (Govee LAN replies, LSC/Tuya announcements)."
)
# Unsolicited inbound traffic LumiSync depends on: LSC/Tuya presence
# broadcasts and Govee discovery replies.
LISTEN_PORTS = frozenset({6667, 4002})

STATUS_OFF = "off"
STATUS_ALLOWED = "allowed"
STATUS_MISSING = "missing"
STATUS_BLOCKED = "blocked"
STATUS_PUBLIC = "public"
STATUS_UNKNOWN = "unknown"

# NET_FW_PROFILE_TYPE2, NET_FW_RULE_DIRECTION, NET_FW_ACTION, NET_FW_IP_PROTOCOL.
PROFILE_DOMAIN = 1
PROFILE_PRIVATE = 2
PROFILE_PUBLIC = 4
PROFILES = (PROFILE_DOMAIN, PROFILE_PRIVATE, PROFILE_PUBLIC)
_PROFILE_NAMES = {PROFILE_DOMAIN: "Domain", PROFILE_PRIVATE: "Private", PROFILE_PUBLIC: "Public"}
DIRECTION_IN = 1
ACTION_BLOCK = 0
ACTION_ALLOW = 1
PROTOCOL_UDP = 17
PROTOCOL_ANY = 256

# allow() result when the user declines the UAC prompt.
CANCELLED = "cancelled"
ERROR_CANCELLED = 1223
SW_HIDE = 0
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_NOASYNC = 0x00000100
WAIT_OBJECT_0 = 0
ALLOW_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class FirewallState:
    """The outcome of one check: a status from ``STATUS_*`` and a short detail."""

    status: str
    detail: str = ""
    # "Block all incoming connections" is on: no rule can help, so the UI
    # explains it instead of offering Allow.
    block_all: bool = False

    @property
    def can_allow(self) -> bool:
        """Whether adding LumiSync's rule would fix the problem."""
        return self.status in (STATUS_MISSING, STATUS_BLOCKED) and not self.block_all


@dataclass(frozen=True)
class FirewallRule:
    """The parts of an ``INetFwRule`` the check looks at."""

    application: Optional[str]
    direction: int = DIRECTION_IN
    enabled: bool = True
    action: int = ACTION_ALLOW
    protocol: int = PROTOCOL_ANY
    local_ports: str = "*"
    profiles: int = PROFILE_DOMAIN | PROFILE_PRIVATE | PROFILE_PUBLIC
    name: str = ""


def program_path() -> Optional[str]:
    """The installed exe to check, or None for source and non-Windows runs."""
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        return sys.executable
    return None


def normalize_program(path: Optional[str]) -> str:
    """A rule's program path in comparable form ("" when there is none).

    Windows stores paths as typed (often lower-case, sometimes with
    ``%LOCALAPPDATA%``-style variables). ``ntpath`` keeps the comparison
    Windows-shaped wherever the tests run.
    """
    text = str(path or "").strip()
    if not text:
        return ""
    return ntpath.normcase(ntpath.abspath(ntpath.expandvars(text)))


def _ports_in(local_ports: Optional[str]) -> frozenset[int]:
    """Which of LISTEN_PORTS a rule's LocalPorts value covers."""
    text = str(local_ports or "").strip()
    if text in ("", "*"):
        return LISTEN_PORTS
    covered: set[int] = set()
    for token in text.split(","):
        low, dash, high = token.strip().partition("-")
        try:
            first = int(low)
            last = int(high) if dash else first
        except ValueError:
            continue  # Keywords such as RPC or mDNS never cover our ports.
        covered.update(port for port in LISTEN_PORTS if first <= port <= last)
    return frozenset(covered)


def _covered_ports(rule: FirewallRule, target: str) -> frozenset[int]:
    """The listen ports an enabled inbound rule for *target* applies to."""
    if not target or normalize_program(rule.application) != target:
        return frozenset()
    if rule.direction != DIRECTION_IN or not rule.enabled:
        return frozenset()
    if rule.protocol not in (PROTOCOL_ANY, PROTOCOL_UDP):
        return frozenset()
    return _ports_in(rule.local_ports)


def _names(profiles: Iterable[int]) -> str:
    return " and ".join(_PROFILE_NAMES[profile] for profile in profiles)


def evaluate(
    program: str,
    *,
    active_profiles: int,
    enabled_profiles: int,
    block_all_profiles: int = 0,
    rules: Iterable[FirewallRule] = (),
) -> FirewallState:
    """Decide the firewall status for *program* from plain policy values.

    Profiles are ``PROFILE_*`` bit masks: the network types in use now, those
    with the firewall on, and those set to block every inbound connection.
    """
    live = [p for p in PROFILES if active_profiles & p and enabled_profiles & p]
    if not live:
        return FirewallState(STATUS_OFF, "Windows Firewall is off for the networks in use")
    # LumiSync's rule never covers Public networks. When a Private or Domain
    # network is in use too, the lights are reached there, and a Public
    # adapter alongside it (a VPN, a café hotspot) must not keep asking for an
    # Allow that can never satisfy it.
    required = [p for p in live if p != PROFILE_PUBLIC] or live
    required_mask = sum(required)
    names = _names(required)

    blocked_all = [p for p in required if block_all_profiles & p]
    if blocked_all:
        return FirewallState(
            STATUS_BLOCKED,
            f"Windows Firewall blocks all incoming connections on {_names(blocked_all)} networks",
            block_all=True,
        )

    target = normalize_program(program)
    matching = [(rule, ports) for rule in rules if (ports := _covered_ports(rule, target))]
    if any(rule.action == ACTION_BLOCK and rule.profiles & required_mask for rule, _ports in matching):
        return FirewallState(STATUS_BLOCKED, f"A firewall rule blocks LumiSync on {names} networks")

    def allowed_on(profile: int) -> bool:
        covered: set[int] = set()
        for rule, ports in matching:
            if rule.action == ACTION_ALLOW and rule.profiles & profile:
                covered |= ports
        return covered >= LISTEN_PORTS

    uncovered = [p for p in required if not allowed_on(p)]
    if not uncovered:
        return FirewallState(STATUS_ALLOWED, f"Allowed on {names} networks")
    if required == [PROFILE_PUBLIC]:
        return FirewallState(STATUS_PUBLIC, "The network in use is set to Public")
    return FirewallState(STATUS_MISSING, f"No firewall rule allows LumiSync on {_names(uncovered)} networks")


# ---------------------------------------------------------------- live policy


@contextmanager
def _com_thread() -> Iterator[None]:
    """COM initialised on the calling thread for the duration, when possible."""
    com = None
    if sys.platform == "win32":
        try:
            import pythoncom

            pythoncom.CoInitialize()
            com = pythoncom
        except Exception:
            # pywin32 missing, or COM already set up in another mode on this
            # thread: then it is usable but not ours to end.
            com = None
    try:
        yield
    finally:
        if com is not None:
            try:
                com.CoUninitialize()
            except Exception:
                pass


def _read_policy(program: str, policy) -> FirewallState:
    """Evaluate an ``INetFwPolicy2`` (or a stand-in with the same members)."""
    active = int(policy.CurrentProfileTypes)
    enabled = 0
    block_all = 0
    for profile in PROFILES:
        if policy.FirewallEnabled(profile):
            enabled |= profile
        if policy.BlockAllInboundTraffic(profile):
            block_all |= profile
    target = normalize_program(program)
    rules: list[FirewallRule] = []
    if active & enabled:
        for rule in policy.Rules:
            application = rule.ApplicationName
            # Most rules belong to other programs; skip their other fields.
            if not application or normalize_program(application) != target:
                continue
            rules.append(FirewallRule(
                application=str(application),
                direction=int(rule.Direction),
                enabled=bool(rule.Enabled),
                action=int(rule.Action),
                protocol=int(rule.Protocol),
                local_ports=str(rule.LocalPorts or ""),
                profiles=int(rule.Profiles),
                name=str(rule.Name or ""),
            ))
    return evaluate(
        program,
        active_profiles=active,
        enabled_profiles=enabled,
        block_all_profiles=block_all,
        rules=rules,
    )


def _query_live(program: str) -> FirewallState:
    if sys.platform != "win32":
        raise OSError("Windows Firewall exists only on Windows")
    import win32com.client

    # The policy object and every rule read from it belong to this call's
    # frames, so they are released before COM is uninitialised.
    return _read_policy(program, win32com.client.Dispatch("HNetCfg.FwPolicy2"))


def check(program: str) -> FirewallState:
    """Read the live Windows Firewall policy for *program*. Never raises.

    Blocking (COM on the calling thread): run it off the GUI thread.
    """
    if sys.platform != "win32":
        return FirewallState(STATUS_UNKNOWN, "Windows Firewall exists only on Windows")
    with _com_thread():
        try:
            return _query_live(program)
        except Exception as exc:
            logger.debug("Windows Firewall check failed", exc_info=True)
            return FirewallState(STATUS_UNKNOWN, f"Could not read the firewall policy ({type(exc).__name__})")


# ---------------------------------------------------------------- allow


class SHELLEXECUTEINFOW(ctypes.Structure):
    # Plain ctypes types instead of ctypes.wintypes, so the module also
    # imports on Linux (the CI runs the pure logic there).
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("fMask", ctypes.c_uint32),
        ("hwnd", ctypes.c_void_p),
        ("lpVerb", ctypes.c_wchar_p),
        ("lpFile", ctypes.c_wchar_p),
        ("lpParameters", ctypes.c_wchar_p),
        ("lpDirectory", ctypes.c_wchar_p),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", ctypes.c_wchar_p),
        ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", ctypes.c_uint32),
        ("hIconOrMonitor", ctypes.c_void_p),
        ("hProcess", ctypes.c_void_p),
    ]


def _system32() -> str:
    root = os.environ.get("SystemRoot") or os.environ.get("windir") or r"C:\Windows"
    return ntpath.join(root, "System32")


def _quote(value: str) -> str:
    return f'"{value}"'


def _safe_for_cmd(path: str) -> bool:
    """Whether *path* can sit inside quotes on a ``cmd /c`` line unchanged.

    Inside quotes cmd leaves ``&|<>()^`` alone, but it still expands
    ``%NAME%`` and a ``"`` would end the quoting. Neither belongs in an install
    path, so such a path is refused rather than escaped.
    """
    return bool(path) and not any(ch in path for ch in '"%') and all(ord(ch) >= 32 for ch in path)


def allow_parameters(program: str) -> str:
    """The ``cmd.exe`` arguments that replace LumiSync's inbound rules.

    The delete clears any inbound rule for this program first, including the
    block rules Windows adds when its own prompt is declined; then the allow
    rule is added for Private and Domain networks only.
    """
    netsh = _quote(ntpath.join(_system32(), "netsh.exe"))
    exe = _quote(program)
    commands = (
        f"{netsh} advfirewall firewall delete rule name=all dir=in program={exe}"
        f" & {netsh} advfirewall firewall add rule name={_quote(RULE_NAME)} dir=in action=allow"
        f" program={exe} enable=yes profile=private,domain protocol=any"
        f" description={_quote(RULE_DESCRIPTION)}"
    )
    # /d skips AutoRun commands, /v:off keeps "!" literal, and /s strips
    # exactly the outer quotes so the inner ones reach netsh intact.
    return f"/d /v:off /s /c {_quote(commands)}"


_dlls: dict[str, Any] = {}


def _shell32() -> Any:
    if sys.platform != "win32":
        raise OSError("ShellExecuteExW exists only on Windows")
    if "shell32" not in _dlls:
        dll = ctypes.WinDLL("shell32", use_last_error=True)
        dll.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
        dll.ShellExecuteExW.restype = ctypes.c_int
        _dlls["shell32"] = dll
    return _dlls["shell32"]


def _kernel32() -> Any:
    if sys.platform != "win32":
        raise OSError("kernel32 exists only on Windows")
    if "kernel32" not in _dlls:
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        dll.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        dll.WaitForSingleObject.restype = ctypes.c_uint32
        dll.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        dll.GetExitCodeProcess.restype = ctypes.c_int
        dll.CloseHandle.argtypes = [ctypes.c_void_p]
        dll.CloseHandle.restype = ctypes.c_int
        _dlls["kernel32"] = dll
    return _dlls["kernel32"]


def _last_error() -> int:
    if sys.platform != "win32":
        return 0
    return ctypes.get_last_error()


def allow(
    program: str,
    *,
    hwnd: Optional[int] = None,
    timeout: float = ALLOW_TIMEOUT_SECONDS,
) -> tuple[bool, str]:
    """Add LumiSync's inbound rule with one UAC prompt. Never raises.

    Returns ``(True, "allowed")``, ``(False, CANCELLED)`` when the user
    declines the prompt, or ``(False, message)`` with a sentence for the user.
    Blocks until the prompt is answered: run it off the GUI thread.
    """
    if not _safe_for_cmd(program):
        return False, "LumiSync's install folder has characters Windows Firewall rules can't use."
    process = None
    kernel32 = None
    try:
        shell32 = _shell32()
        kernel32 = _kernel32()
        info = SHELLEXECUTEINFOW()
        info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
        info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
        info.hwnd = hwnd or None
        info.lpVerb = "runas"
        info.lpFile = ntpath.join(_system32(), "cmd.exe")
        info.lpParameters = allow_parameters(program)
        info.lpDirectory = _system32()
        info.nShow = SW_HIDE
        # ShellExecuteEx may hand off to shell extensions, which need COM.
        with _com_thread():
            started = shell32.ShellExecuteExW(ctypes.pointer(info))
            error = _last_error() if not started else 0
        if not started:
            if error == ERROR_CANCELLED:
                return False, CANCELLED
            return False, f"Windows couldn't start the firewall change (error {error})."
        process = info.hProcess
        if not process:
            # Nothing to wait on; the re-check afterwards tells the truth.
            return True, "allowed"
        if kernel32.WaitForSingleObject(process, int(max(0.0, timeout) * 1000)) != WAIT_OBJECT_0:
            return False, "Windows Firewall didn't finish the change in time."
        code = ctypes.c_uint32(0)
        if not kernel32.GetExitCodeProcess(process, ctypes.pointer(code)):
            return True, "allowed"
        if code.value != 0:
            return False, f"Windows Firewall didn't accept the rule (netsh exit code {code.value})."
        return True, "allowed"
    except Exception as exc:
        logger.debug("Adding the Windows Firewall rule failed", exc_info=True)
        return False, f"Couldn't change Windows Firewall ({type(exc).__name__})."
    finally:
        if process and kernel32 is not None:
            try:
                kernel32.CloseHandle(process)
            except Exception:
                pass


__all__ = [
    "CANCELLED",
    "FirewallRule",
    "FirewallState",
    "LISTEN_PORTS",
    "RULE_DESCRIPTION",
    "RULE_NAME",
    "STATUS_ALLOWED",
    "STATUS_BLOCKED",
    "STATUS_MISSING",
    "STATUS_OFF",
    "STATUS_PUBLIC",
    "STATUS_UNKNOWN",
    "allow",
    "allow_parameters",
    "check",
    "evaluate",
    "normalize_program",
    "program_path",
]
