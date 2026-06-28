"""Governance API — policies, HITL approvals, audit log, and cost budgets."""

from __future__ import annotations

import asyncio  # noqa: F401  (used in SSE generators)
import json as _json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from starlette.responses import StreamingResponse

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


class PolicyGoalSimulateRequest(BaseModel):
    goal: str
    agent_id: str | None = None
    dry_run: bool = True


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


@router.post("/simulate")
async def simulate_policy_for_goal(
    request: Request, body: PolicyGoalSimulateRequest
) -> dict[str, Any]:
    """Simulate what governance policies would fire for a given goal + agent."""
    ctx = _require_tenant(request)
    policy_engine = getattr(request.app.state, "policy_engine", None)
    agent_store = getattr(request.app.state, "agent_store", None)

    # Get agent's connector tools
    tools_to_check: list[str] = []
    if body.agent_id and agent_store is not None:
        try:
            agent = await agent_store.get_async(body.agent_id, tenant_ctx=ctx)
            if agent:
                connector_ids = agent.get("connector_ids", [])
                tools_to_check = (
                    [f"{cid}.read" for cid in connector_ids]
                    + [f"{cid}.write" for cid in connector_ids]
                    + [f"{cid}.delete" for cid in connector_ids]
                )
        except Exception:
            pass

    if not tools_to_check:
        # Default to common high-risk tools
        tools_to_check = [
            "jira.delete", "github.deploy", "stripe.refund",
            "jira.search", "github.read", "slack.message",
        ]

    simulation_result: dict[str, Any] = {
        "goal": body.goal,
        "policy_checks": [],
        "summary": {},
    }

    if policy_engine is not None:
        allowed: list[str] = []
        denied: list[str] = []
        requires_approval: list[str] = []

        for tool in tools_to_check:
            result = policy_engine.evaluate(tool, tenant_ctx=ctx)
            status = result.value if hasattr(result, "value") else str(result)
            check = {"tool": tool, "result": status}
            simulation_result["policy_checks"].append(check)
            if "deny" in status.lower():
                denied.append(tool)
            elif "approval" in status.lower():
                requires_approval.append(tool)
            else:
                allowed.append(tool)

        simulation_result["summary"] = {
            "allowed_tools": allowed,
            "denied_tools": denied,
            "requires_approval": requires_approval,
            "would_block_execution": len(denied) > 0,
            "hitl_approvals_needed": len(requires_approval),
        }
    else:
        # No policy engine — everything is allowed by default
        simulation_result["policy_checks"] = [
            {"tool": t, "result": "allow"} for t in tools_to_check
        ]
        simulation_result["summary"] = {
            "allowed_tools": tools_to_check,
            "denied_tools": [],
            "requires_approval": [],
            "would_block_execution": False,
            "hitl_approvals_needed": 0,
        }

    return simulation_result


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
# Endpoints — real-time SSE streams (additive; wrap existing Redis pub/sub)
# ---------------------------------------------------------------------------

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

# Event types relevant to the approvals UI (forwarded from platform_events).
_APPROVAL_EVENT_TYPES = {
    "waiting_approval",
    "approval_granted",
    "goal_complete",
    "goal_failed",
}


def _pending_snapshot(gateway: HITLGateway, tenant_ctx: TenantContext) -> dict[str, Any]:
    pending = gateway.list_pending(tenant_ctx=tenant_ctx)
    return {
        "type": "approvals_snapshot",
        "pending": [
            {
                "request_id": r.request_id,
                "goal_id": r.goal_id,
                "action": r.action,
                "risk_level": r.risk_level,
                "status": r.status,
            }
            for r in pending
        ],
    }


async def _tail_redis_channel(
    redis: Any, channel: str, allowed_types: set[str] | None
) -> AsyncGenerator[str, None]:
    """Yield SSE frames from a Redis pub/sub channel. Closes cleanly on cancel."""
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel)
    try:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            raw = message.get("data")
            try:
                event = _json.loads(raw) if isinstance(raw, (str, bytes)) else raw
            except Exception:
                continue
            if not isinstance(event, dict):
                continue
            if allowed_types is not None and event.get("type") not in allowed_types:
                continue
            yield f"data: {_json.dumps(event)}\n\n"
    finally:
        with __import__("contextlib").suppress(Exception):
            await pubsub.unsubscribe(channel)
        with __import__("contextlib").suppress(Exception):
            await pubsub.close()


