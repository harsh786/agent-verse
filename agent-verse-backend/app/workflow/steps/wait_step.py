"""WaitStepNode — timer-based or event-gate pause."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowState

# Max seconds an event-gated wait blocks in a single node execution before it
# yields control (the compiler's per-step timeout bounds the node overall).
_EVENT_WAIT_CAP_S = 30.0


class WaitStepNode:
    def __init__(
        self, step: StepDefinition, context_resolver: ContextResolver, **services: Any
    ) -> None:
        self.step = step
        self.ctx = context_resolver
        self.redis = services.get("redis")

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        duration = self.step.duration
        channel = self.step.event_channel

        if state.get("is_test_run"):
            output = {"waited": True, "duration": duration or channel}
        elif duration:
            seconds = self._parse_seconds(duration)
            await asyncio.sleep(min(seconds, 300))  # cap at 5min in non-test
            output = {"waited_seconds": seconds}
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
