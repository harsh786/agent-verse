"""Agents API — CRUD for agent configurations and meta-agent NL creation."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator

from app.agent.pattern_flags import (
    AGENT_PATTERN_FLAG_KEYS,
    normalize_pattern_flags,
    pattern_flags_from_record,
)
from app.api._deps import require_owned_agent
from app.intelligence.meta_agent import MetaAgentPlanner
from app.services.a2a_directory import (
    A2A_DESCRIPTION_MAX,
    A2A_SKILLS_MAX,
    normalize_skills,
)
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/agents", tags=["agents"])

# In-memory fallback snapshot store — only used when DB is not configured.
_AGENT_SNAPSHOTS: dict[str, list[dict[str, Any]]] = {}


# ---------------------------------------------------------------------------
# DB helpers for snapshot persistence
# ---------------------------------------------------------------------------


_SNAPSHOT_INSERT_SQL = """
    WITH nxt AS (
        SELECT COALESCE(MAX(version), 0) + 1 AS v
        FROM agent_snapshots
        WHERE tenant_id = :tid AND agent_id = :aid
    )
    INSERT INTO agent_snapshots (id, tenant_id, agent_id, version, snapshot, snapshotted_at)
    SELECT :id, :tid, :aid, nxt.v,
           jsonb_set(CAST(:snap AS jsonb), '{version}', to_jsonb(nxt.v)), NOW()
    FROM nxt
    RETURNING version
