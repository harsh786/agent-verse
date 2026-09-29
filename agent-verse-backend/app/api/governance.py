"""Governance API — policies, HITL approvals, audit log, and cost budgets."""

from __future__ import annotations

import asyncio  # noqa: F401  (used in SSE generators)
import json as _json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from app.governance.audit import AuditLog
from app.governance.cost import BudgetConfig, CostController
from app.governance.hitl import HITLGateway, HITLResolutionUnavailableError
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


def _resolution_unavailable(exc: HITLResolutionUnavailableError) -> HTTPException:
    """503 for an approval decision that could not be recorded (never a fake 200)."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="The approval decision could not be recorded; retry shortly.",
        headers={"Retry-After": "5"},
    )


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


class PolicyPersistError(RuntimeError):
    """The DB-authoritative policy write failed (nothing was committed)."""


def _policy_rules(record: dict[str, Any]) -> list[dict[str, Any]]:
    """The version snapshot of a policy's enforceable content."""
    return [
        {
            "tools_pattern": record.get("tools_pattern", ""),
            "action": record.get("action", "deny"),
            "priority": record.get("priority", 0),
            "allowed_hours_utc": record.get("allowed_hours_utc"),
            "allowed_weekdays": record.get("allowed_weekdays"),
        }
    ]


async def _insert_policy_version(
    session: Any,
    *,
    tenant_id: str,
    policy_id: str,
    name: str,
    description: str,
    rules: list[dict[str, Any]],
    change_summary: str,
    changed_by: str | None,
    deleted: bool = False,
) -> int:
    """Append the next immutable snapshot for (tenant, policy); returns its number.

    Deactivates the previous active snapshot in the same transaction. Every
    policy_versions statement is tenant-scoped (the table is FORCE RLS, and the
    old queries filtered by policy_id alone).
    """
    from sqlalchemy import text

    await session.execute(
        text(
            "UPDATE policy_versions SET is_active = FALSE "
            "WHERE tenant_id = :tid AND policy_id = :pid AND is_active = TRUE"
        ),
        {"tid": tenant_id, "pid": policy_id},
    )
    max_ver = (
        await session.execute(
            text(
                "SELECT COALESCE(MAX(version_number), 0) FROM policy_versions "
                "WHERE tenant_id = :tid AND policy_id = :pid"
            ),
            {"tid": tenant_id, "pid": policy_id},
        )
    ).scalar() or 0
    new_ver = int(max_ver) + 1
    await session.execute(
        text(
            """
            INSERT INTO policy_versions
                (id, tenant_id, policy_id, version_number, name, description,
                 rules, is_active, change_summary, changed_by, changed_at, deleted_at)
            VALUES
                (:id, :tid, :pid, :ver, :name, :desc,
                 CAST(:rules AS jsonb), :active, :summary, :by, now(),
                 CASE WHEN :deleted THEN now() ELSE NULL END)
            """
        ),
        {
            "id": uuid.uuid4().hex,
            "tid": tenant_id,
            "pid": policy_id,
            "ver": new_ver,
            "name": name,
            "desc": description,
            "rules": _json.dumps(rules),
            "active": not deleted,
            "summary": change_summary,
            "by": changed_by,
            "deleted": deleted,
        },
    )
    return new_ver


async def _db_create_policy(
    request: Request, tenant_id: str, record: dict[str, Any], *, changed_by: str | None = None
) -> None:
    """Insert the policy AND its v1 snapshot atomically; raise on failure.

    policy_versions used to be written by nothing, so history was always empty
    and rollback had nothing to restore. A DB failure used to be swallowed and
    the API answered 201 for a policy other replicas would never load.
    """
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    try:
        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
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
            await _insert_policy_version(
                session,
                tenant_id=tenant_id,
                policy_id=record["policy_id"],
                name=record["name"],
                description=record.get("description", ""),
                rules=_policy_rules(record),
                change_summary="Created",
                changed_by=changed_by,
            )
    except Exception as exc:
        raise PolicyPersistError("policy create not persisted") from exc


async def _db_delete_policy(
    request: Request,
    tenant_id: str,
    policy_id: str,
    record: dict[str, Any],
    *,
    changed_by: str | None = None,
) -> None:
    """Delete the policy and append a deletion snapshot atomically; raise on failure."""
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    try:
        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
            await session.execute(
                text("DELETE FROM governance_policies WHERE id = :id AND tenant_id = :tid"),
                {"id": policy_id, "tid": tenant_id},
            )
            await _insert_policy_version(
                session,
                tenant_id=tenant_id,
                policy_id=policy_id,
                name=record.get("name", ""),
                description=record.get("description", "") or "",
                rules=_policy_rules(record),
                change_summary="Deleted",
                changed_by=changed_by,
                deleted=True,
            )
    except Exception as exc:
        raise PolicyPersistError("policy delete not persisted") from exc


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
async def create_policy(request: Request, body: CreatePolicyRequest) -> dict[str, Any]:
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
        allowed_hours_utc=tuple(body.allowed_hours_utc)
        if body.allowed_hours_utc and len(body.allowed_hours_utc) == 2
        else None,  # type: ignore[arg-type]
        allowed_weekdays=body.allowed_weekdays,
        tenant_id=tenant_ctx.tenant_id,
    )

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
    # Hold the engine's reload lock across the in-memory add + DB insert so
    # this can't interleave with a concurrent reload_from_db() for the same
    # tenant (e.g. this same create's own pub/sub echo, or another operator's
    # change) — otherwise the reload's stale-snapshot replace can silently
    # wipe this policy back out of `_policies` right after we added it.
    #
    # DB first: only a committed policy (+ its v1 version snapshot) is applied to
    # this replica's engine and announced to the others.
    async with engine.lock:
        try:
            await _db_create_policy(
                request, tenant_ctx.tenant_id, record, changed_by=tenant_ctx.api_key_id
            )
        except PolicyPersistError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Policy could not be persisted"
            ) from exc
        engine.add_policy(policy)
        registry.setdefault(tenant_ctx.tenant_id, {})[policy_id] = record
    redis = getattr(request.app.state, "_policy_pubsub_redis", None)
    await PolicyEngine.publish_change(redis, tenant_id=tenant_ctx.tenant_id, action="created")
    return record


