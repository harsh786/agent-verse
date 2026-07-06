"""Agent Memory 2.0 API - governed memory with provenance and lifecycle."""
from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.memory_v2.models import MemoryLifecycleState, MemoryPrivacyClass
from app.observability.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/memory-v2", tags=["memory-v2"])

def _require_tenant(request: Request):
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


def _get_db(request: Request) -> Any:
    """Return the async session factory from app state, or fall back to module-level."""
    db = getattr(request.app.state, "db", None)
    if db is None:
        try:
            from app.db.session import get_session_factory
            db = get_session_factory()
        except Exception:
            pass
    return db


# ── In-memory write-through cache ──────────────────────────────────────────────
# Written to DB on every mutating operation.  On first access per tenant the
# cache is hydrated from DB so that data survives process restarts.
_memories: dict[str, dict] = {}
_conflicts: dict[str, list] = {}
# Tracks which tenant IDs have already been loaded from DB in this process.
_db_loaded_tenants: set[str] = set()


async def _ensure_loaded_from_db(tenant_id: str, db: Any) -> None:
    """Lazy-load v2 memories from DB into the module-level cache on first access."""
    if tenant_id in _db_loaded_tenants or db is None:
        return
    _db_loaded_tenants.add(tenant_id)
    try:
        from sqlalchemy import text
        async with db() as session:
            result = await session.execute(
                text(
                    "SELECT content FROM long_term_memory "
                    "WHERE tenant_id = :tid AND memory_type = 'memory_v2' "
                    "ORDER BY created_at DESC"
                ),
                {"tid": tenant_id},
            )
            for row in result.fetchall():
                try:
                    m = json.loads(row[0])
                    key = f"{m['tenant_id']}:{m['memory_id']}"
                    # Only populate entries absent in the current process to avoid
                    # overwriting writes that happened after this process started.
                    if key not in _memories:
                        _memories[key] = m
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("memory_v2_db_load_failed", error=str(exc))


async def _db_upsert_memory(db: Any, memory: dict) -> None:
    """Persist (insert or update) a v2 memory row in long_term_memory."""
    if db is None:
        return
    try:
        from sqlalchemy import text
        async with db() as session, session.begin():
            await session.execute(
                text(
                    """INSERT INTO long_term_memory
                           (id, tenant_id, content, memory_type, confidence,
                            source_goal_id, tags)
                       VALUES (:id, :tid, :content, 'memory_v2', :conf, :sgid, :tags)
                       ON CONFLICT (id) DO UPDATE SET
                           content    = EXCLUDED.content,
                           confidence = EXCLUDED.confidence,
                           tags       = EXCLUDED.tags"""
                ),
                {
                    "id": memory["memory_id"],
                    "tid": memory["tenant_id"],
                    "content": json.dumps(memory),
                    "conf": float(memory.get("confidence", 0.8)),
                    "sgid": memory.get("memory_id", ""),
                    "tags": json.dumps(memory.get("tags", [])),
                },
            )
    except Exception as exc:
        logger.warning("memory_v2_db_upsert_failed", error=str(exc))


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
    await _ensure_loaded_from_db(tenant.tenant_id, db)

    now = datetime.datetime.now(datetime.UTC).isoformat()
    memory_id = str(uuid.uuid4())

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
    _memories[f"{tenant.tenant_id}:{memory_id}"] = memory
    await _db_upsert_memory(db, memory)

    # Check for conflicts with existing memories (simple content similarity)
    await _detect_conflicts(tenant.tenant_id, memory_id, body.content)

    return {"memory_id": memory_id, "status": "created"}


@router.get("")
async def list_memories(
    request: Request,
    lifecycle_state: str | None = Query(default=None),
    memory_type: str | None = Query(default=None),
    privacy_class: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
) -> dict[str, Any]:
    """List memories with lifecycle filtering."""
    tenant = _require_tenant(request)
    db = _get_db(request)
    await _ensure_loaded_from_db(tenant.tenant_id, db)

    memories = [
        v for k, v in _memories.items()
        if k.startswith(f"{tenant.tenant_id}:")
        and v.get("lifecycle_state") != "deleted"
    ]

    if lifecycle_state:
        memories = [m for m in memories if m["lifecycle_state"] == lifecycle_state]
    if memory_type:
        memories = [m for m in memories if m["memory_type"] == memory_type]
    if privacy_class:
        memories = [m for m in memories if m["privacy_class"] == privacy_class]

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


