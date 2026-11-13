"""Windows Firewall detection, the elevated Allow, and where the app shows it.

Nothing here touches the real firewall: the policy is evaluated from plain
values or stand-in COM objects, ShellExecuteExW and the process waits are
fakes, and background tasks are completed by hand on the test thread.
"""

import os
import re
import sys
import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QApplication, QWidget

from lumisync.gui.controllers import firewall_monitor as monitor_module
from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.controllers.firewall_monitor import FirewallMonitor
from lumisync.gui.main_window import LumiSyncMainWindow
from lumisync.gui.utils import firewall
from lumisync.gui.utils.firewall import (
    ACTION_ALLOW,
    ACTION_BLOCK,
    DIRECTION_IN,
    PROFILE_DOMAIN,
    PROFILE_PRIVATE,
    PROFILE_PUBLIC,
    PROTOCOL_ANY,
    PROTOCOL_UDP,
    FirewallRule,
    FirewallState,
)
from lumisync.gui.views import devices_view as devices_view_module
from lumisync.gui.views import settings_page as settings_page_module
from lumisync.gui.views.devices_view import DevicesView
from lumisync.gui.views.settings_page import SettingsPage

ROOT = Path(__file__).resolve().parents[1]
EXE = r"C:\Users\Fixture\AppData\Local\Programs\LumiSync\LumiSync.exe"
OTHER = r"C:\Users\Fixture\Downloads\LumiSync.exe"
ALL = PROFILE_DOMAIN | PROFILE_PRIVATE | PROFILE_PUBLIC
PROTOCOL_TCP = 6
LAMP = {"mac": "fixture-lamp", "name": "Desk lamp", "model": "H619C", "sku": "H619C", "transport": "lan",
        "ip": "192.0.2.5"}


def rule(application=EXE, **fields) -> FirewallRule:
    fields.setdefault("profiles", PROFILE_PRIVATE)
    fields.setdefault("protocol", PROTOCOL_UDP)
    return FirewallRule(application=application, **fields)


def evaluate(*rules, active=PROFILE_PRIVATE, enabled=ALL, block_all=0, program=EXE) -> FirewallState:
    return firewall.evaluate(
        program, active_profiles=active, enabled_profiles=enabled, block_all_profiles=block_all, rules=rules,
    )