@router.delete("/policies/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(request: Request, policy_id: str) -> None:
    tenant_ctx: TenantContext = _require_tenant(request)
    engine = _policy_engine(request)
    registry = _policy_registry(request)

    # DB-authoritative lookup: the per-pod registry only holds policies THIS pod
    # created, so looking up there 404'd a DELETE on any other pod even though the
    # policy exists in the DB. Resolve the record from the DB first (cross-pod),
    # falling back to the registry only in the no-DB build.
    tenant_policies = registry.get(tenant_ctx.tenant_id, {})
    record: dict[str, Any] | None = None
    for p in await _db_list_policies(request, tenant_ctx.tenant_id):
        if p.get("policy_id") == policy_id:
            record = p
            break
    if record is None:
        record = tenant_policies.get(policy_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Policy {policy_id} not found",
        )

    # Remove from the PolicyEngine's internal list, scoped to THIS tenant only.
    # Matching only on name (without tenant check) would delete identically-named
    # policies belonging to other tenants — the critical isolation bug. Other pods
    # re-sync their engine from the DB via the pub/sub publish below.
    # Held under the engine's reload lock (see create_policy) so this can't
    # interleave with a concurrent reload_from_db() for the same tenant.
    async with engine.lock:
        # DB first (delete + deletion snapshot): a failed delete must not leave
        # this replica believing the policy is gone while every other replica
        # still enforces it.
        try:
            await _db_delete_policy(
                request,
                tenant_ctx.tenant_id,
                policy_id,
                record,
                changed_by=tenant_ctx.api_key_id,
            )
        except PolicyPersistError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Policy delete could not be persisted"
            ) from exc
        engine._policies = [  # type: ignore[attr-defined]
            p
            for p in engine._policies  # type: ignore[attr-defined]
            if not (
                p.name == record["name"] and getattr(p, "tenant_id", "") == tenant_ctx.tenant_id
            )
        ]
        tenant_policies.pop(policy_id, None)
    redis = getattr(request.app.state, "_policy_pubsub_redis", None)
    await PolicyEngine.publish_change(redis, tenant_id=tenant_ctx.tenant_id, action="deleted")


@router.post("/policies/simulate")
async def simulate_policies(request: Request, body: PolicySimulateRequest) -> dict[str, Any]:
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
            "jira.delete",
            "github.deploy",
            "stripe.refund",
            "jira.search",
            "github.read",
            "slack.message",
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


async def _org_gate_approvals(
    tenant_ctx: TenantContext,
    org_id: str | None,
    *,
    resolved: bool,
) -> list[dict[str, Any]]:
    """Return org approval-gate tasks shaped like HITL approval requests.

    ``resolved=False`` returns still-pending gates for the Inbox; ``True`` returns
    decided gates (approved/rejected) for History. Durable and DB-backed, so they
    survive restarts and carry a real risk_level for the risk buckets and sort.
    """
    from sqlalchemy import text

    from app.db.session import get_session_factory

    status_clause = (
        "AND t.status != 'approval_required'" if resolved else "AND t.status = 'approval_required'"
    )
    org_clause = "AND t.org_id = CAST(:o AS uuid)" if org_id else ""
    sql = text(
        "SELECT t.id, t.org_id, t.mission_id, t.title, t.why, t.risk_level, t.status, "
        "t.created_at, t.updated_at, t.outputs "
        "FROM org_tasks t "
        "WHERE t.tenant_id = CAST(:t AS uuid) "
        "AND t.extra_data->>'task_kind' = 'approval_gate' "
        f"{status_clause} {org_clause} "
        "ORDER BY t.updated_at DESC LIMIT 200"
    )
    params: dict[str, Any] = {"t": str(tenant_ctx.tenant_id)}
    if org_id:
        params["o"] = org_id
    out: list[dict[str, Any]] = []
    try:
        db = get_session_factory()
        async with db() as sess:
            rows = (await sess.execute(sql, params)).mappings().all()
    except Exception:
        return out
    for row in rows:
        approver: str | None = None
        note: str = ""
        for o in row["outputs"] or []:
            if isinstance(o, dict):
                approver = o.get("approved_by") or o.get("rejected_by") or approver
                note = o.get("approval_note") or o.get("rejection_note") or note
        raw = str(row["status"])
        status = (
            "pending"
            if raw == "approval_required"
            else ("rejected" if raw == "cancelled" else "approved")
        )
        ts = row["updated_at"] if resolved else row["created_at"]
        out.append(
            {
                "request_id": str(row["id"]),
                "goal_id": str(row["mission_id"]) if row["mission_id"] else None,
                "org_id": str(row["org_id"]),
                "action": row["why"] or row["title"],
                "risk_level": row["risk_level"] or "high",
                "status": status,
                "created_at": ts.isoformat() if ts else "",
                "resolved_at": (
                    row["updated_at"].isoformat() if (resolved and row["updated_at"]) else None
                ),
                "note": note,
                "approver": approver,
                "required_approvers": 1,
                "approvals_received": 1 if status == "approved" else 0,
                "source": "org_gate",
            }
        )
    return out


@router.get("/approvals")
# The frontend's getPendingApprovals() calls /governance/hitl/pending, which had
# no route (404 → the approvals inbox looked empty). Same handler.
@router.get("/hitl/pending")
async def list_approvals(
    request: Request,
    org_id: str | None = Query(default=None, description="Filter approvals by org id (G-10)"),
) -> list[dict[str, Any]]:
    tenant_ctx: TenantContext = _require_tenant(request)
    gateway = _hitl(request)
    # DB-backed: the gateway's in-process dict only holds approvals THIS replica
    # created or hydrated at startup, so a listing served by a different replica
    # than the one that raised the gate silently omitted it.
    pending = await gateway.alist_pending(tenant_ctx=tenant_ctx)

    # G-10: optionally filter by org_id by resolving each approval's goal
    # execution_context["org_id"]. We keep the lookup best-effort and skip
    # approvals whose goal cannot be resolved (treat as unscoped).
    if org_id:
        scoped: list[Any] = []
        for r in pending:
            _goal_id = getattr(r, "goal_id", None)
            if not _goal_id:
                continue
            try:
                from app.db.session import get_session_factory as _gsf

                _db = _gsf()
                from sqlalchemy import select

                from app.db.models.goal import Goal as _GoalM

                async with _db() as _sess:
                    _row = (
                        await _sess.execute(
                            select(_GoalM.execution_context).where(_GoalM.id == _goal_id)
                        )
                    ).first()
                if _row and isinstance(_row[0], dict) and _row[0].get("org_id") == org_id:
                    scoped.append(r)
            except Exception:
                continue
        pending = scoped

    results: list[dict[str, Any]] = [
        {
            "request_id": r.request_id,
            "goal_id": r.goal_id,
            "action": r.action,
            "risk_level": r.risk_level,
            "status": r.status,
            "created_at": getattr(r, "created_at", ""),
            "note": getattr(r, "note", ""),
            "approver": getattr(r, "approver", None),
            "required_approvers": getattr(r, "required_approvers", 1),
            "approvals_received": getattr(r, "approvals_received", 0),
        }
        for r in pending
    ]

    # Bridge durable org approval gates (OrgTasks) into the global inbox so they
    # show up with risk buckets/sort like any HITL request — the in-memory gateway
    # loses its requests on restart, but the gate tasks persist in the DB.
    results.extend(await _org_gate_approvals(tenant_ctx, org_id, resolved=False))

    # Hide phantom approvals whose owning mission/goal already finished: once the
    # work is completed/failed/cancelled there is nothing left to approve, so the
    # gate must not keep nagging the operator. Defensive read-time filter that
    # covers gates still lingering in gateway memory or as org tasks.
    return await _drop_terminal_owner_approvals(tenant_ctx, results)


