"""All LAN callers share one UDP 4002 socket and receive replies."""

import socket
import unittest

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

    def test_port_conflict_is_reported_instead_of_silently_sharing(self):
        holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        holder.bind(("", self.port))
        try:
            with self.assertRaises(OSError) as raised:
                connection.create_lan_socket(bind_port=self.port)
            self.assertIn("already in use", str(raised.exception))
        finally:
            holder.close()


if __name__ == "__main__":
    unittest.main()
