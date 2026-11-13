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
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLockFile, QObject, Signal
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
# LumiSync 0.8.x listened on an unscoped name and treats any message as
# "show yourself". A 1.0 install must not run beside it: both would bind the
# same LAN ports and drive the same lights.
_LEGACY_SERVER_NAME = "LumiSync-single-instance"
_LOCK_PATH = Path(tempfile.gettempdir()) / f"LumiSync-single-instance-{_SCOPE}.lock"

# Kept alive while a helper process asks the GUI to quit.
_QUIT_APP: QCoreApplication | None = None


def _connect_to_existing(timeout: int = 750) -> QLocalSocket | None:
    socket = QLocalSocket()
    socket.connectToServer(_SERVER_NAME)
    if not socket.waitForConnected(timeout):
        return None
    return socket


def request_existing_quit(timeout: int = 12000) -> bool:
    """Ask a running LumiSync instance to quit, then wait for it to disappear.

    The installer calls ``LumiSync.exe --quit`` before replacing files. Waiting
    for the single-instance socket to close avoids racing the file swap.
    """
    global _QUIT_APP
    _QUIT_APP = QCoreApplication.instance() or QCoreApplication([])

    socket = _connect_to_existing()
    if socket is None:
        return False
    socket.write(b"quit")
    socket.flush()
    socket.waitForBytesWritten(500)
    socket.disconnectFromServer()

    deadline = time.monotonic() + max(0.0, timeout / 1000.0)
    while time.monotonic() < deadline:
        probe = _connect_to_existing(150)
        if probe is None:
            return True
        # Say why we connected: a silent connection reads as "show the
        # window", which used to pop the closing app back up every poll.
        probe.write(b"probe")
        probe.flush()
        probe.waitForBytesWritten(100)
        probe.disconnectFromServer()
        time.sleep(0.15)
    return False


def legacy_instance_running(timeout: int = 300) -> bool:
    """Return whether a LumiSync 0.8.x instance is running for this session.

    Connecting brings that old window forward, which is what we want: the
    user is about to be asked to quit it.
    """
    socket = QLocalSocket()
    socket.connectToServer(_LEGACY_SERVER_NAME)
    if not socket.waitForConnected(timeout):
        return False
    socket.write(b"activate")
    socket.flush()
    socket.waitForBytesWritten(200)
    socket.disconnectFromServer()
    return True


class SingleInstance(QObject):
    """Owns the local socket that marks this process as *the* instance."""

    # Emitted when another launch attempt pinged us — bring the UI forward.
    activate_requested = Signal()
    # Emitted when an installer (or `LumiSync --quit`) asks this instance to exit.
    quit_requested = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._server: QLocalServer | None = None
        self._lock: QLockFile | None = None
        self._quit_requested = False

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
            # If the listen failed because another process won the race, let
            # that instance come forward instead of starting a duplicate.
            if self._activate_existing():
                lock.unlock()
                self._lock = None
                return False
            # Nobody else is running. Start anyway: the lock we hold still
            # stops a duplicate, and a launch that silently does nothing is
            # worse than a second launch that cannot bring us forward.
            logger.warning("Continuing without the single-instance server")
        return True

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        message = b""
        socket = self._server.nextPendingConnection()
        if socket is not None:
            try:
                socket.waitForReadyRead(100)
                message = socket.readAll().data()
            finally:
                socket.disconnectFromServer()
                socket.deleteLater()
        if message.startswith(b"probe"):
            return  # An installer waiting for us to exit.
        if b"quit" in message:
            self._quit_requested = True
            self.quit_requested.emit()
        elif not self._quit_requested:
            # Re-showing the window while it shuts down would only confuse.
            self.activate_requested.emit()


__all__ = ["SingleInstance", "legacy_instance_running", "request_existing_quit"]