async def _drop_terminal_owner_approvals(
    tenant_ctx: TenantContext, approvals: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Remove approvals whose goal_id maps to a terminal mission or goal."""
    goal_ids = [str(a["goal_id"]) for a in approvals if a.get("goal_id")]
    if not goal_ids:
        return approvals
    from sqlalchemy import text

    from app.db.session import get_session_factory

    terminal: set[str] = set()
    try:
        db = get_session_factory()
        async with db() as sess, sess.begin():
            # org_missions / goals enforce RLS — scope this session to the tenant
            # so the terminal lookups can see the owning rows.
            await sess.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"),
                {"t": str(tenant_ctx.tenant_id)},
            )
            m = (
                await sess.execute(
                    text(
                        "SELECT id::text FROM org_missions WHERE id::text = ANY(:ids) "
                        "AND status IN ('completed','failed','cancelled','archived')"
                    ),
                    {"ids": goal_ids},
                )
            ).scalars().all()
            g = (
                await sess.execute(
                    text(
                        "SELECT id FROM goals WHERE id = ANY(:ids) "
                        "AND status IN ('complete','failed','cancelled')"
                    ),
                    {"ids": goal_ids},
                )
            ).scalars().all()
            terminal = {str(x) for x in [*m, *g]}
    except Exception:
        return approvals  # never let the filter break the inbox
    if not terminal:
        return approvals
    return [a for a in approvals if str(a.get("goal_id") or "") not in terminal]


async def _resolve_org_gate(
    request: Request,
    tenant_ctx: TenantContext,
    task_id: str,
    decision: str,
    approver: str,
    note: str,
) -> bool:
    """Resolve an org approval-gate task from the global inbox.

    Returns True if ``task_id`` was an org gate and got resolved. Approve flips it
    to 'running' and — when it was the last pending gate — launches the paused
    mission's goal; reject cancels it and fails the guarded mission.
    """
    from datetime import UTC, datetime

    from app.db.rls import sqlalchemy_rls_context
    from app.db.session import get_session_factory
    from app.org.service import OrgService

    tid = str(tenant_ctx.tenant_id)
    # `found` gates error handling: any failure while *looking up* the gate
    # (no DB configured, an id that isn't a valid task, a connection error)
    # means "this id is not a resolvable org gate" → return False so the caller
    # can 404. Once a real gate is confirmed we re-raise, because a failure to
    # apply the decision is a genuine 500 worth surfacing, not a silent no-op.
    found = False
    try:
        db = get_session_factory()
        async with db() as sess, sqlalchemy_rls_context(sess, tid):
            svc = OrgService(sess, tid)
            task = await svc.get_task(task_id)
            if task is None or (task.extra_data or {}).get("task_kind") != "approval_gate":
                return False
            found = True
            now = datetime.now(UTC).isoformat()
            if decision == "approve":
                await svc.update_task_status(
                    task_id,
                    "running",
                    outputs=[{"approved_by": approver, "approval_note": note, "decided_at": now}],
                )
                if task.mission_id:
                    remaining = [
                        t
                        for t in await svc.list_tasks(
                            str(task.org_id), mission_id=str(task.mission_id)
                        )
                        if (t.extra_data or {}).get("task_kind") == "approval_gate"
                        and t.status == "approval_required"
                    ]
                    if not remaining:
                        await svc.dispatch_mission_goal(
                            str(task.mission_id), app_state=request.app.state
                        )
            else:
                await svc.update_task_status(
                    task_id,
                    "cancelled",
                    outputs=[
                        {
                            "rejected_by": approver,
                            "rejection_note": note or "Rejected",
                            "decided_at": now,
                        }
                    ],
                )
                if task.mission_id:
                    mission = await svc.get_mission(str(task.mission_id))
                    if mission and str(mission.status) not in (
                        "completed",
                        "failed",
                        "cancelled",
                        "archived",
                    ):
                        await svc.update_mission_status(str(task.mission_id), "failed")
            await sess.commit()
            return True
    except Exception:
        if found:
            raise
        return False


def _approver_identity(tenant_ctx: TenantContext) -> str:
    """The approver is the authenticated key, never a request field.

    It was ``body.approver``: one key could approve as "alice", then "bob", and
    satisfy a multi-approver rule on its own (same bug fixed in /trust).
    """
    key_id = str(getattr(tenant_ctx, "api_key_id", "") or "")
    if not key_id:
        raise HTTPException(status_code=403, detail="Approver identity unavailable")
    return key_id


@router.post("/approvals/{request_id}/approve")
async def approve_request(
    request: Request,
    request_id: str,
    body: ApproveRejectRequest,
    _rbac: None = Depends(require_role("approver")),
) -> dict[str, Any]:
    tenant_ctx: TenantContext = _require_tenant(request)
    gateway = _hitl(request)
    approver = _approver_identity(tenant_ctx)
    # DB-resolving: the sync approve() looks the request up in this replica's
    # own dict, so an operator routed to a different replica than the one that
    # raised the gate got a false 'not found' for a live approval.
    try:
        ok = await gateway.approve_async(
            request_id, approver=approver, note=body.note, tenant_ctx=tenant_ctx
        )
    except HITLResolutionUnavailableError as exc:
        raise _resolution_unavailable(exc) from exc
    # Not a live gateway request — maybe a durable org approval gate.
    if not ok and not await _resolve_org_gate(
        request, tenant_ctx, request_id, "approve", approver, body.note
    ):
        raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Approval request {request_id} not found",
            )
    return {"request_id": request_id, "status": "approved", "approver": approver}


@router.post("/approvals/{request_id}/reject")
async def reject_request(
    request: Request,
    request_id: str,
    body: ApproveRejectRequest,
    _rbac: None = Depends(require_role("approver")),
) -> dict[str, Any]:
    tenant_ctx: TenantContext = _require_tenant(request)
    gateway = _hitl(request)
    approver = _approver_identity(tenant_ctx)
    try:
        ok = await gateway.reject(
            request_id, approver=approver, note=body.note, tenant_ctx=tenant_ctx
        )
    except HITLResolutionUnavailableError as exc:
        raise _resolution_unavailable(exc) from exc
    if not ok and not await _resolve_org_gate(
        request, tenant_ctx, request_id, "reject", approver, body.note
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval request {request_id} not found",
        )
    return {"request_id": request_id, "status": "rejected", "approver": approver}


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
            yield f"data: {_json.dumps({'type': 'stream_unavailable'})}\n\n"
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
            yield f"data: {_json.dumps({'type': 'stream_unavailable'})}\n\n"
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
    outcome: str | None = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    tenant_ctx: TenantContext = _require_tenant(request)
    log = _audit(request)

    # Use direct DB query for accuracy + pagination support. outcome/q are filtered
    # in SQL so search/filter covers the whole dataset, not just a loaded page.
    from app.governance.audit import AuditQueryUnavailableError

    try:
        events = await log.query_db(
            tenant_ctx=tenant_ctx,
            goal_id=goal_id,
            tool_name=tool_name,
            limit=limit,
            offset=offset,
            start_time=start_time,
            end_time=end_time,
            outcome=outcome,
            q=q,
        )
    except AuditQueryUnavailableError as exc:
        # 503, not a partial per-replica answer presented as the full trail.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Audit store unavailable"
        ) from exc

    return [
        {
            "event_id": e.event_id,
            "goal_id": e.goal_id,
            "tool_name": e.tool_name,
            "action_level": (
                e.action_level.value if hasattr(e.action_level, "value") else e.action_level
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


async def _effective_budget(request: Request, tenant_id: str) -> BudgetConfig:
    """The budget the cost controllers actually enforce for this tenant."""
    for name in ("redis_cost_controller", "cost_controller"):
        cc = getattr(request.app.state, name, None)
        if cc is not None and hasattr(cc, "resolve_config"):
            try:
                return await cc.resolve_config(tenant_id)  # type: ignore[no-any-return]
            except Exception as exc:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE, "Budget store unavailable"
                ) from exc
    return _budget_config(request).get(tenant_id, BudgetConfig())


@router.get("/budget")
async def get_budget(request: Request) -> dict[str, Any]:
    tenant_ctx: TenantContext = _require_tenant(request)
    cfg = await _effective_budget(request, tenant_ctx.tenant_id)
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
    """Set the tenant budget — persisted to budget_configs (same store as /costs/budgets).

    This used to write only a per-replica dict that nothing enforced; now the
    row is DB-authoritative and applied to the cost controllers.
    """
    tenant_ctx: TenantContext = _require_tenant(request)
    cfg = BudgetConfig(
        per_goal_usd=body.per_goal_usd,
        per_tenant_daily_usd=body.per_tenant_daily_usd,
    )
    db = getattr(request.app.state, "db_session_factory", None)
    if db is not None:
        from app.governance.cost import persist_tenant_budget

        try:
            await persist_tenant_budget(
                db,
                tenant_ctx.tenant_id,
                per_goal_usd=cfg.per_goal_usd,
                per_tenant_daily_usd=cfg.per_tenant_daily_usd,
            )
        except Exception as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "Budget could not be persisted"
            ) from exc
    _budget_config(request)[tenant_ctx.tenant_id] = cfg
    for name in ("redis_cost_controller", "cost_controller"):
        cc = getattr(request.app.state, name, None)
        if cc is None:
            continue
        if hasattr(cc, "invalidate_tenant_budget"):
            cc.invalidate_tenant_budget(tenant_ctx.tenant_id)
        if hasattr(cc, "configure_tenant_budget"):
            cc.configure_tenant_budget(tenant_ctx.tenant_id, cfg)
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
    # Awaited, RLS-scoped write: the row exists (for every replica) before the
    # caller is told it was created.
    await svc.add_channel_async(channel)
    return {"channel_id": channel.channel_id, "type": channel.channel_type, "status": "created"}


@router.get("/notifications")
async def list_notification_channels(request: Request) -> list[dict[str, Any]]:
    tenant = _require_tenant(request)
    svc = getattr(request.app.state, "notification_service", None)
    if svc is None:
        return []
    await svc.ensure_tenant_loaded(tenant.tenant_id)
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
    removed = await svc.remove_channel_async(channel_id, tenant.tenant_id)
    if not removed:
        raise HTTPException(404, "Notification channel not found")


@router.post("/notifications/{channel_id}/test")
async def test_notification_channel(request: Request, channel_id: str) -> dict[str, Any]:
    """Send a test notification to verify channel connectivity."""
    tenant = _require_tenant(request)
    svc = getattr(request.app.state, "notification_service", None)
    if svc is None:
        raise HTTPException(503, "Notification service unavailable")
    await svc.ensure_tenant_loaded(tenant.tenant_id)
    channels = svc.get_channels(tenant.tenant_id)
    channel = next((c for c in channels if c.channel_id == channel_id), None)
    if channel is None:
        raise HTTPException(404, "Notification channel not found")
    # Send to THIS channel only and report its real delivery result. It used to
    # broadcast an approval notice to every channel of the tenant and answer
    # success:true even when nothing was delivered.
    message = {
        "type": "test",
        "request_id": "test-" + uuid.uuid4().hex[:8],
        "text": "Test notification from AgentVerse",
    }
    try:
        await svc._send(channel, message)
    except Exception as e:
        return {"success": False, "message": f"Delivery failed: {e}"}
    return {
        "success": True,
        "message": f"Test notification sent to {channel.channel_type} channel.",
    }


# ---------------------------------------------------------------------------
# Request models — legal hold
# ---------------------------------------------------------------------------


class LegalHoldRequest(BaseModel):
    reason: str
    expires_at: str | None = None  # ISO datetime
    # Optional scoping. Default ("tenant", no ids) holds ALL of the tenant's data.
    name: str | None = None
    resource_type: str = "tenant"
    resource_ids: list[str] | None = None
    user_ids: list[str] | None = None
    legal_matter_id: str | None = None


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


def _stop_redis(request: Request) -> Any:
    """The runtime Redis every enforcement point reads (API gates, AgentGraph, worker)."""
    st = request.app.state
    return getattr(st, "_redis", None) or getattr(st, "_policy_pubsub_redis", None)


def _stop_unenforceable(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "Emergency stop cannot be enforced: the shared control store is unavailable "
            f"({type(exc).__name__}). Nothing was stopped; retry or stop goals individually."
        ),
        headers={"Retry-After": "5"},
    )


@router.get("/emergency-stop")
async def get_emergency_stop(request: Request) -> dict[str, Any]:
    """Current tenant emergency-stop state — the source of truth for the UI banner."""
    from app.governance.emergency_stop import (
        EmergencyStopUnavailableError,
        read_stop,
        tenant_stop_key,
    )

    ctx = _require_tenant(request)
    try:
        record = await read_stop(_stop_redis(request), tenant_stop_key(ctx.tenant_id))
    except EmergencyStopUnavailableError as exc:
        raise _stop_unenforceable(exc) from exc
    if record is None:
        return {"active": False, "tenant_id": ctx.tenant_id}
    return {
        "active": True,
        "tenant_id": ctx.tenant_id,
        "activated_at": record.get("activated_at"),
        "activated_by": record.get("activated_by"),
        "reason": record.get("reason", ""),
    }


@router.post("/emergency-stop")
async def emergency_stop(
    request: Request,
    _rbac: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Stop all autonomous work for this tenant until an admin lifts the stop.

    Use for: security incidents, runaway agents, cost overruns.

    1. Persists ``emergency_stop:{tenant}`` in Redis with NO expiry (it used to
       expire after 300 s). Every enforcement point reads it: goal submission,
       goal start (worker + in-process) and every step boundary on every
       replica and worker. If it cannot be written the call fails 503 — it
       used to answer "All running goals cancelled" with nothing persisted.
    2. Cancels every non-terminal goal of the tenant through the cross-replica
       cancel path (DB-backed listing; it used to see only this replica's
       in-memory goals).
    3. Rejects every pending approval.

    Admin-only. Every per-goal cancel and per-approval reject failure is
    reported (``failed_*``, ``partial``); the stop flag itself still blocks
    those goals at their next step boundary.
    """
    import logging

    from app.governance.emergency_stop import (
        EmergencyStopUnavailableError,
        activate_stop,
        tenant_stop_key,
    )

    _log = logging.getLogger(__name__)
    ctx = _require_tenant(request)
    errors: list[str] = []

    # 1. Persist the stop first: from here on nothing new starts and every
    #    running goal halts at its next step boundary, wherever it runs.
    try:
        record = await activate_stop(
            _stop_redis(request),
            tenant_stop_key(ctx.tenant_id),
            activated_by=str(getattr(ctx, "api_key_id", "") or ""),
        )
    except EmergencyStopUnavailableError as exc:
        _log.error("emergency_stop_not_persisted: %s", exc)
        raise _stop_unenforceable(exc) from exc

    # 2. Cancel every non-terminal goal of the tenant, on any replica/worker.
    goal_service = getattr(request.app.state, "goal_service", None)
    cancelled_goals: list[str] = []
    failed_goals: list[dict[str, str]] = []
    if goal_service is not None:
        try:
            running = await goal_service.active_goal_ids(ctx)
        except Exception as exc:
            _log.warning("emergency_stop_enumerate_failed: %s", exc)
            running = []
            errors.append(f"goal_enumeration_failed: {type(exc).__name__}")
        for goal_id in running:
            try:
                await goal_service.cancel_goal(goal_id=goal_id, tenant_ctx=ctx)
                cancelled_goals.append(goal_id)
            except Exception as exc:
                _log.warning("emergency_stop_cancel_failed goal_id=%s: %s", goal_id, exc)
                failed_goals.append({"goal_id": goal_id, "error": type(exc).__name__})

    # 3. Reject all pending HITL approvals (DB-backed listing: approvals raised on
    #    other replicas must be rejected too, not just this replica's cache).
    hitl = getattr(request.app.state, "hitl_gateway", None)
    rejected_approvals: list[str] = []
    failed_approvals: list[dict[str, str]] = []
    if hitl is not None:
        try:
            if hasattr(hitl, "alist_pending"):
                pending = await hitl.alist_pending(tenant_ctx=ctx)
            else:
                pending = hitl.list_pending(tenant_ctx=ctx)
        except Exception as exc:
            _log.warning("emergency_stop_list_approvals_failed: %s", exc)
            pending = []
            errors.append(f"approval_listing_failed: {type(exc).__name__}")
        for approval in pending:
            try:
                ok = await hitl.reject(
                    approval.request_id,
                    tenant_ctx=ctx,
                    approver=str(getattr(ctx, "api_key_id", "") or "emergency-stop"),
                    note="Emergency stop activated by operator",
                )
                if ok is False:
                    failed_approvals.append(
                        {"request_id": approval.request_id, "error": "not_rejected"}
                    )
                else:
                    rejected_approvals.append(approval.request_id)
            except Exception as exc:
                _log.warning(
                    "emergency_stop_reject_failed request_id=%s: %s", approval.request_id, exc
                )
                failed_approvals.append(
                    {"request_id": approval.request_id, "error": type(exc).__name__}
                )

    # 4. Log to audit trail
    audit_log = getattr(request.app.state, "audit_log", None)
    audit_recorded = False
    if audit_log is not None:
        try:
            from app.governance.audit import AuditEvent
            from app.governance.permissions import ActionLevel
            from app.tenancy.context import PlanTier

            _audit_ctx = TenantContext(
                tenant_id=ctx.tenant_id,
                plan=PlanTier.FREE,
                api_key_id=getattr(ctx, "api_key_id", ""),
            )
            audit_log.record(
                AuditEvent(
                    goal_id="emergency_stop",
                    tool_name="emergency_stop",
                    action_level=ActionLevel.DENY,
                    outcome="stop_activated",
                    api_key_id=getattr(ctx, "api_key_id", ""),
                    note=(
                        f"cancelled_goals={len(cancelled_goals)},"
                        f"rejected_approvals={len(rejected_approvals)},"
                        f"failed_goals={len(failed_goals)},"
                        f"failed_approvals={len(failed_approvals)}"
                    ),
                ),
                tenant_ctx=_audit_ctx,
            )
            audit_recorded = True
        except Exception as exc:
            _log.warning("emergency_stop_audit_failed: %s", exc)
            errors.append(f"audit_failed: {type(exc).__name__}")

    partial = bool(failed_goals or failed_approvals or errors)
    return {
        "status": "emergency_stop_partial" if partial else "emergency_stop_activated",
        "active": True,
        "partial": partial,
        "tenant_id": ctx.tenant_id,
        "activated_at": record.get("activated_at"),
        "cancelled_goals": len(cancelled_goals),
        "cancelled_goal_ids": cancelled_goals[:20],
        "failed_goals": failed_goals,
        "rejected_approvals": len(rejected_approvals),
        "failed_approvals": failed_approvals,
        # Kept for API compatibility: the persisted flag is what workers read.
        "celery_signal_sent": True,
        "audit_recorded": audit_recorded,
        "errors": errors,
        "message": (
            "Emergency stop is active and persisted, but some goals/approvals could not be "
            "cancelled directly — they are halted at their next step boundary. See "
            "failed_goals / failed_approvals / errors."
            if partial
            else "Emergency stop is active until cleared: running goals were cancelled and "
            "no goal will start or take another step."
        ),
    }


@router.delete("/emergency-stop")
async def clear_emergency_stop(
    request: Request,
    _rbac: None = Depends(require_role("admin")),
) -> dict[str, Any]:
    """Lift the tenant emergency stop so goals may be submitted again (admin only)."""
    from app.governance.emergency_stop import (
        EmergencyStopUnavailableError,
        clear_stop,
        tenant_stop_key,
    )

    ctx = _require_tenant(request)
    try:
        await clear_stop(_stop_redis(request), tenant_stop_key(ctx.tenant_id))
    except EmergencyStopUnavailableError as exc:
        # The stop may still be set — never report "cleared".
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Failed to clear emergency stop flag"
        ) from exc
    return {"status": "cleared", "active": False, "tenant_id": ctx.tenant_id}


