"""Local and account status passes run independently and stay bounded."""

import os
import socket
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from lumisync import groups
from lumisync.accounts.errors import AccountError
from lumisync.gui.controllers.device_controller import DeviceController
from lumisync.gui.controllers.discovery import ACCOUNT_STATUS_ERROR, DeviceStatusWorker

LAN = {"mac": "fixture-lan", "model": "H619C", "ip": "192.0.2.1"}
ACCOUNT_A = {"mac": "fixture-a", "device_id": "fixture-a", "transport": "tuya_cloud", "account_id": "fixture"}
ACCOUNT_B = {"mac": "fixture-b", "device_id": "fixture-b", "transport": "govee_account", "account_id": "fixture"}
LINKED_STRIP = {"mac": "tuya:fixture-strip", "device_id": "fixture-strip", "transport": "tuya",
                "ip": "192.0.2.5", "account_id": "fixture", "cloud_transport": "tuya_cloud"}


def key(device):
    return groups.device_key(device)


class StatusPassControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch.object(DeviceController, "_init_devices"):
            self.controller = DeviceController()
        self.controller.devices = [LAN, ACCOUNT_A, ACCOUNT_B, LINKED_STRIP]
        self.launched = []

        def launch(indexed, *, account, prefer_keys=()):
            self.launched.append((account, [key(device) for _, device in indexed], set(prefer_keys)))

        patcher = patch.object(self.controller, "_launch_status_worker", side_effect=launch)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.controller.account_status_thread = None
        self.controller.status_thread = None
        self.controller.shutdown()

    def passes(self, account):
        return [entry for entry in self.launched if entry[0] is account]

    def test_account_pass_runs_at_most_once_per_interval(self):
        for _ in range(5):
            self.controller.refresh_device_states()  # Devices page show / rebuild
        for _ in range(3):
            self.controller.refresh_account_device_states()  # 60 s poll

        account = self.passes(True)
        self.assertEqual(len(account), 1)
        self.assertEqual(account[0][1], [key(ACCOUNT_A), key(ACCOUNT_B)])
        # Later requests wait for the next window instead of hitting the vendor.
        self.assertTrue(self.controller._account_pass_timer.isActive())
        # The linked strip's LAN works, so it stays on the local poll.
        self.assertNotIn(key(LINKED_STRIP), self.controller._account_status_queue)

        self.controller._last_account_pass -= self.controller.ACCOUNT_STATUS_INTERVAL + 1
        self.controller._start_account_pass()  # The window timer fires.
        account = self.passes(True)
        self.assertEqual(len(account), 2)
        self.assertEqual(sorted(account[1][1]), sorted([key(ACCOUNT_A), key(ACCOUNT_B)]))
        self.assertFalse(self.controller._account_status_queue)

        # Once its LAN path fails, the 60 s poll reads it through the account.
        self.controller._cloud_preferred_until[key(LINKED_STRIP)] = time.time() + 60
        self.controller._last_account_pass -= self.controller.ACCOUNT_STATUS_INTERVAL + 1
        self.controller.refresh_account_device_states()
        account = self.passes(True)
        self.assertEqual(len(account), 3)
        self.assertIn(key(LINKED_STRIP), account[2][1])
        self.assertIn(key(LINKED_STRIP), account[2][2])  # Asked through the account first.

    def test_a_read_for_one_device_is_not_held_back_by_the_window(self):
        self.controller.refresh_device_states()
        self.assertEqual(len(self.passes(True)), 1)
        self.controller.refresh_device_states()  # Bulk: waits for the window.
        self.assertEqual(len(self.passes(True)), 1)

        # A command confirmation / energy panel read for one account device.
        self.controller.refresh_device_state_at(1)
        account = self.passes(True)
        self.assertEqual(len(account), 2)
        self.assertEqual(account[1][1], [key(ACCOUNT_A)])
        self.assertIn(key(ACCOUNT_B), self.controller._account_status_queue)

    def test_a_running_account_pass_does_not_block_a_local_refresh(self):
        self.controller.account_status_thread = Mock()  # Slow vendor pass in flight.
        self.controller.refresh_local_device_states()
        self.controller.refresh_device_states()

        local = self.passes(False)
        self.assertEqual(len(local), 2)
        self.assertEqual(local[0][1], [key(LAN), key(LINKED_STRIP)])
        self.assertEqual(self.passes(True), [])
        self.assertIn(key(ACCOUNT_A), self.controller._account_status_queue)

    def test_local_failures_join_the_account_pass_without_bypassing_its_window(self):
        self.controller.refresh_device_states()
        self.assertEqual(len(self.passes(True)), 1)

        # The 15 s local poll found the strip's LAN path down: queued, not sent.
        self.controller._on_account_status_needed([key(LINKED_STRIP)])
        self.assertEqual(len(self.passes(True)), 1)
        self.assertTrue(self.controller._account_pass_timer.isActive())
        self.assertTrue(self.controller._account_status_queue[key(LINKED_STRIP)])

        # A read the user is waiting on (command confirmation) goes at once.
        self.controller._account_status_queue.clear()
        self.controller.refresh_device_state_at(3)
        self.assertEqual(len(self.passes(True)), 1)  # Local read first.
        self.controller._on_account_status_needed([key(LINKED_STRIP)])
        account = self.passes(True)
        self.assertEqual(len(account), 2)
        self.assertEqual(account[1][1], [key(LINKED_STRIP)])
        self.assertEqual(account[1][2], {key(LINKED_STRIP)})

    def test_shutdown_interrupts_the_account_pass_too(self):
        thread = Mock()
        thread.wait.return_value = False
        self.controller.account_status_thread = thread
        self.assertFalse(self.controller.shutdown())
        thread.requestInterruption.assert_called_once_with()
        self.controller.account_status_thread = None


class UnvisitedOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_devices_a_pass_did_not_reach_go_first_next_time(self):
        with patch.object(DeviceController, "_init_devices"):
            controller = DeviceController()
        controller.devices = [LAN, LINKED_STRIP]
        workers = []

        def worker(indexed, **kwargs):
            workers.append(([key(device) for _, device in indexed], kwargs))
            return Mock()

        controller._on_status_unvisited([key(LINKED_STRIP)])
        try:
            with patch("lumisync.gui.controllers.device_controller.DeviceStatusWorker", side_effect=worker), \
                    patch("lumisync.gui.controllers.device_controller.QThread"):
                controller.refresh_local_device_states()
        finally:
            controller.status_thread = None
            controller.shutdown()
        self.assertEqual(workers[0][0], [key(LINKED_STRIP), key(LAN)])
        self.assertEqual(workers[0][1]["budget"], DeviceController.STATUS_PASS_BUDGET)
        self.assertTrue(workers[0][1]["defer_account_fallback"])
        self.assertEqual(controller._unvisited_status_keys, [])


class StatusWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def run_worker(self, worker):
        updates, needed, unvisited = [], [], []
        worker.state_updated.connect(lambda _index, state: updates.append(state))
        worker.account_needed.connect(needed.extend)
        worker.unvisited.connect(unvisited.extend)
        worker.run()
        return updates, needed, unvisited

    def test_local_pass_hands_the_account_fallback_to_the_account_pass(self):
        local = Mock()
        local.query_status.return_value = None
        local.device = {"tuya_paint_dp": 61}
        worker = DeviceStatusWorker([(0, LINKED_STRIP)], defer_account_fallback=True)
        with patch("lumisync.gui.controllers.discovery.tuya_presence.presence_age", return_value=None), \
                patch("lumisync.gui.controllers.discovery.pool.acquire", return_value=local), \
                patch("lumisync.gui.controllers.discovery.pool.is_pooled", return_value=True), \
                patch("lumisync.gui.controllers.discovery.create_adapter",
                      side_effect=AssertionError("no vendor call in the local pass")):
            updates, needed, _ = self.run_worker(worker)
        self.assertEqual(needed, [key(LINKED_STRIP)])
        # Only what the device taught us is sent; no Offline flash meanwhile.
        self.assertEqual(updates, [{"device_key": key(LINKED_STRIP), "learned": {"tuya_paint_dp": 61}}])

    def test_pass_stops_when_its_budget_is_spent_and_reports_the_rest(self):
        second = {"mac": "fixture-lan-2", "ip": "192.0.2.2"}
        worker = DeviceStatusWorker([(0, LAN), (1, second)], budget=0)
        with patch("lumisync.gui.controllers.discovery.connection.create_lan_socket", return_value=Mock()), \
                patch("lumisync.gui.controllers.discovery.connection.query_status",
                      side_effect=AssertionError("budget already spent")):
            updates, _, unvisited = self.run_worker(worker)
        self.assertEqual(updates, [])
        self.assertEqual(unvisited, [key(LAN), key(second)])

    def test_pass_checks_for_quit_between_the_local_read_and_the_account_read(self):
        local = Mock()
        local.query_status.return_value = None
        local.device = {}
        worker = DeviceStatusWorker([(0, LINKED_STRIP)], budget=60)
        # Deadline, then the loop check; the slow local read spends the budget.
        times = iter([0.0, 0.0])
        clock = Mock(wraps=time)
        clock.monotonic.side_effect = lambda: next(times, 120.0)
        with patch("lumisync.gui.controllers.discovery.tuya_presence.presence_age", return_value=None), \
                patch("lumisync.gui.controllers.discovery.pool.acquire", return_value=local), \
                patch("lumisync.gui.controllers.discovery.pool.is_pooled", return_value=True), \
                patch("lumisync.gui.controllers.discovery.time", clock), \
                patch("lumisync.gui.controllers.discovery.create_adapter",
                      side_effect=AssertionError("budget spent before the vendor call")):
            updates, _, unvisited = self.run_worker(worker)
        self.assertEqual(updates, [])
        self.assertEqual(unvisited, [key(LINKED_STRIP)])

    def test_account_read_failures_never_show_raw_exception_text(self):
        device = {"transport": "tuya_cloud", "device_id": "fixture-plug", "mac": "fixture-plug",
                  "account_id": "fixture", "cloud_transport": "tuya_cloud"}
        adapter = Mock()
        for error, expected in (
            (RuntimeError("401 https://vendor.example/?token=fixture-secret"), ACCOUNT_STATUS_ERROR),
            (AccountError("This account is disconnected.", "authentication"), "This account is disconnected."),
        ):
            adapter.query_status.side_effect = error
            worker = DeviceStatusWorker([(0, device)], prefer_cloud=True)
            with self.subTest(error=type(error).__name__), \
                    patch("lumisync.gui.controllers.discovery.create_adapter", return_value=adapter):
                updates, _, _ = self.run_worker(worker)
                self.assertEqual(updates[0]["last_error"], expected)
                self.assertEqual(updates[0]["status_source"], "offline")

    def test_inline_account_fallback_failure_is_not_shown_raw(self):
        local = Mock()
        local.query_status.return_value = None
        local.device = {}
        remote = Mock()
        remote.query_status.side_effect = ValueError("mqtt broker said fixture-secret")
        worker = DeviceStatusWorker([(0, LINKED_STRIP)])
        with patch("lumisync.gui.controllers.discovery.tuya_presence.presence_age", return_value=None), \
                patch("lumisync.gui.controllers.discovery.pool.acquire", return_value=local), \
                patch("lumisync.gui.controllers.discovery.pool.is_pooled", return_value=True), \
                patch("lumisync.gui.controllers.discovery.create_adapter", return_value=remote):
            updates, _, _ = self.run_worker(worker)
        self.assertEqual(updates[0]["last_error"], ACCOUNT_STATUS_ERROR)

    def test_only_an_account_read_after_a_lan_failure_is_flagged(self):
        local = Mock()
        local.query_status.return_value = None
        local.device = {}
        remote = Mock()
        remote.query_status.side_effect = lambda: {"power_on": True}
        with patch("lumisync.gui.controllers.discovery.tuya_presence.presence_age", return_value=None), \
                patch("lumisync.gui.controllers.discovery.pool.acquire", return_value=local), \
                patch("lumisync.gui.controllers.discovery.pool.is_pooled", return_value=True), \
                patch("lumisync.gui.controllers.discovery.create_adapter", return_value=remote):
            fallback, _, _ = self.run_worker(DeviceStatusWorker([(0, LINKED_STRIP)]))
            account_first, _, _ = self.run_worker(DeviceStatusWorker([(0, LINKED_STRIP)], prefer_cloud=True))
        self.assertEqual(fallback[0]["status_origin"], "cloud")
        self.assertTrue(fallback[0]["lan_failed"])
        self.assertEqual(account_first[0]["status_origin"], "cloud")
        self.assertNotIn("lan_failed", account_first[0])


