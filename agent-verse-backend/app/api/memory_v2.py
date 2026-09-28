"""Agent Memory 2.0 API - governed memory with provenance and lifecycle.

Storage model
-------------
When a database is configured (``app.state.db_session_factory``, wired by the
lifespan) Postgres is the ONLY source of truth: every read goes to the DB and
every write must commit, otherwise the request fails with 503. There is no
per-process cache in that mode.

Previously a module-level dict was consulted first and written "through" to
the DB with errors swallowed. So on a multi-replica deployment a memory
deleted on replica A was still served by replica B's cache (and a PATCH on B
wrote it back as active — resurrecting a GDPR-deleted memory), and any DB
failure — including every insert, since ``str(uuid4())`` (36 chars) did not
fit ``long_term_memory.id`` VARCHAR(32) — was reported as success.

Without a database (dev/tests) the module dict is the store.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.db.rls import sqlalchemy_rls_context
from app.memory_v2.models import MemoryLifecycleState, MemoryPrivacyClass
from app.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/memory-v2", tags=["memory-v2"])


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


def _get_db(request: Request) -> Any:
    """The configured async session factory, or None (in-memory dev/test build).

    Deliberately no fallback to a module-level factory: that made every no-DB
    build silently attempt (and swallow) connections, and blurred which store
    is authoritative.
    """
    state = request.app.state
    return getattr(state, "db_session_factory", None) or getattr(state, "db", None)


def _unavailable(exc: Exception, op: str) -> HTTPException:
    logger.error("memory_v2_db_error", op=op, error=str(exc))
    return HTTPException(503, "Memory store unavailable; the operation was not applied")


# ── In-memory store: ONLY used when no database is configured ────────────────
_memories: dict[str, dict] = {}
_conflicts: dict[str, list] = {}

_V2_PAGE_SIZE = 500
# Bound for whole-tenant maintenance passes (mark-stale / consolidate / conflict scan).
_V2_SCAN_LIMIT = 5000
# JSON field extraction on the ``content`` column (memory_v2 rows store the whole
# memory as a JSON document there; the row's ``memory_type`` column is the literal
# ``'memory_v2'``, so logical fields must be read out of the JSON).
_LIFECYCLE_EXPR = "(content::jsonb ->> 'lifecycle_state')"


def _v2_key(tenant_id: str, memory_id: str) -> str:
    return f"{tenant_id}:{memory_id}"


def _row_params(memory: dict) -> dict[str, Any]:
    prov = memory.get("provenance") or {}
    goal_id = prov.get("source_goal_id") or prov.get("goal_id")
    return {
        "id": memory["memory_id"],
        "tid": memory["tenant_id"],
        "content": json.dumps(memory),
        "conf": float(memory.get("confidence", 0.8)),
        # Real goal linkage only (the erasure cascade follows source_goal_id).
        "sgid": goal_id if isinstance(goal_id, str) and len(goal_id) <= 32 else None,
        "tags": json.dumps(memory.get("tags", [])),
    }


async def _db_insert_memory(db: Any, memory: dict) -> None:
    """Insert a new v2 memory row. Raises on failure."""
    from sqlalchemy import text

    async with (
        db() as session,
        session.begin(),
        sqlalchemy_rls_context(session, memory["tenant_id"]),
    ):
        await session.execute(
            text(
                """INSERT INTO long_term_memory
                       (id, tenant_id, content, memory_type, confidence, source_goal_id, tags)
                   VALUES (:id, :tid, :content, 'memory_v2', :conf, :sgid, :tags)"""
            ),
            _row_params(memory),
        )


async def _db_update_in_txn(session: Any, memory: dict) -> None:
    from sqlalchemy import text

    await session.execute(
        text(
            "UPDATE long_term_memory SET content = :content, confidence = :conf, "
            "tags = :tags WHERE id = :id AND tenant_id = :tid AND memory_type = 'memory_v2'"
        ),
        _row_params(memory),
    )


async def _db_lock_one(session: Any, tenant_id: str, memory_id: str) -> dict | None:
    """SELECT … FOR UPDATE one memory inside the caller's transaction."""
    from sqlalchemy import text

    row = (
        await session.execute(
            text(
                "SELECT content FROM long_term_memory "
                "WHERE tenant_id = :tid AND id = :mid AND memory_type = 'memory_v2' "
                "FOR UPDATE"
            ),
            {"tid": tenant_id, "mid": memory_id},
        )
    ).fetchone()
    return json.loads(row[0]) if row is not None else None  # type: ignore[no-any-return]


