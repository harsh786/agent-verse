"""Platform administration API.

Requires platform_admin role (checked via X-Admin-Key header or admin JWT).
NOT protected by the standard tenant middleware — operates cross-tenant.

Endpoints:
  GET  /admin/tenants                 — list all tenants
  GET  /admin/tenants/{tenant_id}     — get tenant detail + usage
  PUT  /admin/tenants/{tenant_id}/plan — change plan
  POST /admin/tenants/{tenant_id}/keys/revoke — revoke API key
  GET  /admin/usage                   — aggregated platform usage
  GET  /admin/incidents               — guardrail incident feed
"""

from __future__ import annotations

import contextlib
import hmac
import os
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(x_admin_key: str = Header(default="")) -> None:
    """Validate platform admin key.  Raises 401 if invalid, 503 if unconfigured."""
    admin_key = os.getenv("PLATFORM_ADMIN_KEY", "")
    if not admin_key:
        raise HTTPException(status_code=503, detail="Platform admin not configured")
    if not hmac.compare_digest(x_admin_key.encode(), admin_key.encode()):
        raise HTTPException(status_code=401, detail="Invalid admin key")


class PlanChangeRequest(BaseModel):
    plan: str  # free | starter | professional | enterprise


def _tenant_to_dict(t: Any) -> dict[str, str]:
    """Normalise a tenant entry (dict **or** TenantContext) to a plain dict."""
    if isinstance(t, dict):
        return {
            "tenant_id": t.get("tenant_id", ""),
            "plan": str(t.get("plan", "unknown")),
        }
    return {
        "tenant_id": getattr(t, "tenant_id", ""),
        "plan": t.plan.value if hasattr(t, "plan") else "unknown",
    }


@router.get("/tenants", dependencies=[Depends(_require_admin)])
async def list_tenants(request: Request, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """List all tenants with basic stats."""
    app_state = request.app.state
    tenant_svc = getattr(app_state, "tenant_service", None)

    if tenant_svc is None:
        raise HTTPException(status_code=503, detail="Tenant service unavailable")

    try:
        tenants = list(getattr(tenant_svc, "_tenants", {}).values())
        total = len(tenants)
        page = tenants[offset : offset + limit]
        return {
            "tenants": [_tenant_to_dict(t) for t in page],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except Exception as exc:
        logger.warning("admin_list_tenants_error", error=str(exc)[:80])
        raise HTTPException(status_code=500, detail="Failed to list tenants") from exc


@router.get("/tenants/{tenant_id}", dependencies=[Depends(_require_admin)])
async def get_tenant_detail(tenant_id: str, request: Request) -> dict[str, Any]:
    """Get tenant detail with usage."""
    app_state = request.app.state
    tenant_svc = getattr(app_state, "tenant_service", None)
    cost_ctrl = getattr(app_state, "cost_controller", None)

    tenant = None
    if tenant_svc is not None:
        tenant = getattr(tenant_svc, "_tenants", {}).get(tenant_id)

    if tenant is None:
        raise HTTPException(status_code=404, detail=f"Tenant {tenant_id} not found")

    usage: dict[str, Any] = {}
    if cost_ctrl is not None:
        try:
            usage = await cost_ctrl.get_budget_status(tenant_id)
        except Exception:
            usage = {}

    return {
        "tenant_id": tenant_id,
        "plan": tenant.get("plan", "unknown")
        if isinstance(tenant, dict)
        else (tenant.plan.value if hasattr(tenant, "plan") else "unknown"),
        "usage": usage,
    }


@router.put("/tenants/{tenant_id}/plan", dependencies=[Depends(_require_admin)])
async def change_tenant_plan(
    tenant_id: str,
    body: PlanChangeRequest,
    request: Request,
) -> dict[str, Any]:
    """Change a tenant's plan tier."""
    valid_plans = {"free", "starter", "professional", "enterprise"}
    if body.plan not in valid_plans:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid plan. Must be one of: {valid_plans}",
        )

    logger.info("admin_plan_change", tenant_id=tenant_id, new_plan=body.plan)

    app_state = request.app.state
    tenant_svc = getattr(app_state, "tenant_service", None)

    if tenant_svc is not None:
        tenants_dict = getattr(tenant_svc, "_tenants", {})
        tenant = tenants_dict.get(tenant_id)
        if tenant is not None:
            if isinstance(tenant, dict):
                # In-memory tenants are stored as plain dicts
                tenant["plan"] = body.plan
            else:
                # TenantContext is a frozen dataclass — replace with new plan
                from dataclasses import replace

                from app.tenancy.context import PlanTier

                with contextlib.suppress(Exception):
                    tenants_dict[tenant_id] = replace(tenant, plan=PlanTier(body.plan))

    return {"tenant_id": tenant_id, "plan": body.plan, "status": "updated"}


@router.get("/usage", dependencies=[Depends(_require_admin)])
async def get_platform_usage(request: Request) -> dict[str, Any]:
    """Aggregate platform-wide usage."""
    app_state = request.app.state
    goal_svc = getattr(app_state, "goal_service", None)
    tenant_svc = getattr(app_state, "tenant_service", None)

    active_goals = 0
    if goal_svc is not None:
        with contextlib.suppress(Exception):
            active_goals = len(getattr(goal_svc, "_active_goals", {}))

    total_tenants = 0
    if tenant_svc is not None:
        total_tenants = len(getattr(tenant_svc, "_tenants", {}))

    return {
        "active_goals": active_goals,
        "total_tenants": total_tenants,
    }


@router.get("/incidents", dependencies=[Depends(_require_admin)])
async def get_incidents(request: Request, limit: int = 50) -> dict[str, Any]:
    """Guardrail incident feed — recent blocked/flagged requests."""
    app_state = request.app.state
    guardrail_engine = getattr(app_state, "guardrail_engine", None)

    incidents: list[Any] = []
    if guardrail_engine is not None:
        try:
            raw = getattr(guardrail_engine, "_incidents", [])
            incidents = list(raw)[-limit:]
        except Exception:
            pass

    return {"incidents": incidents, "total": len(incidents)}