# ---------------------------------------------------------------------------
# P1.3: Email approval link handlers (signed URLs from approval emails)
# ---------------------------------------------------------------------------


async def _email_link_decision(
    request: Request, request_id: str, action: str, sig: str, exp: int
) -> tuple[HITLGateway, TenantContext, str]:
    """Verify a signed approve/reject link for the AUTHENTICATED approver.

    The link used to be verified against ``request_id:action`` only, under a
    public default secret with no expiry, and the decision ran as a synthetic
    ``email-link`` principal in whatever tenant owned the request -- so any
    caller could forge a permanent approve link for any tenant's request. Now
    the caller must hold the ``approver`` role (route dependency), the
    signature must be unexpired and bound to this request, action AND the
    caller's own tenant, and the decision is attributed to the caller.
    """
    from app.integrations.email.approval_sender import _verify

    tenant_ctx: TenantContext = _require_tenant(request)
    if not _verify(request_id, action, sig, tenant_id=tenant_ctx.tenant_id, exp=exp):
        raise HTTPException(status_code=403, detail="Invalid or expired approval link")
    gateway = getattr(request.app.state, "hitl_gateway", None)
    if gateway is None:
        raise HTTPException(status_code=503, detail="HITL gateway not available")
    return gateway, tenant_ctx, _approver_identity(tenant_ctx)


