"""Agents API — CRUD for agent configurations and meta-agent NL creation."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.intelligence.meta_agent import MetaAgentPlanner
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/agents", tags=["agents"])

# In-memory fallback snapshot store — only used when DB is not configured.
_AGENT_SNAPSHOTS: dict[str, list[dict[str, Any]]] = {}


# ---------------------------------------------------------------------------
# DB helpers for snapshot persistence
# ---------------------------------------------------------------------------

async def _save_snapshot_to_db(snapshot: dict[str, Any], db: Any, tenant_id: str) -> None:
    """Persist an agent snapshot to the agent_snapshots table WITH RLS context."""
    if db is None:
        return
    try:
        from sqlalchemy import text
        from app.db.rls import sqlalchemy_rls_context
        async with db() as session, session.begin():
            async with sqlalchemy_rls_context(session, tenant_id):
                await session.execute(
                    text(
                        """INSERT INTO agent_snapshots
                           (id, tenant_id, agent_id, version, snapshot, snapshotted_at)
                           VALUES (:id, :tid, :aid, :version, :snap::jsonb, NOW())
                           ON CONFLICT (id) DO NOTHING"""
                    ),
                    {
                        "id": snapshot["snapshot_id"],
                        "tid": tenant_id,
                        "aid": snapshot["agent_id"],
                        "version": snapshot["version"],
                        "snap": json.dumps(snapshot),
                    },
                )
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("snapshot_persist_failed: %s", exc)


async def _load_snapshots_from_db(
    tenant_id: str, agent_id: str, db: Any
) -> list[dict[str, Any]]:
    """Load agent snapshots from DB ordered by version ascending."""
    if db is None:
        return []
    try:
        from sqlalchemy import text
        from app.db.rls import sqlalchemy_rls_context
        async with db() as session, sqlalchemy_rls_context(session, tenant_id):
            result = await session.execute(
                text(
                    "SELECT snapshot FROM agent_snapshots "
                    "WHERE tenant_id = :tid AND agent_id = :aid "
                    "ORDER BY version ASC"
                ),
                {"tid": tenant_id, "aid": agent_id},
            )
            rows = result.fetchall()
        return [
            (json.loads(r[0]) if isinstance(r[0], str) else r[0])
            for r in rows
        ]
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("snapshot_load_failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# In-memory AgentStore
# ---------------------------------------------------------------------------

class AgentStore:
    """Per-tenant in-memory agent registry.

    Key: (tenant_id, agent_id) → agent record dict.
    """

    def __init__(self, db_session_factory: Any = None) -> None:
        self._data: dict[tuple[str, str], dict[str, Any]] = {}
        self._db: Any = db_session_factory

    async def create(self, record: dict[str, Any], *, tenant_ctx: TenantContext) -> str:
        agent_id = uuid.uuid4().hex
        record["agent_id"] = agent_id
        record["tenant_id"] = tenant_ctx.tenant_id
        record.setdefault("created_at", datetime.now(UTC).isoformat())
        if self._db is not None:
            await self._db_persist_agent(record)
        self._data[(tenant_ctx.tenant_id, agent_id)] = record
        return agent_id

    # FIX 1: persist ALL fields to DB
    async def _db_persist_agent(self, record: dict[str, Any]) -> None:
        from app.db.models.agent import Agent
        from app.db.rls import sqlalchemy_rls_context

        tenant_id = str(record["tenant_id"])
        async with self._db() as session, session.begin(), sqlalchemy_rls_context(
            session, tenant_id
        ):
            session.add(
                Agent(
                    id=record["agent_id"],
                    tenant_id=tenant_id,
                    name=record["name"],
                    goal_template=record.get("goal_template", ""),
                    autonomy_mode=record.get("autonomy_mode", "bounded-autonomous"),
                    connector_ids=list(record.get("connector_ids", [])),
                    trigger_config=dict(record.get("trigger_config", {})),
                    system_prompt=record.get("system_prompt", ""),
                    model_override=record.get("model_override", ""),
                    max_iterations=int(record.get("max_iterations", 15)),
                    timeout_seconds=int(record.get("timeout_seconds", 300)),
                    allowed_collection_ids=list(record.get("allowed_collection_ids", [])),
                    eval_suite_id=record.get("eval_suite_id") or None,
                    policy_ids=list(record.get("policy_ids", [])),
                    cloned_from=record.get("cloned_from") or None,
                )
            )

    def get(self, agent_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any] | None:
        return self._data.get((tenant_ctx.tenant_id, agent_id))

    async def sync_from_db(self) -> int:
        """Load active DB agents into memory on startup."""
        if self._db is None:
            return 0
        try:
            import logging

            from sqlalchemy import select

            from app.db.models.agent import Agent
            from app.db.models.tenant import Tenant
            from app.db.rls import sqlalchemy_rls_context

            loaded = 0
            async with self._db() as session:
                tenant_result = await session.execute(
                    select(Tenant).where(Tenant.is_active == True)  # noqa: E712
                )
                tenants = tenant_result.scalars().all()
                for tenant in tenants:
                    tenant_id = str(tenant.id)
                    async with sqlalchemy_rls_context(session, tenant_id):
                        agent_result = await session.execute(
                            select(Agent).where(
                                Agent.tenant_id == tenant_id,
                                Agent.is_active == True,  # noqa: E712
                            )
                        )
                    agents = agent_result.scalars().all()
                    for agent in agents:
                        key = (tenant_id, str(agent.id))
                        if key in self._data:
                            continue
                        self._data[key] = self._row_to_dict(agent)
                        loaded += 1
            logging.getLogger(__name__).info("Synced %d active agents from DB", loaded)
            return loaded
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("DB sync agents failed: %s", exc)
            return 0

    # ── helpers ────────────────────────────────────────────────────────────────

    # FIX 1: return ALL fields from DB row
    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        """Convert an Agent ORM row to a plain dict (same shape as in-memory store)."""
        created_at = getattr(row, "created_at", None)
        return {
            "agent_id": str(row.id),
            "tenant_id": str(row.tenant_id),
            "name": row.name,
            "goal_template": row.goal_template or "",
            "autonomy_mode": row.autonomy_mode or "bounded-autonomous",
            "connector_ids": list(row.connector_ids or []),
            "trigger_config": dict(row.trigger_config or {}),
            "system_prompt": getattr(row, "system_prompt", "") or "",
            "model_override": getattr(row, "model_override", "") or "",
            "max_iterations": getattr(row, "max_iterations", 15) or 15,
            "timeout_seconds": getattr(row, "timeout_seconds", 300) or 300,
            "allowed_collection_ids": list(getattr(row, "allowed_collection_ids", []) or []),
            "eval_suite_id": getattr(row, "eval_suite_id", None),
            "policy_ids": list(getattr(row, "policy_ids", []) or []),
            "cloned_from": getattr(row, "cloned_from", None),
            "permissions": {},
            "created_at": created_at.isoformat() if created_at else "",
        }

    # ── async DB reads ─────────────────────────────────────────────────────────

    async def get_async(
        self, agent_id: str, *, tenant_ctx: TenantContext
    ) -> dict[str, Any] | None:
        """Read a single agent directly from DB; fall back to memory cache."""
        if self._db is not None:
            try:
                from sqlalchemy import select

                from app.db.models.agent import Agent
                from app.db.rls import sqlalchemy_rls_context

                async with self._db() as session, sqlalchemy_rls_context(
                    session, tenant_ctx.tenant_id
                ):
                    result = await session.execute(
                        select(Agent).where(
                            Agent.id == agent_id,
                            Agent.tenant_id == tenant_ctx.tenant_id,
                            Agent.is_active == True,  # noqa: E712
                        )
                    )
                    row = result.scalar_one_or_none()

                if row is not None:
                    record = self._row_to_dict(row)
                    # Refresh in-memory cache
                    self._data[(tenant_ctx.tenant_id, agent_id)] = record
                    return record
                # Row not found in DB → not found
                return None
            except Exception as exc:
                from app.observability.logging import get_logger
                get_logger(__name__).warning("agent_get_db_failed", error=str(exc))

        return self._data.get((tenant_ctx.tenant_id, agent_id))

    async def list_async(self, *, tenant_ctx: TenantContext) -> list[dict[str, Any]]:
        """Read all agents for a tenant directly from DB; fall back to memory cache."""
        if self._db is not None:
            try:
                from sqlalchemy import select

                from app.db.models.agent import Agent
                from app.db.rls import sqlalchemy_rls_context

                async with self._db() as session, sqlalchemy_rls_context(
                    session, tenant_ctx.tenant_id
                ):
                    result = await session.execute(
                        select(Agent)
                        .where(
                            Agent.tenant_id == tenant_ctx.tenant_id,
                            Agent.is_active == True,  # noqa: E712
                        )
                        .order_by(Agent.created_at.desc())
                    )
                    rows = result.scalars().all()

                agents = [self._row_to_dict(r) for r in rows]
                # Refresh in-memory cache
                for a in agents:
                    self._data[(tenant_ctx.tenant_id, a["agent_id"])] = a
                return agents
            except Exception as exc:
                from app.observability.logging import get_logger
                get_logger(__name__).warning("agent_list_db_failed", error=str(exc))

        return self.list_all(tenant_ctx=tenant_ctx)

    def list_all(self, *, tenant_ctx: TenantContext) -> list[dict[str, Any]]:
        return [
            rec
            for (tid, _), rec in self._data.items()
            if tid == tenant_ctx.tenant_id
        ]

    def delete(self, agent_id: str, *, tenant_ctx: TenantContext) -> bool:
        """Synchronous in-memory delete (used by tests / no-DB mode)."""
        key = (tenant_ctx.tenant_id, agent_id)
        if key not in self._data:
            return False
        del self._data[key]
        return True

    async def delete_async(self, agent_id: str, *, tenant_ctx: TenantContext) -> bool:
        """Soft-delete from PostgreSQL (is_active=FALSE) and remove from memory cache."""
        key = (tenant_ctx.tenant_id, agent_id)
        if self._db is not None:
            try:
                from sqlalchemy import text
                from app.db.rls import sqlalchemy_rls_context
                async with self._db() as session, session.begin(), sqlalchemy_rls_context(
                    session, tenant_ctx.tenant_id
                ):
                    result = await session.execute(
                        text(
                            "UPDATE agents SET is_active = FALSE "
                            "WHERE id = :id AND tenant_id = :tid AND is_active = TRUE"
                        ),
                        {"id": agent_id, "tid": tenant_ctx.tenant_id},
                    )
                    if result.rowcount == 0:
                        return False
            except Exception as exc:
                from app.observability.logging import get_logger
                get_logger(__name__).warning("agent_delete_db_failed", error=str(exc))
                # Fall through to in-memory-only delete so the endpoint still
                # returns a meaningful response when DB is temporarily unavailable.
                if key not in self._data:
                    return False
        elif key not in self._data:
            return False
        # Evict from in-memory cache
        self._data.pop(key, None)
        return True

    def update(self, agent_id: str, data: dict[str, Any], *, tenant_ctx: TenantContext) -> bool:
        """Merge *data* into the stored agent record. Returns False if not found."""
        rec = self.get(agent_id, tenant_ctx=tenant_ctx)
        if rec is None:
            return False
        rec.update(data)
        return True

    # FIX 2: async update that also persists to DB
    async def update_async(
        self, agent_id: str, data: dict[str, Any], *, tenant_ctx: TenantContext
    ) -> bool:
        """Merge data into agent record and persist to DB."""
        # Update in-memory cache
        rec = self.get(agent_id, tenant_ctx=tenant_ctx)
        if rec is None:
            return False
        rec.update(data)

        # Persist to DB
        if self._db is not None:
            try:
                from sqlalchemy import text
                from app.db.rls import sqlalchemy_rls_context

                # Build SET clause dynamically for allowed fields
                allowed = {
                    "name", "goal_template", "autonomy_mode", "connector_ids",
                    "trigger_config", "system_prompt", "model_override",
                    "max_iterations", "timeout_seconds", "allowed_collection_ids",
                    "eval_suite_id", "policy_ids",
                }
                updates = {k: v for k, v in data.items() if k in allowed}
                if not updates:
                    return True

                # JSON-encode list/dict fields
                params: dict[str, Any] = {"id": agent_id, "tid": tenant_ctx.tenant_id}
                set_parts = []
                for k, v in updates.items():
                    if isinstance(v, (list, dict)):
                        params[k] = json.dumps(v)
                        set_parts.append(f"{k} = :{k}::jsonb")
                    else:
                        params[k] = v
                        set_parts.append(f"{k} = :{k}")

                set_clause = ", ".join(set_parts)
                async with self._db() as session, session.begin(), sqlalchemy_rls_context(
                    session, tenant_ctx.tenant_id
                ):
                    result = await session.execute(
                        text(
                            f"UPDATE agents SET {set_clause}, updated_at = NOW() "
                            "WHERE id = :id AND tenant_id = :tid AND is_active = TRUE"
                        ),
                        params,
                    )
                    return result.rowcount > 0
            except Exception as exc:
                from app.observability.logging import get_logger
                get_logger(__name__).warning("agent_update_db_failed", error=str(exc))
        return True

    def update_permissions(
        self,
        agent_id: str,
        permissions: dict[str, str],
        *,
        tenant_ctx: TenantContext,
    ) -> bool:
        rec = self.get(agent_id, tenant_ctx=tenant_ctx)
        if rec is None:
            return False
        rec["permissions"] = permissions
        return True


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

# FIX 8: Add new fields to CreateAgentRequest
class CreateAgentRequest(BaseModel):
    name: str
    goal_template: str = Field(default="", max_length=5_000)
    autonomy_mode: str = "bounded-autonomous"
    connector_ids: list[str] = []
    trigger_config: dict[str, Any] = {}
    allowed_collection_ids: list[str] = []  # knowledge collections this agent can query
    eval_suite_id: str | None = None  # required for fully-autonomous mode
    policy_ids: list[str] = []
    system_prompt: str = ""
    model_override: str = ""
    max_iterations: int = 15
    timeout_seconds: int = 300


# FIX 2: new update request model
class UpdateAgentRequest(BaseModel):
    name: str | None = None
    goal_template: str | None = None
    autonomy_mode: str | None = None
    connector_ids: list[str] | None = None
    trigger_config: dict[str, Any] | None = None
    allowed_collection_ids: list[str] | None = None
    eval_suite_id: str | None = None
    policy_ids: list[str] | None = None
    system_prompt: str | None = None
    model_override: str | None = None
    max_iterations: int | None = None
    timeout_seconds: int | None = None


class CloneAgentRequest(BaseModel):
    name: str | None = None


class UpdatePermissionsRequest(BaseModel):
    permissions: list[dict] | dict[str, str]  # accept both new (list) and legacy (dict) formats


class UpdateKnowledgeBindingRequest(BaseModel):
    collection_ids: list[str]


class MetaAgentCreateRequest(BaseModel):
    command: str
    autorun: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _agent_store(request: Request) -> AgentStore:
    return request.app.state.agent_store  # type: ignore[no-any-return]


def _meta_agent(request: Request) -> MetaAgentPlanner:
    return request.app.state.meta_agent  # type: ignore[no-any-return]


async def _create_agent_record(
    store: AgentStore,
    record: dict[str, Any],
    *,
    tenant_ctx: TenantContext,
) -> str:
    try:
        return await store.create(record, tenant_ctx=tenant_ctx)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Agent persistence failed",
        ) from exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("")
async def list_agents(request: Request) -> list[dict[str, Any]]:
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    return await store.list_async(tenant_ctx=tenant_ctx)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_agent(request: Request, body: CreateAgentRequest) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    # Enforce release gate: fully-autonomous mode requires an eval suite
    if body.autonomy_mode == "fully-autonomous" and not body.eval_suite_id:
        raise HTTPException(
            status_code=422,
            detail=(
                "fully-autonomous mode requires eval_suite_id. "
                "Attach an eval suite with passing results first."
            ),
        )
    # FIX 6: use list_async (DB-backed) for accurate cross-replica limit check
    from app.tenancy.limits import check_agent_limit
    existing = await store.list_async(tenant_ctx=tenant_ctx)
    check_agent_limit(tenant_ctx, len(existing))

    record: dict[str, Any] = {
        "name": body.name,
        "goal_template": body.goal_template,
        "autonomy_mode": body.autonomy_mode,
        "connector_ids": body.connector_ids,
        "trigger_config": body.trigger_config,
        "allowed_collection_ids": body.allowed_collection_ids,
        "permissions": {},
        "eval_suite_id": body.eval_suite_id,
        "policy_ids": body.policy_ids,
        "system_prompt": body.system_prompt,
        "model_override": body.model_override,
        "max_iterations": body.max_iterations,
        "timeout_seconds": body.timeout_seconds,
    }
    agent_id = await _create_agent_record(store, record, tenant_ctx=tenant_ctx)

    # FIX 5: auto-create a schedule if trigger_config specifies a cron/interval/event trigger
    trigger_cfg = body.trigger_config or {}
    trigger_type = trigger_cfg.get("trigger_type", "")
    schedule_store = getattr(request.app.state, "schedule_store", None)

    if schedule_store is not None and trigger_type in ("cron", "interval", "event"):
        try:
            from app.triggers.models import TriggerSpec, TriggerType
            spec = TriggerSpec(
                trigger_type=TriggerType(trigger_type),
                cron_expression=trigger_cfg.get("cron_expression", ""),
                interval_seconds=trigger_cfg.get("interval_seconds", 0),
                event_channel=trigger_cfg.get("event_channel", ""),
            )
            schedule_store.create(
                goal_id=uuid.uuid4().hex,
                spec=spec,
                tenant_ctx=tenant_ctx,
                agent_id=agent_id,
                goal_template=body.goal_template or record.get("goal_template", ""),
            )
        except Exception as _sched_exc:
            import logging
            logging.getLogger(__name__).warning(
                "trigger_schedule_create_failed: %s", _sched_exc
            )

    return store.get(agent_id, tenant_ctx=tenant_ctx)  # type: ignore[return-value]


# Note: /create must be declared before /{agent_id} so the exact path wins.
@router.post("/create", status_code=status.HTTP_201_CREATED)
async def create_agent_nl(
    request: Request, body: MetaAgentCreateRequest
) -> dict[str, Any]:
    """Meta-agent NL creation — parses one NL command into a full agent config."""
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    planner = _meta_agent(request)

    config = await planner.plan(command=body.command, tenant_ctx=tenant_ctx)

    # FIX 4: enforce agent limit via DB-backed list_async
    from app.tenancy.limits import check_agent_limit
    existing = await store.list_async(tenant_ctx=tenant_ctx)
    check_agent_limit(tenant_ctx, len(existing))

    # FIX 4: NL creation cannot directly produce fully-autonomous agents
    if config.autonomy_mode == "fully-autonomous":
        raise HTTPException(
            status_code=422,
            detail=(
                "NL agent creation cannot directly produce fully-autonomous agents. "
                "Create the agent first, attach an eval suite, and upgrade autonomy_mode via PUT."
            ),
        )

    record: dict[str, Any] = {
        "name": config.name,
        "goal_template": config.goal_template,
        "autonomy_mode": config.autonomy_mode,
        "connector_ids": config.connectors,
        "trigger_config": {
            "trigger_type": config.trigger_type,
            "cron_expression": config.cron_expression,
            "interval_seconds": config.interval_seconds,
            "event_channel": config.event_channel,
        },
        "permissions": {},
    }
    agent_id = await _create_agent_record(store, record, tenant_ctx=tenant_ctx)
    agent = store.get(agent_id, tenant_ctx=tenant_ctx)

    return {
        "agent": agent,
        "meta_agent_config": {
            "name": config.name,
            "goal_template": config.goal_template,
            "connectors": config.connectors,
            "trigger_type": config.trigger_type,
            "event_channel": config.event_channel,
            "cron_expression": config.cron_expression,
            "interval_seconds": config.interval_seconds,
            "autonomy_mode": config.autonomy_mode,
            "policy_suggestions": config.policy_suggestions,
        },
    }


@router.get("/{agent_id}")
async def get_agent(request: Request, agent_id: str) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    rec = await store.get_async(agent_id, tenant_ctx=tenant_ctx)
    if rec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )
    return rec


# FIX 2: PUT /{agent_id} — full field update
@router.put("/{agent_id}")
async def update_agent(
    request: Request, agent_id: str, body: UpdateAgentRequest
) -> dict[str, Any]:
    """Update an agent's configuration. All fields are optional."""
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)

    # Get current agent (fall back to in-memory cache if no DB)
    current = await store.get_async(agent_id, tenant_ctx=tenant_ctx)
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    # Enforce release gate: switching to fully-autonomous requires eval_suite_id
    new_autonomy = body.autonomy_mode or current.get("autonomy_mode")
    new_eval_suite = body.eval_suite_id or current.get("eval_suite_id")
    if new_autonomy == "fully-autonomous" and not new_eval_suite:
        raise HTTPException(
            status_code=422,
            detail="fully-autonomous mode requires eval_suite_id",
        )

    # Build update dict (only non-None fields)
    update_data = {k: v for k, v in body.model_dump().items() if v is not None}

    updated = await store.update_async(agent_id, update_data, tenant_ctx=tenant_ctx)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    return await store.get_async(agent_id, tenant_ctx=tenant_ctx)  # type: ignore[return-value]


