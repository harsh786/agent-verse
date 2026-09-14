"""Adapt a LongTermMemoryStore into the ChatService memory-recall hook.

ChatService.run_qa calls an async ``memory_recall(query, tenant_id) -> list[str]``.
``LongTermMemoryStore.recall_async`` takes a ``tenant_ctx`` and returns
``LongTermMemory`` objects, so this bridges the two for the lifespan wiring.
"""

from __future__ import annotations

import contextlib
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


def build_memory_writer(
    store: Any, *, memory_type: str = "domain_fact"
) -> Callable[[str, str], Awaitable[None]]:
    """Return a ``(fact, tenant_id) -> None`` coroutine that persists a fact via
    ``LongTermMemoryStore.store_async``. Degrades silently on failure."""

    async def _write(fact: str, tenant_id: str) -> None:
        from app.memory.long_term import LongTermMemory
        from app.tenancy.context import PlanTier, TenantContext

        ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.FREE, api_key_id="chat")
        memory = LongTermMemory(content=fact, source_goal_id="chat", memory_type=memory_type)
        with contextlib.suppress(Exception):
            await store.store_async(memory=memory, tenant_ctx=ctx)

    return _write