"""
_SNAPSHOT_VERSION_RETRIES = 5


def _snapshot_store_unavailable(action: str, exc: Exception) -> HTTPException:
    import logging

    logging.getLogger(__name__).warning("snapshot_%s_failed: %s", action, type(exc).__name__)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Agent snapshots temporarily unavailable; retry",
    )


async def _save_snapshot_to_db(snapshot: dict[str, Any], db: Any, tenant_id: str) -> int:
    """Persist an agent snapshot WITH RLS context; return the version it got.

    The version is allocated in the INSERT itself (MAX + 1 for the agent) and
    a per-agent advisory lock serializes concurrent snapshots; the unique
    ``(tenant_id, agent_id, version)`` constraint (migration e2b6d4f8a1c3) is the
    backstop (a conflict is retried).
    It used to be ``len(existing) + 1`` from a separate read, so two snapshots
    racing got the same number. A failure raises (the route answers 503): the
    old helper logged a warning and the route reported a snapshot that was never
    stored.
    """
    if db is None:  # no database: the caller keeps the in-memory copy
        return int(snapshot.get("version") or 0)
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    from app.db.rls import sqlalchemy_rls_context

    for attempt in range(_SNAPSHOT_VERSION_RETRIES):
        try:
            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_id),
            ):
                # Serialize allocation per agent (transaction-scoped advisory
                # lock): concurrent snapshots then queue instead of colliding and
                # burning retries. The unique constraint stays the backstop.
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
                    {"k": f"agent_snapshot:{tenant_id}:{snapshot['agent_id']}"},
                )
                version = (
                    await session.execute(
                        text(_SNAPSHOT_INSERT_SQL),
                        {
                            "id": snapshot["snapshot_id"],
                            "tid": tenant_id,
                            "aid": snapshot["agent_id"],
                            "snap": json.dumps(snapshot),
                        },
                    )
                ).scalar_one()
            return int(version)
        except IntegrityError:
            if attempt == _SNAPSHOT_VERSION_RETRIES - 1:
                raise
    raise RuntimeError("unreachable")  # pragma: no cover


async def _load_snapshots_from_db(tenant_id: str, agent_id: str, db: Any) -> list[dict[str, Any]]:
    """Load agent snapshots from DB ordered by version ascending (a DB error raises).

    It used to return [] on any error: the versions list looked empty and the
    next snapshot restarted at version 1.
    """
    if db is None:
        return []
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db() as session, sqlalchemy_rls_context(session, tenant_id):
        result = await session.execute(
            text(
                "SELECT snapshot, version FROM agent_snapshots "
                "WHERE tenant_id = :tid AND agent_id = :aid "
                "ORDER BY version ASC"
            ),
            {"tid": tenant_id, "aid": agent_id},
        )
        rows = result.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        snap = json.loads(r[0]) if isinstance(r[0], str) else dict(r[0])
        snap["version"] = int(r[1])  # the column is authoritative
        out.append(snap)
    return out


# ---------------------------------------------------------------------------
# In-memory AgentStore
# ---------------------------------------------------------------------------


def _agent_store_unavailable(op: str, tenant_ctx: TenantContext, exc: Exception) -> Exception:
    """With a database configured, a failed agent read is a retryable 503.

    get/list/count used to fall back to this replica's in-memory cache: an agent
    deleted on another replica was still served (and runnable), one created
    elsewhere was a 404, a list showed a partial set, and plan limits counted the
    cache. Same rule as routing_candidates (RV-02). An HTTPException like the
    store's write paths, so it is a 503 on every router (no app handler needed).
    """
    from app.observability.logging import get_logger

    get_logger(__name__).warning(
        f"agent_{op}_db_failed", tenant_id=tenant_ctx.tenant_id, error=type(exc).__name__
    )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Agent store temporarily unavailable; retry",
        headers={"Retry-After": "5"},
    )


# The routing document (CORE-33); identical to the ix_agents_routing_fts index
# expression (migration b9d4f2a6c8e1) so Postgres can use the GIN index.
_ROUTING_TSV_SQL = (
    "to_tsvector('simple', coalesce(name, '') || ' ' || coalesce(goal_template, '') "
    "|| ' ' || coalesce(connector_ids::text, ''))"
)
_MAX_ROUTING_TOKENS = 32


def _routing_tokens(text_: str) -> set[str]:
    """Lower-case alphanumeric tokens (safe to splice into a tsquery), bounded."""
    import re

    toks = sorted({t for t in re.findall(r"[a-z0-9]+", (text_ or "").lower()) if len(t) > 1})
    return set(toks[:_MAX_ROUTING_TOKENS])


def _revalidation_fields(marker: Any) -> dict[str, Any]:
    """The agent API's view of its re-validation state (a05-F095-04 decision).

    ``autonomy_revalidation`` is the stored marker (or None); ``pending_promotion``
    is True while the agent is demoted and its eval-suite run is under way.
    """
    record = dict(marker) if isinstance(marker, dict) else None
    return {
        "autonomy_revalidation": record,
        "pending_promotion": bool(record and record.get("state") == "pending"),
    }


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
        # D3: every agent starts private (opt-in via PUT /agents/{id}).
        record["a2a_public"] = False
        record.setdefault("a2a_description", "")
        record.setdefault("a2a_skills", [])
        record.update(_revalidation_fields(None))
        if self._db is not None:
            await self._db_persist_agent(record)
        self._data[(tenant_ctx.tenant_id, agent_id)] = record
        return agent_id

    # FIX 1: persist ALL fields to DB
    async def _db_persist_agent(self, record: dict[str, Any]) -> None:
        from app.db.models.agent import Agent
        from app.db.rls import sqlalchemy_rls_context

        tenant_id = str(record["tenant_id"])
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
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
                    timeout_seconds=int(record.get("timeout_seconds", 0) or 0),
                    allowed_collection_ids=list(record.get("allowed_collection_ids", [])),
                    eval_suite_id=record.get("eval_suite_id") or None,
                    policy_ids=list(record.get("policy_ids", [])),
                    pattern_flags=normalize_pattern_flags(record.get("pattern_flags")),
                    cloned_from=record.get("cloned_from") or None,
                    domain_context=str(record.get("domain_context") or "general"),
                    domain_metadata=dict(record.get("domain_metadata") or {}),
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
        flags = normalize_pattern_flags(getattr(row, "pattern_flags", None))
        return {
            **flags,
            "pattern_flags": flags,
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
            "timeout_seconds": getattr(row, "timeout_seconds", 0) or 0,
            "allowed_collection_ids": list(getattr(row, "allowed_collection_ids", []) or []),
            "eval_suite_id": getattr(row, "eval_suite_id", None),
            "policy_ids": list(getattr(row, "policy_ids", []) or []),
            "cloned_from": getattr(row, "cloned_from", None),
            "permissions": {},
            "created_at": created_at.isoformat() if created_at else "",
            "a2a_public": bool(getattr(row, "a2a_public", False)),
            "a2a_description": getattr(row, "a2a_description", "") or "",
            "a2a_skills": list(getattr(row, "a2a_skills", None) or []),
            "domain_context": getattr(row, "domain_context", None) or "general",
            "domain_metadata": dict(getattr(row, "domain_metadata", None) or {}),
            **_revalidation_fields(getattr(row, "autonomy_revalidation", None)),
        }

    # ── async DB reads ─────────────────────────────────────────────────────────

    async def get_async(self, agent_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any] | None:
        """Read a single agent directly from DB; fall back to memory cache."""
        if self._db is not None:
            try:
                from sqlalchemy import select

                from app.db.models.agent import Agent
                from app.db.rls import sqlalchemy_rls_context

                async with (
                    self._db() as session,
                    sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
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
                # Row not found in DB → not found (and drop a stale cached copy,
                # e.g. an agent deleted through another replica).
                self._data.pop((tenant_ctx.tenant_id, agent_id), None)
                return None
            except Exception as exc:
                raise _agent_store_unavailable("get", tenant_ctx, exc) from exc

        return self._data.get((tenant_ctx.tenant_id, agent_id))

    async def list_async(
        self,
        *,
        tenant_ctx: TenantContext,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Read agents for a tenant directly from DB; fall back to memory cache.

        When ``limit`` is given the query is paginated (LIMIT/OFFSET, newest first)
        so the list endpoint never loads the whole table. ``limit=None`` keeps the
        legacy unbounded behaviour for internal callers that need every row.
        """
        if self._db is not None:
            try:
                from sqlalchemy import select

                from app.db.models.agent import Agent
                from app.db.rls import sqlalchemy_rls_context

                async with (
                    self._db() as session,
                    sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
                ):
                    stmt = (
                        select(Agent)
                        .where(
                            Agent.tenant_id == tenant_ctx.tenant_id,
                            Agent.is_active == True,  # noqa: E712
                        )
                        .order_by(Agent.created_at.desc())
                    )
                    if limit is not None:
                        stmt = stmt.limit(limit).offset(max(0, offset))
                    rows = (await session.execute(stmt)).scalars().all()

                agents = [self._row_to_dict(r) for r in rows]
                # Refresh in-memory cache
                for a in agents:
                    self._data[(tenant_ctx.tenant_id, a["agent_id"])] = a
                return agents
            except Exception as exc:
                raise _agent_store_unavailable("list", tenant_ctx, exc) from exc

        rows_mem = self.list_all(tenant_ctx=tenant_ctx)
        if limit is not None:
            return rows_mem[offset : offset + limit]
        return rows_mem

    async def routing_candidates(
        self, *, tenant_ctx: TenantContext, goal: str, limit: int
    ) -> list[dict[str, Any]]:
        """At most *limit* active agents for auto-routing, best text match first (CORE-33).

        Auto-routing used to load the tenant's whole agents table per submission
        and then keep only the 50 NEWEST, so older agents were never candidates.
        Postgres pre-filters with a full-text match of the goal against the
        agent's name / goal template / connector ids (GIN index
        ``ix_agents_routing_fts``), ranked, and tops up with the newest agents
        only when fewer than *limit* match. Without a DB the same ranking runs
        over the in-memory cache.

        With a DB configured, a failed query raises (RV-02): routing over this
        replica's stale cache instead would pick agents another replica deleted
        and miss ones it created, while reporting a normal decision.
        """
        limit = max(1, int(limit))
        tokens = _routing_tokens(goal)
        if self._db is not None:
            try:
                return await self._db_routing_candidates(tenant_ctx, tokens, limit)
            except Exception as exc:
                from app.observability.logging import get_logger

                get_logger(__name__).warning(
                    "agent_routing_candidates_db_failed",
                    tenant_id=tenant_ctx.tenant_id,
                    error=type(exc).__name__,
                )
                raise
        rows = self.list_all(tenant_ctx=tenant_ctx)

        def _hits(rec: dict[str, Any]) -> int:
            text_ = " ".join(
                [str(rec.get("name", "")), str(rec.get("goal_template", ""))]
                + [str(c) for c in rec.get("connector_ids", []) or []]
            )
            return len(tokens & _routing_tokens(text_))

        newest_first = sorted(rows, key=lambda r: str(r.get("created_at", "")), reverse=True)
        return sorted(newest_first, key=_hits, reverse=True)[:limit]

    async def _db_routing_candidates(
        self, tenant_ctx: TenantContext, tokens: set[str], limit: int
    ) -> list[dict[str, Any]]:
        from sqlalchemy import func, literal_column, select

        from app.db.models.agent import Agent
        from app.db.rls import sqlalchemy_rls_context

        # Must match the ix_agents_routing_fts index expression exactly.
        document = literal_column(_ROUTING_TSV_SQL)
        base = select(Agent).where(
            Agent.tenant_id == tenant_ctx.tenant_id,
            Agent.is_active == True,  # noqa: E712
        )
        async with (
            self._db() as session,
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            matched: list[Any] = []
            if tokens:
                query = func.to_tsquery("simple", " | ".join(sorted(tokens)))
                matched = list(
                    (
                        await session.execute(
                            base.where(document.op("@@")(query))
                            .order_by(
                                func.ts_rank(document, query).desc(), Agent.created_at.desc()
                            )
                            .limit(limit)
                        )
                    )
                    .scalars()
                    .all()
                )
            if len(matched) < limit:
                seen = [r.id for r in matched]
                stmt = base.order_by(Agent.created_at.desc()).limit(limit - len(matched))
                if seen:
                    stmt = stmt.where(Agent.id.not_in(seen))
                matched.extend((await session.execute(stmt)).scalars().all())
        return [self._row_to_dict(r) for r in matched]

    async def count_async(self, *, tenant_ctx: TenantContext) -> int:
        """COUNT of a tenant's active agents (for limit enforcement) without
        materialising every row."""
        if self._db is not None:
            try:
                from sqlalchemy import func, select

                from app.db.models.agent import Agent
                from app.db.rls import sqlalchemy_rls_context

                async with (
                    self._db() as session,
                    sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
                ):
                    return int(
                        (
                            await session.execute(
                                select(func.count())
                                .select_from(Agent)
                                .where(
                                    Agent.tenant_id == tenant_ctx.tenant_id,
                                    Agent.is_active == True,  # noqa: E712
                                )
                            )
                        ).scalar_one()
                    )
            except Exception as exc:
                raise _agent_store_unavailable("count", tenant_ctx, exc) from exc
        return len(self.list_all(tenant_ctx=tenant_ctx))

    def list_all(self, *, tenant_ctx: TenantContext) -> list[dict[str, Any]]:
        return [rec for (tid, _), rec in self._data.items() if tid == tenant_ctx.tenant_id]

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

                async with (
                    self._db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
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
                    # D3: a deleted agent leaves the public directory with it.
                    await session.execute(
                        text(
                            "DELETE FROM a2a_public_agents "
                            "WHERE agent_id = :id AND tenant_id = :tid"
                        ),
                        {"id": agent_id, "tid": tenant_ctx.tenant_id},
                    )
            except Exception as exc:
                from app.observability.logging import get_logger

                # Was: fall through to an in-memory-only delete and report success
                # while the agent stayed active in the DB (and on every replica).
                get_logger(__name__).error("agent_delete_db_failed", error=str(exc))
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Agent store unavailable; the agent was not deleted",
                ) from exc
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
        """Merge data into the agent record, DB first.

        The old version mutated the process cache first, then swallowed any DB
        error and returned True — PUT / rollback / knowledge binding reported a
        change that was never persisted (and differed per replica). It also
        looked the agent up in this replica's cache only, so an agent created on
        another replica was a 404. Now: DB-authoritative lookup, DB write first,
        503 when the write fails, cache refreshed only after it succeeds.
        """
        rec = await self.get_async(agent_id, tenant_ctx=tenant_ctx)
        if rec is None:
            return False

        if self._db is None:
            rec.update(data)
            rec.update(_revalidation_fields(rec.get("autonomy_revalidation")))
            return True
        if self._db is not None:
            try:
                from sqlalchemy import text

                from app.db.rls import sqlalchemy_rls_context

                # Build SET clause dynamically for allowed fields
                allowed = {
                    "name",
                    "goal_template",
                    "autonomy_mode",
                    "connector_ids",
                    "trigger_config",
                    "system_prompt",
                    "model_override",
                    "max_iterations",
                    "timeout_seconds",
                    "allowed_collection_ids",
                    "eval_suite_id",
                    "policy_ids",
                    "pattern_flags",
                    "a2a_public",
                    "a2a_description",
                    "a2a_skills",
                    "domain_context",
                    "domain_metadata",
                    "autonomy_revalidation",
                }
                updates = {k: v for k, v in data.items() if k in allowed}
                if not updates:
                    rec.update(data)
                    return True

                # JSON-encode list/dict fields
                params: dict[str, Any] = {"id": agent_id, "tid": tenant_ctx.tenant_id}
                set_parts = []
                for k, v in updates.items():
                    if isinstance(v, (list, dict)):
                        params[k] = json.dumps(v)
                        set_parts.append(f"{k} = CAST(:{k} AS jsonb)")
                    else:
                        params[k] = v
                        set_parts.append(f"{k} = :{k}")

                set_clause = ", ".join(set_parts)
                async with (
                    self._db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
                ):
                    result = await session.execute(
                        text(
                            f"UPDATE agents SET {set_clause}, updated_at = NOW() "
                            "WHERE id = :id AND tenant_id = :tid AND is_active = TRUE"
                        ),
                        params,
                    )
                    updated = result.rowcount > 0
                    if updated:
                        # D3: the public card follows the agent in this transaction.
                        from app.services.a2a_directory import sync_public_agent

                        await sync_public_agent(
                            session, agent_id=agent_id, tenant_id=tenant_ctx.tenant_id
                        )
            except Exception as exc:
                from app.observability.logging import get_logger

                get_logger(__name__).error("agent_update_db_failed", error=str(exc))
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Agent store unavailable; the change was not saved",
                ) from exc
        if updated:
            rec.update(data)
            rec.update(_revalidation_fields(rec.get("autonomy_revalidation")))
            self._data[(tenant_ctx.tenant_id, agent_id)] = rec
        return updated

    async def transition_revalidation(
        self,
        agent_id: str,
        *,
        token: str,
        data: dict[str, Any],
        tenant_ctx: TenantContext,
    ) -> bool:
        """Compare-and-set for the re-validation state machine.

        Writes ``data`` (``autonomy_revalidation`` and optionally
        ``autonomy_mode``) only while the agent is still ``bounded-autonomous``
        with the PENDING marker ``token``. False when anything changed since
        (an operator changed autonomy, a newer config change replaced the
        marker, the marker was already resolved): a stale run never promotes.
        """
        allowed = {"autonomy_revalidation", "autonomy_mode"}
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return False

        def _matches(rec: dict[str, Any] | None) -> bool:
            marker = (rec or {}).get("autonomy_revalidation")
            return (
                rec is not None
                and rec.get("autonomy_mode") == "bounded-autonomous"
                and isinstance(marker, dict)
                and marker.get("state") == "pending"
                and marker.get("token") == token
            )

        key = (tenant_ctx.tenant_id, agent_id)
        if self._db is None:
            rec = self._data.get(key)
            if not _matches(rec):
                return False
            assert rec is not None
            rec.update(updates)
            rec.update(_revalidation_fields(rec.get("autonomy_revalidation")))
            return True

        from sqlalchemy import text

        from app.db.rls import sqlalchemy_rls_context

        params: dict[str, Any] = {"id": agent_id, "tid": tenant_ctx.tenant_id, "token": token}
        set_parts = []
        if "autonomy_revalidation" in updates:
            params["marker"] = json.dumps(updates["autonomy_revalidation"])
            set_parts.append("autonomy_revalidation = CAST(:marker AS jsonb)")
        if "autonomy_mode" in updates:
            params["mode"] = str(updates["autonomy_mode"])
            set_parts.append("autonomy_mode = :mode")
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            result = await session.execute(
                text(
                    f"UPDATE agents SET {', '.join(set_parts)}, updated_at = NOW() "
                    "WHERE id = :id AND tenant_id = :tid AND is_active = TRUE "
                    "AND autonomy_mode = 'bounded-autonomous' "
                    "AND (autonomy_revalidation ->> 'state') = 'pending' "
                    "AND (autonomy_revalidation ->> 'token') = :token"
                ),
                params,
            )
            changed = bool(result.rowcount)
            if changed and "autonomy_mode" in updates:
                from app.services.a2a_directory import sync_public_agent

                await sync_public_agent(
                    session, agent_id=agent_id, tenant_id=tenant_ctx.tenant_id
                )
        if changed:
            cached = self._data.get(key)
            if cached is not None:
                cached.update(updates)
                cached.update(_revalidation_fields(cached.get("autonomy_revalidation")))
        return changed

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
    # 0 = no agent limit (the plan's goal timeout applies); >0 caps each goal.
    timeout_seconds: int = Field(default=0, ge=0)
    domain_context: str = "general"
    domain_metadata: dict[str, Any] = {}
    # Reasoning-pattern opt-ins (app.agent.pattern_flags.AGENT_PATTERN_FLAG_KEYS).
    enable_cot: bool = False
    enable_reflection: bool = False
    enable_goal_tree: bool = False
    enable_self_refine: bool = False
    enable_self_consistency: bool = False
    enable_tree_of_thoughts: bool = False
    enable_peer_review: bool = False
    enable_supervisor: bool = False
    enable_debate: bool = False

    @model_validator(mode="after")
    def _validate_domain_metadata(self) -> CreateAgentRequest:
        """Legal agents must have bar_number in domain_metadata."""
        error = _domain_metadata_error(self.domain_context, self.domain_metadata)
        if error is not None:
            raise ValueError(error)
        return self