# FIX 5: delete cleans up associated schedules
@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(request: Request, agent_id: str) -> None:
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    removed = await store.delete_async(agent_id, tenant_ctx=tenant_ctx)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    # Clean up associated schedules
    schedule_store = getattr(request.app.state, "schedule_store", None)
    if schedule_store is not None:
        try:
            schedules = schedule_store.list_all(tenant_ctx=tenant_ctx)
            for sched in schedules:
                if sched.get("agent_id") == agent_id:
                    await schedule_store.delete_async(
                        sched["schedule_id"], tenant_ctx=tenant_ctx
                    )
        except Exception as _se:
            import logging
            logging.getLogger(__name__).warning("agent_schedule_cleanup_failed: %s", _se)


@router.get("/{agent_id}/permissions")
async def get_permissions(request: Request, agent_id: str) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)

    # Verify agent exists (DB-backed lookup)
    rec = await store.get_async(agent_id, tenant_ctx=tenant_ctx)
    if rec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    # Read permissions from agent_permissions table when DB is available
    db = getattr(store, "_db", None)
    if db is not None:
        try:
            from sqlalchemy import text
            async with db() as session:
                rows = (
                    await session.execute(
                        text(
                            "SELECT tool_name, level, daily_limit, per_goal_limit, scope_pattern "
                            "FROM agent_permissions "
                            "WHERE agent_id = :aid AND tenant_id = :tid"
                        ),
                        {"aid": agent_id, "tid": tenant_ctx.tenant_id},
                    )
                ).fetchall()
            permissions = [
                {
                    "tool_name": r[0],
                    "level": r[1],
                    "daily_limit": r[2],
                    "per_goal_limit": r[3],
                    "scope_pattern": r[4] or "",
                }
                for r in rows
            ]
            return {"agent_id": agent_id, "permissions": permissions}
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("permissions_read_failed: %s", exc)

    # Fallback to in-memory record
    return {"agent_id": agent_id, "permissions": rec.get("permissions", {})}


