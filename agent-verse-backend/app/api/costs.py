"""Cost tracking API — summaries, per-agent breakdown, prediction, anomalies, budgets."""

from __future__ import annotations

import csv
import hashlib
import io
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.governance.cost import BudgetConfig, persist_tenant_budget
from app.observability.logging import get_logger
from app.tenancy.rbac import require_role

logger = get_logger(__name__)

router = APIRouter(prefix="/costs", tags=["costs"])

# budget_configs stores the USD limits as NUMERIC(10,4) (max 999999.9999) and the
# alert thresholds as INTEGER[]; out-of-range values used to pass validation and
# then fail the INSERT, answering 500 for a schema-valid request.
_MAX_BUDGET_USD = 999_999.0
_BudgetUsd = Annotated[float, Field(ge=0, le=_MAX_BUDGET_USD, allow_inf_nan=False)]
_AlertPct = Annotated[int, Field(ge=1, le=100)]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PredictCostRequest(BaseModel):
    agent_id: str | None = None
    goal_description: str
    max_iterations: int = 10


class UpdateBudgetRequest(BaseModel):
    per_goal_usd: _BudgetUsd = 10.0
    per_tenant_daily_usd: _BudgetUsd = 500.0
    per_agent_daily_usd: dict[str, _BudgetUsd] = Field(default_factory=dict, max_length=1000)
    alert_pct_thresholds: list[_AlertPct] = Field(
        default_factory=lambda: [50, 75, 90], max_length=20
    )


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


def _sigma_to_severity(sigma: float) -> str:
    """Convert sigma deviation to human-readable severity level."""
    if sigma >= 4.0:
        return "high"
    if sigma >= 2.5:
        return "medium"
    return "low"


def _anomaly_type_label(anomaly_type: str) -> str:
    """Convert snake_case anomaly_type to human-readable label."""
    labels: dict[str, str] = {
        "spike": "Cost Spike",
        "sustained_high": "Sustained High",
        "budget_exceed": "Budget Exceeded",
    }
    return labels.get(anomaly_type, anomaly_type.replace("_", " ").title())


def _anomaly_id(agent_id: str | None, detected_at: str) -> str:
    """Stable deterministic ID for an anomaly observation."""
    raw = f"{agent_id or 'tenant'}:{detected_at}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/summary")
async def get_cost_summary(
    request: Request,
    period_days: int = 30,
    format: str = "json",  # noqa: A002
) -> Any:
    """Aggregate cost summary for the authenticated tenant.

    Pass ``?format=csv`` to receive a CSV download of daily cost data.
    """
    ctx = _require_tenant(request)
    tracker = _cost_tracker(request)

    budget_status = await tracker.get_budget_status(ctx.tenant_id)
    per_agent = await tracker.get_per_agent_summary(ctx.tenant_id, days=period_days)
    total_usd = sum(a["total_cost_usd"] for a in per_agent)

    if format.lower() == "csv":
        # Build CSV: per-agent cost rows
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=[
                "agent_id",
                "total_cost_usd",
                "goal_count",
                "avg_cost_per_goal",
                "total_prompt_tokens",
                "total_completion_tokens",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(per_agent)
        content = buf.getvalue()
        return Response(
            content=content,
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=cost_summary_{period_days}d.csv"
            },
        )

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
    """Per-agent cost breakdown for the authenticated tenant.

    Includes the computed ``avg_cost_per_goal`` field.
    """
    ctx = _require_tenant(request)
    tracker = _cost_tracker(request)
    agents = await tracker.get_per_agent_summary(ctx.tenant_id, days=period_days)
    return {"period_days": period_days, "agents": agents}


@router.get("/by-model")
async def get_cost_by_model(
    request: Request,
    period_days: int = 30,
) -> dict[str, Any]:
    """Cost breakdown by model for the authenticated tenant."""
    ctx = _require_tenant(request)
    tracker = _cost_tracker(request)
    models = await tracker.get_cost_by_model(ctx.tenant_id, days=period_days)
    return {"period_days": period_days, "models": models}