def _domain_metadata_error(domain_context: str, domain_metadata: dict[str, Any]) -> str | None:
    """Why this domain identity is invalid, or ``None`` (shared by create and update)."""
    if domain_context == "legal" and "bar_number" not in domain_metadata:
        return "Legal agents require 'bar_number' in domain_metadata"
    return None


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
    # QA-14: the merged result is validated like create (legal needs bar_number).
    domain_context: str | None = None
    domain_metadata: dict[str, Any] | None = None
    enable_cot: bool | None = None
    enable_reflection: bool | None = None
    enable_goal_tree: bool | None = None
    enable_self_refine: bool | None = None
    enable_self_consistency: bool | None = None
    enable_tree_of_thoughts: bool | None = None
    enable_peer_review: bool | None = None
    enable_supervisor: bool | None = None
    enable_debate: bool | None = None
    # D3: public A2A directory opt-in and the card text shown there (only these,
    # the name and the endpoint are public — never prompts, tools or connectors).
    a2a_public: bool | None = None
    a2a_description: str | None = Field(default=None, max_length=A2A_DESCRIPTION_MAX)
    a2a_skills: list[str] | None = Field(default=None, max_length=A2A_SKILLS_MAX)

    @field_validator("a2a_skills")
    @classmethod
    def _bounded_skills(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else normalize_skills(v)

    @field_validator("a2a_description")
    @classmethod
    def _stripped_description(cls, v: str | None) -> str | None:
        return None if v is None else v.strip()


class CloneAgentRequest(BaseModel):
    name: str | None = None


class UpdatePermissionsRequest(BaseModel):
    permissions: list[dict] | dict[str, str]  # accept both new (list) and legacy (dict) formats


class UpdateKnowledgeBindingRequest(BaseModel):
    collection_ids: list[str]


class MetaAgentCreateRequest(BaseModel):
    command: str
    autorun: bool = False
    # Explicit confirmation to create the agent from a heuristic draft when the
    # LLM could not design one (error / timeout / non-JSON). Without it the
    # endpoint answers 502 with the draft and creates nothing.
    accept_heuristic: bool = False


def _connector_lookup_key(value: Any) -> str:
    return (
        str(value)
        .strip()
        .lower()
        .replace(".", "")
        .replace("-", "")
        .replace("_", "")
        .replace(" ", "")
    )


def _connector_id_from_value(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("server_id", "id", "connector_id", "type", "name"):
            raw = value.get(key)
            if raw is not None and str(raw).strip():
                return str(raw).strip()
        return ""
    return str(value).strip()


def _normalize_connector_ids(values: list[Any]) -> list[str]:
    connector_ids: list[str] = []
    seen: set[str] = set()
    for value in values:
        connector_id = _connector_id_from_value(value)
        if not connector_id or connector_id in seen:
            continue
        connector_ids.append(connector_id)
        seen.add(connector_id)
    return connector_ids


async def _resolve_connector_ids(
    request: Request, connector_ids: list[Any], tenant_ctx: TenantContext
) -> list[str]:
    normalized = _normalize_connector_ids(connector_ids)
    if not normalized:
        return []

    registry = getattr(request.app.state, "mcp_registry", None)
    if registry is None:
        return normalized

    try:
        if hasattr(registry, "list_server_records"):
            records = await registry.list_server_records(tenant_ctx=tenant_ctx)
        elif hasattr(registry, "list_servers"):
            servers = await registry.list_servers(tenant_ctx=tenant_ctx)
            records = [(str(getattr(server, "server_id", "") or ""), server) for server in servers]
        else:
            return normalized
    except Exception:
        return normalized

    lookup: dict[str, str] = {}
    for server_id, cfg in records:
        server_id_text = str(server_id or getattr(cfg, "server_id", "") or "").strip()
        if not server_id_text:
            continue
        for value in (
            server_id_text,
            getattr(cfg, "server_id", ""),
            getattr(cfg, "name", ""),
        ):
            key = _connector_lookup_key(value)
            if key:
                lookup.setdefault(key, server_id_text)

    resolved: list[str] = []
    seen: set[str] = set()
    for connector_id in normalized:
        connector_key = _connector_lookup_key(connector_id)
        resolved_id = lookup.get(connector_key, connector_id)
        if resolved_id in seen:
            continue
        resolved.append(resolved_id)
        seen.add(resolved_id)
    return resolved


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return ctx


def _agent_store(request: Request) -> AgentStore:
    from app.api._deps import get_agent_store

    return get_agent_store(request)  # type: ignore[no-any-return]


def _meta_agent(request: Request) -> MetaAgentPlanner:
    from app.api._deps import get_meta_agent

    return get_meta_agent(request)  # type: ignore[no-any-return]


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


_NO_SCHEDULE_TRIGGERS = ("", "manual")


def _agent_trigger_spec(cfg: dict[str, Any], tenant_ctx: TenantContext) -> Any:
    """The TriggerSpec an agent's ``trigger_config`` asks for, validated (TRG-50).

    ``None`` for a manual / absent trigger. Anything else must be a creatable
    spec for the tenant's plan (supported type, valid fields, cron plan floor),
    else 422 - before the agent is created.
    """
    from app.triggers.validation import creatable_error

    try:
        spec = _build_trigger_spec(cfg)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"trigger_config: {exc}") from exc
    if spec is None:
        return None
    plan = str(getattr(tenant_ctx, "plan", "free") or "free")
    reason = creatable_error(spec, plan=plan)
    if reason is not None:
        raise HTTPException(status_code=422, detail=f"trigger_config: {reason}")
    return spec


def _build_trigger_spec(cfg: dict[str, Any]) -> Any:
    """``trigger_config`` as a TriggerSpec (``None`` for manual / absent), unvalidated.

    Raises ``ValueError`` for an unknown trigger type or invalid spec fields.
    """
    import dataclasses

    from app.triggers.models import TriggerSpec, TriggerType

    trigger_type = str(cfg.get("trigger_type", "") or "").strip()
    if trigger_type in _NO_SCHEDULE_TRIGGERS:
        return None
    try:
        ttype = TriggerType(trigger_type)
    except ValueError:
        raise ValueError(f"unknown trigger_type {trigger_type!r}") from None
    names = {f.name for f in dataclasses.fields(TriggerSpec)} - {"trigger_type"}
    try:
        return TriggerSpec(trigger_type=ttype, **{k: v for k, v in cfg.items() if k in names})
    except TypeError as exc:
        raise ValueError(str(exc)) from exc


async def _create_agent_schedule(
    request: Request,
    agents: AgentStore,
    spec: Any,
    *,
    agent_id: str,
    goal_template: str,
    tenant_ctx: TenantContext,
) -> str | None:
    """Durably create the agent's schedule with PLAN_MAX_TRIGGERS enforced.

    On quota (403), store outage (503) or no schedule store (503) the agent just
    created is deleted again, so the API never reports an agent whose trigger
    silently does not exist.
    """
    if spec is None:
        return None
    from app.triggers.quota import TriggerQuotaExceeded
    from app.triggers.store import ScheduleStoreUnavailableError

    schedule_store = getattr(request.app.state, "schedule_store", None)
    status_code, detail = 503, "Schedules are unavailable; the agent was not created"
    if schedule_store is not None and hasattr(schedule_store, "create_async"):
        try:
            return str(
                await schedule_store.create_async(
                    goal_id=uuid.uuid4().hex,
                    spec=spec,
                    tenant_ctx=tenant_ctx,
                    agent_id=agent_id,
                    goal_template=goal_template,
                    quota_plan=str(getattr(tenant_ctx, "plan", "free") or "free"),
                )
            )
        except TriggerQuotaExceeded as exc:
            status_code, detail = 403, str(exc)
        except ScheduleStoreUnavailableError:
            pass
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("agent_schedule_create_failed: %s", exc)
    try:
        await agents.delete_async(agent_id, tenant_ctx=tenant_ctx)
    except Exception as exc:  # report the schedule failure either way
        import logging

        logging.getLogger(__name__).error(
            "agent_schedule_rollback_failed agent_id=%s: %s", agent_id, exc
        )
    raise HTTPException(status_code=status_code, detail=detail)


def _reschedule_needed(current: dict[str, Any], update_data: dict[str, Any]) -> bool:
    """Whether an update changes what the agent's schedule fires (QA-15).

    The schedule embeds the trigger and the goal template, so a change to either
    (for a scheduled agent) means the old schedule must be replaced.
    """
    old_cfg = dict(current.get("trigger_config") or {})
    if "trigger_config" in update_data and dict(update_data["trigger_config"]) != old_cfg:
        return True
    old_type = str(old_cfg.get("trigger_type", "") or "").strip()
    return (
        "goal_template" in update_data
        and update_data["goal_template"] != (current.get("goal_template") or "")
        and old_type not in _NO_SCHEDULE_TRIGGERS
    )


async def _restore_agent_schedule(
    schedule_store: Any, current: dict[str, Any], *, agent_id: str, tenant_ctx: TenantContext
) -> None:
    """Best effort: re-create the schedule the agent had before a failed update.

    Not quota- or plan-checked: it is the schedule the agent already had. A
    failure is logged (the update's own error is what the caller reports).
    """
    import logging

    try:
        spec = _build_trigger_spec(dict(current.get("trigger_config") or {}))
        if spec is None:
            return
        await schedule_store.create_async(
            goal_id=uuid.uuid4().hex,
            spec=spec,
            tenant_ctx=tenant_ctx,
            agent_id=agent_id,
            goal_template=str(current.get("goal_template") or ""),
        )
    except Exception as exc:
        logging.getLogger(__name__).error(
            "agent_schedule_restore_failed agent_id=%s: %s", agent_id, exc
        )


async def _replace_agent_schedule(
    request: Request,
    current: dict[str, Any],
    spec: Any,
    *,
    agent_id: str,
    goal_template: str,
    tenant_ctx: TenantContext,
) -> str | None:
    """Remove the agent's schedules and create the one *spec* asks for (QA-15).

    Runs BEFORE the agent row is updated. A failure is reported (403 quota /
    503 outage) with the agent unchanged and its previous schedule restored -
    never an agent whose stored trigger has no (or the old) schedule. The old
    schedules go first so replacing one at the PLAN_MAX_TRIGGERS cap works.
    """
    from app.triggers.quota import TriggerQuotaExceeded
    from app.triggers.store import ScheduleStoreUnavailableError

    schedule_store = getattr(request.app.state, "schedule_store", None)
    if schedule_store is None or not hasattr(schedule_store, "delete_for_agent_async"):
        if spec is None:
            return None  # nothing can be scheduled without a store
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Schedules are unavailable; the agent was not updated",
        )
    try:
        await schedule_store.delete_for_agent_async(agent_id, tenant_ctx=tenant_ctx)
    except ScheduleStoreUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not replace the agent's schedules; the agent was not updated",
        ) from exc
    if spec is None:
        return None
    status_code, detail = 503, "Schedules are unavailable; the agent was not updated"
    try:
        return str(
            await schedule_store.create_async(
                goal_id=uuid.uuid4().hex,
                spec=spec,
                tenant_ctx=tenant_ctx,
                agent_id=agent_id,
                goal_template=goal_template,
                quota_plan=str(getattr(tenant_ctx, "plan", "free") or "free"),
            )
        )
    except TriggerQuotaExceeded as exc:
        status_code, detail = 403, str(exc)
    except ScheduleStoreUnavailableError:
        pass
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("agent_schedule_replace_failed: %s", exc)
    await _restore_agent_schedule(
        schedule_store, current, agent_id=agent_id, tenant_ctx=tenant_ctx
    )
    raise HTTPException(status_code=status_code, detail=detail)


