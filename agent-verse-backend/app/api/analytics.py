"""Analytics REST API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _require_tenant(request: Request) -> Any:
    from fastapi import HTTPException
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return ctx


@router.get("/goals")
async def goal_analytics(
    days: int = Query(30, ge=1, le=365),
    agent_id: str | None = None,
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    agg = _get_aggregator(request)
    tenant = getattr(getattr(request, "state", None), "tenant", None) if request else None
    tenant_id = getattr(tenant, "tenant_id", "") if tenant else ""
    m = await agg.goal_metrics(tenant_id=tenant_id, days=days, agent_id=agent_id)
    return {
        "period_days": days,
        "total": m.total,
        "completed": m.completed,
        "failed": m.failed,
        "cancelled": m.cancelled,
        "success_rate": m.success_rate,
        "avg_duration_s": m.avg_duration_s,
        "avg_cost_usd": m.avg_cost_usd,
        "total_cost_usd": m.total_cost_usd,
        # also expose by_status for frontend compatibility
        "by_status": {
            "complete": m.completed,
            "failed": m.failed,
            "cancelled": m.cancelled,
        },
    }


@router.get("/tools")
async def tool_analytics(
    days: int = Query(30, ge=1, le=365),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    agg = _get_aggregator(request)
    tools = agg.tool_metrics(days=days)
    return {
        "period_days": days,
        "tools": [
            {
                "name": t.tool_name,
                "tool_name": t.tool_name,
                "total": t.call_count,
                "call_count": t.call_count,
                "failure_count": t.failure_count,
                "failure_rate": t.failure_rate,
                "success": t.call_count - t.failure_count,
                "failed": t.failure_count,
                "success_rate": round(1.0 - t.failure_rate, 4),
                "avg_latency_ms": t.avg_latency_ms,
            }
            for t in tools
        ],
    }


@router.get("/costs")
async def cost_analytics(
    days: int = Query(30, ge=1, le=365),
    bucket: str = Query("day", pattern="^(day|week)$"),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    agg = _get_aggregator(request)
    trends = agg.cost_trends(days=days, bucket=bucket)
    total = sum(t["cost_usd"] for t in trends)
    tenant = getattr(getattr(request, "state", None), "tenant", None) if request else None
    tenant_id = getattr(tenant, "tenant_id", "") if tenant else ""
    tenant_ctx = tenant
    m = await agg.goal_metrics(tenant_id=tenant_id, days=days)

    # Use GoalService's accurate cost_today_usd rather than summing the 30-day total
    goal_service = getattr(request.app.state, "goal_service", None) if request else None
    if goal_service is not None and tenant_ctx is not None:
        try:
            gm = await goal_service.get_metrics(tenant_ctx=tenant_ctx)
            cost_today = gm.get("cost_today_usd", 0.0)
        except Exception:
            cost_today = 0.0
    else:
        cost_today = 0.0

    return {
        "period_days": days,
        "bucket": bucket,
        "total_cost_usd": round(total, 6),
        "cost_today_usd": round(cost_today, 6),  # accurate today-only value
        "goals_today": m.total,
        "total_goals": m.total,
        "avg_cost_per_goal": round(total / max(m.total, 1), 6),
        "trends": trends,
    }


@router.get("/agents")
async def agent_analytics(
    days: int = Query(30, ge=1, le=365),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    agg = _get_aggregator(request)
    agents = agg.agent_metrics(days=days)
    return {
        "period_days": days,
        "agents": [
            {
                "agent_id": a.agent_id,
                "goal_count": a.goal_count,
                "success_rate": a.success_rate,
                "avg_eval_score": a.avg_eval_score,
                "avg_cost_usd": a.avg_cost_usd,
            }
            for a in agents
        ],
    }


@router.get("/observability/spans")
async def get_spans(request: Request, limit: int = 50) -> list[dict]:
    """Get recent in-process trace spans (dev mode when OTLP not configured)."""
    from app.observability.tracing import get_recent_spans
    return get_recent_spans(limit=limit)


def _get_aggregator(request: Request):  # type: ignore[return]
    from app.analytics.aggregator import GoalAnalyticsAggregator
    goal_service = getattr(request.app.state, "goal_service", None)
    db = getattr(request.app.state, "db_session_factory", None)
    return GoalAnalyticsAggregator(goal_service=goal_service, db=db)
