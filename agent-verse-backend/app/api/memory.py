"""Memory management REST API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/memory", tags=["memory"])


def _require_tenant(request: Request) -> Any:
    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return ctx


def _get_ltm(request: Request) -> Any:
    return getattr(request.app.state, "long_term_memory", None)


def _get_db(request: Request) -> Any:
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        try:
            from app.db.session import get_session_factory
            db = get_session_factory()
        except Exception:
            pass
    return db


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
            sql = "SELECT id, content, memory_type, confidence, tags, created_at FROM long_term_memory WHERE tenant_id=:tid"
            params: dict[str, Any] = {"tid": tenant_ctx.tenant_id}
            if memory_type:
                sql += " AND memory_type=:mt"
                params["mt"] = memory_type
            sql += f" ORDER BY created_at DESC LIMIT {limit}"
            async with db() as session:
                rows = (await session.execute(text(sql), params)).fetchall()
            return [
                {
                    "id": r[0], "content": r[1], "memory_type": r[2],
                    "confidence": r[3], "tags": r[4] or [], "created_at": r[5].isoformat() if r[5] else ""
                }
                for r in rows
            ]
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("list_memories_db_failed: %s", exc)

    # In-memory fallback
    if ltm is not None:
        memories = getattr(ltm, "_memories", [])
        if hasattr(memories[0], "tenant_id") if memories else False:
            memories = [m for m in memories if getattr(m, "tenant_id", "") == tenant_ctx.tenant_id]
        return [
            {"id": getattr(m, "id", ""), "content": getattr(m, "content", ""),
             "memory_type": getattr(m, "memory_type", ""), "confidence": getattr(m, "confidence", 0.8)}
            for m in memories[:limit]
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
            {"content": getattr(m, "content", str(m)), "confidence": getattr(m, "confidence", 0.8),
             "memory_type": getattr(m, "memory_type", ""), "source": getattr(m, "source_goal_id", "")}
            for m in memories
        ]
    }


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


@router.delete("/{memory_id}")
async def delete_memory_by_id(request: Request, memory_id: str) -> dict:
    """Delete a specific memory entry (GDPR right-to-erasure for individual records)."""
    tenant_ctx = _require_tenant(request)
    db = _get_db(request)

    if db is not None:
        try:
            from sqlalchemy import text
            async with db() as session, session.begin():
                result = await session.execute(
                    text("DELETE FROM long_term_memory WHERE id=:id AND tenant_id=:tid"),
                    {"id": memory_id, "tid": tenant_ctx.tenant_id}
                )
                if result.rowcount == 0:
                    raise HTTPException(404, f"Memory {memory_id} not found")
            return {"deleted": memory_id, "status": "ok"}
        except HTTPException:
            raise
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("delete_memory_db_failed: %s", exc)

    # Fallback to in-memory store
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
    tenant = _require_tenant(request)
    mem = getattr(request.app.state, "long_term_memory", None)
    if mem is None:
        return
    all_mems = mem.list_all(tenant_ctx=tenant)
    for m in all_mems:
        mem.delete(memory_id=m.memory_id, tenant_ctx=tenant)