async def _undo_agent_reschedule(
    request: Request, current: dict[str, Any], *, agent_id: str, tenant_ctx: TenantContext
) -> None:
    """The agent row was not updated: drop the new schedule, restore the old one."""
    import logging

    schedule_store = getattr(request.app.state, "schedule_store", None)
    if schedule_store is None or not hasattr(schedule_store, "delete_for_agent_async"):
        return
    try:
        await schedule_store.delete_for_agent_async(agent_id, tenant_ctx=tenant_ctx)
    except Exception as exc:
        logging.getLogger(__name__).error(
            "agent_schedule_undo_failed agent_id=%s: %s", agent_id, exc
        )
        return
    await _restore_agent_schedule(
        schedule_store, current, agent_id=agent_id, tenant_ctx=tenant_ctx
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("")
async def list_agents(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    return await store.list_async(tenant_ctx=tenant_ctx, limit=limit, offset=offset)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_agent(request: Request, body: CreateAgentRequest) -> dict[str, Any]:
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    # Enforce release gate: fully-autonomous mode requires an eval suite
    # (FULLY_AUTONOMOUS_EVAL_GATE_ENABLED=false is the owner's opt-out).
    _gate_on = _eval_gate_enabled()
    if _gate_on and body.autonomy_mode == "fully-autonomous" and not body.eval_suite_id:
        raise HTTPException(
            status_code=422,
            detail=(
                "fully-autonomous mode requires eval_suite_id. "
                "Attach an eval suite with passing results first."
            ),
        )
    if _gate_on and body.autonomy_mode == "fully-autonomous" and body.eval_suite_id:
        # A new agent has never run the suite, so no run can vouch for it
        # (MEM-52): this always refuses, with the gate's explanation.
        await _enforce_rollout_gate(
            request, tenant_ctx, agent_id=None, eval_suite_id=body.eval_suite_id
        )
    # FIX 6: use list_async (DB-backed) for accurate cross-replica limit check
    from app.tenancy.limits import check_agent_limit

    existing_count = await store.count_async(tenant_ctx=tenant_ctx)
    check_agent_limit(tenant_ctx, existing_count)

    trigger_spec = _agent_trigger_spec(body.trigger_config or {}, tenant_ctx)

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
        "domain_context": body.domain_context,
        "domain_metadata": body.domain_metadata,
    }
    _flags = normalize_pattern_flags(body.model_dump())
    record.update(_flags)
    record["pattern_flags"] = _flags
    agent_id = await _create_agent_record(store, record, tenant_ctx=tenant_ctx)

    # TRG-50: the agent's schedule goes through the same gate as POST /triggers
    # (validated before the agent exists; durable, quota-enforced create; any
    # failure removes the agent and is reported - never a 201 without it).
    schedule_id = await _create_agent_schedule(
        request,
        store,
        trigger_spec,
        agent_id=agent_id,
        goal_template=body.goal_template or record.get("goal_template", ""),
        tenant_ctx=tenant_ctx,
    )

    created = store.get(agent_id, tenant_ctx=tenant_ctx) or {}
    if schedule_id:
        created = {**created, "schedule_id": schedule_id}
    return created


# Note: /create must be declared before /{agent_id} so the exact path wins.
@router.post("/create", status_code=status.HTTP_201_CREATED)
async def create_agent_nl(request: Request, body: MetaAgentCreateRequest) -> dict[str, Any]:
    """Meta-agent NL creation — parses one NL command into a full agent config."""
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    planner = _meta_agent(request)

    from app.providers.guarded_completion import DecisionBudgetExceededError

    try:
        config = await planner.plan(command=body.command, tenant_ctx=tenant_ctx)
    except DecisionBudgetExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"LLM budget exhausted — cannot design the agent: {exc}",
        ) from exc
    generated_by = str(getattr(config, "generated_by", "llm") or "llm")

    # The planner could not reach/parse the LLM and fell back to a name-and-
    # command guess. That used to be persisted with 201 as if it were a
    # designed agent. Safer contract: refuse (502, the upstream LLM failed) and
    # hand back the labelled draft; create only on explicit confirmation.
    if generated_by == "heuristic" and not body.accept_heuristic:
        from fastapi.responses import JSONResponse

        # `detail` stays a plain string (clients render it); the draft rides
        # alongside it.
        return JSONResponse(  # type: ignore[return-value]
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={
                "detail": (
                    "The agent designer LLM did not return a usable config; no agent "
                    "was created. Retry, or resend with accept_heuristic=true to "
                    "create the heuristic draft."
                ),
                "error_code": "meta_agent_llm_unavailable",
                "generated_by": "heuristic",
                "fallback_reason": str(getattr(config, "fallback_reason", "")),
                "draft_config": {
                    "name": config.name,
                    "goal_template": config.goal_template,
                    "connectors": list(config.connectors),
                    "trigger_type": config.trigger_type,
                    "autonomy_mode": config.autonomy_mode,
                },
            },
        )

    # FIX 4: enforce agent limit via DB-backed list_async
    from app.tenancy.limits import check_agent_limit

    existing_count = await store.count_async(tenant_ctx=tenant_ctx)
    check_agent_limit(tenant_ctx, existing_count)

    # FIX 4: NL creation cannot directly produce fully-autonomous agents
    if config.autonomy_mode == "fully-autonomous":
        raise HTTPException(
            status_code=422,
            detail=(
                "NL agent creation cannot directly produce fully-autonomous agents. "
                "Create the agent first, attach an eval suite, and upgrade autonomy_mode via PUT."
            ),
        )

    connector_ids = await _resolve_connector_ids(
        request,
        list(config.connectors),
        tenant_ctx,
    )

    record: dict[str, Any] = {
        "name": config.name,
        "goal_template": config.goal_template,
        "autonomy_mode": config.autonomy_mode,
        "connector_ids": connector_ids,
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
            "connectors": connector_ids,
            "trigger_type": config.trigger_type,
            "event_channel": config.event_channel,
            "cron_expression": config.cron_expression,
            "interval_seconds": config.interval_seconds,
            "autonomy_mode": config.autonomy_mode,
            # MEM-30: free-text governance ideas from the designer LLM. They are
            # NOT turned into tool policies — say so, so nobody assumes they bind.
            "policy_suggestions": config.policy_suggestions,
            "policy_suggestions_applied": False,
            "policy_suggestions_note": (
                "Suggestions only — not applied. Create any you want as tool "
                "policies under Governance."
            ),
            "generated_by": generated_by,
            "fallback_reason": str(getattr(config, "fallback_reason", "")),
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
async def update_agent(request: Request, agent_id: str, body: UpdateAgentRequest) -> dict[str, Any]:
    """Update an agent's configuration. All fields are optional.

    Changing the behaviour config (prompt, goal template, model, connectors,
    policies, collections, limits, reasoning patterns) of a ``fully-autonomous``
    agent demotes it to ``bounded-autonomous`` and re-runs its eval suite
    against the new config; it returns to ``fully-autonomous`` automatically
    when that run passes the rollout gate. The response's
    ``autonomy_revalidation`` / ``pending_promotion`` show that state.
    """
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
    _gate_on = _eval_gate_enabled()
    if _gate_on and new_autonomy == "fully-autonomous" and not new_eval_suite:
        raise HTTPException(
            status_code=422,
            detail="fully-autonomous mode requires eval_suite_id",
        )
    # Build update dict (only non-None fields)
    update_data = {k: v for k, v in body.model_dump().items() if v is not None}
    # QA-14: the domain identity being WRITTEN must be valid, judged on the merge
    # of the given fields over the stored ones (e.g. legal keeps its bar_number).
    if "domain_context" in update_data or "domain_metadata" in update_data:
        domain_error = _domain_metadata_error(
            str(update_data.get("domain_context", current.get("domain_context")) or "general"),
            dict(update_data.get("domain_metadata", current.get("domain_metadata")) or {}),
        )
        if domain_error is not None:
            raise HTTPException(status_code=422, detail=domain_error)
    # Pattern flags are stored as one map: merge the given ones over the current.
    _given_flags = {k: update_data[k] for k in AGENT_PATTERN_FLAG_KEYS if k in update_data}
    if _given_flags:
        _merged = normalize_pattern_flags({**pattern_flags_from_record(current), **_given_flags})
        update_data.update(_merged)
        update_data["pattern_flags"] = _merged

    # Becoming fully-autonomous / a new vouching suite is gated (409); a
    # behaviour change to a fully-autonomous agent demotes and re-validates it
    # (owner decision on a05-F095-04, see _plan_autonomy_change).
    plan = await _plan_autonomy_change(
        request, tenant_ctx, agent_id=agent_id, current=current, update_data=update_data,
        requested_mode=body.autonomy_mode, source="agent_update", refuse_promotion=True,
    )

    # QA-15: a new trigger / goal template goes through the same gate as create
    # (422 before anything is written), and the agent's schedule is replaced
    # BEFORE the row changes; a failed row write puts the old schedule back.
    reschedule = _reschedule_needed(current, update_data)
    schedule_id: str | None = None
    if reschedule:
        new_cfg = dict(update_data.get("trigger_config", current.get("trigger_config")) or {})
        new_template = str(update_data.get("goal_template", current.get("goal_template")) or "")
        spec = _agent_trigger_spec(new_cfg, tenant_ctx)
        schedule_id = await _replace_agent_schedule(
            request,
            current,
            spec,
            agent_id=agent_id,
            goal_template=new_template,
            tenant_ctx=tenant_ctx,
        )

    try:
        updated = await store.update_async(agent_id, update_data, tenant_ctx=tenant_ctx)
    except Exception:
        if reschedule:
            await _undo_agent_reschedule(
                request, current, agent_id=agent_id, tenant_ctx=tenant_ctx
            )
        await _abandon_autonomy_change(plan)
        raise
    if not updated:
        if reschedule:
            await _undo_agent_reschedule(
                request, current, agent_id=agent_id, tenant_ctx=tenant_ctx
            )
        await _abandon_autonomy_change(plan)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    await _finish_autonomy_change(
        request, tenant_ctx, agent_id=agent_id, store=store, plan=plan,
        requested_mode=body.autonomy_mode,
    )

    result = await store.get_async(agent_id, tenant_ctx=tenant_ctx)
    if schedule_id and result is not None:
        return {**result, "schedule_id": schedule_id}
    return result  # type: ignore[return-value]


# ── Autonomy changes behind the rollout gate (a05-F095-04 owner decision) ────


@dataclass
class _AutonomyPlan:
    """What an agent write does to its autonomy, decided before the write."""

    marker: dict[str, Any] | None = None
    eval_store: Any = None
    previous: dict[str, Any] | None = None
    cancelled: bool = False
    tenant_plan: str = "free"
    source: str = "agent_update"


async def _plan_autonomy_change(
    request: Request,
    tenant_ctx: TenantContext,
    *,
    agent_id: str,
    current: dict[str, Any],
    update_data: dict[str, Any],
    requested_mode: str | None,
    source: str,
    refuse_promotion: bool,
) -> _AutonomyPlan:
    """Decide (and stage into ``update_data``) the write's autonomy effects.

    * Becoming fully-autonomous, or changing the suite that vouches for it,
      needs the suite's latest completed run to pass the gate FOR THE CONFIG
      BEING WRITTEN (MEM-52). With ``refuse_promotion`` (PUT) a failing gate is
      409; otherwise (snapshot rollback) the write is applied bounded-autonomous
      and re-validated like a config change.
    * A behaviour-config change to an agent that IS fully-autonomous is
      accepted: demoted to bounded-autonomous in the same write, its suite run
      against the new config, promoted back when it passes
      (app.intelligence.autonomy_revalidation). Nothing is demoted when a run
      already vouches for the new config.
    * Changed again while being re-validated: re-test the newest config.
    * An operator's explicit autonomy change cancels a pending re-validation.
    """
    from app.intelligence import autonomy_revalidation as reval

    gate_on = _eval_gate_enabled()
    current_mode = current.get("autonomy_mode")
    new_autonomy = update_data.get("autonomy_mode") or current_mode
    new_eval_suite = update_data.get("eval_suite_id") or current.get("eval_suite_id")
    proposed = {**current, **update_data}
    behaviour_changed = _behaviour_config_changed(current, proposed)
    suite_changed = new_eval_suite != current.get("eval_suite_id")
    manual_autonomy_change = requested_mode is not None and requested_mode != current_mode
    plan = _AutonomyPlan(
        previous=reval.marker_of(current),
        tenant_plan=str(getattr(getattr(tenant_ctx, "plan", None), "value", None) or "free"),
        source=source,
    )
    revalidate = False
    if gate_on and new_autonomy == reval.FULLY_AUTONOMOUS and (
        current_mode != reval.FULLY_AUTONOMOUS or suite_changed or behaviour_changed
    ):
        if (current_mode == reval.FULLY_AUTONOMOUS and behaviour_changed) or not refuse_promotion:
            report = await _rollout_gate_report(
                request, tenant_ctx, agent_id=agent_id,
                eval_suite_id=str(new_eval_suite or "") or None, agent_config=proposed,
            )
            revalidate = not report["gate_passed"]
        else:
            await _enforce_rollout_gate(
                request, tenant_ctx, agent_id=agent_id, eval_suite_id=str(new_eval_suite),
                agent_config=proposed,
            )
    elif (
        gate_on
        and not manual_autonomy_change
        and reval.is_pending(current)
        and (behaviour_changed or suite_changed)
    ):
        revalidate = True

    if manual_autonomy_change and plan.previous and plan.previous.get("state") == reval.PENDING:
        update_data["autonomy_revalidation"] = reval.cancelled_by_operator(
            plan.previous, new_mode=str(requested_mode), actor=tenant_ctx.api_key_id
        )
        plan.cancelled = True

    if revalidate:
        from app.intelligence.eval_suite_store import EvalSuiteStore

        plan.eval_store = EvalSuiteStore(
            getattr(request.app.state, "db_session_factory", None), tenant_ctx.tenant_id
        )
        update_data["autonomy_mode"] = reval.BOUNDED_AUTONOMOUS
        try:
            plan.marker = await reval.begin_revalidation(
                plan.eval_store,
                agent_id=agent_id,
                proposed={**current, **update_data},
                source=source,
                actor=tenant_ctx.api_key_id,
                tenant_plan=plan.tenant_plan,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "REVALIDATION_UNAVAILABLE",
                    "message": (
                        "The eval run that re-validates this agent could not be "
                        "enqueued; nothing was changed. Try again."
                    ),
                },
            ) from exc
        update_data["autonomy_revalidation"] = plan.marker
        plan.cancelled = False
    return plan


