"""All LAN callers share one UDP 4002 socket and receive replies."""

import errno
import socket
import subprocess
import time
import unittest
from unittest import mock

from lumisync import connection


def free_udp_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
    finally:
        probe.close()


class SharedLanSocketTests(unittest.TestCase):
    def setUp(self):
        self.port = free_udp_port()
        self.sockets = []
        self.senders = []

    def tearDown(self):
        for server in self.sockets:
            try:
                server.close()
            except Exception:
                pass
        for sender in self.senders:
            sender.close()

    def facade(self):
        server = connection.create_lan_socket(bind_port=self.port, timeout=1.0)
        self.sockets.append(server)
        return server

    def send(self, payload=b"payload"):
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.senders.append(sender)
        sender.sendto(payload, ("127.0.0.1", self.port))

    def test_every_consumer_receives_the_same_reply(self):
        first = self.facade()
        second = self.facade()

        self.send(b"one")

        self.assertEqual(first.recvfrom(64)[0], b"one")
        self.assertEqual(second.recvfrom(64)[0], b"one")

    def test_closing_one_consumer_keeps_the_shared_socket_alive(self):
        first = self.facade()
        second = self.facade()
        first.close()

        self.send(b"two")

        self.assertEqual(second.recvfrom(64)[0], b"two")

    def test_socket_can_be_recreated_after_the_last_consumer_closes(self):
        first = self.facade()
        first.close()

        second = self.facade()
        self.send(b"three")

        self.assertEqual(second.recvfrom(64)[0], b"three")

    def test_a_send_to_a_closed_port_does_not_stop_replies(self):
        # Windows reports the ICMP "port unreachable" for this send as a
        # ConnectionResetError on the next receive; the reader must survive it.
        server = self.facade()
        server.sendto(b"stale", ("127.0.0.1", free_udp_port()))
        time.sleep(0.3)

        self.send(b"four")

        self.assertEqual(server.recvfrom(64)[0], b"four")

    def test_port_conflict_is_reported_instead_of_silently_sharing(self):
        holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        holder.bind(("", self.port))
        try:
            with self.assertRaises(OSError) as raised:
                connection.create_lan_socket(bind_port=self.port)
            self.assertIn("already in use", str(raised.exception))
        finally:
            holder.close()

    def test_port_conflict_is_diagnosed_without_holding_the_registry_lock(self):
        held = []
        real = connection.describe_port_conflict

        def describe(port=None, owner=None):
            held.append(connection._lan_cores_lock.locked())
            return real(port, owner="PID 1")

        holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        holder.bind(("", self.port))
        try:
            with mock.patch.object(connection, "describe_port_conflict", side_effect=describe):
                with self.assertRaises(OSError):
                    connection.create_lan_socket(bind_port=self.port)
        finally:
            holder.close()
        self.assertEqual(held, [False])

    def test_netstat_diagnosis_never_opens_a_console_window(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with mock.patch.object(connection.platform, "system", return_value="Windows"), \
                mock.patch.object(connection.subprocess, "run", return_value=completed) as run:
            connection._windows_udp_port_owner(4002)
        self.assertEqual(
            run.call_args.kwargs["creationflags"],
            getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def _break_reader(self, server, error):
        """Make the shared reader's next receive fail once with ``error``."""
        core = server._core
        real = core._socket

        class Failing:
            fired = False

            def recvfrom(self, size):
                if not Failing.fired:
                    Failing.fired = True
                    raise error
                return real.recvfrom(size)

            def __getattr__(self, name):
                return getattr(real, name)

        core._socket = Failing()
        time.sleep(0.6)  # past the reader's 0.25 s receive timeout
        return core

    def test_reader_survives_a_network_reset(self):
        server = self.facade()
        # WSAENETRESET: an OSError that is not ConnectionResetError.
        core = self._break_reader(server, OSError(10052, "network reset"))

        self.assertTrue(core._thread.is_alive())
        self.assertIs(connection._lan_cores.get(self.port), core)
        self.send(b"five")
        self.assertEqual(server.recvfrom(64)[0], b"five")

    def test_dead_socket_is_dropped_and_callers_rebind(self):
        server = self.facade()
        core = self._break_reader(server, OSError(errno.EBADF, "bad file descriptor"))

        self.assertFalse(core._thread.is_alive())
        self.assertTrue(core.dead)
        self.assertIsNot(connection._lan_cores.get(self.port), core)
        # A new caller binds a fresh socket and receives replies again.
        fresh = self.facade()
        self.assertIsNot(fresh._core, core)
        self.send(b"six")
        self.assertEqual(fresh.recvfrom(64)[0], b"six")
        # The long-lived caller (a sync session) moves over on its next use.
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.senders.append(receiver)
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(1.0)
        server.sendto(b"ping", receiver.getsockname())
        self.assertEqual(receiver.recvfrom(64)[0], b"ping")
        self.assertIs(server._core, fresh._core)


if __name__ == "__main__":
    unittest.main()
