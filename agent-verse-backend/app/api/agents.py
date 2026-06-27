"""Agents API — CRUD for agent configurations and meta-agent NL creation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.intelligence.meta_agent import MetaAgentPlanner
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/agents", tags=["agents"])

# Module-level in-memory snapshot store (production: DB table)
_AGENT_SNAPSHOTS: dict[str, list[dict[str, Any]]] = {}


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
                    goal_template=record["goal_template"],
                    autonomy_mode=record["autonomy_mode"],
                    connector_ids=list(record.get("connector_ids", [])),
                    trigger_config=dict(record.get("trigger_config", {})),
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
                        created_at = getattr(agent, "created_at", None)
                        self._data[key] = {
                            "agent_id": str(agent.id),
                            "tenant_id": tenant_id,
                            "name": agent.name,
                            "goal_template": agent.goal_template,
                            "autonomy_mode": agent.autonomy_mode,
                            "connector_ids": list(agent.connector_ids or []),
                            "trigger_config": dict(agent.trigger_config or {}),
                            "permissions": {},
                            "created_at": created_at.isoformat() if created_at else "",
                        }
                        loaded += 1
            logging.getLogger(__name__).info("Synced %d active agents from DB", loaded)
            return loaded
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("DB sync agents failed: %s", exc)
            return 0

    # ── helpers ────────────────────────────────────────────────────────────────

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
        key = (tenant_ctx.tenant_id, agent_id)
        if key not in self._data:
            return False
        del self._data[key]
        return True

    def update(self, agent_id: str, data: dict[str, Any], *, tenant_ctx: TenantContext) -> bool:
        """Merge *data* into the stored agent record. Returns False if not found."""
        rec = self.get(agent_id, tenant_ctx=tenant_ctx)
        if rec is None:
            return False
        rec.update(data)
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

class CreateAgentRequest(BaseModel):
    name: str
    goal_template: str = Field(default="", max_length=5_000)
    autonomy_mode: str = "bounded-autonomous"
    connector_ids: list[str] = []
    trigger_config: dict[str, Any] = {}
    allowed_collection_ids: list[str] = []  # knowledge collections this agent can query


class UpdatePermissionsRequest(BaseModel):
    permissions: dict[str, str]


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
    from app.tenancy.limits import check_agent_limit
    existing = store.list_all(tenant_ctx=tenant_ctx)
    check_agent_limit(tenant_ctx, len(existing))
    record: dict[str, Any] = {
        "name": body.name,
        "goal_template": body.goal_template,
        "autonomy_mode": body.autonomy_mode,
        "connector_ids": body.connector_ids,
        "trigger_config": body.trigger_config,
        "allowed_collection_ids": body.allowed_collection_ids,
        "permissions": {},
    }
    agent_id = await _create_agent_record(store, record, tenant_ctx=tenant_ctx)
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


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(request: Request, agent_id: str) -> None:
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    removed = store.delete(agent_id, tenant_ctx=tenant_ctx)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )


@router.get("/{agent_id}/permissions")
async def get_permissions(request: Request, agent_id: str) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    rec = store.get(agent_id, tenant_ctx=tenant_ctx)
    if rec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )
    return {"agent_id": agent_id, "permissions": rec.get("permissions", {})}


@router.put("/{agent_id}/permissions")
async def update_permissions(
    request: Request, agent_id: str, body: UpdatePermissionsRequest
) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    updated = store.update_permissions(agent_id, body.permissions, tenant_ctx=tenant_ctx)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )
    return {"agent_id": agent_id, "permissions": body.permissions}


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
    snapshots = _AGENT_SNAPSHOTS.get(f"{tenant.tenant_id}:{agent_id}", [])
    return snapshots


@router.post("/{agent_id}/snapshot")
async def snapshot_agent(request: Request, agent_id: str) -> dict[str, Any]:
    """Save a version snapshot of the current agent config."""
    tenant = _require_tenant(request)
    store = _agent_store(request)
    agent = store.get(agent_id, tenant_ctx=tenant)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    key = f"{tenant.tenant_id}:{agent_id}"
    snapshot = {
        **agent,
        "snapshot_id": uuid.uuid4().hex,
        "snapshotted_at": datetime.now(UTC).isoformat(),
        "version": len(_AGENT_SNAPSHOTS.get(key, [])) + 1,
    }
    _AGENT_SNAPSHOTS.setdefault(key, []).append(snapshot)
    return snapshot


@router.post("/{agent_id}/rollback/{snapshot_id}")
async def rollback_agent(
    request: Request, agent_id: str, snapshot_id: str
) -> dict[str, Any]:
    """Roll back agent to a previous snapshot."""
    tenant = _require_tenant(request)
    key = f"{tenant.tenant_id}:{agent_id}"
    snapshots = _AGENT_SNAPSHOTS.get(key, [])
    snapshot = next((s for s in snapshots if s.get("snapshot_id") == snapshot_id), None)
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")

    store = _agent_store(request)
    restore_data = {
        k: v
        for k, v in snapshot.items()
        if k not in ("snapshot_id", "snapshotted_at", "version")
    }
    store.update(agent_id, restore_data, tenant_ctx=tenant)
    return {"agent_id": agent_id, "restored_from": snapshot_id, "status": "rolled_back"}


@router.get("/{agent_id}/export")
async def export_agent(
    request: Request, agent_id: str, format: str = "openai"
) -> dict[str, Any]:
    """Export agent config in a provider-specific format (openai | anthropic)."""
    tenant = _require_tenant(request)
    store = _agent_store(request)
    agent = store.get(agent_id, tenant_ctx=tenant)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    if format == "openai":
        return {
            "object": "assistant",
            "name": agent.get("name", ""),
            "instructions": agent.get("goal_template", ""),
            "model": "gpt-4o",
            "tools": [],
        }
    elif format == "anthropic":
        return {
            "system": agent.get("goal_template", ""),
            "model": "claude-opus-4-8",
            "max_tokens": 4096,
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown export format: {format!r}. Supported: openai, anthropic",
        )
