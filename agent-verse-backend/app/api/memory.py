"""Memory management REST API."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.db.rls import sqlalchemy_rls_context

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
    try:
        from app.guardrails_v2.engine import guardrails_engine
        from app.guardrails_v2.models import GuardrailLayer

        guardrails_engine.ensure_default_rules(tenant_id)
        result = await guardrails_engine.evaluate(
            content=content, layer=GuardrailLayer.MEMORY_WRITE, tenant_id=tenant_id
        )
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("memory_write_guardrail_failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Memory-write guardrail is unavailable; memory was not saved",
        ) from exc
    if result.get("blocked"):
        raise HTTPException(
            status_code=422, detail="Memory content rejected by the memory-write guardrail"
        )
    redacted = result.get("redacted_content")
    return redacted if isinstance(redacted, str) and redacted else content


async def _embed_memory(request: Request, content: str) -> tuple[str, str, int] | None:
    """``(pgvector literal, model, raw dim)`` for *content*, or None.

    Non-fatal like ``LongTermMemoryStore.store_async``: without a vector the
    row is still found by keyword recall. Never silent.
    """
    embedder = getattr(request.app.state, "embedder", None)
    if embedder is None:
        return None
    try:
        from app.memory.long_term import _fit_ltm_vector
        from app.providers.base import EmbedRequest

        resp = await embedder.embed(EmbedRequest(texts=[content]))
        raw_vec = resp.embeddings[0] if resp.embeddings else None
        fitted = _fit_ltm_vector(raw_vec) if raw_vec else None
        if raw_vec is None or fitted is None:
            return None
        literal = "[" + ",".join(str(v) for v in fitted) + "]"
        return literal, str(getattr(resp, "model", "") or ""), len(raw_vec)
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("memory_embedding_failed: %s", str(exc)[:200])
        return None


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
    """List long-term memories for this tenant (bounded + offset-pageable)."""
    tenant = _require_tenant(request)
    mem = getattr(request.app.state, "long_term_memory", None)
    if mem is None:
        return []
    return [
        {
            "memory_id": m.memory_id,
            "content": m.content,
            "memory_type": m.memory_type,
            "confidence": getattr(m, "confidence", 1.0),
            "source_goal_id": getattr(m, "source_goal_id", ""),
            "tags": getattr(m, "tags", []),
        }
        for m in mem.list_all(tenant_ctx=tenant, limit=limit, offset=offset)
    ]


@router.get("/execution")
async def list_execution_memories(request: Request) -> list[dict[str, Any]]:
    """List execution memory entries (winning plans)."""
    tenant = _require_tenant(request)
    # Execution memory is in-memory only for now
    exec_mem = getattr(request.app.state, "exec_memory", None)
    if exec_mem is None:
        return []
    memories = exec_mem._memories.get(tenant.tenant_id, [])
    return [
        {
            "goal_text": m.get("goal_text", "")[:200],
            "success": m.get("success", False),
            "recorded_at": m.get("recorded_at", ""),
        }
        for m in memories[-50:]  # Cap at 50
    ]


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
async def get_tool_reliability(request: Request) -> list[dict]:
    """Get per-tool reliability stats (tools with poor success rates) for this tenant."""
    tenant_ctx = _require_tenant(request)
    from app.memory.tool_reliability import ToolReliabilityStore

    store = ToolReliabilityStore(db_session_factory=_get_db(request))
    return await store.get_unreliable_tools(tenant_id=tenant_ctx.tenant_id, min_calls=3)


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