async def _abandon_autonomy_change(plan: _AutonomyPlan) -> None:
    """The agent write failed: fail the run enqueued for it."""
    from app.intelligence.autonomy_revalidation import abandon_run

    await abandon_run(plan.eval_store, plan.marker, "the agent update was not saved")


async def _finish_autonomy_change(
    request: Request,
    tenant_ctx: TenantContext,
    *,
    agent_id: str,
    store: AgentStore,
    plan: _AutonomyPlan,
    requested_mode: str | None,
) -> None:
    """After the write: audit, dispatch the re-validation run, or cancel the old one."""
    from app.intelligence import autonomy_revalidation as reval

    audit_log = getattr(request.app.state, "audit_log", None)
    if plan.marker is not None:
        from app.api.enterprise import eval_run_dispatcher

        await reval.start_revalidation(
            eval_store=plan.eval_store,
            agent_store=store,
            tenant_ctx=tenant_ctx,
            agent_id=agent_id,
            marker=plan.marker,
            dispatch=eval_run_dispatcher(request, tenant_ctx, plan.eval_store),
            tenant_plan=plan.tenant_plan,
            previous=plan.previous,
            audit_log=audit_log,
        )
    elif plan.cancelled and plan.previous is not None:
        from app.intelligence.eval_suite_store import EvalSuiteStore

        await reval.abandon_run(
            EvalSuiteStore(
                getattr(request.app.state, "db_session_factory", None), tenant_ctx.tenant_id
            ),
            plan.previous,
            "re-validation cancelled: an operator changed the agent's autonomy",
        )
        await reval.audit_transition(
            tenant_id=tenant_ctx.tenant_id, agent_id=agent_id, outcome="revalidation_cancelled",
            actor=tenant_ctx.api_key_id, audit_log=audit_log,
            note=(
                f"autonomy set to {requested_mode} ({plan.source}) while re-validation run "
                f"{plan.previous.get('run_id')} was pending; it will not re-promote"
            ),
        )


