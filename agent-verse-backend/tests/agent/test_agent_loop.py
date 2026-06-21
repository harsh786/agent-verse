"""Tests for the LangGraph agent loop (Phase 3 vertical slice).

Uses FakeProvider to drive the planner/executor/verifier LLM roles deterministically.
No real LLM calls — all responses are scripted.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.state import AgentState, GoalStatus, StepStatus
from app.agent.loop import AgentLoop
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext


_CTX = TenantContext(tenant_id="tid-test", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")


def _make_loop(
    planner_responses: list[str] | None = None,
    executor_responses: list[str] | None = None,
    verifier_responses: list[str] | None = None,
) -> AgentLoop:
    planner = FakeProvider(responses=planner_responses or ['{"steps": ["Step 1: Do the thing"]}'])
    executor = FakeProvider(responses=executor_responses or ["Done: completed the thing"])
    verifier = FakeProvider(responses=verifier_responses or ['{"success": true, "reason": "looks good"}'])
    return AgentLoop(planner=planner, executor=executor, verifier=verifier)


# ── AgentState ────────────────────────────────────────────────────────────────

def test_agent_state_initial_status_is_planning() -> None:
    state = AgentState(goal="test goal", tenant_ctx=_CTX)
    assert state.status == GoalStatus.PLANNING
    assert state.iterations == 0


def test_agent_state_has_required_fields() -> None:
    state = AgentState(goal="do something", tenant_ctx=_CTX)
    assert state.goal == "do something"
    assert state.tenant_ctx is _CTX
    assert state.steps == []
    assert state.context == {}


# ── Loop execution ────────────────────────────────────────────────────────────

async def test_agent_loop_completes_simple_goal() -> None:
    loop = _make_loop()
    state = await loop.run(goal="Write a hello world script", tenant_ctx=_CTX)
    assert state.status == GoalStatus.COMPLETE


async def test_agent_loop_records_steps() -> None:
    loop = _make_loop(
        planner_responses=['{"steps": ["Step 1: Research", "Step 2: Write"]}'],
        executor_responses=["Research done", "Writing done"],
        verifier_responses=[
            '{"success": true, "reason": "ok"}',
            '{"success": true, "reason": "ok"}',
        ],
    )
    state = await loop.run(goal="Create a report", tenant_ctx=_CTX)
    assert len(state.steps) >= 1
    assert all(s.status == StepStatus.COMPLETE for s in state.steps)


async def test_agent_loop_replans_on_verification_failure() -> None:
    loop = _make_loop(
        planner_responses=[
            '{"steps": ["Step 1: Try something"]}',
            '{"steps": ["Step 1: Try again"]}',
        ],
        executor_responses=["Attempted", "Done"],
        verifier_responses=[
            '{"success": false, "reason": "not done yet"}',
            '{"success": true, "reason": "ok now"}',
        ],
    )
    state = await loop.run(goal="Achieve something", tenant_ctx=_CTX)
    assert state.status == GoalStatus.COMPLETE
    assert state.iterations >= 2


async def test_agent_loop_stops_at_max_iterations() -> None:
    # Verifier always fails → loop hits max_iterations ceiling
    loop = AgentLoop(
        planner=FakeProvider(responses=['{"steps": ["Step 1"]}']),
        executor=FakeProvider(responses=["tried"]),
        verifier=FakeProvider(responses=['{"success": false, "reason": "nope"}']),
        max_iterations=3,
    )
    state = await loop.run(goal="Impossible task", tenant_ctx=_CTX)
    assert state.status == GoalStatus.FAILED
    assert state.iterations == 3


async def test_agent_loop_streams_step_events() -> None:
    events: list[dict] = []
    loop = _make_loop()

    async def collect(event: dict) -> None:
        events.append(event)

    await loop.run(goal="A task", tenant_ctx=_CTX, event_callback=collect)
    assert any(e.get("type") == "step_complete" for e in events)


# ── Pipeline stubs ────────────────────────────────────────────────────────────

async def test_pipeline_cost_check_stub_passes() -> None:
    from app.pipeline.steps import cost_check

    result = await cost_check(step="do X", tenant_ctx=_CTX)
    assert result is True  # stub always allows


async def test_pipeline_governance_stub_passes() -> None:
    from app.pipeline.steps import governance_check

    result = await governance_check(tool_name="any_tool", tenant_ctx=_CTX)
    assert result is True  # stub always allows


async def test_pipeline_dedup_stub_is_not_duplicate() -> None:
    from app.pipeline.steps import dedup_check

    result = await dedup_check(content_hash="abc123", tenant_ctx=_CTX)
    assert result is False  # stub always says "not a duplicate"
