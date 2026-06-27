"""Long-term memory — cross-session learnings persisted across agent runs.

Stores domain knowledge extracted from successful goal completions that is
useful across different goals: tool preferences, common patterns, domain facts.

In production backed by PostgreSQL long_term_memory table.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.tenancy.context import TenantContext


@dataclass
class LongTermMemory:
    """A single cross-session learning entry."""

    content: str
    source_goal_id: str
    memory_type: str  # "tool_preference" | "domain_fact" | "failure_pattern" | "success_pattern"
    confidence: float = 1.0
    memory_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    tags: list[str] = field(default_factory=list)


class LongTermMemoryStore:
    """Per-tenant store for cross-session learnings.

    Learnings are extracted from completed goals and used to bias future
    planning prompts.
    """

    def __init__(self) -> None:
        # tenant_id → list of LongTermMemory
        self._memories: dict[str, list[LongTermMemory]] = {}

    def store(self, *, memory: LongTermMemory, tenant_ctx: TenantContext) -> str:
        self._memories.setdefault(tenant_ctx.tenant_id, []).append(memory)
        return memory.memory_id

    def recall(
        self,
        *,
        query: str,
        tenant_ctx: TenantContext,
        top_k: int = 10,
        memory_type: str | None = None,
    ) -> list[LongTermMemory]:
        """Recall relevant memories using simple keyword matching."""
        memories = self._memories.get(tenant_ctx.tenant_id, [])
        if memory_type:
            memories = [m for m in memories if m.memory_type == memory_type]
        query_lower = query.lower()
        scored = [
            (m, sum(1 for word in query_lower.split() if word in m.content.lower()))
            for m in memories
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [m for m, _ in scored[:top_k]]

    def delete(self, *, memory_id: str, tenant_ctx: TenantContext) -> bool:
        memories = self._memories.get(tenant_ctx.tenant_id, [])
        for i, m in enumerate(memories):
            if m.memory_id == memory_id:
                memories.pop(i)
                return True
        return False

    def list_all(self, *, tenant_ctx: TenantContext) -> list[LongTermMemory]:
        return list(self._memories.get(tenant_ctx.tenant_id, []))

    def extract_from_goal(
        self,
        *,
        goal: str,
        result: str,
        goal_id: str,
        tenant_ctx: TenantContext,
    ) -> list[str]:
        """Auto-extract learnings from a completed goal. Returns list of memory IDs."""
        memory = LongTermMemory(
            content=f"Goal: {goal[:200]} → Result: {result[:200]}",
            source_goal_id=goal_id,
            memory_type="success_pattern",
            confidence=0.8,
            tags=["auto-extracted"],
        )
        mid = self.store(memory=memory, tenant_ctx=tenant_ctx)
        return [mid]

    async def store_async(
        self, *, memory: "LongTermMemory", tenant_ctx: Any, db: Any = None
    ) -> str:
        """Async store — persists to DB if available, always updates in-memory."""
        mid = self.store(memory=memory, tenant_ctx=tenant_ctx)
        if db is not None:
            try:
                import json as _json
                from sqlalchemy import text
                async with db() as session, session.begin():
                    await session.execute(
                        text(
                            """INSERT INTO long_term_memory
                                (id, tenant_id, content, memory_type, confidence,
                                 source_goal_id, tags)
                                VALUES (:id, :tid, :content, :mtype, :conf, :sgid, :tags)
                                ON CONFLICT DO NOTHING"""
                        ),
                        {
                            "id": mid,
                            "tid": tenant_ctx.tenant_id,
                            "content": memory.content,
                            "mtype": memory.memory_type,
                            "conf": getattr(memory, "confidence", 1.0),
                            "sgid": getattr(memory, "source_goal_id", ""),
                            "tags": _json.dumps(getattr(memory, "tags", [])),
                        },
                    )
            except Exception as exc:
                from app.observability.logging import get_logger
                get_logger(__name__).warning("ltm_db_write_failed", error=str(exc))
        return mid

    async def recall_async(
        self,
        query: str,
        tenant_ctx: Any,
        top_k: int = 5,
        db: Any = None,
    ) -> list:
        """Async recall — falls back to in-memory keyword search."""
        # DB semantic search (pgvector) is not implemented yet; use in-memory.
        return self.recall(query=query, tenant_ctx=tenant_ctx, top_k=top_k)
