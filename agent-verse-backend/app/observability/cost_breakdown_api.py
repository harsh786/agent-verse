"""
Cost breakdown API endpoint — per-role token/cost attribution per goal.
Exposes the GoalCostBreakdown data collected by graph.py.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/goals", tags=["goals"])


@router.get("/{goal_id}/cost-metrics")
async def get_goal_cost_metrics(goal_id: str, request: Request) -> dict:
    """Return per-role (planner/executor/verifier) token and cost breakdown for a goal.

    Only for the caller's own goal. The breakdown is keyed by goal id alone, and
    this used to serve it to any authenticated tenant that knew (or guessed) the id.
    """
    from app.observability.cost_breakdown import get_breakdown

    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Missing or invalid API key")
    goal_service = getattr(request.app.state, "goal_service", None)
    try:
        owned = goal_service is not None and await goal_service.get_goal(
            goal_id=goal_id, tenant_ctx=tenant_ctx
        )
    except Exception:
        owned = False
    if not owned:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")

    breakdown = get_breakdown(goal_id)
    bd = breakdown.to_dict()

    # Add cache metrics if available
    if tenant_ctx:
        llm_cache = getattr(request.app.state, "llm_response_cache", None)
        if llm_cache is not None:
            try:
                cache_stats = llm_cache.stats(tenant_ctx.tenant_id)
                bd["llm_cache"] = cache_stats
            except Exception:
                pass

    return bd