class NetworkProbeTests(unittest.TestCase):
    def probe_module(self, *, address=None, error=None):
        module = Mock()
        module.AF_INET, module.SOCK_DGRAM = socket.AF_INET, socket.SOCK_DGRAM
        module.getaddrinfo.side_effect = AssertionError("no DNS on the GUI thread")
        module.gethostname.side_effect = AssertionError("no DNS on the GUI thread")
        probe = Mock()
        if error is not None:
            probe.connect.side_effect = error
        probe.getsockname.return_value = (address, 50000)
        module.socket.return_value = probe
        return module, probe

    def check(self, **kwargs):
        module, probe = self.probe_module(**kwargs)
        with patch("lumisync.gui.controllers.device_controller.socket", module):
            started = time.monotonic()
            result = DeviceController._local_network_available()
            self.assertLess(time.monotonic() - started, 1.0)
        return result, probe

    def test_a_lan_address_counts_as_a_network(self):
        result, probe = self.check(address="192.168.1.20")
        self.assertTrue(result)
        probe.connect.assert_called_once_with(DeviceController.NETWORK_PROBE_TARGETS[0])
        probe.close.assert_called_once_with()

    def test_loopback_only_or_no_route_is_no_network(self):
        self.assertFalse(self.check(address="127.0.0.1")[0])
        self.assertFalse(self.check(address="0.0.0.0")[0])
        result, probe = self.check(error=OSError(10051, "unreachable"))
        self.assertFalse(result)
        self.assertEqual(probe.close.call_count, len(DeviceController.NETWORK_PROBE_TARGETS))


if __name__ == "__main__":
    unittest.main()
