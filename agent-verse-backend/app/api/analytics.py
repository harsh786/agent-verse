"""Analytics REST API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

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
    try:
        m = await agg.goal_metrics(tenant_id=tenant_id, days=days, agent_id=agent_id)
    except Exception:
        from app.analytics.aggregator import GoalMetrics

        m = GoalMetrics()
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
    tenant = getattr(getattr(request, "state", None), "tenant", None) if request else None
    tenant_id = getattr(tenant, "tenant_id", "") if tenant else ""
    # Use DB-backed method when tenant_id available (falls back to in-memory)
    tools = await agg.tool_metrics_db(tenant_id=tenant_id, days=days)
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
    tenant = getattr(getattr(request, "state", None), "tenant", None) if request else None
    tenant_id = getattr(tenant, "tenant_id", "") if tenant else ""

    # Use DB-backed methods when tenant_id available (fall back to in-memory)
    trends = await agg.cost_trends_db(tenant_id=tenant_id, days=days, bucket=bucket)
    cost_by_model = await agg.cost_by_model_db(tenant_id=tenant_id, days=days)

    total = sum(t["cost_usd"] for t in trends)
    tenant_ctx = tenant
    try:
        m = await agg.goal_metrics(tenant_id=tenant_id, days=days)
    except Exception:
        from app.analytics.aggregator import GoalMetrics

        m = GoalMetrics()

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

    # Normalize trends to use "date" key for frontend compatibility
    cost_by_day = [{"date": t.get("period", ""), "cost_usd": t["cost_usd"]} for t in trends]

    return {
        "period_days": days,
        "bucket": bucket,
        "total_cost_usd": round(total, 6),
        "cost_today_usd": round(cost_today, 6),
        "goals_today": m.total,
        "total_goals": m.total,
        "avg_cost_per_goal": round(total / max(m.total, 1), 6),
        # Frontend-expected keys:
        "cost_by_day": cost_by_day,  # normalized with "date" key
        "cost_by_model": cost_by_model,  # {model_name: total_cost_usd}
        # Legacy key kept for backward compat:
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


@router.get("/observability/traces")
async def list_traces(
    request: Request,
    goal_id: str | None = None,
    limit: int = 20,
) -> dict:
    """List agent execution traces. Integrates with OTel when configured."""
    tenant_ctx = getattr(request.state, "tenant", None)
    if tenant_ctx is None:
        raise HTTPException(status_code=401, detail="Auth required")

    traces: list[dict] = []
    if goal_id:
        # Prefer REAL captured spans from the run-timeline store (actual start_ns,
        # duration_ms, trace_id/span_id from OpenTelemetry) over any estimate.
        from app.observability.tracing import get_run_timeline_store

        entries = get_run_timeline_store().get(tenant_ctx.tenant_id, goal_id)
        if entries:
            spans = [
                {
                    "span_id": e.get("span_id"),
                    "trace_id": e.get("trace_id"),
                    "name": e.get("name"),
                    "start_time": e.get("start_ns"),
                    "duration_ms": e.get("duration_ms"),
                    "status": (e.get("status") or "UNSET"),
                    "attributes": {
                        "role": e.get("role"),
                        "model": e.get("model"),
                        "tool": e.get("tool"),
                        "input_tokens": e.get("input_tokens"),
                        "output_tokens": e.get("output_tokens"),
                        "cost_usd": e.get("cost_usd"),
                    },
                }
                for e in entries
            ]
            traces.append(
                {
                    "trace_id": entries[0].get("trace_id") or goal_id,
                    "goal_id": goal_id,
                    "goal": "Goal execution",
                    "source": "otel",
                    "spans": spans,
                    "total_cost_usd": sum(float(e.get("cost_usd") or 0.0) for e in entries),
                    "total_tokens": sum(
                        int(e.get("input_tokens") or 0) + int(e.get("output_tokens") or 0)
                        for e in entries
                    ),
                }
            )
        else:
            # No captured spans (tracing off, or the run predates the collector).
            # Fall back to the cost breakdown but DO NOT fabricate per-span timing:
            # report per-role token/cost with source="cost_estimate" and no durations.
            from app.observability.cost_breakdown import get_breakdown

            bd_dict = get_breakdown(goal_id).to_dict()
            if bd_dict.get("roles"):
                traces.append(
                    {
                        "trace_id": goal_id,
                        "goal_id": goal_id,
                        "goal": "Goal execution",
                        "source": "cost_estimate",
                        "spans": [
                            {
                                "span_id": f"{r['role']}_span",
                                "name": f"llm.{r['role']}",
                                "duration_ms": None,  # unknown — never faked
                                "status": "ok",
                                "attributes": {
                                    "model": r["model"],
                                    "tokens": str(r["input_tokens"] + r["output_tokens"]),
                                },
                            }
                            for r in bd_dict["roles"]
                        ],
                        "total_cost_usd": bd_dict["total_cost_usd"],
                        "total_tokens": sum(
                            r["input_tokens"] + r["output_tokens"] for r in bd_dict["roles"]
                        ),
                    }
                )

    return {"traces": traces, "total": len(traces)}


@router.get("/observability/spans")
async def get_spans(request: Request, limit: int = 50) -> list[dict]:
    """Get recent in-process trace spans (dev mode when OTLP not configured)."""
    from app.observability.tracing import get_recent_spans

    return get_recent_spans(limit=limit)


@router.get("/evals")
async def eval_analytics(
    days: int = Query(30, ge=1, le=365),
    request: Request = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Return eval scorecard aggregates for the current tenant."""
    tenant = _require_tenant(request) if request else None
    tenant_id = tenant.tenant_id if tenant else ""

    db_factory = getattr(request.app.state, "db_session_factory", None) if request else None
    if db_factory is None:
        goal_svc = getattr(request.app.state, "goal_service", None) if request else None
        db_factory = getattr(goal_svc, "_db", None) if goal_svc else None

    if db_factory is not None:
        try:
            from sqlalchemy import text as _t

            async with db_factory() as session:
                result = await session.execute(
                    _t(
                        "SELECT"
                        " COUNT(*) as total,"
                        " AVG(score_task_completion) as avg_task_completion,"
                        " AVG(score_efficiency) as avg_efficiency,"
                        " AVG(score_accuracy) as avg_accuracy,"
                        " AVG(score_safety) as avg_safety,"
                        " AVG(score_coherence) as avg_coherence,"
                        " SUM(CASE WHEN passed THEN 1 ELSE 0 END) as passed_count"
                        " FROM evaluations"
                        " WHERE tenant_id = :tid"
                        " AND run_at >= NOW() - (:days * INTERVAL '1 day')"
                    ),
                    {"tid": tenant_id, "days": days},
                )
                row = result.fetchone()
                if row and row[0]:
                    # Also fetch daily breakdown for trend visualization
                    evals_by_day: list[dict[str, Any]] = []
                    try:
                        daily_result = await session.execute(
                            _t(
                                "SELECT"
                                " DATE(run_at) as day,"
                                " COUNT(*) as total,"
                                " AVG(CASE WHEN passed THEN 1.0 ELSE 0.0 END) as pass_rate,"
                                " AVG(score_task_completion) as avg_score"
                                " FROM evaluations"
                                " WHERE tenant_id = :tid"
                                " AND run_at >= NOW() - (:days * INTERVAL '1 day')"
                                " GROUP BY DATE(run_at)"
                                " ORDER BY day ASC"
                            ),
                            {"tid": tenant_id, "days": days},
                        )
                        for dr in daily_result.fetchall():
                            evals_by_day.append(
                                {
                                    "date": str(dr[0]),
                                    "total": int(dr[1] or 0),
                                    "pass_rate": round(float(dr[2] or 0), 4),
                                    "avg_score": round(float(dr[3] or 0), 4),
                                }
                            )
                    except Exception:
                        pass  # Daily breakdown is best-effort

                    return {
                        "period_days": days,
                        "total_evals": int(row[0]),
                        "total": int(row[0]),
                        "passed": int(row[6] or 0),
                        "pass_rate": round((int(row[6] or 0)) / max(int(row[0]), 1), 4),
                        "avg_score": round(float(row[1] or 0), 4),  # use task_completion as primary
                        "avg_scores": {
                            "task_completion": round(float(row[1] or 0), 4),
                            "efficiency": round(float(row[2] or 0), 4),
                            "accuracy": round(float(row[3] or 0), 4),
                            "safety": round(float(row[4] or 0), 4),
                            "coherence": round(float(row[5] or 0), 4),
                        },
                        "evals_by_day": evals_by_day,
                    }
        except Exception:
            pass  # fall through to empty response

    return {
        "period_days": days,
        "total_evals": 0,
        "total": 0,
        "passed": 0,
        "pass_rate": 0.0,
        "avg_score": 0.0,
        "avg_scores": {
            "task_completion": 0.0,
            "efficiency": 0.0,
            "accuracy": 0.0,
            "safety": 0.0,
            "coherence": 0.0,
        },
        "evals_by_day": [],
    }


def _get_aggregator(request: Request):  # type: ignore[return]
    from app.analytics.aggregator import GoalAnalyticsAggregator

    goal_service = getattr(request.app.state, "goal_service", None)
    db = getattr(request.app.state, "db_session_factory", None)
    return GoalAnalyticsAggregator(goal_service=goal_service, db=db)