class EvaluateTests(unittest.TestCase):
    def test_firewall_off_for_every_active_profile_is_off(self):
        self.assertEqual(evaluate(enabled=0).status, firewall.STATUS_OFF)
        # On only for a network type that is not in use.
        self.assertEqual(evaluate(active=PROFILE_PRIVATE, enabled=PROFILE_PUBLIC).status, firewall.STATUS_OFF)
        self.assertEqual(evaluate(rule(action=ACTION_BLOCK), enabled=0).status, firewall.STATUS_OFF,
                         "a block rule means nothing while the firewall is off")
        self.assertFalse(evaluate(enabled=0).can_allow)

    def test_an_allow_rule_for_the_program_is_allowed(self):
        state = evaluate(rule())
        self.assertEqual(state.status, firewall.STATUS_ALLOWED)
        self.assertFalse(state.can_allow)
        self.assertEqual(evaluate(rule(protocol=PROTOCOL_ANY, profiles=0x7FFFFFFF)).status, firewall.STATUS_ALLOWED)
        self.assertEqual(
            evaluate(rule(profiles=PROFILE_PRIVATE | PROFILE_DOMAIN), active=PROFILE_DOMAIN).status,
            firewall.STATUS_ALLOWED,
        )

    def test_no_rule_for_the_program_is_missing(self):
        self.assertEqual(evaluate().status, firewall.STATUS_MISSING)
        self.assertTrue(evaluate().can_allow)
        # Old download copies have Windows' own rules; the installed exe does not.
        self.assertEqual(evaluate(rule(OTHER)).status, firewall.STATUS_MISSING)
        self.assertEqual(evaluate(rule(None)).status, firewall.STATUS_MISSING, "port rules without a program")
        self.assertEqual(evaluate(rule(profiles=PROFILE_DOMAIN)).status, firewall.STATUS_MISSING,
                         "a rule for another network type does not cover this one")
        self.assertEqual(evaluate(rule(enabled=False)).status, firewall.STATUS_MISSING)
        self.assertEqual(evaluate(rule(direction=2)).status, firewall.STATUS_MISSING, "outbound rules")
        self.assertIn("Private", evaluate().detail)

    def test_a_matching_block_rule_is_blocked_even_beside_an_allow_rule(self):
        state = evaluate(rule(), rule(action=ACTION_BLOCK))
        self.assertEqual(state.status, firewall.STATUS_BLOCKED)
        self.assertFalse(state.block_all)
        self.assertTrue(state.can_allow, "Allow deletes the block rule before adding its own")
        # Windows' declined-prompt rules come in UDP/TCP pairs; TCP alone does not block our ports.
        self.assertEqual(evaluate(rule(), rule(action=ACTION_BLOCK, protocol=PROTOCOL_TCP)).status,
                         firewall.STATUS_ALLOWED)
        self.assertEqual(evaluate(rule(), rule(action=ACTION_BLOCK, profiles=PROFILE_DOMAIN)).status,
                         firewall.STATUS_ALLOWED, "a block rule for an inactive network type")
        self.assertEqual(evaluate(rule(), rule(action=ACTION_BLOCK, enabled=False)).status,
                         firewall.STATUS_ALLOWED)

    def test_block_all_inbound_on_an_active_profile_is_blocked_without_allow(self):
        state = evaluate(rule(), block_all=PROFILE_PRIVATE)
        self.assertEqual(state.status, firewall.STATUS_BLOCKED)
        self.assertTrue(state.block_all)
        self.assertFalse(state.can_allow, "no rule gets past Block all incoming connections")
        self.assertEqual(evaluate(rule(), block_all=PROFILE_DOMAIN).status, firewall.STATUS_ALLOWED)
        self.assertEqual(evaluate(rule(), enabled=PROFILE_PUBLIC, block_all=PROFILE_PRIVATE).status,
                         firewall.STATUS_OFF, "block-all on a profile whose firewall is off")

    def test_public_only_network_is_public_and_never_offers_allow(self):
        state = evaluate(active=PROFILE_PUBLIC)
        self.assertEqual(state.status, firewall.STATUS_PUBLIC)
        self.assertFalse(state.can_allow)
        # LumiSync's own rule (private, domain) leaves a Public network uncovered.
        self.assertEqual(evaluate(rule(profiles=PROFILE_PRIVATE | PROFILE_DOMAIN), active=PROFILE_PUBLIC).status,
                         firewall.STATUS_PUBLIC)
        # A rule the user made for Public networks does cover it.
        self.assertEqual(evaluate(rule(profiles=PROFILE_PUBLIC), active=PROFILE_PUBLIC).status,
                         firewall.STATUS_ALLOWED)
        self.assertEqual(evaluate(rule(action=ACTION_BLOCK, profiles=PROFILE_PUBLIC), active=PROFILE_PUBLIC).status,
                         firewall.STATUS_BLOCKED)

    def test_a_public_adapter_beside_a_private_network_does_not_ask_for_allow(self):
        both = PROFILE_PRIVATE | PROFILE_PUBLIC
        self.assertEqual(evaluate(rule(profiles=PROFILE_PRIVATE | PROFILE_DOMAIN), active=both).status,
                         firewall.STATUS_ALLOWED)
        self.assertEqual(evaluate(active=both).status, firewall.STATUS_MISSING)
        self.assertEqual(evaluate(rule(), rule(action=ACTION_BLOCK, profiles=PROFILE_PUBLIC), active=both).status,
                         firewall.STATUS_ALLOWED, "traffic on the Private adapter is allowed")

    def test_environment_variables_in_the_rule_path_are_expanded(self):
        local = r"C:\Users\Fixture\AppData\Local"
        with patch.dict(os.environ, {"LOCALAPPDATA": local}):
            state = evaluate(rule(r"%LOCALAPPDATA%\Programs\LumiSync\LumiSync.exe"))
        self.assertEqual(state.status, firewall.STATUS_ALLOWED)

    def test_path_comparison_ignores_case_and_separators(self):
        self.assertEqual(evaluate(rule(EXE.lower())).status, firewall.STATUS_ALLOWED)
        self.assertEqual(evaluate(rule(EXE.upper())).status, firewall.STATUS_ALLOWED)
        self.assertEqual(evaluate(rule(EXE.replace("\\", "/"))).status, firewall.STATUS_ALLOWED)
        self.assertEqual(evaluate(rule(EXE.replace(r"\LumiSync\L", r"\LumiSync\.\L"))).status,
                         firewall.STATUS_ALLOWED)

    def test_tcp_only_rules_do_not_count(self):
        self.assertEqual(evaluate(rule(protocol=PROTOCOL_TCP)).status, firewall.STATUS_MISSING)
        self.assertEqual(evaluate(rule(protocol=PROTOCOL_TCP), rule(protocol=PROTOCOL_UDP)).status,
                         firewall.STATUS_ALLOWED)

    def test_port_restricted_rules_must_cover_both_listen_ports(self):
        cases = {
            "*": firewall.STATUS_ALLOWED,
            "": firewall.STATUS_ALLOWED,
            "4002,6667": firewall.STATUS_ALLOWED,
            " 6667 , 4002 ": firewall.STATUS_ALLOWED,
            "4000-4010,6000-7000": firewall.STATUS_ALLOWED,
            "6667": firewall.STATUS_MISSING,
            "4002": firewall.STATUS_MISSING,
            "80,443": firewall.STATUS_MISSING,
            "RPC": firewall.STATUS_MISSING,
            "4003-6666": firewall.STATUS_MISSING,
        }
        for ports, expected in cases.items():
            with self.subTest(ports=ports):
                self.assertEqual(evaluate(rule(local_ports=ports)).status, expected)
        self.assertEqual(evaluate(rule(local_ports="6667"), rule(local_ports="4002")).status,
                         firewall.STATUS_ALLOWED, "two rules can cover the ports between them")
        self.assertEqual(evaluate(rule(), rule(action=ACTION_BLOCK, local_ports="4002")).status,
                         firewall.STATUS_BLOCKED, "blocking either port blocks discovery")
        self.assertEqual(evaluate(rule(), rule(action=ACTION_BLOCK, local_ports="80")).status,
                         firewall.STATUS_ALLOWED)