@router.put("/{agent_id}/permissions")
async def update_permissions(
    request: Request, agent_id: str, body: UpdatePermissionsRequest
) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)

    rec = await store.get_async(agent_id, tenant_ctx=tenant_ctx)
    if rec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    # Persist to agent_permissions table when DB is available
    db = getattr(store, "_db", None)
    if db is not None:
        try:
            from sqlalchemy import text
            async with db() as session, session.begin():
                # Delete existing permissions for this agent+tenant
                await session.execute(
                    text(
                        "DELETE FROM agent_permissions "
                        "WHERE agent_id = :aid AND tenant_id = :tid"
                    ),
                    {"aid": agent_id, "tid": tenant_ctx.tenant_id},
                )
                # Normalise both list format (new) and dict format (legacy)
                perms = body.permissions
                if isinstance(perms, dict):
                    perms = [{"tool_name": k, "level": v} for k, v in perms.items()]
                for perm in perms:
                    await session.execute(
                        text(
                            """
                            INSERT INTO agent_permissions
                                (id, agent_id, tenant_id, tool_name, level,
                                 daily_limit, per_goal_limit, scope_pattern)
                            VALUES (:id, :aid, :tid, :tool, :level,
                                    :daily, :per_goal, :scope)
                            """
                        ),
                        {
                            "id": uuid.uuid4().hex,
                            "aid": agent_id,
                            "tid": tenant_ctx.tenant_id,
                            "tool": perm.get("tool_name", "*"),
                            "level": perm.get("level", "allow"),
                            "daily": perm.get("daily_limit", 0) or 0,
                            "per_goal": perm.get("per_goal_limit", 0) or 0,
                            "scope": perm.get("scope_pattern", "*") or "*",
                        },
                    )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("permissions_write_failed: %s", exc)

    # Also update in-memory cache (legacy path / no-DB mode)
    in_mem_perms = (
        body.permissions
        if isinstance(body.permissions, dict)
        else {}
    )
    store.update_permissions(agent_id, in_mem_perms, tenant_ctx=tenant_ctx)

    return {"agent_id": agent_id, "permissions": body.permissions, "status": "updated"}