async def _db_get_one(tenant_id: str, memory_id: str, db: Any) -> dict | None:
    from sqlalchemy import text

    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        row = (
            await session.execute(
                text(
                    "SELECT content FROM long_term_memory "
                    "WHERE tenant_id = :tid AND id = :mid AND memory_type = 'memory_v2' "
                    "LIMIT 1"
                ),
                {"tid": tenant_id, "mid": memory_id},
            )
        ).fetchone()
    return json.loads(row[0]) if row is not None else None  # type: ignore[no-any-return]


async def _query_memories_page_from_db(
    tenant_id: str,
    db: Any,
    *,
    lifecycle_state: str | None,
    memory_type: str | None,
    privacy_class: str | None,
    limit: int,
    exclude_states: tuple[str, ...] = ("deleted",),
) -> list[dict]:
    """List a bounded, filtered page of v2 memories directly from the DB."""
    from sqlalchemy import text

    clauses = ["tenant_id = :tid", "memory_type = 'memory_v2'"]
    params: dict[str, Any] = {"tid": tenant_id, "limit": limit}
    if lifecycle_state:
        clauses.append(f"{_LIFECYCLE_EXPR} = :ls")
        params["ls"] = lifecycle_state
    else:
        for i, st in enumerate(exclude_states):
            clauses.append(f"{_LIFECYCLE_EXPR} IS DISTINCT FROM :ex{i}")
            params[f"ex{i}"] = st
    if memory_type:
        clauses.append("(content::jsonb ->> 'memory_type') = :mt")
        params["mt"] = memory_type
    if privacy_class:
        clauses.append("(content::jsonb ->> 'privacy_class') = :pc")
        params["pc"] = privacy_class
    sql = (
        "SELECT content FROM long_term_memory "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY created_at DESC LIMIT :limit"
    )
    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        rows = (await session.execute(text(sql), params)).fetchall()
    out: list[dict] = []
    for r in rows:
        with contextlib.suppress(Exception):
            out.append(json.loads(r[0]))
    return out


class CreateMemoryRequest(BaseModel):
    content: str
    memory_type: str = "fact"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    lifecycle_state: str = "active"
    privacy_class: str = "internal"
    provenance: dict[str, Any] = Field(default_factory=dict)


class UpdateMemoryRequest(BaseModel):
    content: str | None = None
    confidence: float | None = None
    lifecycle_state: str | None = None
    tags: list[str] | None = None


@router.post("")
async def create_memory(request: Request, body: CreateMemoryRequest) -> dict[str, Any]:
    """Create a memory entry with provenance tracking."""
    tenant = _require_tenant(request)
    db = _get_db(request)

    now = datetime.datetime.now(datetime.UTC).isoformat()
    # 32-char hex: long_term_memory.id is VARCHAR(32).
    memory_id = uuid.uuid4().hex

    try:
        lifecycle = MemoryLifecycleState(body.lifecycle_state)
    except ValueError:
        lifecycle = MemoryLifecycleState.ACTIVE
    try:
        privacy = MemoryPrivacyClass(body.privacy_class)
    except ValueError:
        privacy = MemoryPrivacyClass.INTERNAL

    memory = {
        "memory_id": memory_id,
        "tenant_id": tenant.tenant_id,
        "content": body.content,
        "memory_type": body.memory_type,
        "confidence": body.confidence,
        "tags": body.tags,
        "lifecycle_state": lifecycle.value,
        "privacy_class": privacy.value,
        "provenance": body.provenance,
        "created_at": now,
        "updated_at": now,
        "update_count": 0,
        "graph_links": [],
    }
    if db is not None:
        try:
            await _db_insert_memory(db, memory)
            existing = await _query_memories_page_from_db(
                tenant.tenant_id,
                db,
                lifecycle_state=None,
                memory_type=None,
                privacy_class=None,
                limit=_V2_SCAN_LIMIT,
                exclude_states=("deleted", "archived"),
            )
        except Exception as exc:
            raise _unavailable(exc, "create") from exc
    else:
        _memories[_v2_key(tenant.tenant_id, memory_id)] = memory
        prefix = f"{tenant.tenant_id}:"
        existing = [v for k, v in _memories.items() if k.startswith(prefix)]

    # Check for conflicts with existing memories (simple content similarity)
    _detect_conflicts(tenant.tenant_id, memory_id, body.content, existing)

    return {"memory_id": memory_id, "status": "created"}