@router.get("/hitl/{request_id}/approve")
async def email_approve_link(
    request: Request,
    request_id: str,
    sig: str = "",
    exp: int = 0,
    _rbac: None = Depends(require_role("approver")),
) -> dict[str, Any]:
    """Handle one-click approve link from HITL approval email."""
    gateway, tenant_ctx, approver = await _email_link_decision(
        request, request_id, "approve", sig, exp
    )
    # DB-first: the request may have been raised on another replica, and the
    # waiting agent must only be released once the decision is committed.
    # approve_async answers False unless the request is still PENDING.
    try:
        ok = await gateway.approve_async(request_id, approver=approver, tenant_ctx=tenant_ctx)
    except HITLResolutionUnavailableError as exc:
        raise _resolution_unavailable(exc) from exc
    if not ok:
        raise HTTPException(status_code=409, detail="Approval request is no longer pending")

    return {
        "request_id": request_id,
        "status": "approved",
        "approver": approver,
        "message": "Action approved via email link.",
    }


@router.get("/hitl/{request_id}/reject")
async def email_reject_link(
    request: Request,
    request_id: str,
    sig: str = "",
    exp: int = 0,
    _rbac: None = Depends(require_role("approver")),
) -> dict[str, Any]:
    """Handle one-click reject link from HITL approval email."""
    gateway, tenant_ctx, approver = await _email_link_decision(
        request, request_id, "reject", sig, exp
    )
    try:
        ok = await gateway.reject(
            request_id, approver=approver, note="Rejected via email link", tenant_ctx=tenant_ctx
        )
    except HITLResolutionUnavailableError as exc:
        raise _resolution_unavailable(exc) from exc
    if not ok:
        raise HTTPException(status_code=409, detail="Approval request is no longer pending")

    return {
        "request_id": request_id,
        "status": "rejected",
        "approver": approver,
        "message": "Action rejected via email link.",
    }