@router.get("/approvals/stream")
async def stream_approvals(request: Request) -> StreamingResponse:
    """SSE stream of HITL approval activity.

    Emits an ``approvals_snapshot`` event with current pending requests, then
    tails the ``platform_events:{tenant_id}`` Redis channel and forwards
    approval-relevant events. When Redis is unavailable, emits the snapshot
    then ``stream_unavailable``.
    """
    tenant_ctx: TenantContext = _require_tenant(request)
    gateway = _hitl(request)
    redis = getattr(request.app.state, "_policy_pubsub_redis", None)

    async def gen() -> AsyncGenerator[str, None]:
        yield f"data: {_json.dumps(_pending_snapshot(gateway, tenant_ctx))}\n\n"
        if redis is None:
            yield f'data: {_json.dumps({"type": "stream_unavailable"})}\n\n'
            return
        channel = f"platform_events:{tenant_ctx.tenant_id}"
        async for frame in _tail_redis_channel(redis, channel, _APPROVAL_EVENT_TYPES):
            yield frame

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.get("/policies/stream")
async def stream_policies(request: Request) -> StreamingResponse:
    """SSE stream of policy changes.

    Emits a ``policies_snapshot``, then tails the ``policy_changes`` Redis
    channel (filtered to this tenant). When Redis is unavailable, emits the
    snapshot then ``stream_unavailable``.
    """
    tenant_ctx: TenantContext = _require_tenant(request)
    db_policies = await _db_list_policies(request, tenant_ctx.tenant_id)
    if not db_policies:
        registry = _policy_registry(request)
        db_policies = list(registry.get(tenant_ctx.tenant_id, {}).values())
    redis = getattr(request.app.state, "_policy_pubsub_redis", None)

    async def gen() -> AsyncGenerator[str, None]:
        snapshot: dict[str, Any] = {"type": "policies_snapshot", "policies": db_policies}
        yield f"data: {_json.dumps(snapshot)}\n\n"
        if redis is None:
            yield f'data: {_json.dumps({"type": "stream_unavailable"})}\n\n'
            return
        pubsub = redis.pubsub()
        await pubsub.subscribe("policy_changes")
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                raw = message.get("data")
                try:
                    event = _json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                except Exception:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("tenant_id") != tenant_ctx.tenant_id:
                    continue
                out: dict[str, Any] = {"type": "policy_changed", **event}
                yield f"data: {_json.dumps(out)}\n\n"
        finally:
            with __import__("contextlib").suppress(Exception):
                await pubsub.unsubscribe("policy_changes")
            with __import__("contextlib").suppress(Exception):
                await pubsub.close()

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)

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


