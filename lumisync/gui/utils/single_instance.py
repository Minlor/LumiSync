"""Single-instance guard using QLocalServer.

Launching LumiSync while it is already running (window open or hidden in
the tray) focuses the existing instance instead of starting a second one
that would fight over devices and settings.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_SERVER_NAME = "LumiSync-single-instance"


class SingleInstance(QObject):
    """Owns the local socket that marks this process as *the* instance."""

    # Emitted when another launch attempt pinged us — bring the UI forward.
    activate_requested = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._server: QLocalServer | None = None

    def try_acquire(self) -> bool:
        """Return True if we are the first instance.

        Otherwise the running instance is asked to show itself and the
        caller should exit.
        """
        probe = QLocalSocket()
        probe.connectToServer(_SERVER_NAME)
        if probe.waitForConnected(300):
            probe.write(b"activate")
            probe.flush()
            probe.waitForBytesWritten(300)
            probe.disconnectFromServer()
            return False

        # No live instance. Remove a stale socket left by a crash, then listen.
        QLocalServer.removeServer(_SERVER_NAME)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._server.listen(_SERVER_NAME)
        return True

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        socket = self._server.nextPendingConnection()
        if socket is not None:
            socket.readAll()
            socket.disconnectFromServer()
        self.activate_requested.emit()


__all__ = ["SingleInstance"]