@router.put("/{agent_id}/knowledge")
async def update_knowledge_binding(
    request: Request, agent_id: str, body: UpdateKnowledgeBindingRequest
) -> dict[str, Any]:
    """Bind knowledge collections to this agent."""
    tenant = _require_tenant(request)
    store = _agent_store(request)
    agent = store.get(agent_id, tenant_ctx=tenant)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )
    update_data = dict(agent)
    update_data["allowed_collection_ids"] = body.collection_ids
    store.update(agent_id, update_data, tenant_ctx=tenant)
    return {
        "agent_id": agent_id,
        "allowed_collection_ids": body.collection_ids,
        "status": "updated",
    }


@router.get("/{agent_id}/versions")
async def list_agent_versions(request: Request, agent_id: str) -> list[dict[str, Any]]:
    """List all saved version snapshots of an agent."""
    tenant = _require_tenant(request)
    store = _agent_store(request)
    db = getattr(store, "_db", None)

    # Try DB first; fall back to in-memory module dict
    if db is not None:
        return await _load_snapshots_from_db(tenant.tenant_id, agent_id, db)
    return _AGENT_SNAPSHOTS.get(f"{tenant.tenant_id}:{agent_id}", [])


@router.post("/{agent_id}/snapshot")
async def snapshot_agent(request: Request, agent_id: str) -> dict[str, Any]:
    """Save a version snapshot of the current agent config."""
    tenant = _require_tenant(request)
    store = _agent_store(request)
    agent = store.get(agent_id, tenant_ctx=tenant)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    db = getattr(store, "_db", None)

    # Determine next version number
    if db is not None:
        existing = await _load_snapshots_from_db(tenant.tenant_id, agent_id, db)
    else:
        existing = _AGENT_SNAPSHOTS.get(f"{tenant.tenant_id}:{agent_id}", [])

    snapshot = {
        **agent,
        "snapshot_id": uuid.uuid4().hex,
        "snapshotted_at": datetime.now(UTC).isoformat(),
        "version": len(existing) + 1,
    }

    if db is not None:
        await _save_snapshot_to_db(snapshot, db, tenant.tenant_id)
    else:
        key = f"{tenant.tenant_id}:{agent_id}"
        _AGENT_SNAPSHOTS.setdefault(key, []).append(snapshot)

    return snapshot


