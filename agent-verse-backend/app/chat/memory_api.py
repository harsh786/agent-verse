"""Memory management API layer over LongTermMemoryStore.

Provides REST endpoints for the agent memory management UI:
  GET    /memories         — list all memories
  POST   /memories         — add manual memory
  PATCH  /memories/{id}   — edit memory text
  DELETE /memories/{id}   — delete single memory
  DELETE /memories         — delete ALL memories (GDPR)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


def _now() -> datetime:
    return datetime.now(UTC)


def _hex() -> str:
    return uuid.uuid4().hex


@dataclass
class Memory:
    id: str
    tenant_id: str
    content: str
    source: str = "manual"   # manual | auto
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)


class MemoryAPI:
    """In-memory store backing the memory management REST layer.

    Production delegates to LongTermMemoryStore (Postgres + pgvector).
    """

    def __init__(self) -> None:
        self._memories: dict[str, Memory] = {}
        self._audit_log: list[dict] = []

    def list_memories(self, tenant_id: str) -> list[Memory]:
        return sorted(
            [m for m in self._memories.values() if m.tenant_id == tenant_id],
            key=lambda m: m.created_at,
            reverse=True,
        )

    def create_memory(self, tenant_id: str, content: str, source: str = "manual") -> Memory:
        m = Memory(id=_hex(), tenant_id=tenant_id, content=content, source=source)
        self._memories[m.id] = m
        return m

    def update_memory(self, memory_id: str, tenant_id: str, content: str) -> Memory | None:
        m = self._memories.get(memory_id)
        if not m or m.tenant_id != tenant_id:
            return None
        m.content = content
        m.updated_at = _now()
        return m

    def delete_memory(self, memory_id: str, tenant_id: str) -> bool:
        m = self._memories.get(memory_id)
        if not m or m.tenant_id != tenant_id:
            return False
        del self._memories[memory_id]
        self._audit_log.append({
            "action": "delete_memory",
            "memory_id": memory_id,
            "tenant_id": tenant_id,
            "at": _now().isoformat(),
        })
        return True

    def delete_all_memories(self, tenant_id: str) -> int:
        ids = [mid for mid, m in self._memories.items() if m.tenant_id == tenant_id]
        for mid in ids:
            del self._memories[mid]
        self._audit_log.append({
            "action": "delete_all_memories",
            "count": len(ids),
            "tenant_id": tenant_id,
            "at": _now().isoformat(),
        })
        return len(ids)

    def get_audit_log(self, tenant_id: str) -> list[dict]:
        return [e for e in self._audit_log if e.get("tenant_id") == tenant_id]