@router.post("/lifecycle/mark-stale")
async def mark_stale_memories(request: Request) -> dict[str, Any]:
    """Mark memories as stale based on age."""
    tenant = _require_tenant(request)
    db = _get_db(request)
    await _ensure_loaded_from_db(tenant.tenant_id, db)

    body = await request.json()
    days_threshold = body.get("days_old", 30)

    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_threshold)
    marked = 0

    for key, memory in _memories.items():
        if not key.startswith(f"{tenant.tenant_id}:"):
            continue
        if memory.get("lifecycle_state") != "active":
            continue
        updated = memory.get("updated_at", "")
        if updated:
            try:
                updated_dt = datetime.datetime.fromisoformat(
                    updated.rstrip("Z")
                ).replace(tzinfo=datetime.UTC)
                if updated_dt < cutoff:
                    memory["lifecycle_state"] = "stale"
                    await _db_upsert_memory(db, memory)
                    marked += 1
            except Exception:
                pass

    return {"marked_stale": marked, "days_threshold": days_threshold}


@router.get("/export/gdpr")
async def export_gdpr(request: Request) -> dict[str, Any]:
    """Export all memories for GDPR data subject request."""
    tenant = _require_tenant(request)
    db = _get_db(request)
    await _ensure_loaded_from_db(tenant.tenant_id, db)

    memories = [
        {k: v for k, v in m.items() if k != "tenant_id"}
        for key, m in _memories.items()
        if key.startswith(f"{tenant.tenant_id}:")
    ]
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
    await _ensure_loaded_from_db(tenant.tenant_id, db)

    memory = _memories.get(f"{tenant.tenant_id}:{memory_id}")
    if not memory or memory.get("lifecycle_state") == "deleted":
        raise HTTPException(404, "Memory not found")
    return memory


@router.patch("/{memory_id}")
async def update_memory(
    request: Request, memory_id: str, body: UpdateMemoryRequest
) -> dict[str, Any]:
    """Update a memory entry."""
    tenant = _require_tenant(request)
    db = _get_db(request)
    await _ensure_loaded_from_db(tenant.tenant_id, db)

    memory = _memories.get(f"{tenant.tenant_id}:{memory_id}")
    if not memory or memory.get("lifecycle_state") == "deleted":
        raise HTTPException(404, "Memory not found")

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
    memory["provenance"]["update_count"] = memory["update_count"]

    await _db_upsert_memory(db, memory)

    return {"memory_id": memory_id, "status": "updated", "update_count": memory["update_count"]}


@router.delete("/{memory_id}")
async def delete_memory(request: Request, memory_id: str) -> dict[str, Any]:
    """Soft-delete a memory (GDPR compliant — marks as deleted)."""
    tenant = _require_tenant(request)
    db = _get_db(request)
    await _ensure_loaded_from_db(tenant.tenant_id, db)

    memory = _memories.get(f"{tenant.tenant_id}:{memory_id}")
    if not memory:
        raise HTTPException(404, "Memory not found")

    memory["lifecycle_state"] = "deleted"
    memory["deleted_at"] = datetime.datetime.now(datetime.UTC).isoformat()
    await _db_upsert_memory(db, memory)

    return {"memory_id": memory_id, "status": "deleted"}


@router.post("/consolidate")
async def consolidate_memories(request: Request) -> dict[str, Any]:
    """Run memory consolidation - dedup, merge, lifecycle management."""
    tenant = _require_tenant(request)
    db = _get_db(request)
    await _ensure_loaded_from_db(tenant.tenant_id, db)
    from app.memory_v2.consolidation import memory_consolidator
    stats = await memory_consolidator.consolidate(tenant.tenant_id, _memories)
    return {"status": "consolidated", **stats}


async def _detect_conflicts(tenant_id: str, new_memory_id: str, new_content: str) -> None:
    """Detect potential conflicts with existing memories."""
    new_words = set(new_content.lower().split())

    for key, memory in _memories.items():
        if not key.startswith(f"{tenant_id}:"):
            continue
        if memory["memory_id"] == new_memory_id:
            continue
        if memory.get("lifecycle_state") in ("deleted", "archived"):
            continue

        existing_words = set(memory["content"].lower().split())
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