@router.get("")
async def list_memories(
    request: Request,
    lifecycle_state: str | None = Query(default=None),
    memory_type: str | None = Query(default=None),
    privacy_class: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
) -> dict[str, Any]:
    """List memories with lifecycle filtering (filters + limit pushed into SQL)."""
    tenant = _require_tenant(request)
    db = _get_db(request)

    if db is not None:
        try:
            memories = await _query_memories_page_from_db(
                tenant.tenant_id,
                db,
                lifecycle_state=lifecycle_state,
                memory_type=memory_type,
                privacy_class=privacy_class,
                limit=limit,
            )
        except Exception as exc:
            raise _unavailable(exc, "list") from exc
    else:
        prefix = f"{tenant.tenant_id}:"
        memories = []
        for k, v in _memories.items():
            if not k.startswith(prefix) or v.get("lifecycle_state") == "deleted":
                continue
            if lifecycle_state and v.get("lifecycle_state") != lifecycle_state:
                continue
            if memory_type and v.get("memory_type") != memory_type:
                continue
            if privacy_class and v.get("privacy_class") != privacy_class:
                continue
            memories.append(v)

    memories.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
    return {"memories": memories[:limit], "total": len(memories)}


@router.get("/conflicts/all")
async def list_conflicts(request: Request) -> dict[str, Any]:
    """List detected memory conflicts."""
    tenant = _require_tenant(request)
    conflicts = _conflicts.get(tenant.tenant_id, [])
    return {"conflicts": conflicts, "total": len(conflicts)}


@router.post("/conflicts/{conflict_id}/resolve")
async def resolve_conflict(request: Request, conflict_id: str) -> dict[str, Any]:
    """Resolve a memory conflict."""
    tenant = _require_tenant(request)
    body = await request.json()

    conflicts = _conflicts.get(tenant.tenant_id, [])
    conflict = next((c for c in conflicts if c["conflict_id"] == conflict_id), None)
    if not conflict:
        raise HTTPException(404, "Conflict not found")

    conflict["resolved"] = True
    conflict["resolution"] = body.get("resolution", "manual resolution")
    conflict["resolved_at"] = datetime.datetime.now(datetime.UTC).isoformat()

    return {"conflict_id": conflict_id, "status": "resolved"}


def _older_than(memory: dict, cutoff: datetime.datetime) -> bool:
    updated = memory.get("updated_at", "")
    if not updated:
        return False
    try:
        updated_dt = datetime.datetime.fromisoformat(str(updated).rstrip("Z"))
    except ValueError:
        return False
    if updated_dt.tzinfo is None:
        updated_dt = updated_dt.replace(tzinfo=datetime.UTC)
    return updated_dt < cutoff


@router.post("/lifecycle/mark-stale")
async def mark_stale_memories(request: Request) -> dict[str, Any]:
    """Mark memories as stale based on age."""
    tenant = _require_tenant(request)
    db = _get_db(request)

    body = await request.json()
    days_threshold = body.get("days_old", 30)
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_threshold)
    marked = 0

    if db is not None:
        from sqlalchemy import text

        try:
            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant.tenant_id),
            ):
                rows = (
                    await session.execute(
                        text(
                            "SELECT content FROM long_term_memory WHERE tenant_id = :tid "
                            f"AND memory_type = 'memory_v2' AND {_LIFECYCLE_EXPR} = 'active' "
                            "ORDER BY created_at LIMIT :lim FOR UPDATE"
                        ),
                        {"tid": tenant.tenant_id, "lim": _V2_SCAN_LIMIT},
                    )
                ).fetchall()
                for (content,) in rows:
                    memory = json.loads(content)
                    if _older_than(memory, cutoff):
                        memory["lifecycle_state"] = "stale"
                        await _db_update_in_txn(session, memory)
                        marked += 1
        except Exception as exc:
            raise _unavailable(exc, "mark_stale") from exc
        return {"marked_stale": marked, "days_threshold": days_threshold}

    for key, memory in _memories.items():
        if not key.startswith(f"{tenant.tenant_id}:"):
            continue
        if memory.get("lifecycle_state") != "active":
            continue
        if _older_than(memory, cutoff):
            memory["lifecycle_state"] = "stale"
            marked += 1

    return {"marked_stale": marked, "days_threshold": days_threshold}


