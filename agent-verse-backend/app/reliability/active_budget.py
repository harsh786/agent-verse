"""A goal's wall-clock budget that does not run while the goal is paused.

``run_goal`` wrapped the whole run, including time an operator kept the goal
paused at a step boundary, in ``asyncio.wait_for(timeout=goal_timeout_s)``: a
goal paused longer than its plan timeout was failed as "Goal timed out" the
moment it resumed, or even while still paused (a08-F193-04). The pause gates
now account their waits on an :class:`ActiveTimeBudget` and
:func:`run_within_active_budget` enforces the timeout on active time only.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Coroutine, Iterator
from typing import Any

# How often a paused run's deadline is re-evaluated (it cannot expire while paused).
_PAUSED_POLL_S = 1.0


class ActiveTimeBudget:
    """``seconds`` of run time; time spent inside :meth:`paused` is not counted."""

    def __init__(self, seconds: float) -> None:
        self.seconds = float(seconds)
        self._start = time.monotonic()
        self._paused_total = 0.0
        self._paused_since: float | None = None
        self._depth = 0

    @property
    def is_paused(self) -> bool:
        return self._paused_since is not None

    def paused_seconds(self) -> float:
        extra = time.monotonic() - self._paused_since if self._paused_since is not None else 0.0
        return self._paused_total + extra

    def remaining(self) -> float:
        """Active seconds left (may be negative once exhausted)."""
        active = time.monotonic() - self._start - self.paused_seconds()
        return self.seconds - active

    @contextlib.contextmanager
    def paused(self) -> Iterator[None]:
        """Mark a pause wait (re-entrant: nested waits are counted once)."""
        if self._depth == 0:
            self._paused_since = time.monotonic()
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            if self._depth == 0 and self._paused_since is not None:
                self._paused_total += time.monotonic() - self._paused_since
                self._paused_since = None


def paused_window(budget: ActiveTimeBudget | None) -> contextlib.AbstractContextManager[None]:
    """``budget.paused()`` or a no-op when the run has no budget."""
    return budget.paused() if budget is not None else contextlib.nullcontext()


async def run_within_active_budget[T](
    coro: Coroutine[Any, Any, T], budget: ActiveTimeBudget
) -> T:
    """Await ``coro``; cancel it and raise ``TimeoutError`` once the ACTIVE time
    is spent. Like ``asyncio.wait_for``, cancelling the caller cancels ``coro``."""
    task: asyncio.Task[T] = asyncio.ensure_future(coro)
    try:
        while True:
            if budget.is_paused:
                wait_s = _PAUSED_POLL_S
            else:
                left = budget.remaining()
                if left <= 0:
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        await task
                    raise TimeoutError(f"active time budget of {budget.seconds}s exhausted")
                wait_s = left
            done, _ = await asyncio.wait({task}, timeout=wait_s)
            if task in done:
                return task.result()
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