class _FakeRule:
    def __init__(self, application, **fields):
        self.ApplicationName = application
        self._fields = {
            "Name": "fixture", "Direction": DIRECTION_IN, "Enabled": True, "Action": ACTION_ALLOW,
            "Protocol": PROTOCOL_UDP, "LocalPorts": "*", "Profiles": PROFILE_PRIVATE, **fields,
        }

    def __getattr__(self, name):
        fields = self.__dict__.get("_fields", {})
        if name not in fields:
            raise AttributeError(name)
        if self.ApplicationName != EXE:
            raise AssertionError(f"read {name} of another program's rule")
        return fields[name]


class _FakePolicy:
    def __init__(self, active=PROFILE_PRIVATE, enabled=ALL, block_all=0, rules=()):
        self.CurrentProfileTypes = active
        self._enabled = enabled
        self._block_all = block_all
        self._rules = list(rules)
        self.rules_read = False

    def FirewallEnabled(self, profile):  # noqa: N802 - COM API
        return bool(self._enabled & profile)

    def BlockAllInboundTraffic(self, profile):  # noqa: N802 - COM API
        return bool(self._block_all & profile)

    @property
    def Rules(self):  # noqa: N802 - COM API
        self.rules_read = True
        return iter(self._rules)


class LivePolicyTests(unittest.TestCase):
    def test_policy_members_are_read_into_the_evaluation(self):
        policy = _FakePolicy(rules=[_FakeRule(OTHER), _FakeRule(None), _FakeRule(EXE)])
        self.assertEqual(firewall._read_policy(EXE, policy).status, firewall.STATUS_ALLOWED)
        blocked = _FakePolicy(rules=[_FakeRule(EXE), _FakeRule(EXE, Action=ACTION_BLOCK)])
        self.assertEqual(firewall._read_policy(EXE, blocked).status, firewall.STATUS_BLOCKED)
        self.assertEqual(firewall._read_policy(EXE, _FakePolicy(block_all=PROFILE_PRIVATE)).status,
                         firewall.STATUS_BLOCKED)

    def test_rules_are_not_enumerated_while_the_firewall_is_off(self):
        policy = _FakePolicy(enabled=0, rules=[_FakeRule(EXE)])
        self.assertEqual(firewall._read_policy(EXE, policy).status, firewall.STATUS_OFF)
        self.assertFalse(policy.rules_read)

    def test_any_failure_is_unknown(self):
        with patch.object(firewall, "_query_live", side_effect=RuntimeError("fixture COM failure")), \
                patch.object(firewall, "_com_thread", nullcontext):
            state = firewall.check(EXE)
        self.assertEqual(state.status, firewall.STATUS_UNKNOWN)
        self.assertFalse(state.can_allow)

    def test_only_the_frozen_windows_app_is_checked(self):
        self.assertIsNone(firewall.program_path(), "source runs are not checked")

    @unittest.skipUnless(sys.platform == "win32", "the installed app is a Windows build")
    def test_frozen_windows_app_checks_its_own_exe(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", EXE):
            self.assertEqual(firewall.program_path(), EXE)


class _FakeShell:
    def __init__(self, started=1, process=4242):
        self.started = started
        self.process = process
        self.calls = []

    def ShellExecuteExW(self, pointer):  # noqa: N802 - Win32 API
        info = pointer.contents
        self.calls.append({
            "verb": info.lpVerb, "file": info.lpFile, "parameters": info.lpParameters, "show": info.nShow,
            "mask": info.fMask, "hwnd": info.hwnd, "size": info.cbSize,
        })
        if self.started:
            info.hProcess = self.process
        return self.started


class _FakeKernel:
    def __init__(self, wait=firewall.WAIT_OBJECT_0, exit_code=0):
        self.wait = wait
        self.exit_code = exit_code
        self.waited = []
        self.closed = []

    def WaitForSingleObject(self, handle, milliseconds):  # noqa: N802 - Win32 API
        self.waited.append((handle, milliseconds))
        return self.wait

    def GetExitCodeProcess(self, handle, pointer):  # noqa: N802 - Win32 API
        pointer.contents.value = self.exit_code
        return 1

    def CloseHandle(self, handle):  # noqa: N802 - Win32 API
        self.closed.append(handle)
        return 1


class AllowTests(unittest.TestCase):
    def run_allow(self, shell, kernel, program=EXE, last_error=0, **kwargs):
        with patch.object(firewall, "_shell32", return_value=shell), \
                patch.object(firewall, "_kernel32", return_value=kernel), \
                patch.object(firewall, "_last_error", return_value=last_error), \
                patch.object(firewall, "_com_thread", nullcontext):
            return firewall.allow(program, **kwargs)

    @staticmethod
    def commands(parameters: str) -> list[str]:
        prefix = '/d /v:off /s /c "'
        assert parameters.startswith(prefix) and parameters.endswith('"'), parameters
        return parameters[len(prefix):-1].split(" & ")

    def test_success_runs_one_hidden_elevated_cmd_and_waits_for_it(self):
        shell, kernel = _FakeShell(), _FakeKernel()
        self.assertEqual(self.run_allow(shell, kernel, hwnd=77), (True, "allowed"))
        self.assertEqual(len(shell.calls), 1)
        call = shell.calls[0]
        self.assertEqual(call["verb"], "runas")
        self.assertTrue(call["file"].lower().endswith("\\system32\\cmd.exe"), call["file"])
        self.assertEqual(call["show"], firewall.SW_HIDE)
        self.assertTrue(call["mask"] & firewall.SEE_MASK_NOCLOSEPROCESS)
        self.assertEqual(call["hwnd"], 77)
        self.assertEqual(call["size"], firewall.ctypes.sizeof(firewall.SHELLEXECUTEINFOW))
        self.assertEqual(kernel.waited, [(4242, int(firewall.ALLOW_TIMEOUT_SECONDS * 1000))])
        self.assertEqual(kernel.closed, [4242])

    def test_declining_the_uac_prompt_is_cancelled(self):
        shell, kernel = _FakeShell(started=0), _FakeKernel()
        self.assertEqual(self.run_allow(shell, kernel, last_error=firewall.ERROR_CANCELLED),
                         (False, firewall.CANCELLED))
        self.assertEqual(kernel.waited, [])
        self.assertEqual(kernel.closed, [])

    def test_failures_return_a_message(self):
        ok, message = self.run_allow(_FakeShell(started=0), _FakeKernel(), last_error=5)
        self.assertFalse(ok)
        self.assertIn("error 5", message)
        kernel = _FakeKernel(exit_code=1)
        ok, message = self.run_allow(_FakeShell(), kernel)
        self.assertFalse(ok)
        self.assertIn("exit code 1", message)
        self.assertEqual(kernel.closed, [4242])
        kernel = _FakeKernel(wait=0x102)
        ok, message = self.run_allow(_FakeShell(), kernel)
        self.assertFalse(ok)
        self.assertIn("in time", message)
        self.assertEqual(kernel.closed, [4242], "the process handle is closed after a timeout too")
        ok, message = self.run_allow(Mock(ShellExecuteExW=Mock(side_effect=OSError("fixture"))), _FakeKernel())
        self.assertFalse(ok)
        self.assertNotEqual(message, firewall.CANCELLED)

    def test_a_path_with_spaces_is_quoted_for_both_netsh_calls(self):
        program = r"C:\Users\Fixture User\AppData\Local\Programs\Lumi Sync (test)\LumiSync.exe"
        shell = _FakeShell()
        self.assertEqual(self.run_allow(shell, _FakeKernel(), program=program), (True, "allowed"))
        delete, add = self.commands(shell.calls[0]["parameters"])
        quoted = f'program="{program}"'
        self.assertIn(" advfirewall firewall delete rule name=all dir=in " + quoted, delete)
        self.assertTrue(delete.endswith(quoted))
        self.assertIn(quoted, add)
        self.assertIn('add rule name="LumiSync - local devices" dir=in action=allow', add)
        self.assertIn("enable=yes profile=private,domain protocol=any", add)
        self.assertIn(f'description="{firewall.RULE_DESCRIPTION}"', add)
        self.assertNotIn("public", add.split("description=")[0])
        for command in (delete, add):
            self.assertTrue(command.lower().startswith('"') and 'netsh.exe" advfirewall' in command.lower())
            self.assertEqual(command.count('"') % 2, 0, command)

    def test_paths_cmd_would_rewrite_are_refused_without_a_prompt(self):
        for program in (r"C:\Users\%USERNAME%\LumiSync.exe", 'C:\\Users\\a"b\\LumiSync.exe', "", "C:\\a\nb.exe"):
            shell = _FakeShell()
            with self.subTest(program=program):
                ok, _message = self.run_allow(shell, _FakeKernel(), program=program)
                self.assertFalse(ok)
                self.assertEqual(shell.calls, [])


class _Tasks:
    """Stands in for start_task: actions run when the test says so."""

    def __init__(self):
        self.pending = []

    def start(self, action, callback):
        self.pending.append((action, callback))
        return Mock()

    def run_next(self):
        action, callback = self.pending.pop(0)
        callback(action(), None)

    def run_all(self):
        while self.pending:
            self.run_next()


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tasks = _Tasks()
        patcher = patch.object(monitor_module, "start_task", self.tasks.start)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.states = [FirewallState(firewall.STATUS_MISSING, "fixture")]
        self.allow_results = [(True, "allowed")]
        self.check_calls = []
        self.allow_calls = []

        def check(program):
            self.check_calls.append(program)
            return self.states[0] if len(self.states) == 1 else self.states.pop(0)

        def allow(program, hwnd=None):
            self.allow_calls.append((program, hwnd))
            return self.allow_results.pop(0)

        for name, fake in (("check", check), ("allow", allow)):
            patcher = patch.object(firewall, name, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        self._widgets = []

    def tearDown(self):
        for widget in reversed(self._widgets):
            try:
                widget.close()
                widget.deleteLater()
            except RuntimeError:
                pass
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def keep(self, widget):
        self._widgets.append(widget)
        return widget

    def monitor(self, program=EXE) -> FirewallMonitor:
        return FirewallMonitor(program=program)

    def report(self, monitor: FirewallMonitor, status: str, *, block_all: bool = False) -> None:
        """Complete a check that finds *status*."""
        self.states = [FirewallState(status, "fixture", block_all=block_all)]
        self.assertTrue(monitor.check())
        self.tasks.run_all()


class MonitorTests(_QtCase):
    def test_source_runs_check_nothing(self):
        monitor = self.monitor(program=None)
        self.assertFalse(monitor.is_supported())
        self.assertFalse(monitor.check())
        self.assertFalse(monitor.allow())
        self.assertEqual(self.tasks.pending, [])
        self.assertEqual(monitor.state().status, firewall.STATUS_UNKNOWN)

    def test_check_logs_one_info_line_per_result_and_emits_changed(self):
        monitor = self.monitor()
        seen = []
        monitor.changed.connect(seen.append)
        with self.assertLogs("lumisync_gui", level="DEBUG") as logs:
            self.report(monitor, firewall.STATUS_OFF)
            self.report(monitor, firewall.STATUS_OFF)
        self.assertEqual([state.status for state in seen], ["off", "off"])
        info = [line for line in logs.output if line.startswith("INFO")]
        self.assertEqual(len(info), 1, logs.output)
        self.assertIn("Windows Firewall: off", info[0])
        self.assertEqual(self.check_calls, [EXE, EXE])

    def test_page_rechecks_wait_for_the_startup_check_and_a_minute(self):
        monitor = self.monitor()
        self.assertFalse(monitor.recheck_if_stale(), "the window's startup check comes first")
        self.report(monitor, firewall.STATUS_MISSING)
        self.assertFalse(monitor.recheck_if_stale())
        self.assertEqual(self.tasks.pending, [])
        self.assertTrue(monitor.recheck_if_stale(max_age=0))
        self.tasks.run_all()
        with patch.object(monitor_module.time, "monotonic", return_value=10**9):
            self.assertTrue(monitor.recheck_if_stale())
        self.tasks.run_all()
        self.assertEqual(len(self.check_calls), 3)

    def test_a_check_running_during_allow_is_replaced_by_a_fresh_one(self):
        monitor = self.monitor()
        self.report(monitor, firewall.STATUS_MISSING)
        self.assertTrue(monitor.check())  # In flight from before the Allow.
        self.assertTrue(monitor.allow())
        stale_check, allow_task = self.tasks.pending
        self.tasks.pending = [allow_task]
        self.tasks.run_next()
        self.assertTrue(monitor.is_verifying())
        self.states = [FirewallState(firewall.STATUS_MISSING, "stale")]
        stale_check[1](stale_check[0](), None)
        self.assertTrue(monitor.is_verifying(), "the stale result is dropped")
        self.states = [FirewallState(firewall.STATUS_ALLOWED, "fixture")]
        self.tasks.run_all()
        self.assertFalse(monitor.is_verifying())
        self.assertEqual(monitor.state().status, firewall.STATUS_ALLOWED)


class DevicesViewFirewallTests(_QtCase):
    def setUp(self):
        super().setUp()
        with patch("lumisync.gui.controllers.device_controller.devices.get_data",
                   return_value={"devices": [], "selectedDevice": 0}):
            self.controller = DeviceController()
        self.addCleanup(self.controller.shutdown)
        self.controller.devices = [dict(LAMP)]
        self.notices = []
        self.controller.notice.connect(lambda message, kind: self.notices.append((message, kind)))

    def view(self, monitor) -> DevicesView:
        with patch.object(self.controller, "get_groups", return_value=[]), \
                patch.object(self.controller, "refresh_device_states"):
            view = self.keep(DevicesView(self.controller, monitor))
            view.resize(1100, 760)
            view.show()
            self.app.processEvents()
        return view

    def test_bar_shows_for_missing_and_blocked_only(self):
        monitor = self.monitor()
        view = self.view(monitor)
        bar = view.firewall_info_bar
        self.assertTrue(bar.isHidden(), "nothing before the first check")
        for status in ("missing", "blocked"):
            with self.subTest(status=status):
                self.report(monitor, status)
                self.assertFalse(bar.isHidden())
                self.assertFalse(view.info_host.isHidden())
                self.assertEqual(bar.title(), devices_view_module.FIREWALL_TITLE)
                self.assertEqual(bar.message(), devices_view_module.FIREWALL_MESSAGE)
                self.assertEqual(bar.action_button.text(), "Allow")
                self.assertFalse(bar.action_button.isHidden())
        for status in ("off", "allowed", "unknown"):
            with self.subTest(status=status):
                self.report(monitor, status)
                self.assertTrue(bar.isHidden())
                self.assertTrue(view.info_host.isHidden())

    def test_public_and_block_all_explain_without_allow(self):
        monitor = self.monitor()
        view = self.view(monitor)
        bar = view.firewall_info_bar
        self.report(monitor, firewall.STATUS_PUBLIC)
        self.assertFalse(bar.isHidden())
        self.assertEqual(bar.title(), devices_view_module.FIREWALL_PUBLIC_TITLE)
        self.assertIn("Private", bar.message())
        self.assertTrue(bar.action_button.isHidden())
        self.report(monitor, firewall.STATUS_BLOCKED, block_all=True)
        self.assertFalse(bar.isHidden())
        self.assertIn("block all incoming connections", bar.message())
        self.assertTrue(bar.action_button.isHidden())

    def test_allow_success_hides_the_bar_and_rechecks(self):
        monitor = self.monitor()
        view = self.view(monitor)
        self.report(monitor, firewall.STATUS_MISSING)
        view.firewall_info_bar.action_button.click()
        self.assertEqual(len(self.tasks.pending), 1, "Allow runs in the background")
        self.assertFalse(view.firewall_info_bar.action_button.isEnabled(), "one Allow at a time")
        view.firewall_info_bar.action_button.click()
        self.assertEqual(len(self.tasks.pending), 1)
        checks_before = len(self.check_calls)
        self.states = [FirewallState(firewall.STATUS_ALLOWED, "fixture")]
        self.tasks.run_next()  # The elevated netsh call.
        self.assertEqual([program for program, _hwnd in self.allow_calls], [EXE])
        self.assertTrue(view.firewall_info_bar.isHidden(), "hidden as soon as Allow succeeds")
        self.assertEqual(len(self.tasks.pending), 1, "a re-check follows the Allow")
        self.tasks.run_all()
        self.assertEqual(len(self.check_calls), checks_before + 1)
        self.assertTrue(view.firewall_info_bar.isHidden())
        self.assertEqual(self.notices, [])

    def test_cancelled_allow_keeps_the_bar_without_an_error(self):
        monitor = self.monitor()
        view = self.view(monitor)
        self.report(monitor, firewall.STATUS_MISSING)
        self.allow_results = [(False, firewall.CANCELLED)]
        view.firewall_info_bar.action_button.click()
        self.tasks.run_all()
        self.assertFalse(view.firewall_info_bar.isHidden())
        self.assertTrue(view.firewall_info_bar.action_button.isEnabled())
        self.assertEqual(self.notices, [])

    def test_failed_allow_gives_one_toast(self):
        monitor = self.monitor()
        view = self.view(monitor)
        self.report(monitor, firewall.STATUS_BLOCKED)
        self.allow_results = [(False, "Windows Firewall didn't accept the rule (netsh exit code 1).")]
        view.firewall_info_bar.action_button.click()
        self.tasks.run_all()
        self.assertEqual(self.notices, [("Windows Firewall didn't accept the rule (netsh exit code 1).", "error")])
        self.assertFalse(view.firewall_info_bar.isHidden())

    def test_closing_hides_until_the_problem_changes(self):
        monitor = self.monitor()
        view = self.view(monitor)
        bar = view.firewall_info_bar
        self.report(monitor, firewall.STATUS_MISSING)
        bar.close_button.click()
        self.assertTrue(bar.isHidden())
        self.assertTrue(view.info_host.isHidden())
        self.report(monitor, firewall.STATUS_MISSING)
        self.assertTrue(bar.isHidden(), "the same problem stays dismissed")
        self.report(monitor, firewall.STATUS_BLOCKED)
        self.assertFalse(bar.isHidden(), "a different problem shows again")
        bar.close_button.click()
        self.report(monitor, firewall.STATUS_ALLOWED)
        self.report(monitor, firewall.STATUS_BLOCKED)
        self.assertFalse(bar.isHidden(), "a problem that comes back shows again")

    def test_showing_the_page_rechecks_at_most_once_a_minute(self):
        monitor = self.monitor()
        view = self.view(monitor)
        self.assertEqual(self.check_calls, [], "no check before the window's startup check")
        self.report(monitor, firewall.STATUS_MISSING)
        view.hide()
        view.show()
        self.assertEqual(self.tasks.pending, [])
        view.hide()
        with patch.object(monitor_module.time, "monotonic", return_value=10**9):
            view.show()
        self.assertEqual(len(self.tasks.pending), 1)

    def test_a_view_without_a_monitor_never_shows_the_bar(self):
        view = self.view(None)
        view._update_info_bars()
        self.assertTrue(view.firewall_info_bar.isHidden())


class SettingsCardTests(_QtCase):
    def setUp(self):
        super().setUp()
        temporary = TemporaryDirectory(prefix="lumisync-firewall-test-")
        self.addCleanup(temporary.cleanup)
        self.settings = QSettings(str(Path(temporary.name) / "ui.ini"), QSettings.Format.IniFormat)

    def page(self, monitor):
        window = self.keep(QWidget())
        window.firewall_monitor = monitor
        window.show_notice = Mock()
        page = SettingsPage(self.settings, window)
        return page, window

    def test_card_is_hidden_when_nothing_is_checked(self):
        page, _window = self.page(self.monitor(program=None))
        self.assertTrue(page.firewall_card.isHidden())
        page, _window = self.page(None)
        self.assertTrue(page.firewall_card.isHidden())
        self.assertTrue(page.firewall_allow_button.isHidden())

    def test_description_and_button_follow_the_state(self):
        monitor = self.monitor()
        page, _window = self.page(monitor)
        card, button = page.firewall_card, page.firewall_allow_button
        self.assertFalse(card.isHidden())
        self.assertIs(card.parentWidget(), page.general_group)
        self.assertEqual(card.title_label.text(), "Network access")
        self.assertEqual(card.description_label.text(), "Checking…")
        self.assertTrue(button.isHidden())
        expected = {
            "allowed": ("Allowed through Windows Firewall", False),
            "off": ("Windows Firewall is off", False),
            "blocked": ("Blocked by Windows Firewall", True),
            "missing": ("Not allowed through Windows Firewall yet", True),
            "public": ("Your network is set to Public in Windows", False),
            "unknown": ("Checking…", False),
        }
        for status, (text, allow) in expected.items():
            with self.subTest(status=status):
                self.report(monitor, status)
                self.assertEqual(card.description_label.text(), text)
                self.assertEqual(not button.isHidden(), allow)
        self.report(monitor, firewall.STATUS_BLOCKED, block_all=True)
        self.assertEqual(card.description_label.text(), "Blocked by Windows Firewall")
        self.assertTrue(button.isHidden(), "Allow cannot beat Block all incoming connections")

    def test_allow_from_settings_toasts_failures_once_and_ignores_cancel(self):
        monitor = self.monitor()
        page, window = self.page(monitor)
        self.report(monitor, firewall.STATUS_MISSING)
        self.allow_results = [(False, firewall.CANCELLED), (False, "Windows couldn't start the firewall change.")]
        page.firewall_allow_button.click()
        self.assertFalse(page.firewall_allow_button.isEnabled())
        self.tasks.run_all()
        window.show_notice.assert_not_called()
        self.assertTrue(page.firewall_allow_button.isEnabled())
        page.firewall_allow_button.click()
        self.tasks.run_all()
        window.show_notice.assert_called_once_with("Windows couldn't start the firewall change.", "error")

    def test_successful_allow_hides_the_button_and_rechecks(self):
        monitor = self.monitor()
        page, _window = self.page(monitor)
        self.report(monitor, firewall.STATUS_MISSING)
        page.firewall_allow_button.click()
        self.states = [FirewallState(firewall.STATUS_ALLOWED, "fixture")]
        self.tasks.run_next()
        self.assertTrue(page.firewall_allow_button.isHidden())
        self.assertEqual(page.firewall_card.description_label.text(), settings_page_module.FIREWALL_CHECKING)
        self.tasks.run_all()
        self.assertEqual(page.firewall_card.description_label.text(), "Allowed through Windows Firewall")


class WiringTests(unittest.TestCase):
    def test_startup_check_goes_through_the_monitor(self):
        window = SimpleNamespace(firewall_monitor=Mock(check=Mock(return_value=False)))
        LumiSyncMainWindow.check_firewall_on_startup(window)  # type: ignore[arg-type]
        window.firewall_monitor.check.assert_called_once_with()
        source = (ROOT / "lumisync" / "gui" / "main_window.py").read_text(encoding="utf-8")
        self.assertIn("FirewallMonitor(self)", source)
        self.assertIn("DevicesView(self.device_controller, self.firewall_monitor)", source)
        self.assertRegex(source, r"QTimer\.singleShot\(3000, window\.check_firewall_on_startup\)")

    def test_pyinstaller_bundles_the_com_client(self):
        spec = (ROOT / "packaging" / "pyinstaller" / "lumisync_onedir.spec").read_text(encoding="utf-8")
        for module in ('"pythoncom"', '"win32com.client"'):
            self.assertIn(module, spec)


class InstallerGuardTests(unittest.TestCase):
    def setUp(self):
        self.script = (ROOT / "packaging" / "windows" / "LumiSync.iss").read_text(encoding="utf-8")

    def procedure(self, name: str) -> str:
        match = re.search(rf"^procedure {name};(.*?)^end;", self.script, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match, name)
        assert match is not None
        return match.group(1)

    def test_firewall_task_and_rule_match_the_app(self):
        self.assertRegex(
            self.script,
            r'Name: "firewall"; Description: "Allow \{#MyAppName\} through Windows Firewall to find lights '
            r'on your network \(asks for administrator approval\)"; GroupDescription: "Network:"; '
            r"Flags: checkedonce",
        )
        self.assertIn(f"FirewallRuleName = '{firewall.RULE_NAME}';", self.script)
        self.assertIn(f"FirewallRuleDescription = '{firewall.RULE_DESCRIPTION}';", self.script)
        # The existing tasks and per-user install stay as they were.
        self.assertIn('Name: "autostart"', self.script)
        self.assertIn('Name: "desktopicon"', self.script)
        self.assertIn("PrivilegesRequired=lowest", self.script)

    def test_rule_is_added_in_code_never_during_updates(self):
        add = self.procedure("AddFirewallRule")
        self.assertIn("WizardIsTaskSelected('firewall')", add)
        update_check = add.index("ExpandConstant('{param:update|0}') = '1'")
        exists_check = add.index("if FirewallRuleExists then")
        elevation = add.index("ShellExec(")
        self.assertLess(update_check, elevation)
        self.assertLess(exists_check, elevation)
        self.assertIn("'runas'", add)
        self.assertIn("ewWaitUntilTerminated", add)
        self.assertIn("SW_HIDE", add)
        self.assertIn("Log(", add)
        self.assertIn("advfirewall firewall delete rule name=all dir=in program=", add)
        self.assertIn("dir=in action=allow", add)
        self.assertIn("profile=private,domain protocol=any", add)
        self.assertIn("advfirewall firewall show rule name=", self.script)
        run_section = self.script.split("\n[Run]\n", 1)[1]
        self.assertNotIn("netsh", run_section.lower(), "a [Run] entry would show an error on a declined UAC")
        self.assertRegex(self.script, r"ssPostInstall then\s+begin\s+ApplyAutostartChoice;\s+AddFirewallRule;")

    def test_uninstall_removes_the_rule_best_effort(self):
        remove = self.procedure("RemoveFirewallRule")
        self.assertIn("if not FirewallRuleExists then", remove)
        self.assertIn("'runas'", remove)
        self.assertIn("advfirewall firewall delete rule name=' + Quoted(FirewallRuleName)", remove)
        self.assertRegex(self.script, r"usPostUninstall then\s+RemoveFirewallRule;")


if __name__ == "__main__":
    unittest.main()
