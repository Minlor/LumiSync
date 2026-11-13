"""Short I/O tasks owned by Qt's application-wide pool, with queued UI results."""

import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ...accounts.errors import AccountError
from ...utils.logging import get_logger

logger = get_logger(__name__)


class TaskSignals(QObject):
    completed = Signal(object, object)


def _log_failure(exc: BaseException) -> None:
    # Type and stack frames only: exception values and locals from account
    # I/O can contain credentials or vendor response payloads.
    logger.error(
        "Background task failed (%s):\n%s",
        type(exc).__name__,
        "".join(traceback.format_tb(exc.__traceback__)),
    )


class BackgroundTask(QRunnable):
    def __init__(self, action) -> None:
        super().__init__()
        self.action = action
        self.signals = TaskSignals()

    def run(self) -> None:
        try:
            value = self.action()
            error = None
        except AccountError as exc:
            # Account errors are written for the user and carry no secrets;
            # the code is enough for the log.
            logger.info("Background task reported an account error (%s)", exc.code)
            value, error = None, str(exc)
        except Exception as exc:
            _log_failure(exc)
            value, error = None, "The connection could not complete this operation. Check your account and connection details and try again."
        finally:
            # Login closures can contain a password. Release them before keeping
            # the runnable around as the owner's completion reference.
            self.action = None
        self.signals.completed.emit(value, error)


# Signal objects of tasks whose result has not reached the GUI thread yet.
# The pool deletes a finished runnable on its worker thread; when a caller did
# not keep the returned task, that also destroyed the GUI-thread TaskSignals
# while its queued "completed" call was still pending, and Qt crashed with an
# access violation (seen with Check Firmware).
_pending_signals: set[TaskSignals] = set()


def start_task(action, callback) -> BackgroundTask:
    task = BackgroundTask(action)
    signals = task.signals
    _pending_signals.add(signals)

    def deliver(value, error) -> None:
        try:
            callback(value, error)
        finally:
            _pending_signals.discard(signals)
            # Released on the GUI thread, after delivery, never from the pool.
            signals.deleteLater()

    signals.completed.connect(deliver)
    QThreadPool.globalInstance().start(task)
    return task