# FIX 5: delete cleans up associated schedules
@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(request: Request, agent_id: str) -> None:
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    if await store.get_async(agent_id, tenant_ctx=tenant_ctx) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    # TRG-31: delete the agent's schedules FIRST, in Postgres (every replica's
    # schedules, not this process's cache) — schedules.agent_id is ON DELETE SET
    # NULL, so after the agent row goes they can no longer be found and keep
    # firing. An outage is a 503 and the agent is kept (the call is retryable),
    # never a silently orphaned schedule.
    schedule_store = getattr(request.app.state, "schedule_store", None)
    if schedule_store is not None:
        from app.triggers.store import ScheduleStoreUnavailableError

        try:
            await schedule_store.delete_for_agent_async(agent_id, tenant_ctx=tenant_ctx)
        except ScheduleStoreUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not delete the agent's schedules; retry",
            ) from exc

    removed = await store.delete_async(agent_id, tenant_ctx=tenant_ctx)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )


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

            from app.db.rls import sqlalchemy_rls_context

            # agent_permissions is FORCE RLS: without the tenant GUC the read
            # returned zero rows under the NOBYPASSRLS role.
            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
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

            from app.db.rls import sqlalchemy_rls_context

            # FORCE RLS table: the write needs the tenant GUC, or under the
            # NOBYPASSRLS role it failed, was swallowed, and answered "updated".
            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                # Delete existing permissions for this agent+tenant
                await session.execute(
                    text(
                        "DELETE FROM agent_permissions WHERE agent_id = :aid AND tenant_id = :tid"
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
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Agent permissions could not be persisted",
            ) from exc
        # Enforcement reads these rows (app.governance.agent_permissions) with a
        # short TTL cache; drop this process's copy so the change binds now.
        from app.governance.agent_permissions import invalidate_agent_permissions

        invalidate_agent_permissions(tenant_ctx.tenant_id, agent_id)

    # Also update in-memory cache (legacy path / no-DB mode)
    in_mem_perms = body.permissions if isinstance(body.permissions, dict) else {}
    store.update_permissions(agent_id, in_mem_perms, tenant_ctx=tenant_ctx)

    return {"agent_id": agent_id, "permissions": body.permissions, "status": "updated"}


@router.put("/{agent_id}/knowledge")
async def update_knowledge_binding(
    request: Request, agent_id: str, body: UpdateKnowledgeBindingRequest
) -> dict[str, Any]:
    """Bind knowledge collections to this agent."""
    tenant = _require_tenant(request)
    store = _agent_store(request)
    # The knowledge ACL used the sync, cache-only update: lost on restart and
    # different on every replica. It now persists through update_async.
    if not await store.update_async(
        agent_id, {"allowed_collection_ids": list(body.collection_ids)}, tenant_ctx=tenant
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )
    return {
        "agent_id": agent_id,
        "allowed_collection_ids": body.collection_ids,
        "status": "updated",
    }


@router.post("/{agent_id}/knowledge/{knowledge_id}", status_code=204)
async def assign_knowledge_collection(request: Request, agent_id: str, knowledge_id: str) -> None:
    """Add a single knowledge collection to this agent's allowed list."""
    tenant = _require_tenant(request)
    store = _agent_store(request)
    agent = await store.get_async(agent_id, tenant_ctx=tenant)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    current_ids: list[str] = list(agent.get("allowed_collection_ids") or [])
    if knowledge_id not in current_ids:
        current_ids.append(knowledge_id)
        if not await store.update_async(
            agent_id, {"allowed_collection_ids": current_ids}, tenant_ctx=tenant
        ):
            raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")


@router.delete("/{agent_id}/knowledge/{knowledge_id}", status_code=204)
async def remove_knowledge_collection(request: Request, agent_id: str, knowledge_id: str) -> None:
    """Remove a single knowledge collection from this agent's allowed list."""
    tenant = _require_tenant(request)
    store = _agent_store(request)
    agent = await store.get_async(agent_id, tenant_ctx=tenant)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")
    current_ids: list[str] = [
        i for i in (agent.get("allowed_collection_ids") or []) if i != knowledge_id
    ]
    if not await store.update_async(
        agent_id, {"allowed_collection_ids": current_ids}, tenant_ctx=tenant
    ):
        raise HTTPException(status_code=404, detail=f"Agent {agent_id} not found")


@router.get("/{agent_id}/versions")
async def list_agent_versions(request: Request, agent_id: str) -> list[dict[str, Any]]:
    """List all saved version snapshots of an agent (503 when the store is down)."""
    tenant = _require_tenant(request)
    store = _agent_store(request)
    db = getattr(store, "_db", None)

    if db is not None:
        try:
            return await _load_snapshots_from_db(tenant.tenant_id, agent_id, db)
        except Exception as exc:
            raise _snapshot_store_unavailable("load", exc) from exc
    return _AGENT_SNAPSHOTS.get(f"{tenant.tenant_id}:{agent_id}", [])


@router.post("/{agent_id}/snapshot")
async def snapshot_agent(request: Request, agent_id: str) -> dict[str, Any]:
    """Save a version snapshot of the current agent config.

    With a database the snapshot is stored (and numbered) before it is
    returned; a failure is a 503, never a snapshot that does not exist.
    """
    tenant = _require_tenant(request)
    store = _agent_store(request)
    agent = await store.get_async(agent_id, tenant_ctx=tenant)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    db = getattr(store, "_db", None)
    snapshot: dict[str, Any] = {
        **agent,
        "snapshot_id": uuid.uuid4().hex,
        "snapshotted_at": datetime.now(UTC).isoformat(),
    }

    if db is not None:
        snapshot["version"] = 0  # allocated by the INSERT
        try:
            snapshot["version"] = await _save_snapshot_to_db(snapshot, db, tenant.tenant_id)
        except Exception as exc:
            raise _snapshot_store_unavailable("persist", exc) from exc
    else:
        key = f"{tenant.tenant_id}:{agent_id}"
        existing = _AGENT_SNAPSHOTS.setdefault(key, [])
        snapshot["version"] = max((int(s.get("version", 0)) for s in existing), default=0) + 1
        existing.append(snapshot)

    return snapshot


# FIX 3: rollback now persists to DB via update_async
@router.post("/{agent_id}/rollback/{snapshot_id}")
async def rollback_agent(request: Request, agent_id: str, snapshot_id: str) -> dict[str, Any]:
    """Roll back agent to a previous snapshot (404 when the agent is gone)."""
    tenant = _require_tenant(request)
    store = _agent_store(request)
    db = getattr(store, "_db", None)

    if db is not None:
        try:
            snapshots = await _load_snapshots_from_db(tenant.tenant_id, agent_id, db)
        except Exception as exc:
            raise _snapshot_store_unavailable("load", exc) from exc
    else:
        key = f"{tenant.tenant_id}:{agent_id}"
        snapshots = _AGENT_SNAPSHOTS.get(key, [])

    snapshot = next((s for s in snapshots if s.get("snapshot_id") == snapshot_id), None)
    if snapshot is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")

    restore_data = {
        k: v
        for k, v in snapshot.items()
        if k not in (
            "snapshot_id", "snapshotted_at", "version", "agent_id", "tenant_id",
            # The re-validation state is the live state machine's, never a
            # snapshot's: restoring an old pending marker could re-promote.
            "autonomy_revalidation", "pending_promotion",
        )
    }

    current = await store.get_async(agent_id, tenant_ctx=tenant)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    # A snapshot's fully-autonomous mode is restored only through the rollout
    # gate: when no run vouches for the restored config, it is restored
    # bounded-autonomous and re-validated (auto-promoted when the suite passes).
    requested_mode = restore_data.get("autonomy_mode")
    plan = await _plan_autonomy_change(
        request, tenant, agent_id=agent_id, current=current, update_data=restore_data,
        requested_mode=str(requested_mode) if requested_mode else None,
        source=f"snapshot_rollback:{snapshot_id}", refuse_promotion=False,
    )

    # Persist rollback to DB (and update in-memory cache)
    try:
        updated = await store.update_async(agent_id, restore_data, tenant_ctx=tenant)
    except Exception:
        await _abandon_autonomy_change(plan)
        raise
    if not updated:
        await _abandon_autonomy_change(plan)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    await _finish_autonomy_change(
        request, tenant, agent_id=agent_id, store=store, plan=plan,
        requested_mode=str(requested_mode) if requested_mode else None,
    )
    restored = await store.get_async(agent_id, tenant_ctx=tenant) or {}
    return {
        "agent_id": agent_id,
        "restored_from": snapshot_id,
        "status": "rolled_back",
        "autonomy_mode": restored.get("autonomy_mode"),
        "autonomy_revalidation": restored.get("autonomy_revalidation"),
        "pending_promotion": bool(restored.get("pending_promotion")),
    }


@router.get("/{agent_id}/export")
async def export_agent(
    request: Request, agent_id: str, format: str = "openai"  # noqa: A002  # public query param name, part of the API contract
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
            "model": model_override or "gpt-5.2",
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
    request: Request, agent_id: str, body: CloneAgentRequest | None = None
) -> dict[str, Any]:
    """Clone an existing agent with optional name override.

    The body is optional — a plain "clone" with no overrides (the common case,
    e.g. the Clone button) must not 422 on an empty POST.
    """
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)

    original = await store.get_async(agent_id, tenant_ctx=tenant_ctx)
    if original is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    # A clone has never passed its OWN rollout gate (runs vouch per agent id),
    # so a fully-autonomous original is cloned bounded-autonomous.
    original_mode = original.get("autonomy_mode", "bounded-autonomous")
    clone_mode = (
        "bounded-autonomous"
        if original_mode == "fully-autonomous" and _eval_gate_enabled()
        else original_mode
    )
    clone_data: dict[str, Any] = {
        "name": (body.name if body else None) or f"{original['name']} (copy)",
        "goal_template": original.get("goal_template", ""),
        "autonomy_mode": clone_mode,
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
        "timeout_seconds": original.get("timeout_seconds", 0),
        "domain_context": original.get("domain_context") or "general",
        "domain_metadata": dict(original.get("domain_metadata") or {}),
    }
    _clone_flags = normalize_pattern_flags(pattern_flags_from_record(original))
    clone_data.update(_clone_flags)
    clone_data["pattern_flags"] = _clone_flags

    # Check agent limit before creating the clone
    from app.tenancy.limits import check_agent_limit

    # The durable COUNT (every replica's agents), not this replica's cache —
    # the cache let a clone exceed the plan limit (create already uses it).
    check_agent_limit(tenant_ctx, await store.count_async(tenant_ctx=tenant_ctx))

    clone_id = await _create_agent_record(store, clone_data, tenant_ctx=tenant_ctx)
    result: dict[str, Any] = {**clone_data, "agent_id": clone_id, "cloned_from": agent_id}
    if clone_mode != original_mode:
        note = (
            f"Cloned as bounded-autonomous: agent {agent_id} is fully-autonomous, but the "
            "clone has never passed its own rollout gate. Run its eval suite against the "
            "clone, then promote it."
        )
        result["autonomy_note"] = note
        from app.intelligence.autonomy_revalidation import audit_transition

        await audit_transition(
            tenant_id=tenant_ctx.tenant_id, agent_id=clone_id, outcome="clone_bounded",
            actor=tenant_ctx.api_key_id, note=note,
            audit_log=getattr(request.app.state, "audit_log", None),
        )
    return result