# ---------------------------------------------------------------------------
# Endpoints — legal hold
# ---------------------------------------------------------------------------


def _legal_hold_manager(request: Request) -> Any:
    """LegalHoldManager over the request DB factory (+ the wired Redis cache)."""
    db = _get_db(request)
    if db is None:
        return None
    from app.governance.legal_holds import LegalHoldManager

    wired = getattr(request.app.state, "legal_hold_manager", None)
    return LegalHoldManager(redis=getattr(wired, "_redis", None), db_factory=db)


@router.post("/legal-hold")
async def create_legal_hold(request: Request, body: LegalHoldRequest) -> dict:
    """Place a legal hold on tenant data to prevent retention deletion.

    This used to INSERT a ``reason`` column that ``legal_holds`` (migration 0057)
    does not have, omit the NOT NULL ``name``/``resource_type`` and run without
    the RLS tenant context — so every call failed. It now goes through
    ``LegalHoldManager`` (the real schema, under RLS). The default hold is
    tenant-wide (``resource_type="tenant"``), which the delete gates and the
    retention sweeps honour.
    """
    from datetime import datetime

    from app.governance.legal_holds import TENANT_WIDE

    ctx = _require_tenant(request)
    mgr = _legal_hold_manager(request)
    if mgr is None:
        raise HTTPException(503, "Database not available")

    expires_at = None
    if body.expires_at:
        try:
            expires_at = datetime.fromisoformat(body.expires_at)
        except ValueError as exc:
            raise HTTPException(422, "expires_at must be an ISO-8601 datetime") from exc
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
    resource_type = (body.resource_type or TENANT_WIDE).strip() or TENANT_WIDE
    if resource_type != TENANT_WIDE and not (body.resource_ids or body.user_ids):
        raise HTTPException(422, "A non-tenant-wide hold needs resource_ids or user_ids")

    try:
        hold = await mgr.create_hold(
            tenant_id=ctx.tenant_id,
            name=(body.name or body.reason)[:500],
            description=body.reason,
            resource_type=resource_type,
            resource_ids=body.resource_ids,
            user_ids=body.user_ids,
            legal_matter_id=body.legal_matter_id,
            created_by=getattr(ctx, "api_key_id", None) or None,
            expires_at=expires_at,
        )
    except Exception as exc:
        raise HTTPException(
            503, "Legal hold could not be persisted; no hold is in place"
        ) from exc
    return {
        "status": "legal_hold_placed",
        "id": hold["id"],
        "tenant_id": ctx.tenant_id,
        "reason": body.reason,
        "resource_type": resource_type,
        "resource_ids": hold["resource_ids"],
        "user_ids": hold["user_ids"],
        "expires_at": expires_at.isoformat() if expires_at else None,
    }