@router.get("/trends")
async def get_cost_trends(
    request: Request,
    period_days: int = 30,
) -> dict[str, Any]:
    """Daily cost trends with 7-day moving average and anomaly flags."""
    ctx = _require_tenant(request)
    tracker = _cost_tracker(request)
    trends = await tracker.get_cost_trends_with_anomalies(ctx.tenant_id, days=period_days)
    return {"period_days": period_days, "trends": trends}


@router.get("/projection")
async def get_monthly_projection(request: Request) -> dict[str, Any]:
    """Linear projection of monthly cost based on the last 7 days of spend."""
    ctx = _require_tenant(request)
    tracker = _cost_tracker(request)
    projection = await tracker.get_projected_monthly_cost(ctx.tenant_id)
    return {"tenant_id": ctx.tenant_id, **projection}


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
    """Return recently detected cost anomalies for the authenticated tenant.

    Maps internal fields to the frontend-friendly shape:
    ``{id, severity, message, cost_delta_usd, anomaly_type, agent_id, detected_at}``.
    """
    ctx = _require_tenant(request)
    tracker = _cost_tracker(request)
    anomalies = await tracker.detect_anomaly(ctx.tenant_id)
    return {
        "anomalies": [
            {
                "id": _anomaly_id(a.agent_id, a.detected_at),
                "tenant_id": a.tenant_id,
                "agent_id": a.agent_id,
                "anomaly_type": a.anomaly_type,
                "message": (
                    f"{_anomaly_type_label(a.anomaly_type)}: "
                    f"${a.cost_actual_usd:.4f} actual vs "
                    f"${a.cost_baseline_usd:.4f} baseline ({a.sigma_deviation:.1f}σ)"
                ),
                "cost_actual_usd": a.cost_actual_usd,
                "cost_baseline_usd": a.cost_baseline_usd,
                "cost_delta_usd": round(a.cost_actual_usd - a.cost_baseline_usd, 6),
                "sigma_deviation": round(a.sigma_deviation, 2),
                "severity": _sigma_to_severity(a.sigma_deviation),
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
    _rbac: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Update budget limits for the authenticated tenant (admin only).

    budget_configs is the single source of truth: every replica and Celery worker
    enforces it (via the cost controllers' TenantBudgetSource). With no DB the
    budget cannot be made durable or shared, so this is a 503 — it used to answer
    success without persisting anything.
    """
    ctx = _require_tenant(request)
    tracker = _cost_tracker(request)
    db = getattr(tracker, "_db", None)
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Budget store unavailable",
        )
    try:
        await persist_tenant_budget(
            db,
            ctx.tenant_id,
            per_goal_usd=body.per_goal_usd,
            per_tenant_daily_usd=body.per_tenant_daily_usd,
            per_agent_daily_usd=body.per_agent_daily_usd,
            alert_pct_thresholds=body.alert_pct_thresholds,
        )
    except Exception as exc:
        # Log the driver error server-side; do not echo SQL/driver text.
        logger.warning("budget_persist_failed", tenant_id=ctx.tenant_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist budget",
        ) from exc
    _apply_budget_locally(
        request,
        ctx.tenant_id,
        BudgetConfig(
            per_goal_usd=body.per_goal_usd, per_tenant_daily_usd=body.per_tenant_daily_usd
        ),
    )
    return {
        "tenant_id": ctx.tenant_id,
        "per_goal_usd": body.per_goal_usd,
        "per_tenant_daily_usd": body.per_tenant_daily_usd,
        "per_agent_daily_usd": body.per_agent_daily_usd,
        "alert_pct_thresholds": body.alert_pct_thresholds,
    }


def _apply_budget_locally(request: Request, tenant_id: str, cfg: BudgetConfig) -> None:
    """Make a just-committed budget bind on THIS replica immediately.

    Other replicas/workers re-read budget_configs within BUDGET_CACHE_TTL_S.
    """
    for name in ("redis_cost_controller", "cost_controller"):
        cc = getattr(request.app.state, name, None)
        if cc is None:
            continue
        if hasattr(cc, "invalidate_tenant_budget"):
            cc.invalidate_tenant_budget(tenant_id)
        if hasattr(cc, "configure_tenant_budget"):
            cc.configure_tenant_budget(tenant_id, cfg)


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
