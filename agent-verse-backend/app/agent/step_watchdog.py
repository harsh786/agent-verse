"""Per-step deadline + heartbeat for the agent executor (GOAL-STALL / RW-21).

A step used to await its LLM / tool calls with no deadline and no progress
signal: a hung provider (300 s client timeout x retries) or a stuck tool left
the goal "executing" with no events for many minutes. Every governed step now
runs under a watchdog:

* while it runs, a ``step_heartbeat`` event is emitted every
  ``agent_step_heartbeat_seconds`` (the UI and the goal's event log see that it
  is alive and for how long it has been running);
* once it has been ACTIVE for ``agent_step_timeout_seconds`` it is cancelled and
  :class:`StepDeadlineExceededError` is raised. The executor records the step as
  FAILED with that reason, so the normal verify / replan path takes over.

Time spent waiting for a human approval is not active time: approval waits run
inside :func:`approval_wait`, which pauses the deadline (a supervised goal may
legitimately wait hours for a decision; the HITL timeout governs that).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import time
from collections.abc import Awaitable, Callable, Iterator
from typing import Any

from app.agent.graph_types import StepNotExecutedError

EmitFn = Callable[[dict[str, Any]], Awaitable[None]]


class StepDeadlineExceededError(StepNotExecutedError):
    """A step ran past its deadline and was cancelled (it produced no result)."""


class _StepClock:
    """Active-time clock of one step: time inside :func:`approval_wait` is excluded."""

    def __init__(self) -> None:
        self.started = time.monotonic()
        self._paused_depth = 0
        self._paused_since = 0.0
        self._paused_total = 0.0

    def pause(self) -> None:
        if self._paused_depth == 0:
            self._paused_since = time.monotonic()
        self._paused_depth += 1

    def resume(self) -> None:
        self._paused_depth = max(0, self._paused_depth - 1)
        if self._paused_depth == 0:
            self._paused_total += time.monotonic() - self._paused_since

    @property
    def paused(self) -> bool:
        return self._paused_depth > 0

    def active_seconds(self) -> float:
        paused = self._paused_total
        if self._paused_depth:
            paused += time.monotonic() - self._paused_since
        return time.monotonic() - self.started - paused

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started


_CURRENT_CLOCK: contextvars.ContextVar[_StepClock | None] = contextvars.ContextVar(
    "agentverse_step_clock", default=None
)


@contextlib.contextmanager
def approval_wait() -> Iterator[None]:
    """Exclude the enclosed wait (for a human decision) from the step deadline."""
    clock = _CURRENT_CLOCK.get()
    if clock is None:
        yield
        return
    clock.pause()
    try:
        yield
    finally:
        clock.resume()


def _settings() -> tuple[float, float]:
    try:
        from app.core.config import get_settings

        s = get_settings()
        return float(s.agent_step_timeout_seconds), float(s.agent_step_heartbeat_seconds)
    except Exception:
        return 900.0, 30.0


async def run_step_with_deadline(
    step: str,
    run: Callable[[], Awaitable[str]],
    *,
    emit: EmitFn,
    timeout_s: float | None = None,
    heartbeat_s: float | None = None,
) -> str:
    """Run ``run()`` for ``step`` with heartbeats and an active-time deadline."""
    default_timeout, default_heartbeat = _settings()
    timeout = float(timeout_s if timeout_s is not None else default_timeout)
    heartbeat = max(0.05, float(heartbeat_s if heartbeat_s is not None else default_heartbeat))
    if _CURRENT_CLOCK.get() is not None:
        # Nested (a loop step re-entering the pipeline): the outer watchdog owns it.
        return await run()

    clock = _StepClock()
    token = _CURRENT_CLOCK.set(clock)
    try:
        # The task copies the current context, so approval_wait() inside it sees
        # this step's clock.
        task: asyncio.Task[str] = asyncio.ensure_future(run())
    finally:
        _CURRENT_CLOCK.reset(token)
    beats = 0
    try:
        while True:
            remaining = timeout - clock.active_seconds()
            wait = heartbeat if clock.paused else min(heartbeat, max(remaining, 0.0))
            done, _ = await asyncio.wait({task}, timeout=wait)
            if done:
                return task.result()
            if not clock.paused and clock.active_seconds() >= timeout:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
                await _safe_emit(
                    emit,
                    {
                        "type": "step_timeout",
                        "step": step,
                        "timeout_s": timeout,
                        "elapsed_s": round(clock.elapsed_seconds(), 1),
                    },
                )
                raise StepDeadlineExceededError(
                    f"Step exceeded its {timeout:g}s deadline without a result and was "
                    "cancelled"
                )
            beats += 1
            await _safe_emit(
                emit,
                {
                    "type": "step_heartbeat",
                    "step": step,
                    "beat": beats,
                    "elapsed_s": round(clock.elapsed_seconds(), 1),
                    "active_s": round(clock.active_seconds(), 1),
                    "waiting_for_approval": clock.paused,
                },
            )
    except asyncio.CancelledError:
        # The goal itself was cancelled (operator cancel / goal timeout): take
        # the step down with it.
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        raise


async def _safe_emit(emit: EmitFn, event: dict[str, Any]) -> None:
    with contextlib.suppress(Exception):
        await emit(event)
