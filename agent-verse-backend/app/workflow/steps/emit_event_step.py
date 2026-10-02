"""EmitEventStepNode — publishes a workflow event.

Old bug: neither the API nor the worker gave the compiler a Redis client, so
``self.redis`` was always None and the step reported success having published
nothing. Now a real run publishes on Redis pub/sub (tenant-namespaced channel,
for inline listeners and external subscribers) AND delivers the event durably
to every run of the same tenant suspended on a wait-on-event step for that
channel (via the run store). With neither dependency wired the step FAILS —
only ``is_test_run`` simulates.
"""

from __future__ import annotations

import json
from typing import Any

from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.state import WorkflowState


def tenant_event_channel(tenant_id: str, channel: str) -> str:
    """Redis channel for a workflow event. Namespaced per tenant so one tenant's
    emit can never satisfy another tenant's wait (the raw channel used to be
    shared across tenants)."""
    return f"wf_event:{tenant_id}:{channel}"


class EmitEventStepNode:
    def __init__(
        self, step: StepDefinition, context_resolver: ContextResolver, **services: Any
    ) -> None:
        self.step = step
        self.ctx = context_resolver
        self.redis = services.get("redis")
        self.run_store = services.get("run_store")

    async def execute(self, state: WorkflowState) -> dict[str, Any]:
        channel = str(self.ctx.resolve(self.step.event_channel_out, state))
        payload = self.ctx.resolve_dict(self.step.event_payload or {}, state)
        payload.setdefault("_source_run_id", state.get("run_id"))
        tenant_id = str(state.get("tenant_id") or "")
        payload.setdefault("_tenant_id", tenant_id)
        # WF-14: subscribers dedupe a replay of this emit after a worker crash.
        from app.workflow.idempotency import step_idempotency_key

        payload.setdefault("_idempotency_key", step_idempotency_key(state, self.step.id))
        output: dict[str, Any] = {"channel": channel, "payload": payload}

        if state.get("is_test_run"):
            output["simulated"] = True
        else:
            if not channel:
                raise RuntimeError(f"emit_event step {self.step.id!r}: event_channel_out is empty")
            can_deliver = self.run_store is not None and hasattr(
                self.run_store, "deliver_event"
            )
            if self.redis is None and not can_deliver:
                raise RuntimeError(
                    f"emit_event step {self.step.id!r} cannot publish: no Redis client or "
                    "durable run store is configured for the workflow engine"
                )
            if self.redis is not None:
                redis_channel = tenant_event_channel(tenant_id, channel)
                receivers = await self.redis.publish(redis_channel, json.dumps(payload))
                output["redis_channel"] = redis_channel
                output["subscribers"] = int(receivers or 0)
            if can_deliver and tenant_id:
                output["delivered_to_waits"] = await self.run_store.deliver_event(
                    tenant_id, channel, payload
                )

        return {"step_outputs": {**(state.get("step_outputs") or {}), self.step.id: output}}