# ── Agent Identity / JWT Service-Account Credentials ─────────────────────────


class IssueCredentialRequest(BaseModel):
    # Every credential is an RS256 service-account keypair; other "types" the UI
    # used to offer (api_key, mtls) were stored as labels on the same keypair.
    key_type: Literal["service_account"] = "service_account"
    # The agent JWT's scopes are intersected with the agent role's scopes, so any
    # other value (e.g. "read:goals", "tenancy:write") yields a credential that
    # can never authorize anything. Refuse it at issuance instead.
    scopes: list[str] = Field(min_length=1, max_length=32)
    expires_in_days: int | None = Field(default=90, ge=1, le=3650)
    description: str = Field(default="", max_length=500)

    @field_validator("scopes")
    @classmethod
    def _agent_scopes_only(cls, v: list[str]) -> list[str]:
        from app.auth.scope_enforcement import ROLE_SCOPES

        unknown = sorted(set(v) - ROLE_SCOPES["agent"])
        if unknown:
            raise ValueError(
                f"scopes not grantable to an agent: {unknown}; "
                f"allowed: {sorted(ROLE_SCOPES['agent'])}"
            )
        return sorted(set(v))


@router.get("/{agent_id}/credentials")
async def list_agent_credentials(
    agent_id: str, request: Request, _owned: dict[str, Any] = Depends(require_owned_agent)
) -> list[dict[str, Any]]:
    """List service-account credentials for an agent (public keys only — private keys never returned)."""  # noqa: E501
    tenant = _require_tenant(request)
    svc = getattr(request.app.state, "agent_identity_service", None)
    if svc is None:
        # Same as issue/revoke: a misconfigured replica must not report "no credentials".
        raise HTTPException(503, "Agent identity service not available")
    try:
        return await svc.list_credentials(agent_id=agent_id, tenant_id=tenant.tenant_id)
    except Exception as exc:
        raise HTTPException(500, f"Failed to list credentials: {exc}") from exc


