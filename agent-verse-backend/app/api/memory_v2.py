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

Conflicts (a10-F238-01/02) live in ``memory_conflicts`` (FORCE RLS) with a DB:
they used to be a module dict, per replica, lost on restart and never bounded.
Every store is capped per tenant (newest kept).

Writes pass the MEMORY_WRITE gate (a10-F238-04): v2 rows sit in
``long_term_memory`` and are recalled into planner prompts, so unscreened
content was a stored-injection / PII path.

DELETE removes the row and its conflicts (a10-F238-05): a soft ``deleted``
flag inside the JSON was ignored by long-term recall and listing, so a
"deleted" memory was kept and recalled.
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


async def _screened(content: str, tenant_id: str) -> str:
    """The MEMORY_WRITE gate for v2 content: 422 when refused, 503 when it cannot vet."""
    from app.memory.long_term import (
        LongTermMemoryBlockedError,
        LongTermMemoryUnavailableError,
        screen_user_memory_content,
    )

    try:
        return await screen_user_memory_content(content, tenant_id=tenant_id)
    except LongTermMemoryBlockedError as exc:
        raise HTTPException(422, "Memory content rejected by the memory-write guardrail") from exc
    except LongTermMemoryUnavailableError as exc:
        raise HTTPException(
            503, "Memory-write guardrail unavailable; the memory was not stored"
        ) from exc


# ── In-memory store: ONLY used when no database is configured ────────────────
_memories: dict[str, dict] = {}
_conflicts: dict[str, list] = {}

_V2_PAGE_SIZE = 500
# Bound for whole-tenant maintenance passes (mark-stale / consolidate).
_V2_SCAN_LIMIT = 5000
# A new memory is compared with at most this many of the tenant's newest live
# memories, and only when it carries a negation (a10-F238-03).
_CONFLICT_SCAN_LIMIT = 500
# Conflicts kept per tenant (newest first) and returned by one list call.
_CONFLICTS_MAX_PER_TENANT = 1000
_CONFLICTS_PAGE = 200
_NEGATION_WORDS = ("not", "never", "no ", "don't", "doesn't", "isn't")
_MAX_STALE_DAYS = 36_500
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

    where, params = _v2_filter(
        tenant_id,
        lifecycle_state=lifecycle_state,
        memory_type=memory_type,
        privacy_class=privacy_class,
        exclude_states=exclude_states,
    )
    params["limit"] = limit
    sql = (
        "SELECT content FROM long_term_memory "
        f"WHERE {where} "
        "ORDER BY created_at DESC LIMIT :limit"
    )
    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        rows = (await session.execute(text(sql), params)).fetchall()
    out: list[dict] = []
    for r in rows:
        with contextlib.suppress(Exception):
            out.append(json.loads(r[0]))
    return out


async def _count_memories_in_db(
    tenant_id: str,
    db: Any,
    *,
    lifecycle_state: str | None,
    memory_type: str | None,
    privacy_class: str | None,
) -> int:
    """How many v2 memories match the list filters (a10-F238-06: total was the page size)."""
    from sqlalchemy import text

    where, params = _v2_filter(
        tenant_id,
        lifecycle_state=lifecycle_state,
        memory_type=memory_type,
        privacy_class=privacy_class,
        exclude_states=("deleted",),
    )
    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tenant_id):
        sql = f"SELECT count(*) FROM long_term_memory WHERE {where}"
        total = (await session.execute(text(sql), params)).scalar()
    return int(total or 0)


