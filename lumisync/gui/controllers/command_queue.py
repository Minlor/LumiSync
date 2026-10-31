"""Per-device command coalescing for rapid slider and toggle input.

A device executes at most one command at a time. While it is busy, later
requests for the same control replace one another instead of replaying a long
burst of obsolete values. The queue is keyed by the device's stable identity, so
a removed device's pending work cannot leak into a re-added one.
"""

from __future__ import annotations

from typing import Any, Dict, List


class CoalescingCommandQueue:
    """Keep one in-flight request and one pending request per control."""

    def __init__(self) -> None:
        self.tasks: Dict[str, Any] = {}
        self.pending: Dict[str, List[Any]] = {}
        self.requested_power: Dict[str, bool] = {}

    def enqueue(self, key: str, method: str, request: Any) -> bool:
        """Queue a request; return True when it can start immediately."""
        if key not in self.tasks:
            return True
        queue = self.pending.setdefault(key, [])
        # Keep the final intent for each control instead of replaying a burst
        # of obsolete slider positions.
        queue[:] = [item for item in queue if item[1] != method]
        queue.append(request)
        return False

    def pop_next(self, key: str) -> Any | None:
        queue = self.pending.get(key)
        return queue.pop(0) if queue else None

    def complete(self, key: str) -> None:
        self.tasks.pop(key, None)
        self.pending.pop(key, None)
        self.requested_power.pop(key, None)

    def clear(self) -> None:
        self.tasks.clear()
        self.pending.clear()
        self.requested_power.clear()

    def discard(self, key: str) -> None:
        self.tasks.pop(key, None)
        self.pending.pop(key, None)
        self.requested_power.pop(key, None)
