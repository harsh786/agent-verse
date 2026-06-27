"""Governance API — policies, HITL approvals, audit log, and cost budgets."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.governance.audit import AuditLog
from app.governance.cost import BudgetConfig, CostController
from app.governance.hitl import HITLGateway
from app.governance.policies import Policy, PolicyEngine
from app.tenancy.context import TenantContext
from app.tenancy.rbac import require_role

router = APIRouter(prefix="/governance", tags=["governance"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreatePolicyRequest(BaseModel):
    name: str
    description: str = ""
    tools_pattern: str
    action: str = "deny"  # "deny" or "require_approval"
    priority: int = 0
    allowed_hours_utc: list[int] | None = None  # [start_hour, end_hour]
    allowed_weekdays: list[int] | None = None


class ApproveRejectRequest(BaseModel):
    approver: str
    note: str = ""


class SetBudgetRequest(BaseModel):
    per_goal_usd: float = 10.0
    per_tenant_daily_usd: float = 500.0


class CreateNotificationChannelRequest(BaseModel):
    channel_type: str = "webhook"  # slack | webhook | teams
    config: dict[str, Any] = {}


class PolicySimulateRequest(BaseModel):
    tool_calls: list[str] = []


# ---------------------------------------------------------------------------
# Helpers — lazy app.state init keeps tests isolated per FastAPI instance
# ---------------------------------------------------------------------------

def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _hitl(request: Request) -> HITLGateway:
    return request.app.state.hitl_gateway  # type: ignore[no-any-return]


def _audit(request: Request) -> AuditLog:
    return request.app.state.audit_log  # type: ignore[no-any-return]


def _cost(request: Request) -> CostController:
    return request.app.state.cost_controller  # type: ignore[no-any-return]


def _policy_engine(request: Request) -> PolicyEngine:
    return request.app.state.policy_engine  # type: ignore[no-any-return]


def _policy_registry(request: Request) -> dict[str, dict[str, Any]]:
    """Per-tenant dict of {policy_id: policy_record} stored on app.state."""
    if not hasattr(request.app.state, "_policy_registry"):
        request.app.state._policy_registry = {}
    return request.app.state._policy_registry  # type: ignore[no-any-return]


def _budget_config(request: Request) -> dict[str, BudgetConfig]:
    """Per-tenant budget config dict stored on app.state."""
    if not hasattr(request.app.state, "_budget_config"):
        request.app.state._budget_config = {}
    return request.app.state._budget_config  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# DB-backed policy helpers (fall back gracefully if DB is unavailable)
# ---------------------------------------------------------------------------

async def _db_list_policies(request: Request, tenant_id: str) -> list[dict[str, Any]]:
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return []
    try:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context
        async with db() as session, sqlalchemy_rls_context(session, tenant_id):
            result = await session.execute(
                text(
                    "SELECT id, name, tools_pattern, action, priority, description "
                    "FROM governance_policies WHERE tenant_id = :tid ORDER BY priority DESC"
                ),
                {"tid": tenant_id},
            )
            rows = result.fetchall()
        return [
            {
                "policy_id": r[0],
                "name": r[1],
                "tools_pattern": r[2],
                "action": r[3],
                "priority": r[4],
                "description": r[5] or "",
            }
            for r in rows
        ]
    except Exception:
        return []


async def _db_create_policy(
    request: Request, tenant_id: str, record: dict[str, Any]
) -> None:
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return
    try:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context
        async with db() as session, session.begin(), \
                   sqlalchemy_rls_context(session, tenant_id):
            await session.execute(
                text(
                    """INSERT INTO governance_policies
                        (id, tenant_id, name, tools_pattern, action, priority, description)
                        VALUES (:id, :tid, :name, :pattern, :action, :priority, :desc)
                        ON CONFLICT (id) DO NOTHING"""
                ),
                {
                    "id": record["policy_id"],
                    "tid": tenant_id,
                    "name": record["name"],
                    "pattern": record["tools_pattern"],
                    "action": record["action"],
                    "priority": record.get("priority", 0),
                    "desc": record.get("description", ""),
                },
            )
    except Exception:
        pass


async def _db_delete_policy(
    request: Request, tenant_id: str, policy_id: str
) -> None:
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return
    try:
        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context
        async with db() as session, session.begin(), \
                   sqlalchemy_rls_context(session, tenant_id):
            await session.execute(
                text(
                    "DELETE FROM governance_policies WHERE id = :id AND tenant_id = :tid"
                ),
                {"id": policy_id, "tid": tenant_id},
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Endpoints — policies
# ---------------------------------------------------------------------------

@router.get("/policies")
async def list_policies(request: Request) -> list[dict[str, Any]]:
    tenant_ctx: TenantContext = _require_tenant(request)
    # Try DB-backed first
    db_policies = await _db_list_policies(request, tenant_ctx.tenant_id)
    if db_policies:
        return db_policies
    # Fall back to in-memory (no DB available)
    registry = _policy_registry(request)
    return list(registry.get(tenant_ctx.tenant_id, {}).values())


@router.post("/policies", status_code=status.HTTP_201_CREATED)
async def create_policy(
    request: Request, body: CreatePolicyRequest
) -> dict[str, Any]:
    tenant_ctx: TenantContext = _require_tenant(request)
    engine = _policy_engine(request)
    registry = _policy_registry(request)

    policy_id = uuid.uuid4().hex
    denied_tools: list[str] = []
    approval_tools: list[str] = []

    if body.action == "deny":
        denied_tools = [body.tools_pattern]
    elif body.action == "require_approval":
        approval_tools = [body.tools_pattern]

    policy = Policy(
        name=body.name,
        description=body.description,
        denied_tools=denied_tools,
        approval_tools=approval_tools,
        allowed_hours_utc=tuple(body.allowed_hours_utc) if body.allowed_hours_utc and len(body.allowed_hours_utc) == 2 else None,  # type: ignore[arg-type]
        allowed_weekdays=body.allowed_weekdays,
        tenant_id=tenant_ctx.tenant_id,
    )
    engine.add_policy(policy)

    record: dict[str, Any] = {
        "policy_id": policy_id,
        "name": body.name,
        "description": body.description,
        "tools_pattern": body.tools_pattern,
        "action": body.action,
        "priority": body.priority,
        "allowed_hours_utc": body.allowed_hours_utc,
        "allowed_weekdays": body.allowed_weekdays,
    }
    registry.setdefault(tenant_ctx.tenant_id, {})[policy_id] = record
    await _db_create_policy(request, tenant_ctx.tenant_id, record)
    redis = getattr(request.app.state, "_policy_pubsub_redis", None)
    await PolicyEngine.publish_change(redis, tenant_id=tenant_ctx.tenant_id, action="created")
    return record


@router.delete("/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(request: Request, policy_id: str) -> None:
    tenant_ctx: TenantContext = _require_tenant(request)
    engine = _policy_engine(request)
    registry = _policy_registry(request)

    tenant_policies = registry.get(tenant_ctx.tenant_id, {})
    record = tenant_policies.get(policy_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy {policy_id} not found",
        )

    # Remove from the PolicyEngine's internal list, scoped to THIS tenant only.
    # Matching only on name (without tenant check) would delete identically-named
    # policies belonging to other tenants — the critical isolation bug.
    engine._policies = [  # type: ignore[attr-defined]
        p for p in engine._policies  # type: ignore[attr-defined]
        if not (
            p.name == record["name"]
            and getattr(p, "tenant_id", "") == tenant_ctx.tenant_id
        )
    ]
    del tenant_policies[policy_id]
    await _db_delete_policy(request, tenant_ctx.tenant_id, policy_id)
    redis = getattr(request.app.state, "_policy_pubsub_redis", None)
    await PolicyEngine.publish_change(redis, tenant_id=tenant_ctx.tenant_id, action="deleted")


@router.post("/policies/simulate")
async def simulate_policies(
    request: Request, body: PolicySimulateRequest
) -> dict[str, Any]:
    """Dry-run policy evaluation without executing anything."""
    tenant = _require_tenant(request)
    engine = _policy_engine(request)
    results = {}
    for tool_name in body.tool_calls:
        try:
            action = engine.evaluate(tool_name=tool_name, tenant_ctx=tenant)
            results[tool_name] = action.value if hasattr(action, "value") else str(action)
        except Exception as exc:
            results[tool_name] = f"error: {exc}"
    return {"simulation_results": results, "tenant_id": tenant.tenant_id}


# ---------------------------------------------------------------------------
# Endpoints — HITL approvals
# ---------------------------------------------------------------------------

@router.get("/approvals")
async def list_approvals(request: Request) -> list[dict[str, Any]]:
    tenant_ctx: TenantContext = _require_tenant(request)
    gateway = _hitl(request)
    pending = gateway.list_pending(tenant_ctx=tenant_ctx)
    return [
        {
            "request_id": r.request_id,
            "goal_id": r.goal_id,
            "action": r.action,
            "risk_level": r.risk_level,
            "status": r.status,
        }
        for r in pending
    ]


@router.post("/approvals/{request_id}/approve")
async def approve_request(
    request: Request,
    request_id: str,
    body: ApproveRejectRequest,
    _rbac: None = Depends(require_role("approver")),
) -> dict[str, Any]:
    tenant_ctx: TenantContext = _require_tenant(request)
    gateway = _hitl(request)
    ok = gateway.approve(
        request_id, approver=body.approver, note=body.note, tenant_ctx=tenant_ctx
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval request {request_id} not found",
        )
    return {"request_id": request_id, "status": "approved", "approver": body.approver}


@router.post("/approvals/{request_id}/reject")
async def reject_request(
    request: Request,
    request_id: str,
    body: ApproveRejectRequest,
    _rbac: None = Depends(require_role("approver")),
) -> dict[str, Any]:
    tenant_ctx: TenantContext = _require_tenant(request)
    gateway = _hitl(request)
    ok = await gateway.reject(
        request_id, approver=body.approver, note=body.note, tenant_ctx=tenant_ctx
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval request {request_id} not found",
        )
    return {"request_id": request_id, "status": "rejected", "approver": body.approver}


# ---------------------------------------------------------------------------
# Endpoints — audit log
# ---------------------------------------------------------------------------

@router.get("/audit")
async def query_audit(
    request: Request,
    goal_id: str | None = None,
    tool_name: str | None = None,
    limit: int = 100,
    offset: int = 0,
    start_time: str | None = None,
    end_time: str | None = None,
) -> list[dict[str, Any]]:
    tenant_ctx: TenantContext = _require_tenant(request)
    log = _audit(request)

    # Use direct DB query for accuracy + pagination support
    events = await log.query_db(
        tenant_ctx=tenant_ctx,
        goal_id=goal_id,
        tool_name=tool_name,
        limit=limit,
        offset=offset,
        start_time=start_time,
        end_time=end_time,
    )

    return [
        {
            "event_id": e.event_id,
            "goal_id": e.goal_id,
            "tool_name": e.tool_name,
            "action_level": (
                e.action_level.value
                if hasattr(e.action_level, "value")
                else e.action_level
            ),
            "outcome": e.outcome,
            "step_id": e.step_id,
            "approver": e.approver,
            "note": e.note,
        }
        for e in events
    ]


# ---------------------------------------------------------------------------
# Endpoints — cost budget
# ---------------------------------------------------------------------------

@router.get("/budget")
async def get_budget(request: Request) -> dict[str, Any]:
    tenant_ctx: TenantContext = _require_tenant(request)
    configs = _budget_config(request)
    cfg = configs.get(tenant_ctx.tenant_id, BudgetConfig())
    return {
        "tenant_id": tenant_ctx.tenant_id,
        "per_goal_usd": cfg.per_goal_usd,
        "per_tenant_daily_usd": cfg.per_tenant_daily_usd,
    }


@router.put("/budget")
async def set_budget(
    request: Request,
    body: SetBudgetRequest,
    _rbac: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    tenant_ctx: TenantContext = _require_tenant(request)
    configs = _budget_config(request)
    cfg = BudgetConfig(
        per_goal_usd=body.per_goal_usd,
        per_tenant_daily_usd=body.per_tenant_daily_usd,
    )
    configs[tenant_ctx.tenant_id] = cfg
    return {
        "tenant_id": tenant_ctx.tenant_id,
        "per_goal_usd": cfg.per_goal_usd,
        "per_tenant_daily_usd": cfg.per_tenant_daily_usd,
    }


# ---------------------------------------------------------------------------
# Endpoints — notification channels
# ---------------------------------------------------------------------------

@router.post("/notifications", status_code=201)
async def create_notification_channel(
    request: Request, body: CreateNotificationChannelRequest
) -> dict[str, Any]:
    tenant = _require_tenant(request)
    svc = getattr(request.app.state, "notification_service", None)
    if svc is None:
        raise HTTPException(503, "Notification service not configured")
    from app.services.notification_service import NotificationChannel
    channel = NotificationChannel(
        channel_id=uuid.uuid4().hex,
        tenant_id=tenant.tenant_id,
        channel_type=body.channel_type,
        config=body.config,
    )
    svc.add_channel(channel)
    return {"channel_id": channel.channel_id, "type": channel.channel_type, "status": "created"}


@router.get("/notifications")
async def list_notification_channels(request: Request) -> list[dict[str, Any]]:
    tenant = _require_tenant(request)
    svc = getattr(request.app.state, "notification_service", None)
    if svc is None:
        return []
    return [
        {"channel_id": c.channel_id, "type": c.channel_type, "enabled": c.enabled}
        for c in svc.get_channels(tenant.tenant_id)
    ]


# ---------------------------------------------------------------------------
# Request models — legal hold
# ---------------------------------------------------------------------------

class LegalHoldRequest(BaseModel):
    reason: str
    expires_at: str | None = None  # ISO datetime


# ---------------------------------------------------------------------------
# Helpers — DB session factory
# ---------------------------------------------------------------------------

def _get_db(request: Request) -> Any:
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        try:
            from app.db.session import get_session_factory
            db = get_session_factory()
        except Exception:
            pass
    return db


# ---------------------------------------------------------------------------
# Endpoints — emergency stop
# ---------------------------------------------------------------------------

@router.post("/emergency-stop")
async def emergency_stop(request: Request) -> dict:
    """Immediately cancel all running and queued goals for this tenant.

    Use for: security incidents, runaway agents, cost overruns.
    This is irreversible — cancelled goals must be resubmitted.
    """
    ctx = _require_tenant(request)

    # 1. Cancel all running in-memory goals via GoalService
    goal_service = getattr(request.app.state, "goal_service", None)
    cancelled_goals = []
    if goal_service is not None:
        try:
            # Get all running goals for this tenant
            running = [
                gid for gid, record in goal_service._goals.items()
                if getattr(record, "tenant_id", "") == ctx.tenant_id
                and str(getattr(record, "status", "")).lower() not in (
                    "complete", "completed", "failed", "cancelled"
                )
            ]
            for goal_id in running:
                try:
                    await goal_service.cancel_goal(goal_id=goal_id, tenant_ctx=ctx)
                    cancelled_goals.append(goal_id)
                except Exception:
                    pass
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("emergency_stop_cancel_failed: %s", exc)

    # 2. Publish emergency stop signal to Redis so Celery workers abort
    redis = getattr(request.app.state, "_policy_pubsub_redis", None)
    if redis is not None:
        try:
            import json
            from datetime import UTC, datetime
            await redis.publish(
                "emergency_stop",
                json.dumps({"tenant_id": ctx.tenant_id, "ts": datetime.now(UTC).isoformat()})
            )
            # Also set a flag that Celery workers can poll
            await redis.set(
                f"emergency_stop:{ctx.tenant_id}",
                "1",
                ex=300  # 5 minute window
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("emergency_stop_redis_failed: %s", exc)

    # 3. Reject all pending HITL approvals
    hitl = getattr(request.app.state, "hitl_gateway", None)
    rejected_approvals = []
    if hitl is not None:
        try:
            pending = hitl.list_pending(tenant_ctx=ctx)
            for approval in pending:
                try:
                    await hitl.reject(
                        approval.request_id,
                        tenant_ctx=ctx,
                        note="Emergency stop activated by operator",
                    )
                    rejected_approvals.append(approval.request_id)
                except Exception:
                    pass
        except Exception:
            pass

    # 4. Log to audit trail
    audit_log = getattr(request.app.state, "audit_log", None)
    if audit_log is not None:
        try:
            from app.governance.audit import AuditEvent
            from app.governance.permissions import ActionLevel
            from app.tenancy.context import PlanTier, TenantContext as _TC
            _audit_ctx = _TC(
                tenant_id=ctx.tenant_id,
                plan=PlanTier.FREE,
                api_key_id=getattr(ctx, "api_key_id", ""),
            )
            audit_log.record(AuditEvent(
                goal_id="emergency_stop",
                tool_name="emergency_stop",
                action_level=ActionLevel.DENY,
                outcome="stop_activated",
                api_key_id=getattr(ctx, "api_key_id", ""),
                note=(
                    f"cancelled_goals={len(cancelled_goals)},"
                    f"rejected_approvals={len(rejected_approvals)}"
                ),
            ), tenant_ctx=_audit_ctx)
        except Exception:
            pass

    return {
        "status": "emergency_stop_activated",
        "tenant_id": ctx.tenant_id,
        "cancelled_goals": len(cancelled_goals),
        "cancelled_goal_ids": cancelled_goals[:20],
        "rejected_approvals": len(rejected_approvals),
        "celery_signal_sent": redis is not None,
        "message": (
            "All running goals cancelled. "
            "Celery workers will abort in-progress tasks."
        ),
    }


@router.delete("/emergency-stop")
async def clear_emergency_stop(request: Request) -> dict:
    """Clear the emergency stop signal to allow new goals to be submitted."""
    ctx = _require_tenant(request)
    redis = getattr(request.app.state, "_policy_pubsub_redis", None)
    if redis is not None:
        try:
            await redis.delete(f"emergency_stop:{ctx.tenant_id}")
        except Exception:
            pass
    return {"status": "cleared", "tenant_id": ctx.tenant_id}


# ---------------------------------------------------------------------------
# Endpoints — legal hold
# ---------------------------------------------------------------------------

@router.post("/legal-hold")
async def create_legal_hold(request: Request, body: LegalHoldRequest) -> dict:
    """Place a legal hold on tenant data to prevent retention deletion."""
    ctx = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        raise HTTPException(503, "Database not available")
    import uuid
    from sqlalchemy import text
    async with db() as session, session.begin():
        await session.execute(text("""
            INSERT INTO legal_holds (id, tenant_id, reason, expires_at, created_by)
            VALUES (:id, :tid, :reason, :exp, :by)
        """), {
            "id": uuid.uuid4().hex,
            "tid": ctx.tenant_id,
            "reason": body.reason,
            "exp": body.expires_at,
            "by": getattr(ctx, "api_key_id", "unknown"),
        })
    return {
        "status": "legal_hold_placed",
        "tenant_id": ctx.tenant_id,
        "reason": body.reason,
    }
