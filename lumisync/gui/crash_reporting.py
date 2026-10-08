"""Persist Python stacks for native Qt failures in windowed Windows builds."""

import datetime
import faulthandler
import os
import re
import sys
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import qInstallMessageHandler

from ..utils.logging import get_logs_directory

_fault_file = None
_qt_handler = None


def _safe_message(message: str) -> str:
    message = re.sub(r"[\w.+%-]+@[\w.-]+\.[A-Za-z]{2,}", "[email masked]", message)
    return re.sub(r"(?i)(password|token|secret|local_key)([\s=:]+)\S+", r"\1\2[masked]", message)


def install_crash_reporting(logger) -> None:
    """Keep the fault stream open for the process, including Qt shutdown."""
    global _fault_file, _qt_handler
    if _fault_file is not None:
        return
    date = datetime.date.today().isoformat()
    try:
        path = Path(get_logs_directory()) / f"lumisync_fault_{date}_{os.getpid()}.log"
        _fault_file = path.open("a", encoding="utf-8", buffering=1)
        _fault_file.write(f"LumiSync crash diagnostics started {datetime.datetime.now().isoformat()}\n")
        faulthandler.enable(file=_fault_file, all_threads=True)
        logger.info("Native crash diagnostics: %s", path)
    except (OSError, RuntimeError) as exc:
        logger.warning("Could not enable native crash diagnostics: %s", type(exc).__name__)

    def qt_message(kind, _context, message):
        logger.warning("Qt %s: %s", kind.name, _safe_message(message))

    _qt_handler = qt_message
    qInstallMessageHandler(_qt_handler)

    def thread_exception(args):
        # A stack with exception type is sufficient here; exception values or
        # locals from account I/O can contain credentials or response payloads.
        logger.error("Uncaught background exception (%s):\n%s", args.exc_type.__name__,
                     "".join(traceback.format_tb(args.exc_traceback)))
        if issubclass(args.exc_type, KeyboardInterrupt):
            sys.__excepthook__(args.exc_type, args.exc_value, args.exc_traceback)

    threading.excepthook = thread_exception
