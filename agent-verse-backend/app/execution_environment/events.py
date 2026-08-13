"""Execution-environment event helpers.

Utilities for wrapping raw agent-loop events into :class:`ExecutionEvent`
objects and translating them back to the SSE format expected by the existing
GoalService event pipeline.
"""
from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from app.execution_environment.models import ExecutionEvent


def wrap_agent_event(
    *,
    goal_id: str,
    tenant_id: str,
    raw_event: dict[str, Any],
    runner_type: str = "",
    capsule_id: str = "",
    attempt_id: str = "",
) -> ExecutionEvent:
    """Wrap a raw agent-loop event dict into an :class:`ExecutionEvent`."""
    return ExecutionEvent(
        goal_id=goal_id,
        tenant_id=tenant_id,
        event_type=raw_event.get("type", "unknown"),
        payload=raw_event,
        runner_type=runner_type,
        capsule_id=capsule_id,
        attempt_id=attempt_id,
    )


def make_isolation_event(
    *,
    goal_id: str,
    tenant_id: str,
    event_type: str,
    runner_type: str = "",
    capsule_id: str = "",
    attempt_id: str = "",
    **extra: Any,
) -> ExecutionEvent:
    """Build an isolation-specific metadata event (not a core agent event)."""
    return ExecutionEvent(
        goal_id=goal_id,
        tenant_id=tenant_id,
        event_type=event_type,
        payload={"type": event_type, **extra},
        runner_type=runner_type,
        capsule_id=capsule_id,
        attempt_id=attempt_id,
    )


# Type alias for the callback signature used by GoalService / Celery tasks
GoalEventCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


def make_forwarding_callback(
    *,
    goal_id: str,
    tenant_id: str,
    downstream: GoalEventCallback,
    runner_type: str = "",
    capsule_id: str = "",
    attempt_id: str = "",
) -> GoalEventCallback:
    """Return an async callback that wraps events and forwards them downstream.

    The returned callback can be passed directly to AgentGraph.run() or
    AgentGraph.run() in place of the original GoalService callback — it adds
    isolation metadata without breaking the existing SSE event contract.
    """
    async def _callback(raw_event: dict[str, Any]) -> None:
        wrapped = wrap_agent_event(
            goal_id=goal_id,
            tenant_id=tenant_id,
            raw_event=raw_event,
            runner_type=runner_type,
            capsule_id=capsule_id,
            attempt_id=attempt_id,
        )
        await downstream(wrapped.to_sse_dict())

    return _callback
