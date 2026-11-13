"""The latest Windows Firewall state, shared by every page that shows it.

The main window owns one monitor. Pages subscribe to ``changed`` instead of
querying the firewall themselves; checks and the elevated Allow run on the
background pool, so the GUI thread never waits on COM or a UAC prompt.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from ...utils.logging import get_logger
from ..utils import firewall
from ..utils.firewall import FirewallState
from .background import start_task

# The main GUI log (each module logger writes its own file), so the
# "Windows Firewall: <status>" line sits beside the startup messages.
logger = get_logger("lumisync_gui")

_AUTO = object()

AllowCallback = Callable[[bool, str], None]


class FirewallMonitor(QObject):
    """Runs firewall checks and Allow attempts; remembers the latest state."""

    # Emitted after every completed check (and when a check starts verifying
    # an Allow), carrying the current FirewallState.
    changed = Signal(object)
    # (ok, message) after each Allow attempt; message is firewall.CANCELLED
    # when the user declined the UAC prompt.
    allow_finished = Signal(bool, str)

    RECHECK_SECONDS = 60.0

    def __init__(self, parent: Optional[QObject] = None, *, program: object = _AUTO):
        """*program* defaults to :func:`firewall.program_path`; tests pass a path or None."""
        super().__init__(parent)
        self._program: Optional[str] = (
            firewall.program_path() if program is _AUTO
            else program if isinstance(program, str) and program
            else None
        )
        self._state = FirewallState(firewall.STATUS_UNKNOWN, "Not checked yet")
        self._checked_once = False
        self._checking = False
        self._recheck = False
        self._allowing = False
        self._verifying = False
        self._last_started: Optional[float] = None

    # ------------------------------------------------------------------ state

    def program(self) -> Optional[str]:
        """The exe being checked; None for source runs (nothing is checked)."""
        return self._program

    def is_supported(self) -> bool:
        return self._program is not None

    def state(self) -> FirewallState:
        return self._state

    def is_checking(self) -> bool:
        return self._checking

    def is_allowing(self) -> bool:
        return self._allowing

    def is_verifying(self) -> bool:
        """True after a successful Allow until the re-check confirms it."""
        return self._verifying

    # ------------------------------------------------------------------ checks

    def check(self) -> bool:
        """Start a background check. Returns False when nothing is checked."""
        program = self._program
        if program is None:
            return False
        if self._checking:
            # The running check may predate a change (an Allow); its result
            # is dropped and a fresh check follows.
            self._recheck = True
            return True
        self._checking = True
        self._last_started = time.monotonic()
        start_task(lambda: firewall.check(program), self._on_checked)
        return True

    def recheck_if_stale(self, max_age: Optional[float] = None) -> bool:
        """Check again unless the last check started within *max_age* seconds.

        Does nothing before the first check: the window schedules that one
        shortly after startup, and a page shown at launch must not double it.
        """
        if self._last_started is None:
            return False
        age = self.RECHECK_SECONDS if max_age is None else max_age
        if time.monotonic() - self._last_started < age:
            return False
        return self.check()

    def _on_checked(self, value, error) -> None:
        self._checking = False
        if self._recheck:
            self._recheck = False
            self.check()
            return
        if isinstance(value, FirewallState):
            state = value
        else:
            logger.debug("Windows Firewall check returned no state (%s)", error)
            state = FirewallState(firewall.STATUS_UNKNOWN, "The check did not complete")
        previous = self._state
        self._state = state
        self._verifying = False
        if not self._checked_once or state.status != previous.status:
            # One line per result change, so the packaged log shows it.
            logger.info("Windows Firewall: %s (%s)", state.status, state.detail)
        else:
            logger.debug("Windows Firewall: %s (%s)", state.status, state.detail)
        self._checked_once = True
        self.changed.emit(state)

    # ------------------------------------------------------------------ allow

    def allow(self, hwnd: Optional[int] = None, on_finished: Optional[AllowCallback] = None) -> bool:
        """Add LumiSync's firewall rule (one UAC prompt) in the background.

        *on_finished* gets ``(ok, message)`` for the caller that asked, so
        only that page reports a failure. Returns False when not started.
        """
        program = self._program
        if program is None or self._allowing:
            return False
        self._allowing = True

        def finished(value, error) -> None:
            self._on_allowed(value, error, on_finished)

        start_task(lambda: firewall.allow(program, hwnd=hwnd), finished)
        return True

    def _on_allowed(self, value, error, on_finished: Optional[AllowCallback]) -> None:
        self._allowing = False
        if isinstance(value, tuple) and len(value) == 2:
            ok, message = bool(value[0]), str(value[1])
        else:
            ok, message = False, str(error or "Couldn't change Windows Firewall.")
        if ok:
            logger.info("Windows Firewall: added the LumiSync rule")
        elif message == firewall.CANCELLED:
            logger.info("Windows Firewall: the administrator prompt was declined")
        else:
            logger.warning("Windows Firewall: could not add the rule: %s", message)
        # Until the re-check lands, pages treat a successful Allow as done.
        self._verifying = ok
        if on_finished is not None:
            on_finished(ok, message)
        self.allow_finished.emit(ok, message)
        if ok:
            self.changed.emit(self._state)
        self.check()


__all__ = ["FirewallMonitor"]
