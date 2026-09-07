"""WT-1 / P0-5: GoalService.create_goal adapter over submit_goal for trigger dispatch."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext


def _ctx() -> TenantContext:
    return TenantContext(tenant_id="t-1", plan=PlanTier.FREE, api_key_id="k1")


async def test_create_goal_delegates_to_submit_goal():
    """FAILS TODAY: GoalService has no create_goal (dispatcher calls a missing method)."""
    svc = GoalService.__new__(GoalService)  # bypass heavy __init__
    svc.submit_goal = AsyncMock(return_value={"goal_id": "g-1", "status": "running"})

    out = await svc.create_goal(
        tenant_ctx=_ctx(), goal_text="do X", agent_id="a-1", idempotency_key="idem-1"
    )

    assert out["goal_id"] == "g-1"
    kwargs = svc.submit_goal.await_args.kwargs
    assert kwargs["goal"] == "do X"
    assert kwargs["dry_run"] is False
    assert kwargs["agent_id"] == "a-1"
    assert kwargs["execution_context"]["trigger_idempotency_key"] == "idem-1"
    assert kwargs["execution_context"]["source"] == "trigger"


async def test_create_goal_propagates_submit_errors():
    """Plan/limit errors from submit_goal must propagate unchanged."""
    svc = GoalService.__new__(GoalService)
    svc.submit_goal = AsyncMock(side_effect=RuntimeError("plan limit"))
    with pytest.raises(RuntimeError, match="plan limit"):
        await svc.create_goal(tenant_ctx=_ctx(), goal_text="do X")
