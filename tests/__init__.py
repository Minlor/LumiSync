"""Test package bootstrap.

Setting this before any test module is imported keeps logs, including those
from subprocesses spawned by the GUI packaging tests, out of the developer's
real ``~/.lumisync/logs`` folder. It also gives the suite a per-test watchdog:
one hung test fails the run with a stack dump instead of eating the whole CI
job budget.
"""

import faulthandler
import os
import sys
import threading
import unittest

os.environ.setdefault("LUMISYNC_TESTING", "1")

_TEST_TIMEOUT = max(5.0, float(os.environ.get("LUMISYNC_TEST_TIMEOUT", "120")))
_original_run = unittest.TestCase.run


def _expire(test) -> None:
    sys.stderr.write(f"\nTest timed out after {_TEST_TIMEOUT:g}s: {test.id()}\n")
    sys.stderr.flush()
    faulthandler.dump_traceback()
    # The test owns the process; leaving it alive would hang the CI job.
    os._exit(1)


def _run_with_timeout(self, result=None):
    watchdog = threading.Timer(_TEST_TIMEOUT, _expire, args=(self,))
    watchdog.daemon = True
    watchdog.start()
    try:
        return _original_run(self, result)
    finally:
        watchdog.cancel()


unittest.TestCase.run = _run_with_timeout