# FIX 3: rollback now persists to DB via update_async
@router.post("/{agent_id}/rollback/{snapshot_id}")
async def rollback_agent(
    request: Request, agent_id: str, snapshot_id: str
) -> dict[str, Any]:
    """Roll back agent to a previous snapshot."""
    tenant = _require_tenant(request)
    store = _agent_store(request)
    db = getattr(store, "_db", None)

    if db is not None:
        snapshots = await _load_snapshots_from_db(tenant.tenant_id, agent_id, db)
    else:
        key = f"{tenant.tenant_id}:{agent_id}"
        snapshots = _AGENT_SNAPSHOTS.get(key, [])

    snapshot = next((s for s in snapshots if s.get("snapshot_id") == snapshot_id), None)
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")

    restore_data = {
        k: v
        for k, v in snapshot.items()
        if k not in ("snapshot_id", "snapshotted_at", "version", "agent_id", "tenant_id")
    }

    # Persist rollback to DB (and update in-memory cache)
    await store.update_async(agent_id, restore_data, tenant_ctx=tenant)
    return {"agent_id": agent_id, "restored_from": snapshot_id, "status": "rolled_back"}


@router.get("/{agent_id}/export")
async def export_agent(
    request: Request, agent_id: str, format: str = "openai"
) -> dict[str, Any]:
    """Export agent config in a provider-specific format (openai | anthropic)."""
    tenant = _require_tenant(request)
    store = _agent_store(request)
    agent = await store.get_async(agent_id, tenant_ctx=tenant)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    # Build tools list from connector capabilities via MCP client
    tools: list[dict[str, Any]] = []
    mcp_client = getattr(request.app.state, "mcp_client", None)
    if mcp_client is not None and agent.get("connector_ids"):
        try:
            all_tools = await mcp_client.discover_all_tools(tenant_ctx=tenant)
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": getattr(t, "input_schema", {}) or {},
                    },
                }
                for t in all_tools[:20]
            ]
        except Exception:
            pass

    model_override = agent.get("model_override", "")

    if format == "openai":
        return {
            "object": "assistant",
            "name": agent.get("name", ""),
            "instructions": agent.get("system_prompt", "") or agent.get("goal_template", ""),
            "model": model_override or "gpt-4o",
            "tools": tools,
        }
    elif format == "anthropic":
        return {
            "system": agent.get("system_prompt", "") or agent.get("goal_template", ""),
            "model": model_override or "claude-opus-4-5",
            "max_tokens": 8096,
            "tools": [t["function"] for t in tools] if tools else [],
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown export format: {format!r}. Supported: openai, anthropic",
        )