@router.get("/legal-holds")
async def list_legal_holds(request: Request) -> list[dict[str, Any]]:
    """List active legal holds for this tenant (empty when no DB is configured).

    A query failure is a 503, not ``[]``: an empty list reads as "nothing is on
    hold", which is exactly the wrong conclusion to hand a compliance officer.
    """
    ctx = _require_tenant(request)
    mgr = _legal_hold_manager(request)
    if mgr is None:
        return []
    try:
        holds = await mgr.list_holds(ctx.tenant_id)
    except Exception as exc:
        raise HTTPException(503, "Legal holds could not be read") from exc
    return [{**h, "reason": h.get("description") or h.get("name")} for h in holds]


# ---------------------------------------------------------------------------
# NEW (governance v2): Batch HITL approval
# ---------------------------------------------------------------------------


class BatchApproveRequest(BaseModel):
    action: str  # "approve" | "reject"
    request_ids: list[str]
    # Ignored: the approver is the authenticated key (see _approver_identity).
    # Kept optional so existing clients that still send it are not rejected.
    approver: str = ""
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
    # The approver used to be body.approver: one key could approve as anyone and
    # satisfy a multi-approver gate alone.
    approver = _approver_identity(tenant_ctx)

    approved = 0
    rejected_count = 0
    not_found = 0
    unavailable = 0
    results: list[dict[str, Any]] = []

    for req_id in body.request_ids:
        if body.action == "approve":
            # DB-resolving (tenant-scoped): the sync approve() only sees this
            # replica's cache, which no longer gets a cross-tenant warm-up at
            # startup — a live approval raised before a restart or on another
            # replica would otherwise report not_found.
            try:
                ok = await gateway.approve_async(
                    req_id, approver=approver, note=body.note, tenant_ctx=tenant_ctx
                )
            except HITLResolutionUnavailableError:
                unavailable += 1
                results.append({"request_id": req_id, "result": "unavailable"})
                continue
            if ok:
                # approve_async already delivered the resolution to cross-replica
                # waiters ("approved"). The extra publish here sent "approve" —
                # a value no waiter recognises — and could consume the waiter's
                # BLPOP instead of the real decision.
                approved += 1
                results.append({"request_id": req_id, "result": "approved"})
            else:
                not_found += 1
                results.append({"request_id": req_id, "result": "not_found"})
        elif body.action == "reject":
            try:
                ok = await gateway.reject(
                    req_id, approver=approver, note=body.note, tenant_ctx=tenant_ctx
                )
            except HITLResolutionUnavailableError:
                unavailable += 1
                results.append({"request_id": req_id, "result": "unavailable"})
                continue
            if ok:
                # reject() already published "rejected" to cross-replica waiters.
                rejected_count += 1
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
        "unavailable": unavailable,
        "results": results,
    }


# ---------------------------------------------------------------------------
# NEW (governance v2): Policy version history & rollback
# ---------------------------------------------------------------------------


