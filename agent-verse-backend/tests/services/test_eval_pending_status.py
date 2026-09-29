"""GET /goals/{id}/eval reports "pending" while completion-time scoring runs.

The goal is marked complete before its (charged, circuit-broken) scorer calls
finish; answering "not_evaluated" in that window told clients the goal would
never be scored.
"""

from __future__ import annotations

from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k")


async def test_eval_status_is_pending_while_scoring_then_not_evaluated() -> None:
    svc = GoalService()
    created = await svc.submit_goal(
        goal="Summarise the churn cohort", tenant_ctx=_CTX, priority="normal", dry_run=True
    )
    goal_id = created["goal_id"] if isinstance(created, dict) else created.goal_id
    svc._eval_pending.add(goal_id)
    assert (await svc.get_eval(goal_id, _CTX))["status"] == "pending"
    svc._eval_pending.discard(goal_id)
    assert (await svc.get_eval(goal_id, _CTX))["status"] == "not_evaluated"