async def _stream_all_v2_from_db(tenant_id: str, db: Any) -> list[dict]:
    """Fetch every v2 memory for a tenant via bounded keyset pages.

    Reads in ``_V2_PAGE_SIZE`` chunks keyed on (created_at, id) rather than issuing
    one unbounded SELECT, so a GDPR export never materialises the whole table in a
    single query. A hard page cap guards against pathological pagination.
    """
    from sqlalchemy import text

    out: list[dict] = []
    after_created: Any = None
    after_id: str | None = None
    max_pages = 10_000  # safety cap: max_pages * _V2_PAGE_SIZE rows
    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        for _ in range(max_pages):
            clause = ""
            params: dict[str, Any] = {"tid": tenant_id, "limit": _V2_PAGE_SIZE}
            if after_created is not None:
                clause = " AND (created_at, id) < (CAST(:ac AS timestamptz), :ai)"
                params["ac"] = after_created
                params["ai"] = after_id
            rows = (
                await session.execute(
                    text(
                        "SELECT id, created_at, content FROM long_term_memory "
                        "WHERE tenant_id = :tid AND memory_type = 'memory_v2'"
                        f"{clause} "
                        "ORDER BY created_at DESC, id DESC LIMIT :limit"
                    ),
                    params,
                )
            ).fetchall()
            if not rows:
                break
            for row in rows:
                with contextlib.suppress(Exception):
                    out.append(json.loads(row[2]))
            after_created, after_id = rows[-1][1], rows[-1][0]
            if len(rows) < _V2_PAGE_SIZE:
                break
    return out


@router.get("/export/gdpr")
async def export_gdpr(request: Request) -> dict[str, Any]:
    """Export all memories for GDPR data subject request.

    With a DB this is read entirely from Postgres; a DB failure is a 503 rather
    than a silently partial export.
    """
    tenant = _require_tenant(request)
    db = _get_db(request)

    if db is not None:
        try:
            rows = await _stream_all_v2_from_db(tenant.tenant_id, db)
        except Exception as exc:
            raise _unavailable(exc, "export") from exc
    else:
        rows = [m for k, m in _memories.items() if k.startswith(f"{tenant.tenant_id}:")]

    memories = [{k: v for k, v in m.items() if k != "tenant_id"} for m in rows]
    return {
        "tenant_id": tenant.tenant_id,
        "exported_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "total_memories": len(memories),
        "memories": memories,
        "format": "gdpr_data_export_v1",
    }


@router.get("/{memory_id}")
async def get_memory(request: Request, memory_id: str) -> dict[str, Any]:
    """Get a specific memory with provenance."""
    tenant = _require_tenant(request)
    db = _get_db(request)

    if db is not None:
        try:
            memory = await _db_get_one(tenant.tenant_id, memory_id, db)
        except Exception as exc:
            raise _unavailable(exc, "get") from exc
    else:
        memory = _memories.get(_v2_key(tenant.tenant_id, memory_id))
    if not memory or memory.get("lifecycle_state") == "deleted":
        raise HTTPException(404, "Memory not found")
    return memory


def _apply_update(memory: dict, body: UpdateMemoryRequest) -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat()
    if body.content is not None:
        memory["content"] = body.content
    if body.confidence is not None:
        memory["confidence"] = body.confidence
    if body.lifecycle_state is not None:
        try:
            MemoryLifecycleState(body.lifecycle_state)
            memory["lifecycle_state"] = body.lifecycle_state
        except ValueError as exc:
            raise HTTPException(400, f"Invalid lifecycle state: {body.lifecycle_state}") from exc
    if body.tags is not None:
        memory["tags"] = body.tags
    memory["updated_at"] = now
    memory["update_count"] = memory.get("update_count", 0) + 1
    memory.setdefault("provenance", {})["update_count"] = memory["update_count"]


@router.patch("/{memory_id}")
async def update_memory(
    request: Request, memory_id: str, body: UpdateMemoryRequest
) -> dict[str, Any]:
    """Update a memory entry.

    With a DB the read-modify-write happens in ONE transaction on a row locked
    ``FOR UPDATE``, so a concurrent (or earlier, on another replica) delete is
    seen and the memory is never written back as active.
    """
    tenant = _require_tenant(request)
    db = _get_db(request)

    if db is not None:
        try:
            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant.tenant_id),
            ):
                memory = await _db_lock_one(session, tenant.tenant_id, memory_id)
                if not memory or memory.get("lifecycle_state") == "deleted":
                    raise HTTPException(404, "Memory not found")
                _apply_update(memory, body)
                await _db_update_in_txn(session, memory)
        except HTTPException:
            raise
        except Exception as exc:
            raise _unavailable(exc, "update") from exc
    else:
        memory = _memories.get(_v2_key(tenant.tenant_id, memory_id))
        if not memory or memory.get("lifecycle_state") == "deleted":
            raise HTTPException(404, "Memory not found")
        _apply_update(memory, body)

    return {"memory_id": memory_id, "status": "updated", "update_count": memory["update_count"]}


