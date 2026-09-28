"""Tests for GoalService.resume_goal — HITL re-invocation path."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.agent.state import GoalStatus
from app.services.goal_service import _GOAL_PAUSE_EVENTS, GoalRecord, GoalService
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
async def test_pause_gate_blocks_the_running_goal_until_resume() -> None:
    """Regression: pause_goal created an event nothing waited on, so a paused
    in-process goal kept executing. The agent loop now awaits a pause gate at
    every step boundary; resume_goal releases it."""
    svc = GoalService()
    record = _make_waiting_goal(svc, goal_id="g-gate-1")
    _GOAL_PAUSE_EVENTS["g-gate-1"] = asyncio.Event()  # what pause_goal installs
    gate = svc._make_pause_gate("g-gate-1", _CTX)

    task = asyncio.create_task(gate())
    await asyncio.sleep(0.05)
    assert not task.done(), "a paused goal must not pass the step boundary"

    await svc.resume_goal("g-gate-1", _CTX, approved=True)
    await asyncio.wait_for(task, timeout=1.0)
    assert record.status == GoalStatus.EXECUTING
    types = [e.get("type") for e in record.events]
    assert "goal_paused_at_step_boundary" in types
    assert "goal_execution_resumed" in types


@pytest.mark.asyncio
async def test_pause_gate_passes_straight_through_when_not_paused() -> None:
    svc = GoalService()
    _GOAL_PAUSE_EVENTS.pop("g-gate-2", None)
    await asyncio.wait_for(svc._make_pause_gate("g-gate-2", _CTX)(), timeout=0.5)


@pytest.mark.asyncio
async def test_pause_gate_honours_a_pause_issued_on_another_replica() -> None:
    """A pause handled by a different API replica only sets the Redis flag."""
    from app.reliability.goal_lifecycle import GoalCancelledError

    svc = GoalService()
    flags = {"goal_paused:g-gate-3": "1"}

    class _Redis:
        async def get(self, key: str) -> str | None:
            return flags.get(key)

    svc._redis = _Redis()
    task = asyncio.create_task(svc._make_pause_gate("g-gate-3", _CTX)())
    await asyncio.sleep(0.05)
    assert not task.done()
    flags["goal_cancelled:g-gate-3"] = "1"
    with pytest.raises(GoalCancelledError):
        await asyncio.wait_for(task, timeout=5.0)


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


@pytest.mark.asyncio
async def test_resume_goal_concurrent_calls_do_not_double_resume() -> None:
    """Regression (race): racing two resume_goal() calls must not both succeed
    (double-click "Approve", a retried HTTP request, two replicas handling the
    same webhook)."""
    svc = GoalService()
    record = _make_waiting_goal(svc, goal_id="g-race-1")

    results = await asyncio.gather(
        svc.resume_goal("g-race-1", _CTX, approved=True, feedback="a"),
        svc.resume_goal("g-race-1", _CTX, approved=True, feedback="b"),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1, f"exactly one resume_goal call should succeed, got {results}"
    assert len(failures) == 1
    assert isinstance(failures[0], ValueError)
    assert [e.get("type") for e in record.events].count("goal_resumed") == 1
