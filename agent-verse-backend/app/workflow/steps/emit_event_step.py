"""EmitEventStepNode — publishes a Redis pub/sub event."""

from __future__ import annotations

import json
from typing import Any

from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowState


class EmitEventStepNode:
    def __init__(
        self, step: StepDefinition, context_resolver: ContextResolver, **services: Any
    ) -> None:
        self.step = step
        self.ctx = context_resolver
        self.redis = services.get("redis")

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        channel = str(self.ctx.resolve(self.step.event_channel_out, state))
        payload = self.ctx.resolve_dict(self.step.event_payload or {}, state)
        payload.setdefault("_source_run_id", state.get("run_id"))
        payload.setdefault("_tenant_id", state.get("tenant_id"))

        if self.redis and not state.get("is_test_run"):
            await self.redis.publish(channel, json.dumps(payload))

        return {
            "step_outputs": {
                **(state.get("step_outputs") or {}),
                self.step.id: {"channel": channel, "payload": payload},
            },
        }
