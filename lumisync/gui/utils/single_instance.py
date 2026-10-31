"""Single-instance guard using QLocalServer.

Launching LumiSync while it is already running (window open or hidden in
the tray) focuses the existing instance instead of starting a second one
that would fight over devices and settings.
"""

from __future__ import annotations

import getpass
import hashlib
import logging
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)


def _user_scope() -> str:
    """A stable per-user suffix so different accounts do not share a lock."""
    try:
        home = str(Path.home())
    except OSError:
        home = ""
    identity = "|".join(
        (
            getpass.getuser(),
            home,
            os.environ.get("USERNAME", ""),
            os.environ.get("USER", ""),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]


_SCOPE = _user_scope()
_SERVER_NAME = f"LumiSync-single-instance-{_SCOPE}"
_LOCK_PATH = Path(tempfile.gettempdir()) / f"LumiSync-single-instance-{_SCOPE}.lock"


class SingleInstance(QObject):
    """Owns the local socket that marks this process as *the* instance."""

    # Emitted when another launch attempt pinged us — bring the UI forward.
    activate_requested = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._server: QLocalServer | None = None
        self._lock: QLockFile | None = None

    def _activate_existing(self) -> bool:
        probe = QLocalSocket()
        probe.connectToServer(_SERVER_NAME)
        if not probe.waitForConnected(750):
            return False
        probe.write(b"activate")
        probe.flush()
        probe.waitForBytesWritten(300)
        probe.disconnectFromServer()
        return True

    def try_acquire(self) -> bool:
        """Return True if we are the first instance.

        Otherwise the running instance is asked to show itself and the
        caller should exit.
        """
        if self._activate_existing():
            return False

        # Serialize the startup race with a per-user lock file. Without it two
        # simultaneous launches can both remove the stale socket and both
        # listen successfully.
        lock = QLockFile(str(_LOCK_PATH))
        lock.setStaleLockTime(10_000)
        if not lock.tryLock(100):
            if self._activate_existing():
                return False
            # Another process owns the lock but has not started its local
            # server yet. Do not steal it: that is exactly how two launches
            # both ended up running. QLockFile removes genuinely stale locks
            # after the grace period on a later attempt.
            logger.warning("Another LumiSync instance holds the startup lock")
            return False
        self._lock = lock

        if self._activate_existing():
            lock.unlock()
            self._lock = None
            return False

        # No live instance. Remove a stale socket left by a crash, then listen.
        QLocalServer.removeServer(_SERVER_NAME)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        if not self._server.listen(_SERVER_NAME):
            logger.error(
                "Could not start the single-instance server: %s",
                self._server.errorString(),
            )
            self._server = None
            lock.unlock()
            self._lock = None
            # If the listen failed because another process won the race, let
            # that instance come forward instead of starting a duplicate.
            self._activate_existing()
            return False
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
