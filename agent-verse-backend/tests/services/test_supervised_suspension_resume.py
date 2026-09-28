"""Regression: a supervised goal whose graph ends waiting for approvals can be resumed.

In supervised autonomy the router sends a verified-but-gated goal to
``waiting_human`` → END. ``_run_agent_loop`` ignored the returned state, so the
goal stayed "executing" forever (holding its concurrency slot) and
``resume_goal`` — which requires WAITING_HUMAN — could never continue it.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from app.agent.state import AgentState, GoalStatus
from app.services.goal_service import _SUSPENDED_KEY, GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="tid-sup", plan=PlanTier.PROFESSIONAL, api_key_id="kid-sup")


class _Loop:
    """Stands in for AgentGraph: first run ends waiting, the next completes."""

    def __init__(self) -> None:
        self.runs: list[str] = []

    async def run(self, *, goal: str, tenant_ctx: Any, goal_id: str, **_: Any) -> AgentState:
        self.runs.append(goal_id)
        st = AgentState(goal=goal, tenant_ctx=tenant_ctx)
        st.goal_id = goal_id
        st.status = GoalStatus.WAITING_HUMAN if len(self.runs) == 1 else GoalStatus.COMPLETE
        return st


async def test_waiting_human_end_suspends_and_approval_relaunches_same_goal() -> None:
    svc = GoalService()
    record = GoalRecord(
        goal_id="g-sup-1",
        goal_text="deploy the release",
        status=GoalStatus.EXECUTING,
        tenant_id=CTX.tenant_id,
        priority="normal",
        dry_run=False,
        created_at="2026-01-01T00:00:00",
    )
    svc._goals["g-sup-1"] = record
    loop = _Loop()
    decrement = AsyncMock()
    increment = AsyncMock()

    with (
        patch.object(svc, "_make_agent_loop_for_tenant", return_value=loop),
        patch.object(svc, "_build_tool_context", AsyncMock(return_value=None)),
        patch("app.tenancy.limits.decrement_concurrent_goals", decrement),
        patch("app.tenancy.limits.check_and_increment_concurrent_goals", increment),
    ):
        await svc._run_agent_loop("g-sup-1", "deploy the release", CTX)

        assert record.status == GoalStatus.WAITING_HUMAN
        assert record.execution_context.get(_SUSPENDED_KEY) is True
        assert "goal_waiting_human" in [e.get("type") for e in record.events]
        decrement.assert_awaited()  # no task holds the goal while it waits

        result = await svc.resume_goal("g-sup-1", CTX, approved=True)
        assert result["status"] == "resumed"
        assert record.task is not None
        await asyncio.wait_for(record.task, timeout=5)

    assert loop.runs == ["g-sup-1", "g-sup-1"], "must relaunch under the SAME goal id"
    increment.assert_awaited_once()
    assert _SUSPENDED_KEY not in record.execution_context
