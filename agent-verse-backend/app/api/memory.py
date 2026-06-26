"""Memory management REST API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/memory", tags=["memory"])


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return ctx


@router.get("/long-term")
async def list_long_term_memories(request: Request) -> list[dict[str, Any]]:
    """List all long-term memories for this tenant."""
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
        for m in mem.list_all(tenant_ctx=tenant)
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
        {"goal_text": m.get("goal_text", "")[:200],
         "success": m.get("success", False),
         "recorded_at": m.get("recorded_at", "")}
        for m in memories[-50:]  # Cap at 50
    ]


@router.delete("/long-term/{memory_id}", status_code=204)
async def delete_memory(request: Request, memory_id: str) -> None:
    """Delete a specific long-term memory."""
    tenant = _require_tenant(request)
    mem = getattr(request.app.state, "long_term_memory", None)
    if mem is None:
        raise HTTPException(404, "Memory store not available")
    ok = mem.delete(memory_id=memory_id, tenant_ctx=tenant)
    if not ok:
        raise HTTPException(404, "Memory not found")


@router.delete("", status_code=204)
async def clear_all_memories(request: Request) -> None:
    """Clear all long-term memories for this tenant (GDPR erasure)."""
    tenant = _require_tenant(request)
    mem = getattr(request.app.state, "long_term_memory", None)
    if mem is None:
        return
    all_mems = mem.list_all(tenant_ctx=tenant)
    for m in all_mems:
        mem.delete(memory_id=m.memory_id, tenant_ctx=tenant)
