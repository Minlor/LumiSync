"""Update checking controller for the LumiSync GUI."""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ... import __version__
from ...updates import UpdateCheckResult, check_for_update


class UpdateCheckWorker(QObject):
    finished = pyqtSignal(object)

    def run(self) -> None:
        try:
            result = check_for_update(current_version=__version__)
        except Exception as exc:
            result = UpdateCheckResult(
                current_version=__version__,
                error=f"Unexpected update check error: {exc}",
            )
        self.finished.emit(result)


class UpdateController(QObject):
    check_started = pyqtSignal()
    check_finished = pyqtSignal(object)
    update_available = pyqtSignal(object)
    status_updated = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.check_thread: Optional[QThread] = None
        self.check_worker: Optional[UpdateCheckWorker] = None
        self.last_result: Optional[UpdateCheckResult] = None

    def _check_running(self) -> bool:
        if self.check_thread is None:
            return False
        try:
            return self.check_thread.isRunning()
        except RuntimeError:
            self._clear_refs()
            return False

    def _clear_refs(self) -> None:
        self.check_thread = None
        self.check_worker = None

    def check_now(self) -> None:
        if self._check_running():
            self.status_updated.emit("Update check already in progress...")
            return

        self.check_started.emit()
        self.status_updated.emit("Checking for updates...")

        thread = QThread()
        worker = UpdateCheckWorker()
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_refs)

        self.check_thread = thread
        self.check_worker = worker
        thread.start()

    def _on_finished(self, result: UpdateCheckResult) -> None:
        self.last_result = result
        self.check_finished.emit(result)

        if result.error:
            self.status_updated.emit(f"Update check failed: {result.error}")
            return

        if result.is_update_available and result.latest_version:
            self.status_updated.emit(f"Update available: v{result.latest_version}")
            self.update_available.emit(result)
            return

        self.status_updated.emit("LumiSync is up to date")

    def __del__(self) -> None:
        thread = self.check_thread
        if thread is not None:
            try:
                if thread.isRunning():
                    thread.quit()
                    thread.wait(1000)
            except Exception:
                pass
