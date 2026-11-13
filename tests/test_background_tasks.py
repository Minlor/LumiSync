"""Fire-and-forget background tasks must deliver safely on the GUI thread."""

import os
import sys
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread, QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from lumisync.gui.controllers import background  # noqa: E402


class StartTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _drain(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        QThreadPool.globalInstance().waitForDone(1000)
        self.app.processEvents()

    def test_unreferenced_tasks_deliver_on_the_gui_thread(self):
        results = []
        threads = []

        def callback(value, error):
            results.append((value, error))
            threads.append(QThread.currentThread() is self.app.thread())

        for number in range(40):
            # Deliberately drop the returned task, as the firmware check did.
            background.start_task(lambda n=number: n * 2, callback)

        self._drain(lambda: len(results) == 40)
        self.assertEqual(sorted(value for value, _error in results), [n * 2 for n in range(40)])
        self.assertTrue(all(threads))
        self.assertEqual(background._pending_signals, set())

    def test_failed_task_still_delivers_and_is_released(self):
        results = []

        def boom():
            raise RuntimeError("secret detail")

        background.start_task(boom, lambda value, error: results.append((value, error)))
        self._drain(lambda: bool(results))
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0][0])
        self.assertNotIn("secret detail", results[0][1])
        self.assertEqual(background._pending_signals, set())

    def test_callback_exception_does_not_leak_the_task(self):
        delivered = threading.Event()

        def callback(_value, _error):
            delivered.set()
            raise ValueError("callback failed")

        previous = sys.excepthook
        sys.excepthook = lambda *_args: None  # PySide reports the slot error here
        try:
            background.start_task(lambda: 1, callback)
            self._drain(delivered.is_set)
        finally:
            sys.excepthook = previous
        self.assertTrue(delivered.is_set())
        self.assertEqual(background._pending_signals, set())


if __name__ == "__main__":
    unittest.main()