@router.delete("/{memory_id}")
async def delete_memory(request: Request, memory_id: str) -> dict[str, Any]:
    """Soft-delete a memory (GDPR compliant — marks as deleted). 503 if not durable."""
    tenant = _require_tenant(request)
    db = _get_db(request)
    deleted_at = datetime.datetime.now(datetime.UTC).isoformat()

    if db is not None:
        try:
            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant.tenant_id),
            ):
                memory = await _db_lock_one(session, tenant.tenant_id, memory_id)
                if not memory:
                    raise HTTPException(404, "Memory not found")
                memory["lifecycle_state"] = "deleted"
                memory["deleted_at"] = deleted_at
                await _db_update_in_txn(session, memory)
        except HTTPException:
            raise
        except Exception as exc:
            raise _unavailable(exc, "delete") from exc
    else:
        memory = _memories.get(_v2_key(tenant.tenant_id, memory_id))
        if not memory:
            raise HTTPException(404, "Memory not found")
        memory["lifecycle_state"] = "deleted"
        memory["deleted_at"] = deleted_at

    return {"memory_id": memory_id, "status": "deleted"}


@router.post("/consolidate")
async def consolidate_memories(request: Request) -> dict[str, Any]:
    """Run memory consolidation - dedup, merge, lifecycle management.

    With a DB the tenant's live memories are loaded and locked in one
    transaction, consolidated, and every lifecycle change is written back (the
    old code only mutated the per-process cache, so nothing persisted).
    """
    tenant = _require_tenant(request)
    db = _get_db(request)
    from app.memory_v2.consolidation import memory_consolidator

    if db is None:
        stats = await memory_consolidator.consolidate(tenant.tenant_id, _memories)
        return {"status": "consolidated", **stats}

    from sqlalchemy import text

    try:
        async with (
            db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant.tenant_id),
        ):
            rows = (
                await session.execute(
                    text(
                        "SELECT content FROM long_term_memory WHERE tenant_id = :tid "
                        "AND memory_type = 'memory_v2' "
                        f"AND {_LIFECYCLE_EXPR} NOT IN ('deleted', 'archived') "
                        "ORDER BY created_at DESC LIMIT :lim FOR UPDATE"
                    ),
                    {"tid": tenant.tenant_id, "lim": _V2_SCAN_LIMIT},
                )
            ).fetchall()
            store: dict[str, dict] = {}
            for (content,) in rows:
                m = json.loads(content)
                store[_v2_key(tenant.tenant_id, str(m["memory_id"]))] = m
            before = {k: v.get("lifecycle_state") for k, v in store.items()}
            stats = await memory_consolidator.consolidate(tenant.tenant_id, store)
            for k, m in store.items():
                if m.get("lifecycle_state") != before.get(k):
                    await _db_update_in_txn(session, m)
    except Exception as exc:
        raise _unavailable(exc, "consolidate") from exc
    return {"status": "consolidated", **stats}


def _detect_conflicts(
    tenant_id: str, new_memory_id: str, new_content: str, existing: list[dict]
) -> None:
    """Detect potential conflicts with existing memories."""
    new_words = set(new_content.lower().split())

    for memory in existing:
        if memory.get("tenant_id") not in (None, tenant_id):
            continue
        if memory["memory_id"] == new_memory_id:
            continue
        if memory.get("lifecycle_state") in ("deleted", "archived"):
            continue

        existing_words = set(str(memory.get("content", "")).lower().split())
        overlap = len(new_words & existing_words) / max(len(new_words | existing_words), 1)

        # High overlap but potentially contradictory (contains negations)
        if overlap > 0.5:
            negation_words = ["not", "never", "no ", "don't", "doesn't", "isn't"]
            has_negation = any(w in new_content.lower() for w in negation_words)
            if has_negation:
                conflict = {
                    "conflict_id": str(uuid.uuid4()),
                    "tenant_id": tenant_id,
                    "memory_id_a": memory["memory_id"],
                    "memory_id_b": new_memory_id,
                    "conflict_description": "High content overlap with potential contradiction",
                    "severity": "medium",
                    "resolved": False,
                    "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
                }
                _conflicts.setdefault(tenant_id, []).append(conflict)
                break  # Max 1 conflict per new memory
