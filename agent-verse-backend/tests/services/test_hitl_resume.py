"""Tests for GoalService.resume_goal — HITL re-invocation path."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.agent.state import GoalStatus
from app.services.goal_service import GoalRecord, GoalService, _GOAL_PAUSE_EVENTS
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-hitl", plan=PlanTier.PROFESSIONAL, api_key_id="kid-hitl")


def _make_waiting_goal(svc: GoalService, goal_id: str = "g-hitl-1") -> GoalRecord:
    """Insert a goal in WAITING_HUMAN status into the service's registry."""
    record = GoalRecord(
        goal_id=goal_id,
        goal_text="test goal",
        status=GoalStatus.WAITING_HUMAN,
        tenant_id=_CTX.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="2024-01-01T00:00:00",
    )
    svc._goals[goal_id] = record
    return record


@pytest.mark.asyncio
async def test_resume_goal_approved_sets_status_to_executing() -> None:
    """Approved resume must transition goal status from WAITING_HUMAN → EXECUTING."""
    svc = GoalService()
    record = _make_waiting_goal(svc)

    result = await svc.resume_goal("g-hitl-1", _CTX, approved=True)

    assert record.status == GoalStatus.EXECUTING
    assert result["status"] == "resumed"
    assert result["goal_id"] == "g-hitl-1"


@pytest.mark.asyncio
async def test_resume_goal_fires_pause_event_signal() -> None:
    """Approved resume (no graph instance) must set() the asyncio Event."""
    svc = GoalService()
    _make_waiting_goal(svc)

    evt = asyncio.Event()
    _GOAL_PAUSE_EVENTS["g-hitl-1"] = evt

    await svc.resume_goal("g-hitl-1", _CTX, approved=True)

    assert evt.is_set(), "pause event must be set on approved resume"
    assert "g-hitl-1" not in _GOAL_PAUSE_EVENTS, "event must be popped from registry"


@pytest.mark.asyncio
async def test_resume_goal_rejected_sets_status_to_failed() -> None:
    """Rejected resume must transition goal status to FAILED."""
    svc = GoalService()
    record = _make_waiting_goal(svc)

    result = await svc.resume_goal(
        "g-hitl-1", _CTX, approved=False, feedback="Not allowed"
    )

    assert record.status == GoalStatus.FAILED
    assert result["status"] == "rejected"


@pytest.mark.asyncio
async def test_resume_goal_rejected_does_not_fire_pause_event() -> None:
    """A rejected resume must NOT set the asyncio pause event."""
    svc = GoalService()
    _make_waiting_goal(svc)

    evt = asyncio.Event()
    _GOAL_PAUSE_EVENTS["g-hitl-1"] = evt

    await svc.resume_goal("g-hitl-1", _CTX, approved=False, feedback="denied")

    assert not evt.is_set(), "pause event must NOT be set on rejection"


@pytest.mark.asyncio
async def test_resume_goal_approved_default_is_true() -> None:
    """Calling resume_goal with no approved kwarg defaults to approved=True (backward compat)."""
    svc = GoalService()
    record = _make_waiting_goal(svc)

    # Old-style call (no approved kwarg) — must still work
    result = await svc.resume_goal("g-hitl-1", _CTX)

    assert record.status == GoalStatus.EXECUTING
    assert result["status"] == "resumed"


@pytest.mark.asyncio
async def test_resume_goal_raises_for_terminal_goal() -> None:
    """Calling resume on an already-terminal goal raises ValueError."""
    svc = GoalService()
    record = GoalRecord(
        goal_id="g-done-1",
        goal_text="done goal",
        status=GoalStatus.COMPLETE,
        tenant_id=_CTX.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="2024-01-01T00:00:00",
    )
    svc._goals["g-done-1"] = record

    with pytest.raises(ValueError, match="already terminal"):
        await svc.resume_goal("g-done-1", _CTX, approved=True)


@pytest.mark.asyncio
async def test_resume_goal_uses_graph_checkpoint_when_instance_stored() -> None:
    """When a graph instance is stored on the record, resume should attempt checkpoint re-invocation."""
    svc = GoalService()
    record = _make_waiting_goal(svc)

    streamed_inputs: list[Any] = []

    class FakeGraph:
        async def astream(self, input_state: Any, config: Any):
            streamed_inputs.append(input_state)
            yield {"result": "ok"}

    class FakeAgentGraph:
        _graph = FakeGraph()

    record._graph_instance = FakeAgentGraph()

    result = await svc.resume_goal("g-hitl-1", _CTX, approved=True, feedback="looks good")

    # Wait a tick for the create_task to run
    await asyncio.sleep(0.05)

    assert result["status"] == "resumed"
    assert record.status == GoalStatus.EXECUTING
    # The graph's astream should have been invoked
    assert streamed_inputs, "graph.astream was not called for checkpoint resume"
    assert streamed_inputs[0].get("hitl_decision") == "approved"


@pytest.mark.asyncio
async def test_resume_goal_checkpoint_failure_fires_fallback_event() -> None:
    """If graph.astream raises, the _resume_graph task catches it and fires the pause event."""
    svc = GoalService()
    record = _make_waiting_goal(svc)

    # Graph that raises on first iteration
    class BrokenGraph:
        async def astream(self, input_state: Any, config: Any):
            raise RuntimeError("checkpoint unavailable")
            yield  # make it a generator  # pragma: no cover

    class BrokenAgentGraph:
        _graph = BrokenGraph()

    record._graph_instance = BrokenAgentGraph()

    evt = asyncio.Event()
    _GOAL_PAUSE_EVENTS["g-hitl-1"] = evt

    result = await svc.resume_goal("g-hitl-1", _CTX, approved=True)

    # The coroutine is fire-and-forget, wait for it to execute
    await asyncio.sleep(0.05)

    # Status should be EXECUTING (set before the task runs)
    assert result["status"] == "resumed"
    assert record.status == GoalStatus.EXECUTING
    # The fallback inside _resume_graph should have set the event
    assert evt.is_set(), "fallback pause event must be set when checkpoint fails"


@pytest.mark.asyncio
async def test_resume_goal_dispatches_resumed_event() -> None:
    """resume_goal must emit a goal_resumed event to SSE subscribers."""
    svc = GoalService()
    record = _make_waiting_goal(svc)

    received_events: list[dict[str, Any]] = []

    q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    record.subscribers.append(q)

    await svc.resume_goal("g-hitl-1", _CTX, approved=True)

    # Drain the subscriber queue
    while not q.empty():
        item = q.get_nowait()
        if item is not None:
            received_events.append(item)

    types = [e.get("type") for e in received_events]
    assert "goal_resumed" in types, f"goal_resumed event not dispatched; got: {types}"
