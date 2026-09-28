"""WaitStepNode — timer-based or event-gate pause."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowRunStatus, WorkflowState

# Max seconds an event-gated wait blocks in a single node execution before it
# yields control (the compiler's per-step timeout bounds the node overall).
_EVENT_WAIT_CAP_S = 30.0

# Timer waits up to this long sleep inline even when durable waits are enabled
# (cheaper than a suspend/beat round-trip, whose granularity is ~30 s).
_INLINE_WAIT_MAX_S = 30.0


class WaitStepNode:
    def __init__(
        self, step: StepDefinition, context_resolver: ContextResolver, **services: Any
    ) -> None:
        self.step = step
        self.ctx = context_resolver
        self.redis = services.get("redis")
        self.run_store = services.get("run_store")
        # Set by the wiring that also runs the ``workflow.wake_due_timer_waits``
        # beat (Celery worker + DB-backed API runner). Without that beat a
        # suspended run would never be woken, so it must be opt-in.
        self.durable = bool(services.get("durable_timer_waits"))

    def _can_suspend(self, state: WorkflowState) -> bool:
        return bool(
            self.durable
            and self.run_store is not None
            and hasattr(self.run_store, "set_timer_wait")
            and state.get("run_id")
            and state.get("tenant_id")
        )

    async def _timer_wait(self, state: WorkflowState, seconds: float) -> dict[str, Any]:
        """Timer wait.

        Old bug: ``asyncio.sleep(min(seconds, 300))`` then reported
        ``waited_seconds = seconds`` — a 1h wait finished after 5 minutes while
        claiming it waited an hour, and held a worker slot throughout.

        * Durable (long wait, backend wired): persist the wake time, suspend the
          run as ``waiting_timer`` and end the task; the beat re-dispatches the
          run after ``wake_at`` and this step then completes. The wake time is
          recorded once, so an early/duplicate wake re-suspends until the SAME
          deadline instead of restarting the clock.
        * Otherwise: sleep the FULL duration inline and report the measured time.
        """
        step_id = self.step.id
        if seconds > _INLINE_WAIT_MAX_S and self._can_suspend(state):
            rid, tid = str(state["run_id"]), str(state["tenant_id"])
            now = datetime.now(UTC)
            raw = await self.run_store.get_timer_wait(tid, rid, step_id)
            wake_at = datetime.fromisoformat(raw) if raw else None
            if wake_at is None:
                wake_at = now + timedelta(seconds=seconds)
                await self.run_store.set_timer_wait(tid, rid, step_id, wake_at)
            if now < wake_at:
                if raw:  # woken early: make sure the scan still sees the wake time
                    await self.run_store.set_timer_wait(tid, rid, step_id, wake_at)
                return {
                    "status": WorkflowRunStatus.WAITING_TIMER,
                    # Downstream nodes short-circuit on this marker (see the
                    # compiler's node_fn) so nothing after the wait runs yet.
                    "paused_by": f"wait_timer:{step_id}",
                    "step_outputs": {
                        **(state.get("step_outputs") or {}),
                        step_id: {
                            "waiting": True,
                            "wake_at": wake_at.isoformat(),
                            "requested_seconds": seconds,
                        },
                    },
                }
            started = wake_at - timedelta(seconds=seconds)
            output: dict[str, Any] = {
                "requested_seconds": seconds,
                "waited_seconds": (now - started).total_seconds(),
                "wake_at": wake_at.isoformat(),
                "durable": True,
            }
        else:
            t0 = time.monotonic()
            await asyncio.sleep(seconds)
            output = {
                "requested_seconds": seconds,
                "waited_seconds": time.monotonic() - t0,
                "durable": False,
            }
        return {"step_outputs": {**(state.get("step_outputs") or {}), step_id: output}}

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        duration = self.step.duration
        channel = self.step.event_channel

        if state.get("is_test_run"):
            output = {"waited": True, "duration": duration or channel}
        elif duration:
            return await self._timer_wait(state, self._parse_seconds(duration))
        elif channel and self.redis:
            # Block on a Redis pub/sub event, resuming when it arrives.
            resolved_channel = str(self.ctx.resolve(channel, state))
            event = await self._await_event(resolved_channel, _EVENT_WAIT_CAP_S)
            output = {
                "waited_channel": resolved_channel,
                "event": event,
                "timed_out": event is None,
            }
        else:
            output = {"waited": True}

        return {"step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output}}

    async def _await_event(self, channel: str, timeout_s: float) -> dict[str, Any] | None:
        """Subscribe to ``channel`` and return the first event payload, or None
        on timeout. Never raises — a subscribe failure is treated as a timeout."""

        async def _listen() -> dict[str, Any] | None:
            async with self.redis.pubsub() as pubsub:
                await pubsub.subscribe(channel)
                async for message in pubsub.listen():
                    if message.get("type") != "message":
                        continue
                    data = message.get("data")
                    try:
                        parsed = json.loads(data)
                    except (TypeError, ValueError):
                        parsed = {"raw": data}
                    return parsed if isinstance(parsed, dict) else {"value": parsed}
            return None

        try:
            return await asyncio.wait_for(_listen(), min(timeout_s, _EVENT_WAIT_CAP_S))
        except TimeoutError:
            return None
        except Exception:  # subscribe/redis failure → behave as a timeout
            return None

    @staticmethod
    def _parse_seconds(s: str) -> float:
        s = s.strip()
        if s.endswith("s"):
            return float(s[:-1])
        if s.endswith("m"):
            return float(s[:-1]) * 60
        if s.endswith("h"):
            return float(s[:-1]) * 3600
        return float(s)