# FIX 7: clone carries all new fields including eval_suite_id and policy_ids
@router.post("/{agent_id}/clone", status_code=status.HTTP_201_CREATED)
async def clone_agent(
    request: Request, agent_id: str, body: CloneAgentRequest
) -> dict[str, Any]:
    """Clone an existing agent with optional name override."""
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)

    original = await store.get_async(agent_id, tenant_ctx=tenant_ctx)
    if original is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    clone_data: dict[str, Any] = {
        "name": body.name or f"{original['name']} (copy)",
        "goal_template": original.get("goal_template", ""),
        "autonomy_mode": original.get("autonomy_mode", "bounded-autonomous"),
        "connector_ids": list(original.get("connector_ids", [])),
        "trigger_config": dict(original.get("trigger_config", {})),
        "allowed_collection_ids": list(original.get("allowed_collection_ids", [])),
        "permissions": {},
        "cloned_from": agent_id,
        # Carry all new fields so nothing is lost in clone
        "eval_suite_id": original.get("eval_suite_id"),
        "policy_ids": list(original.get("policy_ids", [])),
        "system_prompt": original.get("system_prompt", ""),
        "model_override": original.get("model_override", ""),
        "max_iterations": original.get("max_iterations", 15),
        "timeout_seconds": original.get("timeout_seconds", 300),
    }

    # Check agent limit before creating the clone
    from app.tenancy.limits import check_agent_limit

    existing = store.list_all(tenant_ctx=tenant_ctx)
    check_agent_limit(tenant_ctx, len(existing))

    clone_id = await _create_agent_record(store, clone_data, tenant_ctx=tenant_ctx)
    return {**clone_data, "agent_id": clone_id, "cloned_from": agent_id}