@router.get("/policies/{policy_id}/versions")
async def get_policy_versions(request: Request, policy_id: str) -> list[dict[str, Any]]:
    """Return the full version history for a policy (this tenant only)."""
    tenant_ctx: TenantContext = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        return []
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    try:
        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            result = await session.execute(
                text(
                    """
                    SELECT id, version_number, name, description, is_active,
                           change_summary, changed_by, changed_at, deleted_at
                    FROM policy_versions
                    WHERE tenant_id = :tid AND policy_id = :pid
                    ORDER BY version_number ASC
                    """
                ),
                {"tid": tenant_ctx.tenant_id, "pid": policy_id},
            )
            rows = result.fetchall()
    except Exception as exc:
        # An unreadable history is not an empty history.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Policy history unavailable"
        ) from exc
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
    """Roll back a policy to a previous version snapshot — and actually apply it.

    Previously this only appended a policy_versions row: governance_policies (what
    every engine loads) and the in-process PolicyEngine were never touched, so the
    "rolled back" policy kept being enforced exactly as before. Now, in one
    tenant-scoped transaction, the target snapshot is re-written into
    governance_policies (or the policy is removed, if the target is a deletion
    snapshot) and a new version row records the rollback; then this replica's
    engine reloads from the DB and the change is published so every other
    replica reloads too.
    """
    tenant_ctx: TenantContext = _require_tenant(request)
    tid = tenant_ctx.tenant_id
    db = _get_db(request)
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Database not available")

    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    engine = _policy_engine(request)
    try:
        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tid):
            target = (
                await session.execute(
                    text(
                        "SELECT id, name, description, rules, version_number, deleted_at "
                        "FROM policy_versions "
                        "WHERE tenant_id = :tid AND policy_id = :pid AND version_number = :ver"
                    ),
                    {"tid": tid, "pid": policy_id, "ver": body.target_version},
                )
            ).fetchone()
            if not target:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    f"Version {body.target_version} not found for policy {policy_id}",
                )
            rules = target[3]
            if isinstance(rules, str):
                rules = _json.loads(rules)
            rule: dict[str, Any] = (rules[0] if isinstance(rules, list) and rules else {}) or {}
            restored_deleted = target[5] is not None
            if restored_deleted:
                await session.execute(
                    text("DELETE FROM governance_policies WHERE id = :id AND tenant_id = :tid"),
                    {"id": policy_id, "tid": tid},
                )
            else:
                await session.execute(
                    text(
                        """INSERT INTO governance_policies
                            (id, tenant_id, name, tools_pattern, action, priority, description)
                        VALUES (:id, :tid, :name, :pattern, :action, :priority, :desc)
                        ON CONFLICT (id) DO UPDATE SET
                            name = EXCLUDED.name,
                            tools_pattern = EXCLUDED.tools_pattern,
                            action = EXCLUDED.action,
                            priority = EXCLUDED.priority,
                            description = EXCLUDED.description"""
                    ),
                    {
                        "id": policy_id,
                        "tid": tid,
                        "name": target[1],
                        "pattern": rule.get("tools_pattern") or "*",
                        "action": rule.get("action") or "deny",
                        "priority": int(rule.get("priority") or 0),
                        "desc": target[2] or "",
                    },
                )
            new_ver = await _insert_policy_version(
                session,
                tenant_id=tid,
                policy_id=policy_id,
                name=target[1],
                description=target[2] or "",
                rules=rules if isinstance(rules, list) else [],
                change_summary=f"Rollback to v{body.target_version}: {body.reason}",
                changed_by=tenant_ctx.api_key_id,
                deleted=restored_deleted,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Policy rollback failed"
        ) from exc

    # Apply to this replica's engine from the now-committed DB state.
    engine_reloaded = True
    try:
        await engine.reload_from_db(db, tenant_id=tid, strict=True)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).error("policy_rollback_engine_reload_failed: %s", exc)
        engine_reloaded = False
    registry = _policy_registry(request).setdefault(tid, {})
    if restored_deleted:
        registry.pop(policy_id, None)
    else:
        registry[policy_id] = {
            "policy_id": policy_id,
            "name": target[1],
            "description": target[2] or "",
            "tools_pattern": rule.get("tools_pattern") or "*",
            "action": rule.get("action") or "deny",
            "priority": int(rule.get("priority") or 0),
            "allowed_hours_utc": rule.get("allowed_hours_utc"),
            "allowed_weekdays": rule.get("allowed_weekdays"),
        }
    redis = getattr(request.app.state, "_policy_pubsub_redis", None)
    await PolicyEngine.publish_change(redis, tenant_id=tid, action="rolled_back")
    if not engine_reloaded:
        # The DB rollback committed (and other replicas reload on the pub/sub
        # message), but THIS replica's engine is stale — say so, don't claim success.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Rollback persisted but this replica's policy engine failed to reload",
        )
    return {
        "policy_id": policy_id,
        "new_version": new_ver,
        "rolled_back_to": body.target_version,
        "reason": body.reason,
        "policy_deleted": restored_deleted,
    }


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
        fd = datetime.fromisoformat(from_date) if from_date else datetime(2026, 1, 1, tzinfo=UTC)
        td = datetime.fromisoformat(to_date) if to_date else datetime.now(UTC)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    from app.governance.audit_v3 import AuditChainVerificationError, HashChainVerifier

    try:
        async with db() as session, session.begin():
            verifier = HashChainVerifier()
            return await verifier.verify(session, tenant_ctx.tenant_id, fd, td)
    except AuditChainVerificationError as exc:
        # Integrity is UNKNOWN — never answer verified=True for an unreadable chain.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Audit chain could not be verified"
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "Audit chain verification failed"
        ) from exc


# ---------------------------------------------------------------------------
# NEW (audit v2): SLA violation stats
# ---------------------------------------------------------------------------


@router.get("/approvals/history")
async def list_approval_history(
    request: Request,
    limit: int = 50,
    status_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Return resolved approval requests from the DB (approved / rejected / timed_out)."""
    tenant_ctx: TenantContext = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        return []
    try:
        from sqlalchemy import text as _text

        sql = """
            SELECT id, goal_id, action, risk_level, status, approver, note,
                   created_at, resolved_at
            FROM approval_requests
            WHERE tenant_id = :tid AND status != 'pending'
        """
        params: dict[str, Any] = {"tid": tenant_ctx.tenant_id}
        if status_filter:
            sql += " AND status = :sf"
            params["sf"] = status_filter
        sql += f" ORDER BY created_at DESC LIMIT {max(1, min(limit, 200))}"

        from app.db.rls import sqlalchemy_rls_context

        # approval_requests is FORCE-RLS: without the tenant GUC the application
        # role reads zero rows, so the history was always empty.
        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            rows = (await session.execute(_text(sql), params)).fetchall()

        history = [
            {
                "request_id": r[0],
                "goal_id": r[1],
                "action": r[2],
                "risk_level": r[3],
                "status": r[4],
                "approver": r[5],
                "note": r[6],
                "created_at": r[7].isoformat() if r[7] else "",
                "resolved_at": r[8].isoformat() if r[8] else "",
            }
            for r in rows
        ]
    except Exception as exc:
        # An unreadable history is a 503, not an empty (fake) one.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Approval history is temporarily unavailable.",
        ) from exc

    # Include resolved org approval gates (durable OrgTasks) in the History tab.
    gate_history = await _org_gate_approvals(tenant_ctx, None, resolved=True)
    if status_filter:
        gate_history = [g for g in gate_history if g["status"] == status_filter]
    return (history + gate_history)[: min(limit, 200)]


@router.get("/approvals/sla-stats")
async def get_sla_stats(request: Request) -> dict[str, Any]:
    """SLA compliance stats for HITL approvals, from ``approval_requests``.

    Previously read ``hitl_approval_requests``, which no code path ever writes,
    so every tenant saw all-zero stats. A missing/failed DB is a 503, never a
    zeroed (fake) answer.
    """
    tenant_ctx: TenantContext = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Database not available")

    from app.db.rls import sqlalchemy_rls_context
    from app.governance.hitl_sla import compute_sla_stats

    try:
        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            return await compute_sla_stats(session, tenant_ctx.tenant_id)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Approval SLA stats unavailable"
        ) from exc
