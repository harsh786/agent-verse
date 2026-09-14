"""Adapt a LongTermMemoryStore into the ChatService memory-recall hook.

ChatService.run_qa calls an async ``memory_recall(query, tenant_id) -> list[str]``.
``LongTermMemoryStore.recall_async`` takes a ``tenant_ctx`` and returns
``LongTermMemory`` objects, so this bridges the two for the lifespan wiring.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


def build_memory_recall(
    store: Any, *, top_k: int = 5
) -> Callable[[str, str], Awaitable[list[str]]]:
    """Return a ``(query, tenant_id) -> list[str]`` coroutine over *store*."""

    async def _recall(query: str, tenant_id: str) -> list[str]:
        from app.tenancy.context import PlanTier, TenantContext

        ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="chat")
        try:
            memories = await store.recall_async(query, ctx, top_k=top_k)
        except Exception:
            return []
        out: list[str] = []
        for m in memories:
            content = getattr(m, "content", None)
            if content:
                out.append(str(content))
        return out

    return _recall
