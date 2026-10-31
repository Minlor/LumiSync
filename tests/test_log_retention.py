"""Old logs are pruned without touching unrelated files."""

import os
import tempfile
import time
import unittest
from pathlib import Path

from lumisync.utils import logging as lumisync_logging


class LogRetentionTests(unittest.TestCase):
    def test_old_lumisync_logs_are_removed_and_other_files_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "lumisync_gui_2020-01-01.log"
            old.write_text("old", encoding="utf-8")
            old_time = time.time() - 60 * 86400
            os.utime(old, (old_time, old_time))
            current = root / "lumisync_gui_current.log"
            current.write_text("new", encoding="utf-8")
            unrelated = root / "notes.txt"
            unrelated.write_text("keep", encoding="utf-8")

            previous = lumisync_logging._pruned
            lumisync_logging._pruned = False
            try:
                lumisync_logging._prune_logs(directory)
            finally:
                lumisync_logging._pruned = previous

            self.assertFalse(old.exists())
            self.assertTrue(current.exists())
            self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()
