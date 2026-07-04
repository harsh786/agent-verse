"""
Cost breakdown API endpoint — per-role token/cost attribution per goal.
Exposes the GoalCostBreakdown data collected by graph.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(prefix="/goals", tags=["goals"])


@router.get("/{goal_id}/cost-metrics")
async def get_goal_cost_metrics(goal_id: str, request: Request) -> dict:
    """Return per-role (planner/executor/verifier) token and cost breakdown for a goal."""
    from app.observability.cost_breakdown import get_breakdown

    breakdown = get_breakdown(goal_id)
    bd = breakdown.to_dict()

    # Add cache metrics if available
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx:
        llm_cache = getattr(request.app.state, "llm_response_cache", None)
        if llm_cache is not None:
            try:
                cache_stats = llm_cache.stats(tenant_ctx.tenant_id)
                bd["llm_cache"] = cache_stats
            except Exception:
                pass

    return bd