@router.post("/{agent_id}/credentials", status_code=status.HTTP_201_CREATED)
async def issue_agent_credential(
    agent_id: str,
    request: Request,
    body: IssueCredentialRequest,
    _owned: dict[str, Any] = Depends(require_owned_agent),
) -> dict[str, Any]:
    """Issue a new RS256 service-account credential for an agent.

    The private key is returned ONCE in the response — save it immediately.
    """
    tenant = _require_tenant(request)

    # Rate limit: max 10 credential requests per minute per agent
    redis_client = getattr(request.app.state, "_rate_limiter_redis", None)
    if redis_client:
        try:
            rl_key = f"cred_rl:{agent_id}:{tenant.tenant_id}"
            count = await redis_client.incr(rl_key)
            if count == 1:
                await redis_client.expire(rl_key, 60)
            if count > 10:
                raise HTTPException(
                    429,
                    "Too many credential requests",
                    headers={"Retry-After": "60"},
                )
        except HTTPException:
            raise
        except Exception:
            pass  # Non-fatal: rate limit check failed, continue

    svc = getattr(request.app.state, "agent_identity_service", None)
    if svc is None:
        raise HTTPException(503, "Agent identity service not available")
    try:
        result = await svc.issue_credential(
            agent_id=agent_id,
            tenant_id=tenant.tenant_id,
            created_by=tenant.api_key_id,
            scopes=body.scopes,
            key_type=body.key_type,
            expires_in_days=body.expires_in_days,
            description=body.description,
        )
        return result
    except RuntimeError as exc:
        # No credential store / vault: the credential could never be exchanged
        # for a token, so it is not issued (fail closed).
        raise HTTPException(503, f"Agent identity unavailable: {exc}") from exc
    except Exception as exc:
        raise HTTPException(500, f"Failed to issue credential: {exc}") from exc


@router.delete("/{agent_id}/credentials/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_agent_credential(
    agent_id: str,
    key_id: str,
    request: Request,
    _owned: dict[str, Any] = Depends(require_owned_agent),
) -> None:
    """Immediately revoke a service-account credential by key_id."""
    tenant = _require_tenant(request)
    svc = getattr(request.app.state, "agent_identity_service", None)
    if svc is None:
        raise HTTPException(503, "Agent identity service not available")
    revoked = await svc.revoke_credential(
        key_id=key_id, tenant_id=tenant.tenant_id, agent_id=agent_id
    )
    if not revoked:
        raise HTTPException(404, f"Credential {key_id} not found or already revoked")


@router.post("/{agent_id}/token")
async def exchange_agent_token(
    agent_id: str, request: Request, _owned: dict[str, Any] = Depends(require_owned_agent)
) -> dict[str, Any]:
    """Exchange a service-account credential for a short-lived RS256 JWT (15 minutes).

    Requires proof of possession of the credential's private key: the JSON body
    carries an RFC 7523 client assertion,
    ``{"client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
    "client_assertion": "<JWT>"}``, RS256-signed with the private key returned at
    issuance: ``kid`` = the key_id, ``iss`` = ``sub`` = ``agent:<agent_id>``,
    ``aud`` = ``/agents/<agent_id>/token``, ``exp`` at most 5 minutes out and a
    single-use ``jti`` (see ``app.auth.agent_identity.build_client_assertion``).
    The key_id alone is public (it is the JWT ``kid``) and no longer suffices.

    The returned token authenticates as ``Authorization: Bearer <jwt>``: the
    TenantMiddleware verifies it against this tenant's registered, non-revoked
    key and grants roles=("agent",) limited to the credential's scopes.
    """
    from app.auth.agent_identity import CLIENT_ASSERTION_TYPE

    tenant = _require_tenant(request)
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        body = {}
    assertion = body.get("client_assertion")
    if (
        body.get("client_assertion_type") != CLIENT_ASSERTION_TYPE
        or not isinstance(assertion, str)
        or not assertion
    ):
        raise HTTPException(
            401,
            "client_assertion required: a private_key_jwt signed with the credential's "
            f"private key (client_assertion_type={CLIENT_ASSERTION_TYPE})",
        )

    svc = getattr(request.app.state, "agent_identity_service", None)
    if svc is None:
        raise HTTPException(503, "Agent identity service not available")

    try:
        key_id = await svc.verify_client_assertion(
            assertion, agent_id, tenant.tenant_id, f"/agents/{agent_id}/token"
        )
    except RuntimeError as exc:
        raise HTTPException(503, f"Agent identity unavailable: {exc}") from exc
    if key_id is None:
        raise HTTPException(401, "Invalid, expired or replayed client assertion")
    claimed = request.headers.get("X-Agent-Key-Id") or body.get("key_id")
    if claimed is not None and claimed != key_id:
        raise HTTPException(401, "key_id does not match the client assertion")

    token = await svc.issue_agent_jwt(agent_id=agent_id, key_id=key_id, tenant_id=tenant.tenant_id)
    if token is None:
        raise HTTPException(404, "Credential not found, expired, or revoked")

    from datetime import datetime

    from jose import jwt as _jwt

    try:
        claims = _jwt.get_unverified_claims(token)
        exp = datetime.fromtimestamp(claims.get("exp", 0), tz=UTC).isoformat()
    except Exception:
        exp = None

    return {"token": token, "expires_at": exp, "token_type": "Bearer"}


def _eval_gate_enabled() -> bool:
    """Owner decision: is the fully-autonomous eval rollout gate enforced?"""
    from app.intelligence.rollout_gate import eval_gate_enabled

    return eval_gate_enabled()


def _behaviour_config_changed(current: dict[str, Any], proposed: dict[str, Any]) -> bool:
    from app.intelligence.rollout_gate import behaviour_config_changed

    return behaviour_config_changed(current, proposed)


async def _rollout_gate_report(
    request: Request,
    tenant_ctx: TenantContext,
    *,
    agent_id: str | None,
    eval_suite_id: str | None,
    agent_config: dict[str, Any] | None = None,
    min_pass_rate: float | None = None,
) -> dict[str, Any]:
    """The rollout-gate report for *eval_suite_id*; 503 when it cannot be computed.

    ``agent_config`` is the configuration the gate must vouch for (MEM-52): the
    agent's record, or the record an update is about to write.
    """
    from app.intelligence.rollout_gate import check_agent_rollout_gate

    try:
        return await check_agent_rollout_gate(
            agent_id=agent_id,
            eval_suite_id=eval_suite_id,
            tenant_id=tenant_ctx.tenant_id,
            db=getattr(request.app.state, "db_session_factory", None),
            agent_config=agent_config,
            min_pass_rate=min_pass_rate,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "ROLLOUT_GATE_UNAVAILABLE",
                "message": "The rollout gate could not be evaluated; try again.",
            },
        ) from exc


async def _enforce_rollout_gate(
    request: Request,
    tenant_ctx: TenantContext,
    *,
    agent_id: str | None,
    eval_suite_id: str,
    agent_config: dict[str, Any] | None = None,
) -> None:
    """Refuse (409, with the gate report) to make an agent fully-autonomous on a failing suite."""
    report = await _rollout_gate_report(
        request, tenant_ctx, agent_id=agent_id, eval_suite_id=eval_suite_id,
        agent_config=agent_config,
    )
    if not report["gate_passed"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "ROLLOUT_GATE_FAILED",
                "message": f"Rollout gate failed: {report['reason']}",
                "gate": report,
            },
        )


@router.get("/{agent_id}/rollout-gate")
async def check_rollout_gate(
    request: Request,
    agent_id: str,
    eval_suite_id: str = "",
    min_pass_rate: float | None = Query(default=None, ge=0.0, le=1.0),
) -> dict[str, Any]:
    """Whether the agent's eval suite passes well enough for fully-autonomous rollout.

    Reads the named suite's (default: the agent's attached suite) latest
    completed run. The same check is enforced when an agent is created as, or
    switched to, fully-autonomous.
    """
    tenant_ctx = _require_tenant(request)
    store = _agent_store(request)
    agent = await store.get_async(agent_id, tenant_ctx=tenant_ctx)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )
    report = await _rollout_gate_report(
        request,
        tenant_ctx,
        agent_id=agent_id,
        eval_suite_id=eval_suite_id or agent.get("eval_suite_id") or "",
        agent_config=agent,
        min_pass_rate=min_pass_rate,
    )
    report["agent_id"] = agent_id
    return report


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
                        "fully-autonomous mode requires an attached eval suite with passing results"
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
                        missing_reason = f"Connector '{cid}' not yet registered in MCP registry"
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
                                        missing_reason = f"Connector '{cid}' missing API key secret"
                                except Exception:
                                    pass  # Secret store check failed — assume OK
                except Exception:
                    pass  # Registry check failed — skip check

            check_status = check_status_override or ("pass" if connector_ready else "fail")
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
