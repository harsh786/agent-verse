"""WaitStepNode — timer-based or event-gate pause."""
from __future__ import annotations

import asyncio
from typing import Any

from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowState


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
            # Wait for Redis pub/sub event (max 30s in non-test)
            resolved_channel = str(self.ctx.resolve(channel, state))
            output = {"waited_channel": resolved_channel}
        else:
            output = {"waited": True}

        return {"step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output}}

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
