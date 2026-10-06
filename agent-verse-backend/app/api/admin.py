"""Platform administration API.

Requires a platform admin (app.tenancy.platform_admin): a tenant admin of an
operator tenant (PLATFORM_ADMIN_TENANT_IDS), or a caller presenting the
platform admin key as X-Admin-Key. Operates cross-tenant.

Endpoints:
  GET  /admin/tenants                 — list all tenants
  GET  /admin/tenants/{tenant_id}     — get tenant detail + usage
  PUT  /admin/tenants/{tenant_id}/plan — change plan
  POST /admin/tenants/{tenant_id}/keys/revoke — revoke API key
  GET  /admin/usage                   — aggregated platform usage (Postgres)
  GET  /admin/incidents               — 501: guardrail incidents are not persisted
  GET  /admin/channel-mappings/review — sms/email claims awaiting operator approval
  POST /admin/channel-mappings/{id}/approve|reject — operator decision (audited)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Select, extract, func, select

from app.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(request: Request) -> None:
    """Allow a platform admin: a tenant admin of an operator tenant, or a caller
    presenting the matching ``X-Admin-Key`` (see :mod:`app.tenancy.platform_admin`).

    It used to accept only the admin key and answer 401 otherwise; the web
    client sends no admin header and logs out on a 401, so merely opening /admin
    signed the admin out. Refusals are now 403 (a wrong or missing key), and 503
    only when a key is presented but ``PLATFORM_ADMIN_KEY`` is unset. The key is
    compared with ``hmac.compare_digest`` (constant time) in the shared helper.
    """
    from app.tenancy.platform_admin import require_platform_admin

    require_platform_admin(request)


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
async def list_tenants(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
) -> dict[str, Any]:
    """List all tenants with basic stats. Optional ``search`` filters by name/email/id
    server-side (so it covers all tenants, not just the current page)."""
    app_state = request.app.state
    tenant_svc = getattr(app_state, "tenant_service", None)

    if tenant_svc is None:
        raise HTTPException(status_code=503, detail="Tenant service unavailable")

    try:
        tenants = list(getattr(tenant_svc, "_tenants", {}).values())
        if search:
            needle = search.lower()
            tenants = [
                t
                for t in tenants
                if needle in str(t.get("name", "")).lower()
                or needle in str(t.get("email", "")).lower()
                or needle in str(t.get("tenant_id", "")).lower()
            ]
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

    # Durable change through TenantService.update_plan (tenants.plan_tier +
    # cache invalidation). This used to edit only this replica's in-memory dict
    # and answer "updated": the DB, every other replica and every cached key
    # context kept the old plan.
    tenant_svc = getattr(request.app.state, "tenant_service", None)
    if tenant_svc is None or not hasattr(tenant_svc, "update_plan"):
        raise HTTPException(status_code=503, detail="Tenant service unavailable")
    try:
        await tenant_svc.update_plan(tenant_id, body.plan)
    except HTTPException:
        raise
    except Exception as exc:
        from app.core.errors import NotFoundError

        if isinstance(exc, NotFoundError):
            raise HTTPException(status_code=404, detail="Tenant not found") from exc
        logger.error("admin_plan_change_failed", tenant_id=tenant_id, error=str(exc))
        raise HTTPException(status_code=503, detail="Plan change could not be saved") from exc
    return {"tenant_id": tenant_id, "plan": body.plan, "status": "updated"}


# ── Platform usage (computed from Postgres on the maintenance session) ────────
# Old bug: this read ``GoalService._active_goals`` — an attribute that does not
# exist — so ``active_goals`` was always 0, and the tenant count was whatever one
# replica happened to hold in memory. Both now come from the goals / tenants
# tables. The query is cross-tenant, so it runs on the maintenance (BYPASSRLS)
# session factory with ``row_security = off``; under FORCE RLS a tenant session
# would count zero rows.

_ACTIVE_STATUSES = ("planning", "executing", "verifying", "waiting_human")
_SUCCESS_STATUSES = ("complete", "completed")


def build_usage_summary_stmt(day_start: datetime) -> Select[Any]:
    from app.db.models.goal import Goal

    g = Goal.__table__
    today = g.c.created_at >= day_start
    done_today = today & g.c.status.in_(_SUCCESS_STATUSES) & g.c.completed_at.is_not(None)
    return select(
        func.count().label("total"),
        func.count().filter(g.c.status.in_(_ACTIVE_STATUSES)).label("active"),
        func.count().filter(today).label("goals_today"),
        func.count().filter(done_today).label("completed_today"),
        func.avg(extract("epoch", g.c.completed_at - g.c.created_at))
        .filter(done_today)
        .label("avg_latency_s_today"),
    )


def build_goals_by_status_stmt() -> Select[Any]:
    from app.db.models.goal import Goal

    g = Goal.__table__
    return select(g.c.status.label("status"), func.count().label("n")).group_by(g.c.status)


def build_tenant_count_stmt() -> Select[Any]:
    from app.db.models.tenant import Tenant

    return select(func.count().label("total_tenants")).select_from(Tenant.__table__)


@router.get("/usage", dependencies=[Depends(_require_admin)])
async def get_platform_usage(request: Request) -> dict[str, Any]:
    """Aggregate platform-wide usage from the goals and tenants tables.

    No database configured -> 501; database error -> 503 (never zeros).
    ``avg_latency_ms`` is the mean wall-clock time of goals completed today
    (UTC), ``null`` when none has.
    """
    system_db = getattr(request.app.state, "system_db_session_factory", None)
    if system_db is None:
        raise HTTPException(
            status_code=501,
            detail="Platform usage is computed in Postgres and needs a database",
        )
    from app.db.rls import system_session

    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        async with system_db() as session, session.begin(), system_session(session):
            summary = (await session.execute(build_usage_summary_stmt(day_start))).mappings().one()
            by_status = (await session.execute(build_goals_by_status_stmt())).mappings().all()
            tenants = (await session.execute(build_tenant_count_stmt())).mappings().one()
    except Exception as exc:
        logger.warning("admin_usage_query_failed", error=str(exc)[:200])
        raise HTTPException(
            status_code=503, detail="Platform usage unavailable: database query failed"
        ) from exc

    latency_s = summary["avg_latency_s_today"]
    return {
        "active_goals": int(summary["active"] or 0),
        "total_tenants": int(tenants["total_tenants"] or 0),
        "total_goals": int(summary["total"] or 0),
        "goals_today": int(summary["goals_today"] or 0),
        "completed_today": int(summary["completed_today"] or 0),
        "avg_latency_ms": None if latency_s is None else round(float(latency_s) * 1000),
        "goals_by_status": {str(r["status"]): int(r["n"] or 0) for r in by_status},
        "source": "postgres",
    }


@router.get("/incidents", dependencies=[Depends(_require_admin)])
async def get_incidents(request: Request, limit: int = 50) -> dict[str, Any]:
    """Guardrail incident feed — not implemented (honest 501).

    This read ``guardrail_engine._incidents``, which no engine defines, so it was
    always an empty feed that looked like "no incidents". Guardrail blocks are
    not persisted anywhere (the ``guardrail_violations`` table from migration
    0055 has no writer), so there is no source to serve a feed from.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "Guardrail incidents are not persisted, so there is no incident feed to "
            "serve. Per-goal guardrail blocks appear in each goal's event stream."
        ),
    )