@router.delete("/notifications/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification_channel(request: Request, channel_id: str) -> None:
    """Delete a notification channel by ID."""
    tenant = _require_tenant(request)
    svc = getattr(request.app.state, "notification_service", None)
    if svc is None:
        raise HTTPException(404, "Notification channel not found")
    removed = svc.remove_channel(channel_id, tenant.tenant_id)
    if not removed:
        raise HTTPException(404, "Notification channel not found")


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
            from app.tenancy.context import PlanTier
            from app.tenancy.context import TenantContext as _TC
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
# P1.3: Email approval link handlers (signed URLs from approval emails)
# ---------------------------------------------------------------------------

@router.get("/hitl/{request_id}/approve")
async def email_approve_link(
    request: Request, request_id: str, sig: str = ""
) -> dict[str, Any]:
    """Handle one-click approve link from HITL approval email.

    Validates HMAC signature and approves the request on behalf of the email recipient.
    """
    from app.integrations.email.approval_sender import _verify

    if not sig or not _verify(request_id, "approve", sig):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Invalid or expired approval link")

    gateway = getattr(request.app.state, "hitl_gateway", None)
    if gateway is None:
        raise HTTPException(status_code=503, detail="HITL gateway not available")

    # Find the request across all tenants (email links are tenant-scoped via the signature)
    matching_req = None
    for (tid, rid), req in gateway._requests.items():
        if rid == request_id:
            matching_req = (tid, req)
            break

    if matching_req is None:
        raise HTTPException(status_code=404, detail=f"Approval request {request_id} not found")

    tenant_id, _req_obj = matching_req
    from app.tenancy.context import PlanTier, TenantContext
    tenant_svc = getattr(request.app.state, "tenant_service", None)
    actual_plan = PlanTier.FREE  # safe default
    if tenant_svc is not None:
        try:
            real_tenant = await tenant_svc.get_tenant(tenant_id)
            if real_tenant and real_tenant.get("plan"):
                actual_plan = PlanTier(real_tenant["plan"])
        except Exception:
            pass  # fall through to FREE
    fake_ctx = TenantContext(
        tenant_id=tenant_id,
        plan=actual_plan,
        api_key_id="email-link-approver",
    )

    ok = gateway.approve(request_id, approver="email-link", tenant_ctx=fake_ctx)
    if not ok:
        raise HTTPException(status_code=409, detail="Approval request is no longer pending")

    return {
        "request_id": request_id,
        "status": "approved",
        "approver": "email-link",
        "message": "Action approved via email link.",
    }


@router.get("/hitl/{request_id}/reject")
async def email_reject_link(
    request: Request, request_id: str, sig: str = ""
) -> dict[str, Any]:
    """Handle one-click reject link from HITL approval email.

    Validates HMAC signature and rejects the request.
    """
    from app.integrations.email.approval_sender import _verify

    if not sig or not _verify(request_id, "reject", sig):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Invalid or expired rejection link")

    gateway = getattr(request.app.state, "hitl_gateway", None)
    if gateway is None:
        raise HTTPException(status_code=503, detail="HITL gateway not available")

    # Find the request across all tenants
    matching_req = None
    for (tid, rid), req in gateway._requests.items():
        if rid == request_id:
            matching_req = (tid, req)
            break

    if matching_req is None:
        raise HTTPException(status_code=404, detail=f"Approval request {request_id} not found")

    tenant_id, _req_obj = matching_req
    from app.tenancy.context import PlanTier, TenantContext
    tenant_svc = getattr(request.app.state, "tenant_service", None)
    actual_plan = PlanTier.FREE  # safe default
    if tenant_svc is not None:
        try:
            real_tenant = await tenant_svc.get_tenant(tenant_id)
            if real_tenant and real_tenant.get("plan"):
                actual_plan = PlanTier(real_tenant["plan"])
        except Exception:
            pass  # fall through to FREE
    fake_ctx = TenantContext(
        tenant_id=tenant_id,
        plan=actual_plan,
        api_key_id="email-link-approver",
    )

    ok = await gateway.reject(request_id, approver="email-link", note="Rejected via email link", tenant_ctx=fake_ctx)
    if not ok:
        raise HTTPException(status_code=409, detail="Approval request is no longer pending")

    return {
        "request_id": request_id,
        "status": "rejected",
        "approver": "email-link",
        "message": "Action rejected via email link.",
    }


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


@router.get("/legal-holds")
async def list_legal_holds(request: Request) -> list[dict[str, Any]]:
    """List active legal holds for this tenant (empty when DB unavailable)."""
    ctx = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        return []
    try:
        from sqlalchemy import text
        async with db() as session:
            rows = (await session.execute(text(
                "SELECT id, reason, expires_at, created_by "
                "FROM legal_holds WHERE tenant_id = :tid ORDER BY id"
            ), {"tid": ctx.tenant_id})).fetchall()
        return [
            {
                "id": r[0],
                "reason": r[1],
                "expires_at": r[2].isoformat() if r[2] else None,
                "created_by": r[3],
            }
            for r in rows
        ]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# NEW (governance v2): Batch HITL approval
# ---------------------------------------------------------------------------

class BatchApproveRequest(BaseModel):
    action: str  # "approve" | "reject"
    request_ids: list[str]
    approver: str
    note: str = ""


@router.post("/hitl/batch-approve")
async def batch_approve(
    request: Request,
    body: BatchApproveRequest,
    _rbac: None = Depends(require_role("approver")),
) -> dict[str, Any]:
    """Approve or reject up to 100 HITL requests in a single call."""
    if len(body.request_ids) > 100:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Maximum 100 request IDs per batch",
        )
    tenant_ctx: TenantContext = _require_tenant(request)
    gateway = _hitl(request)

    approved = 0
    rejected_count = 0
    not_found = 0
    results: list[dict[str, Any]] = []

    for req_id in body.request_ids:
        if body.action == "approve":
            ok = gateway.approve(
                req_id, approver=body.approver, note=body.note, tenant_ctx=tenant_ctx
            )
            if ok:
                approved += 1
                # Also publish via Redis BLPOP path if available
                await gateway.publish_resolution(
                    request_id=req_id,
                    action="approve",
                    approver=body.approver,
                    note=body.note,
                )
                results.append({"request_id": req_id, "result": "approved"})
            else:
                not_found += 1
                results.append({"request_id": req_id, "result": "not_found"})
        elif body.action == "reject":
            ok = await gateway.reject(
                req_id, approver=body.approver, note=body.note, tenant_ctx=tenant_ctx
            )
            if ok:
                rejected_count += 1
                await gateway.publish_resolution(
                    request_id=req_id,
                    action="reject",
                    approver=body.approver,
                    note=body.note,
                )
                results.append({"request_id": req_id, "result": "rejected"})
            else:
                not_found += 1
                results.append({"request_id": req_id, "result": "not_found"})
        else:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Unknown action: {body.action!r}",
            )

    return {
        "approved": approved,
        "rejected": rejected_count,
        "not_found": not_found,
        "results": results,
    }


