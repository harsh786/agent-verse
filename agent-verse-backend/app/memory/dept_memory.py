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

# Minimum confidence required to promote a memory into the department tier.
# Mirrors app.org.org_learning.OrgLearningLoop.MIN_CONFIDENCE_FOR_PROMOTION.
MIN_CONFIDENCE_FOR_PROMOTION = 0.70


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
        # Wired by the app lifespan; when set, entries persist to Postgres
        # (durable + cross-pod) instead of only this process's dict.
        self._db_factory: Any = None

    def set_db(self, db_factory: Any) -> None:
        self._db_factory = db_factory

    async def _db_rows(self, dept_id: str, tenant_id: str, active_only: bool) -> list[MemoryEntry]:
        """Load a department's entries from Postgres (RLS-scoped)."""
        import json as _json

        from sqlalchemy import text as _t

        clause = " AND is_active IS TRUE" if active_only else ""
        async with self._db_factory() as s, s.begin():
            await s.execute(
                _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            rows = (
                await s.execute(
                    _t(
                        "SELECT entry_id, dept_id, org_id, tenant_id, content, source, "
                        "confidence, tags, is_active, corrections, created_at, updated_at "
                        f"FROM department_memory_entries WHERE tenant_id = :tid "
                        f"AND dept_id = :did{clause} ORDER BY created_at DESC LIMIT 1000"
                    ),
                    {"tid": tenant_id, "did": dept_id},
                )
            ).mappings().all()

        def _load(v: Any) -> Any:
            return _json.loads(v) if isinstance(v, str) else (v or [])

        return [
            MemoryEntry(
                entry_id=r["entry_id"], dept_id=r["dept_id"], org_id=r["org_id"],
                tenant_id=r["tenant_id"], content=r["content"], source=r["source"],
                confidence=r["confidence"], tags=list(_load(r["tags"])),
                is_active=r["is_active"], corrections=list(_load(r["corrections"])),
                created_at=r["created_at"].isoformat() if r["created_at"] else "",
                updated_at=r["updated_at"].isoformat() if r["updated_at"] else "",
            )
            for r in rows
        ]

    async def retrieve(
        self,
        dept_id: str,
        query: str,
        top_k: int = 5,
        active_only: bool = True,
        tenant_id: str | None = None,
    ) -> list[MemoryEntry]:
        """Retrieve relevant department memories for a query.

        Uses simple keyword scoring when no vector store is available;
        upgrades to semantic search when an embedder is configured.
        """
        with _tracer.start_as_current_span("dept_memory.retrieve") as span:
            span.set_attribute("dept_id", dept_id)
            span.set_attribute("query_len", len(query))
            span.set_attribute("top_k", top_k)

            if self._db_factory is not None and tenant_id is not None:
                entries = await self._db_rows(dept_id, tenant_id, active_only)
            else:
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
        """Add a new memory entry to the department store.

        Raises:
            ValueError: if confidence is below MIN_CONFIDENCE_FOR_PROMOTION — low
                confidence facts must not be promoted into durable department memory.
        """
        if confidence < MIN_CONFIDENCE_FOR_PROMOTION:
            raise ValueError(
                f"confidence {confidence:.2f} is below the promotion threshold "
                f"of {MIN_CONFIDENCE_FOR_PROMOTION:.2f}"
            )
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
            if self._db_factory is not None:
                import json as _json

                from sqlalchemy import text as _t

                async with self._db_factory() as s, s.begin():
                    await s.execute(
                        _t("SELECT set_config('app.tenant_id', :tid, true)"),
                        {"tid": tenant_id},
                    )
                    await s.execute(
                        _t(
                            "INSERT INTO department_memory_entries (entry_id, dept_id, "
                            "org_id, tenant_id, content, source, confidence, tags) VALUES "
                            "(:eid, :did, :oid, :tid, :c, :src, :conf, CAST(:tags AS jsonb))"
                        ),
                        {
                            "eid": entry.entry_id, "did": dept_id, "oid": org_id,
                            "tid": tenant_id, "c": content, "src": source,
                            "conf": confidence, "tags": _json.dumps(tags or []),
                        },
                    )
            else:
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
        tenant_id: str | None = None,
    ) -> MemoryEntry | None:
        """Append a correction to an existing entry (non-destructive).

        The original content is preserved; the correction is logged
        and the confidence is adjusted.
        """
        with _tracer.start_as_current_span("dept_memory.correct") as span:
            span.set_attribute("dept_id", dept_id)
            span.set_attribute("entry_id", entry_id)

            if self._db_factory is not None and tenant_id is not None:
                import json as _json

                from sqlalchemy import text as _t

                corr = {
                    "correction": correction,
                    "corrector": corrector,
                    "corrected_at": datetime.now(UTC).isoformat(),
                }
                async with self._db_factory() as s, s.begin():
                    await s.execute(
                        _t("SELECT set_config('app.tenant_id', :tid, true)"),
                        {"tid": tenant_id},
                    )
                    row = (
                        await s.execute(
                            _t(
                                "UPDATE department_memory_entries SET "
                                "corrections = corrections || CAST(:corr AS jsonb), "
                                "confidence = GREATEST(0.1, confidence - 0.1), "
                                "updated_at = now() WHERE entry_id = :eid AND tenant_id = :tid "
                                "RETURNING entry_id, dept_id, org_id, tenant_id, content, "
                                "source, confidence, tags, is_active, corrections, "
                                "created_at, updated_at"
                            ),
                            {"corr": _json.dumps(corr), "eid": entry_id, "tid": tenant_id},
                        )
                    ).mappings().first()
                if row is None:
                    return None
                return MemoryEntry(
                    entry_id=row["entry_id"], dept_id=row["dept_id"], org_id=row["org_id"],
                    tenant_id=row["tenant_id"], content=row["content"], source=row["source"],
                    confidence=row["confidence"],
                    tags=list(
                        _json.loads(row["tags"])
                        if isinstance(row["tags"], str)
                        else row["tags"] or []
                    ),
                    is_active=row["is_active"],
                    created_at=row["created_at"].isoformat() if row["created_at"] else "",
                    updated_at=row["updated_at"].isoformat() if row["updated_at"] else "",
                )

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
        tenant_id: str | None = None,
    ) -> MemoryEntry | None:
        """Mark an entry as no longer valid."""
        with _tracer.start_as_current_span("dept_memory.deprecate") as span:
            span.set_attribute("dept_id", dept_id)
            span.set_attribute("entry_id", entry_id)

            if self._db_factory is not None and tenant_id is not None:
                from sqlalchemy import text as _t

                async with self._db_factory() as s, s.begin():
                    await s.execute(
                        _t("SELECT set_config('app.tenant_id', :tid, true)"),
                        {"tid": tenant_id},
                    )
                    res = await s.execute(
                        _t(
                            "UPDATE department_memory_entries SET is_active = FALSE, "
                            "tags = tags || CAST(:tag AS jsonb), updated_at = now() "
                            "WHERE entry_id = :eid AND tenant_id = :tid"
                        ),
                        {"tag": f'["deprecated:{reason}"]', "eid": entry_id, "tid": tenant_id},
                    )
                return None if not res.rowcount else MemoryEntry(
                    entry_id=entry_id, dept_id=dept_id, org_id="", tenant_id=tenant_id,
                    content="", source="", is_active=False,
                )

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

    async def list_entries_async(
        self,
        dept_id: str,
        tenant_id: str,
        active_only: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """DB-backed list of a department's entries (falls back to in-memory)."""
        if self._db_factory is None:
            return self.list_entries(dept_id, active_only=active_only, limit=limit)
        entries = await self._db_rows(dept_id, tenant_id, active_only)
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