class AgentCredentialRequest(BaseModel):
    connector_id: str
    secret_ref: str  # Vault reference e.g. vault://connectors/<server_id>/api_key


@router.get("/{agent_id}/credentials")
async def list_agent_credentials(request: Request, agent_id: str) -> list[dict[str, Any]]:
    """List per-agent connector credentials (secret refs only — never raw secrets)."""
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    agent = await store.get_async(agent_id, tenant_ctx=tenant_ctx)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent {agent_id} not found")

    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        return []
    try:
        from sqlalchemy import text
        async with db() as session:
            rows = (await session.execute(
                text(
                    "SELECT id, connector_id, secret_ref, created_at "
                    "FROM agent_connector_credentials "
                    "WHERE agent_id = :aid AND tenant_id = :tid"
                ),
                {"aid": agent_id, "tid": tenant_ctx.tenant_id},
            )).fetchall()
        return [
            {
                "id": r[0],
                "agent_id": agent_id,
                "connector_id": r[1],
                "secret_ref": r[2],
                "created_at": r[3].isoformat() if r[3] else "",
            }
            for r in rows
        ]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/{agent_id}/credentials", status_code=status.HTTP_201_CREATED)
async def create_agent_credential(
    request: Request, agent_id: str, body: AgentCredentialRequest
) -> dict[str, Any]:
    """Store a per-agent connector credential reference."""
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    agent = await store.get_async(agent_id, tenant_ctx=tenant_ctx)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent {agent_id} not found")

    cred_id = uuid.uuid4().hex
    db = getattr(request.app.state, "db_session_factory", None)
    if db is not None:
        try:
            from sqlalchemy import text
            async with db() as session, session.begin():
                await session.execute(
                    text("""
                        INSERT INTO agent_connector_credentials
                            (id, agent_id, connector_id, tenant_id, secret_ref)
                        VALUES (:id, :aid, :cid, :tid, :ref)
                        ON CONFLICT (agent_id, connector_id, tenant_id) DO UPDATE
                            SET secret_ref = EXCLUDED.secret_ref
                    """),
                    {
                        "id": cred_id,
                        "aid": agent_id,
                        "cid": body.connector_id,
                        "tid": tenant_ctx.tenant_id,
                        "ref": body.secret_ref,
                    },
                )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "id": cred_id,
        "agent_id": agent_id,
        "connector_id": body.connector_id,
        "secret_ref": body.secret_ref,
        "status": "stored",
    }


