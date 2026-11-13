import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtNetwork import QLocalSocket
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from lumisync.gui.utils import single_instance


class SingleInstanceQuitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.guard = single_instance.SingleInstance()
        if not self.guard.try_acquire():
            self.skipTest("another LumiSync instance owns the test lock")
        self.messages = []
        self.guard.quit_requested.connect(lambda: self.messages.append("quit"))
        self.guard.activate_requested.connect(lambda: self.messages.append("activate"))

    def tearDown(self):
        if self.guard._server is not None:
            self.guard._server.close()
        if self.guard._lock is not None:
            self.guard._lock.unlock()

    def _send_and_wait(self, payload: bytes) -> None:
        socket = QLocalSocket()
        socket.connectToServer(single_instance._SERVER_NAME)
        self.assertTrue(socket.waitForConnected(750))
        socket.write(payload)
        socket.flush()
        socket.waitForBytesWritten(300)
        for _ in range(50):
            self.app.processEvents()
            if self.messages:
                break
            QTest.qWait(10)
        socket.disconnectFromServer()

    def test_quit_message_requests_shutdown(self):
        self._send_and_wait(b"quit")
        self.assertEqual(self.messages, ["quit"])

    def test_activate_message_still_shows_the_window(self):
        self._send_and_wait(b"activate")
        self.assertEqual(self.messages, ["activate"])

    def test_installer_probe_does_not_reopen_the_window(self):
        self._send_and_wait(b"probe")
        self.assertEqual(self.messages, [])

    def test_activation_is_ignored_once_quit_was_requested(self):
        self._send_and_wait(b"quit")
        self._send_and_wait(b"activate")
        self.assertEqual(self.messages, ["quit"])


if __name__ == "__main__":
    unittest.main()