def _v2_filter(
    tenant_id: str,
    *,
    lifecycle_state: str | None,
    memory_type: str | None,
    privacy_class: str | None,
    exclude_states: tuple[str, ...],
) -> tuple[str, dict[str, Any]]:
    clauses = ["tenant_id = :tid", "memory_type = 'memory_v2'"]
    params: dict[str, Any] = {"tid": tenant_id}
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
    return " AND ".join(clauses), params


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

    content = await _screened(body.content, tenant.tenant_id)
    memory = {
        "memory_id": memory_id,
        "tenant_id": tenant.tenant_id,
        "content": content,
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
    # The conflict check needs the tenant's other memories only when the new one
    # can contradict them (it carries a negation): it used to load and compare up
    # to 5000 memories on every create (a10-F238-03).
    may_conflict = _has_negation(content)
    if db is not None:
        try:
            await _db_insert_memory(db, memory)
            if may_conflict:
                existing = await _query_memories_page_from_db(
                    tenant.tenant_id,
                    db,
                    lifecycle_state=None,
                    memory_type=None,
                    privacy_class=None,
                    limit=_CONFLICT_SCAN_LIMIT,
                    exclude_states=("deleted", "archived"),
                )
                conflict = _find_conflict(tenant.tenant_id, memory_id, content, existing)
                if conflict is not None:
                    await _db_insert_conflict(db, conflict)
        except Exception as exc:
            raise _unavailable(exc, "create") from exc
    else:
        _memories[_v2_key(tenant.tenant_id, memory_id)] = memory
        if may_conflict:
            prefix = f"{tenant.tenant_id}:"
            existing = [v for k, v in _memories.items() if k.startswith(prefix)]
            existing = sorted(existing, key=lambda m: m.get("created_at", ""), reverse=True)
            conflict = _find_conflict(
                tenant.tenant_id, memory_id, content, existing[:_CONFLICT_SCAN_LIMIT]
            )
            if conflict is not None:
                bucket = _conflicts.setdefault(tenant.tenant_id, [])
                bucket.append(conflict)
                del bucket[:-_CONFLICTS_MAX_PER_TENANT]  # newest kept

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
            total = await _count_memories_in_db(
                tenant.tenant_id,
                db,
                lifecycle_state=lifecycle_state,
                memory_type=memory_type,
                privacy_class=privacy_class,
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
        total = len(memories)

    memories.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
    return {"memories": memories[:limit], "total": total}


# ── Conflicts (memory_conflicts with a DB; a capped dict without) ────────────


def _conflict_from_row(row: Any) -> dict[str, Any]:
    created = row["created_at"]
    return {
        "conflict_id": row["id"],
        "tenant_id": row["tenant_id"],
        "memory_id_a": row["memory_id_a"],
        "memory_id_b": row["memory_id_b"],
        "conflict_description": row["description"],
        "severity": "medium",
        "resolved": bool(row["resolved"]),
        "resolution": row["resolution"],
        "created_at": created.isoformat() if created is not None else None,
    }


async def _db_insert_conflict(db: Any, conflict: dict[str, Any]) -> None:
    """Persist one conflict, then keep only the tenant's newest N (a10-F238-02)."""
    from sqlalchemy import text

    tid = conflict["tenant_id"]
    async with db() as session, session.begin(), sqlalchemy_rls_context(session, tid):
        await session.execute(
            text(
                "INSERT INTO memory_conflicts "
                "(id, tenant_id, memory_id_a, memory_id_b, conflict_type, description, "
                " resolved) VALUES (:id, :tid, :a, :b, 'contradiction', :d, false)"
            ),
            {
                "id": conflict["conflict_id"],
                "tid": tid,
                "a": conflict["memory_id_a"],
                "b": conflict["memory_id_b"],
                "d": conflict["conflict_description"],
            },
        )
        await session.execute(
            text(
                "DELETE FROM memory_conflicts WHERE tenant_id = :tid AND id IN ("
                " SELECT id FROM memory_conflicts WHERE tenant_id = :tid"
                " ORDER BY created_at DESC, id DESC OFFSET :keep)"
            ),
            {"tid": tid, "keep": _CONFLICTS_MAX_PER_TENANT},
        )


@router.get("/conflicts/all")
async def list_conflicts(request: Request) -> dict[str, Any]:
    """List the tenant's detected memory conflicts (newest first, bounded)."""
    tenant = _require_tenant(request)
    db = _get_db(request)
    if db is None:
        conflicts = list(reversed(_conflicts.get(tenant.tenant_id, [])))
        return {"conflicts": conflicts[:_CONFLICTS_PAGE], "total": len(conflicts)}

    from sqlalchemy import text

    tid = tenant.tenant_id
    try:
        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tid):
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT id, tenant_id, memory_id_a, memory_id_b, description, "
                            "resolved, resolution, created_at FROM memory_conflicts "
                            "WHERE tenant_id = :tid ORDER BY created_at DESC, id DESC "
                            "LIMIT :lim"
                        ),
                        {"tid": tid, "lim": _CONFLICTS_PAGE},
                    )
                )
                .mappings()
                .all()
            )
            total = (
                await session.execute(
                    text("SELECT count(*) FROM memory_conflicts WHERE tenant_id = :tid"),
                    {"tid": tid},
                )
            ).scalar()
    except Exception as exc:
        raise _unavailable(exc, "list_conflicts") from exc
    return {"conflicts": [_conflict_from_row(r) for r in rows], "total": int(total or 0)}


class ResolveConflictRequest(BaseModel):
    resolution: str = Field(default="manual resolution", max_length=4000)