# ── Channel-mapping operator review (TRG-03 follow-up) ────────────────────────
# A one-time code received on an SMS number / email address only proves someone
# can SEND there, so tenants cannot self-verify those mappings: the claim waits
# here for a platform operator. Legacy (pre-TRG-03) send-only mappings keep
# routing and are listed for review too. Every decision is audited on the
# mapping tenant's trail in the same transaction.


class ChannelMappingDecision(BaseModel):
    operator: str = ""  # who decided (recorded; the admin key itself is shared)
    reason: str = ""


def _mapping_review_db(request: Request) -> Any:
    db = getattr(request.app.state, "system_db_session_factory", None)
    if db is None:
        raise HTTPException(status_code=503, detail="Channel mapping review needs the database")
    return db


@router.get("/channel-mappings/review", dependencies=[Depends(_require_admin)])
async def list_channel_mapping_review(request: Request) -> dict[str, Any]:
    """Claims awaiting operator approval + legacy send-only mappings (cross-tenant)."""
    from app.api.channels import verification

    db = _mapping_review_db(request)
    try:
        mappings = await verification.list_operator_review(db)
    except Exception as exc:
        logger.error("admin_channel_review_failed", error=str(exc)[:200])
        raise HTTPException(status_code=503, detail="Channel mapping review unavailable") from exc
    return {"mappings": mappings, "total": len(mappings)}


async def _decide_channel_mapping(
    request: Request, mapping_id: str, body: ChannelMappingDecision | None, *, approve: bool
) -> dict[str, Any]:
    from app.api.channels import verification

    db = _mapping_review_db(request)
    decision = body or ChannelMappingDecision()
    try:
        issued = await verification.operator_decide(
            db, mapping_id, approve=approve, operator=decision.operator, reason=decision.reason
        )
    except verification.MappingNotFoundError:
        raise HTTPException(status_code=404, detail="Channel mapping not found") from None
    except verification.NotReviewableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except verification.ChannelClaimedError:
        raise HTTPException(
            status_code=409, detail="Channel is already verified by another tenant"
        ) from None
    except Exception as exc:
        logger.error("admin_channel_decision_failed", mapping_id=mapping_id, error=str(exc)[:200])
        raise HTTPException(status_code=503, detail="Decision could not be saved") from exc
    return issued.to_response()


@router.post("/channel-mappings/{mapping_id}/approve", dependencies=[Depends(_require_admin)])
async def approve_channel_mapping(
    mapping_id: str, request: Request, body: ChannelMappingDecision | None = None
) -> dict[str, Any]:
    """Approve a claim (or a legacy send-only mapping): it becomes ``verified``."""
    return await _decide_channel_mapping(request, mapping_id, body, approve=True)


@router.post("/channel-mappings/{mapping_id}/reject", dependencies=[Depends(_require_admin)])
async def reject_channel_mapping(
    mapping_id: str, request: Request, body: ChannelMappingDecision | None = None
) -> dict[str, Any]:
    """Reject a claim (or a legacy send-only mapping): it becomes ``rejected``, no routing."""
    return await _decide_channel_mapping(request, mapping_id, body, approve=False)
