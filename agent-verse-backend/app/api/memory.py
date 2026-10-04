"""Memory management REST API."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.db.rls import sqlalchemy_rls_context
from app.tenancy.rbac import require_role

router = APIRouter(prefix="/memory", tags=["memory"])


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return ctx


def _get_ltm(request: Request) -> Any:
    return getattr(request.app.state, "long_term_memory", None)


def _get_db(request: Request) -> Any:
    """The configured DB session factory, or None when no DB is wired.

    Only ``app.state.db_session_factory`` (set by the lifespan once pools are up)
    counts as "a DB is configured". This used to fall back to building the
    global engine, so a DB-less build tried a phantom DB on every call and
    relied on the error fallback below to reach the cache. With a DB configured
    the DB is authoritative: failures are 503, never a cache answer.
    """
    return getattr(request.app.state, "db_session_factory", None)


def _db_unavailable(op: str, exc: Exception) -> HTTPException:
    import logging

    logging.getLogger(__name__).warning("%s_db_failed: %s", op, exc)
    return HTTPException(status_code=503, detail="Memory store unavailable; please retry")


async def _db_delete_memory(db: Any, tenant_id: str, memory_id: str) -> bool:
    """Delete one row under RLS. True if a row was deleted. Raises on DB error."""
    from sqlalchemy import text

    async with (
        db() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        result = await session.execute(
            text("DELETE FROM long_term_memory WHERE id=:id AND tenant_id=:tid"),
            {"id": memory_id, "tid": tenant_id},
        )
    return bool(result.rowcount)


def _evict_cached(request: Request, tenant_ctx: Any, memory_id: str) -> bool:
    mem = _get_ltm(request)
    if mem is None:
        return False
    return bool(mem.delete(memory_id=memory_id, tenant_ctx=tenant_ctx))


async def _screen_memory_or_http(tenant_id: str, content: str) -> str:
    """MEMORY_WRITE guardrail for a user-authored memory (create / edit).

    Every other write path (``LongTermMemoryStore.store_async``, chat, goal
    learning) screens memory content; these routes wrote it unvetted. Scans the
    full text. A block is a 422, a redacting rule's output is what gets stored,
    and content the guardrail could not vet is never stored (503, fail closed).
    """
    from app.memory.long_term import (
        LongTermMemoryBlockedError,
        LongTermMemoryUnavailableError,
        screen_user_memory_content,
    )

    try:
        return await screen_user_memory_content(content, tenant_id=tenant_id)
    except LongTermMemoryBlockedError as exc:
        raise HTTPException(
            status_code=422, detail="Memory content rejected by the memory-write guardrail"
        ) from exc
    except LongTermMemoryUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail="Memory-write guardrail is unavailable; memory was not saved",
        ) from exc


async def _embed_memory(request: Request, content: str) -> tuple[str, str, int] | None:
    """``(pgvector literal, model, raw dim)`` for *content*, or None.

    Non-fatal like ``LongTermMemoryStore.store_async``: without a vector the
    row is still found by keyword recall. Never silent.
    """
    from app.memory.long_term import embed_ltm_content

    return await embed_ltm_content(getattr(request.app.state, "embedder", None), content)


class CreateMemoryRequest(BaseModel):
    content: str
    memory_type: str = "fact"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    tags: list[str] = []


@router.post("", status_code=201)
async def create_memory(request: Request, body: CreateMemoryRequest) -> dict:
    """Manually create a long-term memory entry."""
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)
    # long_term_memory.id is VARCHAR(32): a dashed uuid4 (36 chars) failed every insert.
    memory_id = uuid.uuid4().hex
    content = await _screen_memory_or_http(tenant_ctx.tenant_id, body.content)

    if db is not None:
        embedded = await _embed_memory(request, content)
        try:
            from sqlalchemy import text

            params: dict[str, Any] = {
                "id": memory_id,
                "tid": tenant_ctx.tenant_id,
                "content": content,
                "mt": body.memory_type,
                "conf": body.confidence,
                # JSON column: a raw Python list failed every insert
                # ("invalid input for query argument $6: []").
                "tags": json.dumps(body.tags),
            }
            if embedded is not None:
                params["emb"], params["emodel"], params["edim"] = embedded
                sql = """
                        INSERT INTO long_term_memory
                            (id, tenant_id, content, memory_type, confidence, tags, created_at,
                             embedding, embedding_model, embedding_dim)
                        VALUES (:id, :tid, :content, :mt, :conf, CAST(:tags AS json), NOW(),
                                CAST(:emb AS vector), :emodel, :edim)
                    """
            else:
                sql = """
                        INSERT INTO long_term_memory
                            (id, tenant_id, content, memory_type, confidence, tags, created_at)
                        VALUES (:id, :tid, :content, :mt, :conf, CAST(:tags AS json), NOW())
                    """
            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                await session.execute(text(sql), params)
            return {
                "id": memory_id,
                "content": content,
                "memory_type": body.memory_type,
                "confidence": body.confidence,
                "tags": body.tags,
                "created_at": "",
            }
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("create_memory_db_failed: %s", exc)
            # With a database the write must be durable. It used to fall through
            # to the per-process cache below and answer 201 — the memory was
            # never persisted, and invisible to every other replica.
            raise HTTPException(
                status_code=503, detail="Memory store unavailable; memory was not saved"
            ) from exc

    # In-memory fallback
    ltm = _get_ltm(request)
    if ltm is not None:
        raw = getattr(ltm, "_memories", {})
        if isinstance(raw, dict):
            if tenant_ctx.tenant_id not in raw:
                raw[tenant_ctx.tenant_id] = []
            from types import SimpleNamespace

            mem_obj = SimpleNamespace(
                id=memory_id,
                memory_id=memory_id,
                content=content,
                memory_type=body.memory_type,
                confidence=body.confidence,
                tags=body.tags,
                created_at="",
                tenant_id=tenant_ctx.tenant_id,
            )
            raw[tenant_ctx.tenant_id].append(mem_obj)

    return {
        "id": memory_id,
        "content": content,
        "memory_type": body.memory_type,
        "confidence": body.confidence,
        "tags": body.tags,
        "created_at": "",
    }


@router.get("")
async def list_memories(
    request: Request,
    limit: int = Query(20, ge=1, le=200),
    memory_type: str | None = Query(None),
) -> list[dict]:
    """List long-term memories stored for this tenant."""
    tenant_ctx = _require_tenant(request)
    ltm = _get_ltm(request)
    db = _get_db(request)

    if db is not None:
        try:
            from sqlalchemy import text

            sql = "SELECT id, content, memory_type, confidence, tags, created_at FROM long_term_memory WHERE tenant_id=:tid"  # noqa: E501
            params: dict[str, Any] = {"tid": tenant_ctx.tenant_id}
            if memory_type:
                sql += " AND memory_type=:mt"
                params["mt"] = memory_type
            sql += f" ORDER BY created_at DESC LIMIT {limit}"
            async with (
                db() as session,
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                rows = (await session.execute(text(sql), params)).fetchall()
            return [
                {
                    "id": r[0],
                    "content": r[1],
                    "memory_type": r[2],
                    "confidence": r[3],
                    "tags": r[4] or [],
                    "created_at": r[5].isoformat() if r[5] else "",
                }
                for r in rows
            ]
        except Exception as exc:
            # Was: log and serve this replica's cache as if it were the tenant's
            # memories. With a DB configured the DB is the only truth.
            raise _db_unavailable("list_memories", exc) from exc

    # In-memory fallback (no DB configured: tests / single-process dev).
    # _memories is dict[tenant_id, list[LongTermMemory]].
    if ltm is not None:
        raw = getattr(ltm, "_memories", {})
        tenant_memories: list = raw.get(tenant_ctx.tenant_id, []) if isinstance(raw, dict) else []
        if memory_type:
            tenant_memories = [
                m for m in tenant_memories if getattr(m, "memory_type", "") == memory_type
            ]
        return [
            {
                "id": getattr(m, "id", getattr(m, "memory_id", "")),
                "content": getattr(m, "content", ""),
                "memory_type": getattr(m, "memory_type", ""),
                "confidence": getattr(m, "confidence", 0.8),
                "tags": list(getattr(m, "tags", []) or []),
                "created_at": getattr(m, "created_at", "") or "",
            }
            for m in tenant_memories[:limit]
        ]
    return []


@router.get("/recall")
async def recall_memories(
    request: Request,
    q: str = Query(..., description="Query to recall relevant memories"),
    limit: int = Query(5, ge=1, le=20),
) -> dict:
    """Recall memories relevant to a query using semantic search."""
    tenant_ctx = _require_tenant(request)
    ltm = _get_ltm(request)
    embedder = getattr(request.app.state, "embedder", None)
    db = _get_db(request)

    if ltm is None:
        return {"query": q, "results": []}

    memories = await ltm.recall_async(
        query=q,
        tenant_ctx=tenant_ctx,
        top_k=limit,
        db=db,
        embedder=embedder,
    )

    return {
        "query": q,
        "results": [
            {
                "content": getattr(m, "content", str(m)),
                "confidence": getattr(m, "confidence", 0.8),
                "memory_type": getattr(m, "memory_type", ""),
                "source": getattr(m, "source_goal_id", ""),
            }
            for m in memories
        ],
    }


@router.get("/long-term")
async def list_long_term_memories(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """List long-term memories for this tenant (bounded + offset-pageable).

    Read from Postgres under the tenant's RLS (MEM-04) — it listed this
    replica's per-process cache, so another replica or a restart showed a
    partial or empty list. A DB failure is 503.
    """
    from app.memory.long_term import LongTermMemoryUnavailableError

    tenant = _require_tenant(request)
    mem = getattr(request.app.state, "long_term_memory", None)
    if mem is None:
        return []
    try:
        memories = await mem.list_all_async(tenant_ctx=tenant, limit=limit, offset=offset)
    except LongTermMemoryUnavailableError as exc:
        raise _db_unavailable("list_long_term_memories", exc) from exc
    return [
        {
            "memory_id": m.memory_id,
            "content": m.content,
            "memory_type": m.memory_type,
            "confidence": getattr(m, "confidence", 1.0),
            "source_goal_id": getattr(m, "source_goal_id", ""),
            "tags": getattr(m, "tags", []),
        }
        for m in memories
    ]


@router.get("/execution")
async def list_execution_memories(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """List recorded executions (winning plans and failures), newest first.

    Served from ``execution_memory`` under the tenant's RLS (MEM-06) — it
    returned the in-process list of whichever replica ran the goal. 503 on a
    DB error.
    """
    tenant = _require_tenant(request)
    exec_mem = getattr(request.app.state, "exec_memory", None)
    if exec_mem is None:
        return []
    try:
        rows: list[dict[str, Any]] = await exec_mem.list_async(
            tenant_id=tenant.tenant_id, db=_get_db(request), limit=limit, offset=offset
        )
    except Exception as exc:
        raise _db_unavailable("list_execution_memories", exc) from exc
    return rows


@router.get("/records")
async def list_memory_records(
    request: Request,
    kind: str | None = Query(
        None, description="Filter by memory_kind (episodic|procedural|reflexion|…)"
    ),
    goal_id: str | None = Query(None, description="Filter by source_goal_id (goal-linkage)"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    """List canonical governed memory records for this tenant.

    Unlike ``GET /memory`` (flat long_term_memory rows), this surfaces the real
    ``memory_kind`` categorization (episodic / procedural / reflexion / semantic /
    …) and ``source_goal_id`` goal-linkage that live in the canonical
    ``memory_records`` table, with per-record TTL (``expires_at``). RLS/tenant
    scoped; returns an honest empty list when there are none.
    """
    from typing import get_args

    from app.memory.contracts import MemoryKind

    tenant_ctx = _require_tenant(request)
    allowed_kinds = set(get_args(MemoryKind))
    if kind is not None and kind not in allowed_kinds:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown memory kind {kind!r}; expected one of {sorted(allowed_kinds)}",
        )

    repo = getattr(request.app.state, "memory_repository", None)
    if repo is None or not hasattr(repo, "list_records"):
        return {"records": [], "total": 0, "kinds": {}}

    memory_kinds = frozenset({kind}) if kind else None
    records = await repo.list_records(
        tenant_ctx.tenant_id,
        memory_kinds=memory_kinds,
        source_goal_id=goal_id,
        limit=limit,
    )

    kind_counts: dict[str, int] = {}
    serialized: list[dict[str, Any]] = []
    for rec in records:
        kind_counts[rec.memory_kind] = kind_counts.get(rec.memory_kind, 0) + 1
        serialized.append(
            {
                "memory_id": rec.memory_id,
                "memory_kind": rec.memory_kind,
                "content": rec.safe_summary,
                "source_goal_id": rec.source_goal_id,
                "source_execution_id": rec.source_execution_id,
                "classification": rec.classification,
                "confidence": rec.confidence,
                "lifecycle_state": rec.lifecycle_state,
                "evidence_refs": list(rec.evidence_refs),
                "recall_count": rec.recall_count,
                "helpful_count": rec.helpful_count,
                "harmful_count": rec.harmful_count,
                "expires_at": rec.expires_at.isoformat() if rec.expires_at else None,
                "created_at": rec.created_at.isoformat() if rec.created_at else None,
                "updated_at": rec.updated_at.isoformat() if rec.updated_at else None,
            }
        )

    return {"records": serialized, "total": len(serialized), "kinds": kind_counts}


class UpdateMemoryRequest(BaseModel):
    content: str | None = Field(default=None, min_length=1)
    memory_type: str | None = Field(default=None, min_length=1, max_length=50)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    tags: list[str] | None = None


_UPDATABLE_MEMORY_FIELDS = ("content", "memory_type", "confidence", "tags")


class CreateIntentionRequest(BaseModel):
    intention: str = Field(min_length=1, max_length=2000)
    due_at: datetime
    expires_at: datetime | None = None
    agent_id: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=200)


def _prospective_service(request: Request) -> Any:
    svc = getattr(request.app.state, "prospective_memory_service", None)
    if svc is None:
        raise HTTPException(503, "Prospective memory is not available")
    return svc


async def _authorize_goal_submission(
    request: Request, tenant: Any, agent_id: str | None
) -> tuple[Any, str | None]:
    """RV-07: an intention runs as a goal, so creating one needs goal-submit rights.

    The route itself is gated by ``memory:write`` only; here the caller must
    also hold ``goals:write`` (key scopes AND role/assignment scopes, as the
    scope middleware evaluates POST /goals), be an active API key that can be
    re-verified when the intention fires, and — with an ``agent_id`` — that
    agent must exist in this tenant (what POST /goals checks). Returns the
    principal to store and the agent id to run.
    """
    from app.memory import prospective_auth
    from app.memory.prospective_auth import IntentionNotAuthorizedError, IntentionPrincipal

    try:
        principal = await prospective_auth.authorize_intention_principal(
            _get_db(request), tenant.tenant_id, IntentionPrincipal.from_context(tenant)
        )
    except IntentionNotAuthorizedError as exc:
        raise HTTPException(
            403,
            {
                "error": "INSUFFICIENT_SCOPE",
                "required_scope": prospective_auth.GOAL_SUBMIT_SCOPE,
                "detail": f"A deferred intention runs as a goal: {exc.reason}",
            },
        ) from exc
    except prospective_auth.PrincipalCheckUnavailableError as exc:
        raise HTTPException(503, "Could not verify goal-submit permission; nothing stored") from exc

    # Agent-scoped keys (``agentkey:`` / ``agent:``) are not api_keys rows, so
    # the principal check above already refused them.
    if agent_id:
        store = getattr(request.app.state, "agent_store", None)
        if store is None:
            raise HTTPException(503, "Agent registry unavailable; nothing stored")
        try:
            getter = getattr(store, "get_async", None)
            if getter is not None:
                record = await getter(agent_id, tenant_ctx=tenant)
            else:
                record = store.get(agent_id, tenant_ctx=tenant)
        except Exception as exc:
            raise _db_unavailable("create_prospective_intention_agent", exc) from exc
        if record is None:
            raise HTTPException(404, f"Agent not found: {agent_id}")
    return principal, agent_id


@router.post("/prospective", status_code=201)
async def create_prospective_intention(request: Request, body: CreateIntentionRequest) -> dict:
    """Schedule a deferred intention; it runs as a goal for this tenant when due (MEM-16)."""
    from app.memory.long_term import LongTermMemoryBlockedError, LongTermMemoryUnavailableError
    from app.memory.prospective_runtime import (
        ProspectiveIntentionError,
        create_intention,
        intention_json,
    )

    tenant = _require_tenant(request)
    service = _prospective_service(request)
    principal, agent_id = await _authorize_goal_submission(request, tenant, body.agent_id)
    try:
        item = await create_intention(
            service,
            tenant_id=tenant.tenant_id,
            intention=body.intention,
            due_at=body.due_at,
            expires_at=body.expires_at,
            agent_id=agent_id,
            idempotency_key=body.idempotency_key,
            principal=principal,
        )
    except ProspectiveIntentionError as exc:
        raise HTTPException(422, str(exc)) from exc
    except LongTermMemoryBlockedError as exc:
        raise HTTPException(422, "Intention rejected by the memory-write guardrail") from exc
    except LongTermMemoryUnavailableError as exc:
        raise HTTPException(503, "Memory-write guardrail unavailable; nothing stored") from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_unavailable("create_prospective_intention", exc) from exc
    return intention_json(item)


@router.get("/prospective")
async def list_prospective_intentions(
    request: Request, include_failed: bool = False
) -> list[dict]:
    """The tenant's pending (non-terminal, unexpired) intentions, due first.

    ``include_failed`` also returns intentions that kept failing to fire
    (state ``failed``, with their attempts and error) so a user can see them.
    """
    from datetime import UTC

    from app.memory.prospective_runtime import intention_json

    tenant = _require_tenant(request)
    try:
        items = await _prospective_service(request).list_active(
            tenant.tenant_id, now=datetime.now(UTC), include_failed=include_failed
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_unavailable("list_prospective_intentions", exc) from exc
    return [intention_json(i) for i in items]


@router.delete("/prospective/{intention_id}", status_code=204)
async def cancel_prospective_intention(request: Request, intention_id: str) -> None:
    tenant = _require_tenant(request)
    svc = _prospective_service(request)
    try:
        if await svc.get(tenant.tenant_id, intention_id) is None:
            raise HTTPException(404, "Intention not found")
        await svc.cancel(tenant.tenant_id, intention_id, reason="cancelled by user")
    except HTTPException:
        raise
    except (KeyError, LookupError) as exc:
        raise HTTPException(404, "Intention not found") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.patch("/{memory_id}")
async def update_memory(request: Request, memory_id: str, body: UpdateMemoryRequest) -> dict:
    """Edit a long-term memory (only the fields sent are changed).

    With a database the row is authoritative: 404 when the caller has no such
    memory, 503 on a DB error (never a cache-only "updated").
    """
    tenant_ctx = _require_tenant(request)
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not changes:
        raise HTTPException(status_code=422, detail="No fields to update")
    if "content" in changes:
        # Same MEMORY_WRITE gate as every other memory write (it used to be
        # skipped here, so an edit could store what a create would refuse).
        changes["content"] = await _screen_memory_or_http(
            tenant_ctx.tenant_id, str(changes["content"])
        )

    db = _get_db(request)
    if db is not None:
        from sqlalchemy import text

        sets: list[str] = []
        params: dict[str, Any] = {"id": memory_id, "tid": tenant_ctx.tenant_id}
        for field in _UPDATABLE_MEMORY_FIELDS:  # fixed column list — no user SQL
            if field not in changes:
                continue
            if field == "tags":
                sets.append("tags = CAST(:tags AS json)")
                params["tags"] = json.dumps(changes["tags"])
            else:
                sets.append(f"{field} = :{field}")
                params[field] = changes[field]
        if "content" in changes:
            # Re-embed the new content. The row used to keep the OLD content's
            # vector, so semantic recall matched text that no longer exists;
            # without a new vector the stale one is cleared (keyword recall
            # still finds the row).
            embedded = await _embed_memory(request, str(changes["content"]))
            if embedded is not None:
                sets.append(
                    "embedding = CAST(:emb AS vector), embedding_model = :emodel, "
                    "embedding_dim = :edim"
                )
                params["emb"], params["emodel"], params["edim"] = embedded
            else:
                sets.append("embedding = NULL, embedding_model = NULL, embedding_dim = NULL")
        sql = (
            f"UPDATE long_term_memory SET {', '.join(sets)} "
            "WHERE id = :id AND tenant_id = :tid "
            "RETURNING id, content, memory_type, confidence, tags, created_at"
        )
        try:
            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                row = (await session.execute(text(sql), params)).fetchone()
        except Exception as exc:
            raise _db_unavailable("update_memory", exc) from exc
        if row is None:
            raise HTTPException(404, f"Memory {memory_id} not found")
        # Keep this replica's cache consistent with the row.
        _evict_cached(request, tenant_ctx, memory_id)
        return {
            "id": row[0],
            "content": row[1],
            "memory_type": row[2],
            "confidence": row[3],
            "tags": row[4] or [],
            "created_at": row[5].isoformat() if row[5] else "",
        }

    # No DB configured: the in-memory store is the only store.
    ltm = _get_ltm(request)
    raw = getattr(ltm, "_memories", None) if ltm is not None else None
    if not isinstance(raw, dict):
        raise HTTPException(503, "Memory store not available")
    for mem in raw.get(tenant_ctx.tenant_id, []):
        if memory_id in (getattr(mem, "id", None), getattr(mem, "memory_id", None)):
            for field, value in changes.items():
                setattr(mem, field, value)
            return {
                "id": memory_id,
                "content": getattr(mem, "content", ""),
                "memory_type": getattr(mem, "memory_type", ""),
                "confidence": getattr(mem, "confidence", 0.8),
                "tags": list(getattr(mem, "tags", []) or []),
                "created_at": str(getattr(mem, "created_at", "") or ""),
            }
    raise HTTPException(404, f"Memory {memory_id} not found")


@router.delete("/{memory_id}")
async def delete_memory_by_id(request: Request, memory_id: str) -> dict:
    """Delete a specific memory entry (GDPR right-to-erasure for individual records)."""
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)

    if db is not None:
        # GDPR erasure: the DB row is the record. A DB error used to fall
        # through to deleting only this replica's cache and answering "ok" —
        # the row survived. Now: 503, and the cache is left alone.
        try:
            deleted = await _db_delete_memory(db, tenant_ctx.tenant_id, memory_id)
        except Exception as exc:
            raise _db_unavailable("delete_memory", exc) from exc
        _evict_cached(request, tenant_ctx, memory_id)  # keep this replica's cache in sync
        if not deleted:
            raise HTTPException(404, f"Memory {memory_id} not found")
        return {"deleted": memory_id, "status": "ok"}

    # No DB configured: the in-memory store is the only store.
    mem = _get_ltm(request)
    if mem is None:
        raise HTTPException(503, "Memory store not available")
    ok = mem.delete(memory_id=memory_id, tenant_ctx=tenant_ctx)
    if not ok:
        raise HTTPException(404, f"Memory {memory_id} not found")
    return {"deleted": memory_id, "status": "ok"}


@router.delete("/long-term/{memory_id}", status_code=204)
async def delete_memory(request: Request, memory_id: str) -> None:
    """Delete a specific long-term memory."""
    tenant = _require_tenant(request)
    db = _get_db(request)
    if db is not None:
        # Was cache-only even with a DB: the row survived the "deleted" 204.
        try:
            deleted = await _db_delete_memory(db, tenant.tenant_id, memory_id)
        except Exception as exc:
            raise _db_unavailable("delete_long_term_memory", exc) from exc
        _evict_cached(request, tenant, memory_id)
        if not deleted:
            raise HTTPException(404, "Memory not found")
        return
    mem = getattr(request.app.state, "long_term_memory", None)
    if mem is None:
        raise HTTPException(404, "Memory store not available")
    ok = mem.delete(memory_id=memory_id, tenant_ctx=tenant)
    if not ok:
        raise HTTPException(404, "Memory not found")


@router.get("/tool-reliability")
async def get_tool_reliability(request: Request, unreliable_only: bool = False) -> list[dict]:
    """Per-tool reliability learned from real tool calls for this tenant (MEM-01).

    Every tool with recorded calls (or a blacklist flag), least reliable first;
    each row carries ``unreliable`` / ``blacklisted`` flags. An empty list means
    no tool call has been recorded yet. A store outage is 503, never ``[]``.
    """
    tenant_ctx = _require_tenant(request)
    from app.memory.tool_reliability import (
        ToolReliabilityStore,
        ToolReliabilityUnavailableError,
    )

    store = getattr(request.app.state, "tool_reliability_store", None)
    if store is None:
        store = ToolReliabilityStore(db_session_factory=_get_db(request))
    try:
        if unreliable_only:
            rows: list[dict] = await store.get_unreliable_tools(tenant_id=tenant_ctx.tenant_id)
        else:
            rows = await store.list_tools(tenant_id=tenant_ctx.tenant_id)
    except ToolReliabilityUnavailableError as exc:
        raise _db_unavailable("tool_reliability", exc) from exc
    return rows


@router.delete(
    "/tool-reliability/{tool_name}/blacklist",
    status_code=204,
    dependencies=[Depends(require_role("admin"))],
)
async def clear_tool_blacklist(request: Request, tool_name: str) -> None:
    """Lift a self-improvement blacklist on a tool now (admin, audited — MEM-45).

    The audit record is written durably BEFORE the change (fail closed: no
    unaudited clear). 404 when the tool has no active blacklist flag; a store
    or audit outage is 503.
    """
    tenant_ctx = _require_tenant(request)
    from app.governance.audit import AuditEvent
    from app.governance.permissions import ActionLevel
    from app.memory.tool_reliability import (
        ToolReliabilityStore,
        ToolReliabilityUnavailableError,
    )

    audit_log = getattr(request.app.state, "audit_log", None)
    if audit_log is None:
        raise HTTPException(503, "audit log unavailable; the blacklist was not cleared")
    try:
        await audit_log.record_async(
            AuditEvent(
                goal_id="memory.tool_reliability",
                tool_name=tool_name,
                action_level=ActionLevel.ALLOW_LOG,
                outcome="blacklist_cleared",
                api_key_id=getattr(tenant_ctx, "api_key_id", None),
                note="admin cleared the tool's self-improvement blacklist",
            ),
            tenant_ctx=tenant_ctx,
        )
    except Exception as exc:
        raise _db_unavailable("tool_blacklist_audit", exc) from exc
    store = getattr(request.app.state, "tool_reliability_store", None)
    if store is None:
        store = ToolReliabilityStore(db_session_factory=_get_db(request))
    try:
        cleared = await store.clear_blacklist(
            tenant_id=tenant_ctx.tenant_id, tool_name=tool_name
        )
    except ToolReliabilityUnavailableError as exc:
        raise _db_unavailable("tool_blacklist_clear", exc) from exc
    if not cleared:
        raise HTTPException(404, "tool has no blacklist flag")


@router.delete("", status_code=204)
async def clear_all_memories(request: Request) -> None:
    """Clear all long-term memories for this tenant (GDPR erasure)."""
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)

    if db is not None:
        try:
            from sqlalchemy import text

            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                await session.execute(
                    text("DELETE FROM long_term_memory WHERE tenant_id=:tid"),
                    {"tid": tenant_ctx.tenant_id},
                )
        except Exception as exc:
            # Was: clear only this replica's cache and answer 204 — a faked
            # GDPR erasure. The DB is the record; fail loudly instead.
            raise _db_unavailable("clear_all_memories", exc) from exc
        mem = _get_ltm(request)
        raw = getattr(mem, "_memories", {}) if mem is not None else {}
        if isinstance(raw, dict):
            raw.pop(tenant_ctx.tenant_id, None)  # keep this replica's cache in sync
        return

    # In-memory fallback (no DB configured)
    mem = _get_ltm(request)
    if mem is None:
        return
    raw = getattr(mem, "_memories", {})
    if isinstance(raw, dict):
        raw.pop(tenant_ctx.tenant_id, None)
