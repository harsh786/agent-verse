"""Cost tracking API — summaries, per-agent breakdown, prediction, anomalies, budgets."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

router = APIRouter(prefix="/costs", tags=["costs"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PredictCostRequest(BaseModel):
    agent_id: str | None = None
    goal_description: str
    max_iterations: int = 10


class UpdateBudgetRequest(BaseModel):
    per_goal_usd: float = 10.0
    per_tenant_daily_usd: float = 500.0
    per_agent_daily_usd: dict[str, float] = {}
    alert_pct_thresholds: list[int] = [50, 75, 90]


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _cost_tracker(request: Request) -> Any:
    tracker = getattr(request.app.state, "cost_tracker", None)
    if tracker is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cost tracker not initialised",
        )
    return tracker


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/summary")
async def get_cost_summary(
    request: Request,
    period_days: int = 30,
) -> dict[str, Any]:
    """Aggregate cost summary for the authenticated tenant."""
    ctx = _require_tenant(request)
    tracker = _cost_tracker(request)

    budget_status = await tracker.get_budget_status(ctx.tenant_id)
    per_agent = await tracker.get_per_agent_summary(ctx.tenant_id, days=period_days)
    total_usd = sum(a["total_cost_usd"] for a in per_agent)

    return {
        "period_days": period_days,
        "total_usd": round(total_usd, 6),
        "by_agent": per_agent,
        "budget_status": budget_status,
    }


@router.get("/per-agent")
async def get_per_agent_costs(
    request: Request,
    period_days: int = 30,
) -> dict[str, Any]:
    """Per-agent cost breakdown for the authenticated tenant."""
    ctx = _require_tenant(request)
    tracker = _cost_tracker(request)
    agents = await tracker.get_per_agent_summary(ctx.tenant_id, days=period_days)
    return {"period_days": period_days, "agents": agents}


@router.post("/predict")
async def predict_cost(
    request: Request,
    body: PredictCostRequest,
) -> dict[str, Any]:
    """Pre-run cost estimate.  Read-only — no Redis or DB writes."""
    ctx = _require_tenant(request)
    tracker = _cost_tracker(request)

    result = await tracker.predict_cost(
        tenant_id=ctx.tenant_id,
        agent_id=body.agent_id,
        goal_description=body.goal_description,
        max_iterations=body.max_iterations,
    )
    return result


@router.get("/anomalies")
async def get_anomalies(request: Request) -> dict[str, Any]:
    """Return recently detected cost anomalies for the authenticated tenant."""
    ctx = _require_tenant(request)
    tracker = _cost_tracker(request)
    anomalies = await tracker.detect_anomaly(ctx.tenant_id)
    return {
        "anomalies": [
            {
                "tenant_id": a.tenant_id,
                "agent_id": a.agent_id,
                "anomaly_type": a.anomaly_type,
                "cost_actual_usd": a.cost_actual_usd,
                "cost_baseline_usd": a.cost_baseline_usd,
                "sigma_deviation": round(a.sigma_deviation, 2),
                "detected_at": a.detected_at,
            }
            for a in anomalies
        ]
    }


@router.get("/budgets")
async def get_budgets(request: Request) -> dict[str, Any]:
    """Return budget limits for the authenticated tenant."""
    ctx = _require_tenant(request)
    tracker = _cost_tracker(request)
    status_data = await tracker.get_budget_status(ctx.tenant_id)
    return {"tenant_id": ctx.tenant_id, **status_data}


@router.put("/budgets")
async def update_budgets(
    request: Request,
    body: UpdateBudgetRequest,
) -> dict[str, Any]:
    """Update budget limits for the authenticated tenant."""
    ctx = _require_tenant(request)
    tracker = _cost_tracker(request)

    if tracker._db is not None:
        try:
            import json as _json

            from sqlalchemy import text as _t
            async with tracker._db() as session:
                await session.execute(
                    _t(
                        "INSERT INTO budget_configs "
                        "(tenant_id, per_goal_usd, per_tenant_daily_usd, "
                        " per_agent_daily_usd, alert_pct_thresholds) "
                        "VALUES (:tid, :pg, :ptd, :pad::jsonb, :apt) "
                        "ON CONFLICT (tenant_id) DO UPDATE SET "
                        "  per_goal_usd = EXCLUDED.per_goal_usd, "
                        "  per_tenant_daily_usd = EXCLUDED.per_tenant_daily_usd, "
                        "  per_agent_daily_usd  = EXCLUDED.per_agent_daily_usd, "
                        "  alert_pct_thresholds = EXCLUDED.alert_pct_thresholds, "
                        "  updated_at = NOW()"
                    ),
                    {
                        "tid": ctx.tenant_id,
                        "pg": body.per_goal_usd,
                        "ptd": body.per_tenant_daily_usd,
                        "pad": _json.dumps(body.per_agent_daily_usd),
                        "apt": body.alert_pct_thresholds,
                    },
                )
                await session.commit()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to persist budget: {exc}",
            ) from exc

    return {
        "tenant_id": ctx.tenant_id,
        "per_goal_usd": body.per_goal_usd,
        "per_tenant_daily_usd": body.per_tenant_daily_usd,
        "per_agent_daily_usd": body.per_agent_daily_usd,
        "alert_pct_thresholds": body.alert_pct_thresholds,
    }


@router.get("/pricing")
async def get_pricing(request: Request) -> dict[str, Any]:
    """Return current model pricing table (read-only)."""
    _require_tenant(request)
    from app.intelligence.cost_tracker import MODEL_PRICING

    return {
        "models": [
            {"model_id": model_id, "pricing_usd_per_1m": pricing}
            for model_id, pricing in MODEL_PRICING.items()
        ]
    }
