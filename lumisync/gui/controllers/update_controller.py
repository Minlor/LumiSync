"""Update checking controller for the LumiSync GUI."""

from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Optional

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot

from ... import __version__
from ...updates import (
    UpdateCheckResult,
    check_for_update,
    download_update_asset,
    expected_sha256,
    select_update_asset,
    verify_file_sha256,
)

# Downloaded installers live in their own temp folder; old ones are removed on
# the next start so repeated updates do not pile up ~80 MB files in %TEMP%.
UPDATE_TEMP_PREFIX = "lumisync-update-"


def cleanup_old_update_downloads(max_age_seconds: float = 6 * 3600) -> int:
    """Delete update folders left by earlier runs. Returns how many went."""
    import shutil
    import time

    removed = 0
    root = Path(tempfile.gettempdir())
    now = time.time()
    try:
        candidates = list(root.glob(f"{UPDATE_TEMP_PREFIX}*"))
    except OSError:
        return 0
    for folder in candidates:
        try:
            if not folder.is_dir() or now - folder.stat().st_mtime < max_age_seconds:
                continue
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
        except OSError:
            continue
    return removed


class UpdateCheckWorker(QObject):
    finished = Signal(object)

    def run(self) -> None:
        try:
            result = check_for_update(current_version=__version__)
        except Exception as exc:
            result = UpdateCheckResult(
                current_version=__version__,
                error=f"Unexpected update check error: {exc}",
            )
        self.finished.emit(result)


class UpdateDownloadWorker(QObject):
    """Download and verify the release asset on a worker thread."""

    progress = Signal(int, int)  # bytes written, total bytes (0 if unknown)
    finished = Signal(str)  # local path to the verified installer
    failed = Signal(str)

    def __init__(self, result: UpdateCheckResult) -> None:
        super().__init__()
        self.result = result

    def run(self) -> None:
        try:
            asset = select_update_asset(self.result.assets)
            if asset is None:
                raise RuntimeError(
                    "This release has no compatible update asset for this platform."
                )

            expected = expected_sha256(asset, self.result.assets)
            if not expected:
                raise RuntimeError(
                    "The release does not publish a SHA-256 checksum, so the "
                    "download cannot be verified. Use Open Release instead."
                )

            destination = Path(tempfile.mkdtemp(prefix=UPDATE_TEMP_PREFIX)) / asset.name
            download_update_asset(
                asset,
                destination,
                progress=self.progress.emit,
            )
            if not verify_file_sha256(destination, expected):
                raise RuntimeError(
                    "The downloaded file failed SHA-256 verification and was not run."
                )
            self.finished.emit(str(destination))
        except Exception as exc:
            self.failed.emit(str(exc))


class UpdateController(QObject):
    check_started = Signal()
    check_finished = Signal(object)
    update_available = Signal(object)
    status_updated = Signal(str)
    download_started = Signal()
    download_progress = Signal(int, int)
    download_finished = Signal(str)
    download_failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.check_thread: Optional[QThread] = None
        self.check_worker: Optional[UpdateCheckWorker] = None
        self.download_thread: Optional[QThread] = None
        self.download_worker: Optional[UpdateDownloadWorker] = None
        self.last_result: Optional[UpdateCheckResult] = None
        self.last_check_silent = False
        # Path of a downloaded, checksum-verified installer waiting to run.
        self.ready_installer: Optional[str] = None
        self._ready_version: Optional[str] = None
        self._silent_check = False
        self._closing = False

    def _check_running(self) -> bool:
        if self.check_thread is None:
            return False
        try:
            self.check_thread.isRunning()
            return True
        except RuntimeError:
            self.check_thread = None
            self.check_worker = None
            return False

    @Slot()
    def _clear_refs(self) -> None:
        thread = self.check_thread
        sender = self.sender()
        if thread is None or (isinstance(sender, QThread) and sender is not thread):
            return
        if not thread.wait(0):
            QTimer.singleShot(1, self._clear_refs)
            return
        self.check_worker = None
        self.check_thread = None
        thread.deleteLater()

    def check_now(self, *, silent: bool = False) -> None:
        if self._closing:
            return
        if self._check_running():
            if not silent:
                self.status_updated.emit("Update check already in progress...")
            return

        self._silent_check = silent
        self.last_check_silent = silent
        self.check_started.emit()
        if not silent:
            self.status_updated.emit("Checking for updates...")

        thread = QThread(self)
        worker = UpdateCheckWorker()
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._clear_refs, Qt.ConnectionType.QueuedConnection)

        self.check_thread = thread
        self.check_worker = worker
        thread.start()

    def _on_finished(self, result: UpdateCheckResult) -> None:
        if self._closing:
            return
        silent = self._silent_check
        self._silent_check = False
        self.last_result = result
        self.check_finished.emit(result)

        if result.error:
            if not silent:
                self.status_updated.emit(f"Update check failed: {result.error}")
            return

        if result.is_update_available and result.latest_version:
            if not silent:
                self.status_updated.emit(f"Update available: v{result.latest_version}")
            self.update_available.emit(result)
            return

        if not silent:
            self.status_updated.emit("LumiSync is up to date")

    def _download_running(self) -> bool:
        if self.download_thread is None:
            return False
        try:
            return self.download_thread.isRunning()
        except RuntimeError:
            self.download_thread = None
            self.download_worker = None
            return False

    @Slot()
    def _clear_download_refs(self) -> None:
        thread = self.download_thread
        sender = self.sender()
        if thread is None or (isinstance(sender, QThread) and sender is not thread):
            return
        if not thread.wait(0):
            QTimer.singleShot(1, self._clear_download_refs)
            return
        self.download_worker = None
        self.download_thread = None
        thread.deleteLater()

    def download_and_install(self, result: UpdateCheckResult) -> None:
        """Download and verify the platform's update asset in the background."""
        if self._closing:
            return
        if self._download_running():
            self.status_updated.emit("Update download already in progress...")
            return
        if (
            self.ready_installer
            and self._ready_version == result.latest_version
            and Path(self.ready_installer).is_file()
        ):
            # Already downloaded and verified this release.
            self.download_finished.emit(self.ready_installer)
            return

        self.download_started.emit()
        self.status_updated.emit("Downloading update...")

        thread = QThread(self)
        worker = UpdateDownloadWorker(result)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.progress.connect(self.download_progress)
        worker.finished.connect(self._on_download_finished)
        worker.failed.connect(self._on_download_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(
            self._clear_download_refs, Qt.ConnectionType.QueuedConnection
        )

        self.download_thread = thread
        self.download_worker = worker
        thread.start()

    def _on_download_finished(self, path: str) -> None:
        if self._closing:
            return
        self.ready_installer = path
        self._ready_version = (
            self.last_result.latest_version if self.last_result is not None else None
        )
        self.status_updated.emit("Update downloaded and verified.")
        self.download_finished.emit(path)

    def _on_download_failed(self, message: str) -> None:
        if self._closing:
            return
        self.status_updated.emit(f"Update download failed: {message}")
        self.download_failed.emit(message)

    def shutdown(self) -> bool:
        self._closing = True
        still_running = False
        thread = self.check_thread
        if self._check_running() and thread is not None:
            thread.quit()
            still_running = True
        thread = self.download_thread
        if self._download_running() and thread is not None:
            thread.quit()
            still_running = True
        return not still_running