@router.post("/conflicts/{conflict_id}/resolve")
async def resolve_conflict(
    request: Request, conflict_id: str, body: ResolveConflictRequest | None = None
) -> dict[str, Any]:
    """Resolve a memory conflict (durably, with a DB)."""
    tenant = _require_tenant(request)
    resolution = (body or ResolveConflictRequest()).resolution
    db = _get_db(request)
    if db is None:
        conflicts = _conflicts.get(tenant.tenant_id, [])
        conflict = next((c for c in conflicts if c["conflict_id"] == conflict_id), None)
        if not conflict:
            raise HTTPException(404, "Conflict not found")
        conflict["resolved"] = True
        conflict["resolution"] = resolution
        return {"conflict_id": conflict_id, "status": "resolved"}

    from sqlalchemy import text

    tid = tenant.tenant_id
    try:
        async with db() as session, session.begin(), sqlalchemy_rls_context(session, tid):
            hit = (
                await session.execute(
                    text(
                        "UPDATE memory_conflicts SET resolved = true, resolution = :r "
                        "WHERE tenant_id = :tid AND id = :cid RETURNING id"
                    ),
                    {"r": resolution, "tid": tid, "cid": conflict_id},
                )
            ).fetchone()
    except Exception as exc:
        raise _unavailable(exc, "resolve_conflict") from exc
    if hit is None:
        raise HTTPException(404, "Conflict not found")
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
    days_threshold = body.get("days_old", 30) if isinstance(body, dict) else 30
    if (
        isinstance(days_threshold, bool)
        or not isinstance(days_threshold, int | float)
        or not 0 <= days_threshold <= _MAX_STALE_DAYS
    ):
        raise HTTPException(422, f"days_old must be a number between 0 and {_MAX_STALE_DAYS}")
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


def _apply_update(memory: dict, body: UpdateMemoryRequest, content: str | None) -> None:
    now = datetime.datetime.now(datetime.UTC).isoformat()
    if content is not None:
        memory["content"] = content
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
    if body.lifecycle_state == MemoryLifecycleState.DELETED.value:
        # A flag inside the JSON is not a delete: recall ignores it (a10-F238-05).
        raise HTTPException(400, "Use DELETE /memory-v2/{memory_id} to delete a memory")
    content = None if body.content is None else await _screened(body.content, tenant.tenant_id)

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
                _apply_update(memory, body, content)
                await _db_update_in_txn(session, memory)
        except HTTPException:
            raise
        except Exception as exc:
            raise _unavailable(exc, "update") from exc
    else:
        memory = _memories.get(_v2_key(tenant.tenant_id, memory_id))
        if not memory or memory.get("lifecycle_state") == "deleted":
            raise HTTPException(404, "Memory not found")
        _apply_update(memory, body, content)

    return {"memory_id": memory_id, "status": "updated", "update_count": memory["update_count"]}


@router.delete("/{memory_id}")
async def delete_memory(request: Request, memory_id: str) -> dict[str, Any]:
    """Delete a memory and the conflicts that name it (GDPR erasure). 503 if not durable.

    The row is removed. It used to stay with ``lifecycle_state: deleted`` inside
    its JSON, which long-term recall and listing never read, so the "deleted"
    content kept being recalled into planner prompts (a10-F238-05).
    """
    from sqlalchemy import text

    tenant = _require_tenant(request)
    db = _get_db(request)
    tid = tenant.tenant_id

    if db is not None:
        try:
            async with db() as session, session.begin(), sqlalchemy_rls_context(session, tid):
                gone = (
                    await session.execute(
                        text(
                            "DELETE FROM long_term_memory WHERE tenant_id = :tid "
                            "AND id = :mid AND memory_type = 'memory_v2' RETURNING id"
                        ),
                        {"tid": tid, "mid": memory_id},
                    )
                ).fetchone()
                if gone is None:
                    raise HTTPException(404, "Memory not found")
                await session.execute(
                    text(
                        "DELETE FROM memory_conflicts WHERE tenant_id = :tid "
                        "AND (memory_id_a = :mid OR memory_id_b = :mid)"
                    ),
                    {"tid": tid, "mid": memory_id},
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise _unavailable(exc, "delete") from exc
    else:
        if _memories.pop(_v2_key(tid, memory_id), None) is None:
            raise HTTPException(404, "Memory not found")
        _conflicts[tid] = [
            c
            for c in _conflicts.get(tid, [])
            if memory_id not in (c.get("memory_id_a"), c.get("memory_id_b"))
        ]

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


def _has_negation(content: str) -> bool:
    lowered = content.lower()
    return any(w in lowered for w in _NEGATION_WORDS)


def _find_conflict(
    tenant_id: str, new_memory_id: str, new_content: str, existing: list[dict]
) -> dict[str, Any] | None:
    """The first existing memory the new one may contradict (high overlap + negation)."""
    if not _has_negation(new_content):
        return None
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
        if overlap > 0.5:
            return {
                "conflict_id": uuid.uuid4().hex,
                "tenant_id": tenant_id,
                "memory_id_a": memory["memory_id"],
                "memory_id_b": new_memory_id,
                "conflict_description": "High content overlap with potential contradiction",
                "severity": "medium",
                "resolved": False,
                "resolution": None,
                "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
            }
    return None
