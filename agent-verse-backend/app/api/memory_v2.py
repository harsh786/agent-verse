"""Agent Memory 2.0 API - governed memory with provenance and lifecycle."""
from __future__ import annotations
import uuid
import datetime
from typing import Any
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel, Field
from app.memory_v2.models import MemoryLifecycleState, MemoryPrivacyClass

router = APIRouter(prefix="/memory-v2", tags=["memory-v2"])

def _require_tenant(request: Request):
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx

# In-memory store for demo
_memories: dict[str, dict] = {}
_conflicts: dict[str, list] = {}


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
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
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
    conflict["resolved_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    return {"conflict_id": conflict_id, "status": "resolved"}


@router.post("/lifecycle/mark-stale")
async def mark_stale_memories(request: Request) -> dict[str, Any]:
    """Mark memories as stale based on age."""
    tenant = _require_tenant(request)
    body = await request.json()
    days_threshold = body.get("days_old", 30)

    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_threshold)
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
                ).replace(tzinfo=datetime.timezone.utc)
                if updated_dt < cutoff:
                    memory["lifecycle_state"] = "stale"
                    marked += 1
            except Exception:
                pass

    return {"marked_stale": marked, "days_threshold": days_threshold}


@router.get("/export/gdpr")
async def export_gdpr(request: Request) -> dict[str, Any]:
    """Export all memories for GDPR data subject request."""
    tenant = _require_tenant(request)
    memories = [
        {k: v for k, v in m.items() if k != "tenant_id"}
        for key, m in _memories.items()
        if key.startswith(f"{tenant.tenant_id}:")
    ]
    return {
        "tenant_id": tenant.tenant_id,
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "total_memories": len(memories),
        "memories": memories,
        "format": "gdpr_data_export_v1",
    }


@router.get("/{memory_id}")
async def get_memory(request: Request, memory_id: str) -> dict[str, Any]:
    """Get a specific memory with provenance."""
    tenant = _require_tenant(request)
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
    memory = _memories.get(f"{tenant.tenant_id}:{memory_id}")
    if not memory or memory.get("lifecycle_state") == "deleted":
        raise HTTPException(404, "Memory not found")

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if body.content is not None:
        memory["content"] = body.content
    if body.confidence is not None:
        memory["confidence"] = body.confidence
    if body.lifecycle_state is not None:
        try:
            MemoryLifecycleState(body.lifecycle_state)
            memory["lifecycle_state"] = body.lifecycle_state
        except ValueError:
            raise HTTPException(400, f"Invalid lifecycle state: {body.lifecycle_state}")
    if body.tags is not None:
        memory["tags"] = body.tags

    memory["updated_at"] = now
    memory["update_count"] = memory.get("update_count", 0) + 1
    memory["provenance"]["update_count"] = memory["update_count"]

    return {"memory_id": memory_id, "status": "updated", "update_count": memory["update_count"]}


@router.delete("/{memory_id}")
async def delete_memory(request: Request, memory_id: str) -> dict[str, Any]:
    """Soft-delete a memory (GDPR compliant — marks as deleted)."""
    tenant = _require_tenant(request)
    memory = _memories.get(f"{tenant.tenant_id}:{memory_id}")
    if not memory:
        raise HTTPException(404, "Memory not found")

    memory["lifecycle_state"] = "deleted"
    memory["deleted_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return {"memory_id": memory_id, "status": "deleted"}


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
                    "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }
                _conflicts.setdefault(tenant_id, []).append(conflict)
                break  # Max 1 conflict per new memory