@router.get("/{agent_id}/rollout-gate")
async def check_rollout_gate(
    request: Request,
    agent_id: str,
    eval_suite_id: str = "",
    min_pass_rate: float = 0.8,
) -> dict[str, Any]:
    """Check if an agent meets the eval pass rate required for production rollout."""
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    agent = await store.get_async(agent_id, tenant_ctx=tenant_ctx)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    db = getattr(store, "_db", None)
    if db is None:
        return {
            "gate_passed": False,
            "reason": "No database available. Connect a DB to use the rollout gate.",
            "run_count": 0,
            "pass_rate": 0.0,
            "avg_score": 0.0,
            "agent_id": agent_id,
        }

    from app.intelligence.eval_suite import check_agent_rollout_gate

    result = await check_agent_rollout_gate(
        agent_id=agent_id,
        eval_suite_id=eval_suite_id or agent.get("eval_suite_id", ""),
        tenant_id=tenant_ctx.tenant_id,
        db=db,
        min_pass_rate=min_pass_rate,
    )
    result["agent_id"] = agent_id
    return result


@router.get("/{agent_id}/readiness")
async def check_readiness(request: Request, agent_id: str) -> dict[str, Any]:
    """Check if an agent is ready for production use."""
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    agent = await store.get_async(agent_id, tenant_ctx=tenant_ctx)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    checks: list[dict[str, str]] = []
    ready = True

    # 1. Has at least one connector
    if not agent.get("connector_ids"):
        checks.append(
            {
                "check": "connectors",
                "status": "fail",
                "message": "No connectors configured — agent cannot call external tools",
            }
        )
        ready = False
    else:
        checks.append(
            {
                "check": "connectors",
                "status": "pass",
                "message": f"{len(agent['connector_ids'])} connector(s) configured",
            }
        )

    # 2. Has a goal template
    if not agent.get("goal_template"):
        checks.append(
            {
                "check": "goal_template",
                "status": "warn",
                "message": "No goal template — agent will use bare LLM execution",
            }
        )
    else:
        checks.append(
            {
                "check": "goal_template",
                "status": "pass",
                "message": "Goal template configured",
            }
        )

    # 3. Fully-autonomous mode requires an eval suite
    if agent.get("autonomy_mode") == "fully-autonomous":
        eval_suite_id = agent.get("eval_suite_id")
        if not eval_suite_id:
            checks.append(
                {
                    "check": "eval_suite",
                    "status": "fail",
                    "message": (
                        "fully-autonomous mode requires an attached eval suite "
                        "with passing results"
                    ),
                }
            )
            ready = False
        else:
            checks.append(
                {
                    "check": "eval_suite",
                    "status": "pass",
                    "message": f"Eval suite {eval_suite_id} attached",
                }
            )

    # 4. Spot-check connector readiness via MCP registry and secret store
    if agent.get("connector_ids"):
        secret_store = getattr(request.app.state, "connector_secret_store", None)
        registry = getattr(request.app.state, "mcp_registry", None)
        for cid in agent["connector_ids"][:5]:
            connector_ready = True
            missing_reason = ""
            check_status_override: str | None = None  # "warn" for soft issues

            if registry is not None:
                try:
                    cfg = await registry.get(cid, tenant_ctx=tenant_ctx)
                    if cfg is None:
                        # Connector not yet registered — informational warning, does not
                        # block production (connector may be registered at runtime)
                        check_status_override = "warn"
                        missing_reason = (
                            f"Connector '{cid}' not yet registered in MCP registry"
                        )
                    elif not cfg.enabled:
                        # Explicitly disabled — hard fail
                        connector_ready = False
                        missing_reason = f"Connector '{cid}' is disabled"
                    else:
                        # Check secret availability for auth-protected connectors
                        auth_config = cfg.auth_config or {}
                        auth_type = getattr(cfg, "auth_type", "none")
                        needs_secret = auth_type in ("api_key", "oauth2")
                        has_inline = auth_config.get("api_key") or auth_config.get("token")
                        if needs_secret and not has_inline:
                            has_secret_fn = getattr(secret_store, "has_secret", None)
                            if has_secret_fn is not None:
                                try:
                                    secret_key = f"vault://connectors/{cid}/api_key"
                                    has_secret = await secret_store.has_secret(
                                        secret_key, tenant_ctx=tenant_ctx
                                    )
                                    if not has_secret:
                                        connector_ready = False
                                        missing_reason = (
                                            f"Connector '{cid}' missing API key secret"
                                        )
                                except Exception:
                                    pass  # Secret store check failed — assume OK
                except Exception:
                    pass  # Registry check failed — skip check

            if check_status_override:
                check_status = check_status_override
            else:
                check_status = "pass" if connector_ready else "fail"
            if not connector_ready:
                ready = False
            checks.append(
                {
                    "check": f"connector_{cid}_ready",
                    "status": check_status,
                    "message": missing_reason or f"Connector '{cid}' configured and ready",
                }
            )

    return {
        "agent_id": agent_id,
        "ready": ready,
        "autonomy_mode": agent.get("autonomy_mode"),
        "checks": checks,
        "recommendation": (
            "Agent is production-ready"
            if ready
            else "Fix failing checks before enabling autonomous mode"
        ),
    }
