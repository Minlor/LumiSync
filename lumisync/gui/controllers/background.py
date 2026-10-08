"""Short I/O tasks owned by Qt's application-wide pool, with queued UI results."""

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ...accounts.errors import AccountError


class TaskSignals(QObject):
    completed = Signal(object, object)


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
            value, error = None, str(exc)
        except Exception:
            value, error = None, "The connection could not complete this operation. Check your account and connection details and try again."
        finally:
            # Login closures can contain a password. Release them before keeping
            # the runnable around as the owner's completion reference.
            self.action = None
        self.signals.completed.emit(value, error)


def start_task(action, callback) -> BackgroundTask:
    task = BackgroundTask(action)
    task.signals.completed.connect(callback)
    QThreadPool.globalInstance().start(task)
    return task