# ---------------------------------------------------------------------------
# NEW (governance v2): Policy version history & rollback
# ---------------------------------------------------------------------------


@router.get("/policies/{policy_id}/versions")
async def get_policy_versions(
    request: Request, policy_id: str
) -> list[dict[str, Any]]:
    """Return the full version history for a policy."""
    _require_tenant(request)
    db = _get_db(request)
    if db is None:
        return []
    try:
        from sqlalchemy import text

        async with db() as session:
            result = await session.execute(
                text(
                    """
                    SELECT id, version_number, name, description, is_active,
                           change_summary, changed_by, changed_at, deleted_at
                    FROM policy_versions
                    WHERE policy_id = :pid
                    ORDER BY version_number ASC
                    """
                ),
                {"pid": policy_id},
            )
            rows = result.fetchall()
        return [
            {
                "id": r[0],
                "policy_id": policy_id,
                "version_number": r[1],
                "name": r[2],
                "description": r[3],
                "is_active": r[4],
                "change_summary": r[5],
                "changed_by": r[6],
                "changed_at": r[7].isoformat() if r[7] else None,
                "deleted_at": r[8].isoformat() if r[8] else None,
            }
            for r in rows
        ]
    except Exception:
        return []


class RollbackRequest(BaseModel):
    target_version: int
    reason: str


