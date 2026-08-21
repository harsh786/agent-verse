"""Department Memory — PART 14 (6-tier memory, tier: department-scoped).

Department-scoped persistent knowledge that outlives individual agents.
Examples:
- Engineering: "Stack is FastAPI + React + Postgres + pgvector"
- Marketing: "ICP is mid-market SaaS, 50-500 employees"
- Finance: "Budget approval required for spend > $5,000"
- Legal: "Must comply with GDPR for EU customers"

The spec defines dept memory as one of the 6 memory tiers:
1. working (per-task, ephemeral)
2. session (per-goal)
3. agent (per-agent, persistent)
4. DEPARTMENT (this module)
5. org (cross-department)
6. long-term (cross-time-period)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import structlog
from opentelemetry import trace

_log = structlog.get_logger(__name__)
_tracer = trace.get_tracer(__name__)


@dataclass
class MemoryEntry:
    """A single department memory entry."""

    entry_id: str
    dept_id: str
    org_id: str
    tenant_id: str
    content: str
    source: str  # who/what wrote this (agent_id, user_id, etc.)
    confidence: float = 0.9  # 0-1
    tags: list[str] = field(default_factory=list)
    is_active: bool = True
    corrections: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        now = datetime.now(UTC).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


class DepartmentMemory:
    """PART 14 — Persistent department-scoped knowledge store.

    Supports:
    - retrieve:  semantic search across dept memories
    - add:       add new department knowledge
    - correct:   append correction to an existing entry (non-destructive)
    - deprecate: mark an entry as no longer valid
    """

    def __init__(self) -> None:
        # dept_id → [entries]
        self._store: dict[str, list[MemoryEntry]] = {}

    async def retrieve(
        self,
        dept_id: str,
        query: str,
        top_k: int = 5,
        active_only: bool = True,
    ) -> list[MemoryEntry]:
        """Retrieve relevant department memories for a query.

        Uses simple keyword scoring when no vector store is available;
        upgrades to semantic search when an embedder is configured.
        """
        with _tracer.start_as_current_span("dept_memory.retrieve") as span:
            span.set_attribute("dept_id", dept_id)
            span.set_attribute("query_len", len(query))
            span.set_attribute("top_k", top_k)

            entries = self._store.get(dept_id, [])
            if active_only:
                entries = [e for e in entries if e.is_active]

            if not entries:
                return []

            # Score by simple keyword overlap (replaced by vector search in prod)
            query_lower = query.lower()
            scored = []
            for entry in entries:
                content_lower = entry.content.lower()
                keywords = query_lower.split()
                overlap = sum(1 for kw in keywords if kw in content_lower)
                score = overlap / max(1, len(keywords))
                scored.append((score * entry.confidence, entry))

            results = [e for _, e in sorted(scored, key=lambda x: x[0], reverse=True)[:top_k]]
            span.set_attribute("results_count", len(results))
            return results

    async def add(
        self,
        dept_id: str,
        org_id: str,
        tenant_id: str,
        content: str,
        source: str,
        confidence: float = 0.9,
        tags: list[str] | None = None,
    ) -> MemoryEntry:
        """Add a new memory entry to the department store."""
        with _tracer.start_as_current_span("dept_memory.add") as span:
            span.set_attribute("dept_id", dept_id)
            span.set_attribute("source", source)

            entry = MemoryEntry(
                entry_id=str(uuid4()),
                dept_id=dept_id,
                org_id=org_id,
                tenant_id=tenant_id,
                content=content,
                source=source,
                confidence=confidence,
                tags=tags or [],
            )
            self._store.setdefault(dept_id, []).append(entry)
            span.set_attribute("entry_id", entry.entry_id)
            _log.info(
                "dept_memory.added",
                dept_id=dept_id,
                entry_id=entry.entry_id,
                source=source,
            )
            return entry

    async def correct(
        self,
        dept_id: str,
        entry_id: str,
        correction: str,
        corrector: str,
    ) -> MemoryEntry | None:
        """Append a correction to an existing entry (non-destructive).

        The original content is preserved; the correction is logged
        and the confidence is adjusted.
        """
        with _tracer.start_as_current_span("dept_memory.correct") as span:
            span.set_attribute("dept_id", dept_id)
            span.set_attribute("entry_id", entry_id)

            for entry in self._store.get(dept_id, []):
                if entry.entry_id == entry_id:
                    entry.corrections.append(
                        {
                            "correction": correction,
                            "corrector": corrector,
                            "corrected_at": datetime.now(UTC).isoformat(),
                        }
                    )
                    # Reduce confidence slightly after correction
                    entry.confidence = max(0.1, entry.confidence - 0.1)
                    entry.updated_at = datetime.now(UTC).isoformat()
                    _log.info("dept_memory.corrected", dept_id=dept_id, entry_id=entry_id)
                    return entry
            return None

    async def deprecate(
        self,
        dept_id: str,
        entry_id: str,
        reason: str,
    ) -> MemoryEntry | None:
        """Mark an entry as no longer valid."""
        with _tracer.start_as_current_span("dept_memory.deprecate") as span:
            span.set_attribute("dept_id", dept_id)
            span.set_attribute("entry_id", entry_id)

            for entry in self._store.get(dept_id, []):
                if entry.entry_id == entry_id:
                    entry.is_active = False
                    entry.tags.append(f"deprecated:{reason}")
                    entry.updated_at = datetime.now(UTC).isoformat()
                    _log.info(
                        "dept_memory.deprecated", dept_id=dept_id, entry_id=entry_id, reason=reason
                    )
                    return entry
            return None

    def list_entries(
        self,
        dept_id: str,
        active_only: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return all entries for a department."""
        entries = self._store.get(dept_id, [])
        if active_only:
            entries = [e for e in entries if e.is_active]
        return [
            {
                "entry_id": e.entry_id,
                "content": e.content,
                "source": e.source,
                "confidence": e.confidence,
                "is_active": e.is_active,
                "tags": e.tags,
                "created_at": e.created_at,
            }
            for e in entries[:limit]
        ]

    def dept_summary(self, dept_id: str) -> dict[str, Any]:
        """Return summary statistics for a department's memory."""
        entries = self._store.get(dept_id, [])
        active = [e for e in entries if e.is_active]
        avg_conf = sum(e.confidence for e in active) / len(active) if active else 0.0
        return {
            "dept_id": dept_id,
            "total_entries": len(entries),
            "active_entries": len(active),
            "deprecated": len(entries) - len(active),
            "avg_confidence": round(avg_conf, 3),
        }


# ── Singleton ─────────────────────────────────────────────────────────────────

_dept_memory = DepartmentMemory()


def get_dept_memory() -> DepartmentMemory:
    """Return the process-local DepartmentMemory singleton."""
    return _dept_memory