@router.post("/policies/{policy_id}/rollback")
async def rollback_policy(
    request: Request,
    policy_id: str,
    body: RollbackRequest,
    _rbac: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Roll back a policy to a previous version snapshot."""
    tenant_ctx: TenantContext = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Database not available")

    try:
        from sqlalchemy import text

        async with db() as session, session.begin():
            # Deactivate current active version
            await session.execute(
                text(
                    "UPDATE policy_versions SET is_active = FALSE "
                    "WHERE policy_id = :pid AND is_active = TRUE"
                ),
                {"pid": policy_id},
            )
            # Fetch target snapshot
            r = await session.execute(
                text(
                    "SELECT id, name, description, rules, version_number "
                    "FROM policy_versions "
                    "WHERE policy_id = :pid AND version_number = :ver"
                ),
                {"pid": policy_id, "ver": body.target_version},
            )
            target = r.fetchone()
            if not target:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    f"Version {body.target_version} not found for policy {policy_id}",
                )
            # Find max version
            max_r = await session.execute(
                text(
                    "SELECT COALESCE(MAX(version_number), 0) FROM policy_versions "
                    "WHERE policy_id = :pid"
                ),
                {"pid": policy_id},
            )
            max_ver = max_r.scalar() or 0
            new_ver = max_ver + 1
            import uuid as _uuid
            new_id = _uuid.uuid4().hex
            await session.execute(
                text(
                    """
                    INSERT INTO policy_versions
                        (id, tenant_id, policy_id, version_number, name, description,
                         rules, is_active, change_summary, changed_at)
                    VALUES
                        (:id, :tid, :pid, :ver, :name, :desc,
                         :rules::jsonb, TRUE, :summary, now())
                    """
                ),
                {
                    "id": new_id,
                    "tid": tenant_ctx.tenant_id,
                    "pid": policy_id,
                    "ver": new_ver,
                    "name": target[1],
                    "desc": target[2],
                    "rules": json.dumps(target[3]) if not isinstance(target[3], str) else target[3],
                    "summary": f"Rollback to v{body.target_version}: {body.reason}",
                },
            )
        return {
            "policy_id": policy_id,
            "new_version": new_ver,
            "rolled_back_to": body.target_version,
            "reason": body.reason,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc


# ---------------------------------------------------------------------------
# NEW (audit v2): hash chain verification
# ---------------------------------------------------------------------------


@router.get("/audit/integrity/verify")
async def verify_audit_chain(
    request: Request,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """Verify the cryptographic hash chain of audit events."""
    tenant_ctx: TenantContext = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Database not available")

    from datetime import datetime

    try:
        fd = (
            datetime.fromisoformat(from_date)
            if from_date
            else datetime(2026, 1, 1, tzinfo=UTC)
        )
        td = (
            datetime.fromisoformat(to_date)
            if to_date
            else datetime.now(UTC)
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    try:
        from app.governance.audit_v2 import HashChainVerifier

        async with db() as session:
            verifier = HashChainVerifier()
            return await verifier.verify(session, tenant_ctx.tenant_id, fd, td)
    except Exception as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, str(exc)) from exc


# ---------------------------------------------------------------------------
# NEW (audit v2): SLA violation stats
# ---------------------------------------------------------------------------


@router.get("/approvals/sla-stats")
async def get_sla_stats(request: Request) -> dict[str, Any]:
    """Return SLA compliance stats for HITL approvals."""
    tenant_ctx: TenantContext = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        return {"error": "Database not available"}

    try:
        from sqlalchemy import text

        async with db() as session:
            result = await session.execute(
                text(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE status = 'pending') AS pending,
                        COUNT(*) FILTER (WHERE status = 'approved') AS approved,
                        COUNT(*) FILTER (WHERE status = 'denied') AS denied,
                        COUNT(*) FILTER (WHERE status = 'timed_out') AS timed_out,
                        COUNT(*) FILTER (WHERE status = 'escalated') AS escalated,
                        COUNT(*) FILTER (
                            WHERE sla_deadline IS NOT NULL
                              AND resolved_at IS NOT NULL
                              AND resolved_at <= sla_deadline
                        ) AS within_sla,
                        AVG(
                            EXTRACT(EPOCH FROM (resolved_at - created_at))
                        ) FILTER (WHERE resolved_at IS NOT NULL) AS avg_resolution_seconds
                    FROM hitl_approval_requests
                    WHERE tenant_id = :tid
                    """
                ),
                {"tid": tenant_ctx.tenant_id},
            )
            row = result.fetchone()
            if not row:
                return {}
            return {
                "pending": row[0] or 0,
                "approved": row[1] or 0,
                "denied": row[2] or 0,
                "timed_out": row[3] or 0,
                "escalated": row[4] or 0,
                "within_sla": row[5] or 0,
                "avg_resolution_seconds": float(row[6]) if row[6] else None,
            }
    except Exception:
        return {}
