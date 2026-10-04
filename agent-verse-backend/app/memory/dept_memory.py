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

# Keywords of a retrieval query that take part in the SQL ranking.
_MAX_QUERY_KEYWORDS = 32

# MEM-42: semantic recall. Entries are embedded on add into a vector(2048)
# column (halfvec HNSW index); the rank blends cosine similarity (same
# embedding model only) with the keyword share, times confidence.
_EMBED_DIM = 2048
_SEMANTIC_WEIGHT = 0.7
_LEXICAL_WEIGHT = 0.3
# Candidates each side (semantic / lexical) contributes before blending.
_CANDIDATES = 50
# A department this small is searched exactly (no ANN), like canonical memory.
_EXACT_SEARCH_MAX_ROWS = 5_000
_HNSW_EF_SEARCH = 200

_COLS = (
    "entry_id, dept_id, org_id, tenant_id, content, source, confidence, tags, "
    "is_active, corrections, created_at, updated_at"
)
_DIST = f"(embedding::halfvec({_EMBED_DIM}) <=> CAST(:qvec AS halfvec({_EMBED_DIM})))"


def _lexical_expr(n_keywords: int) -> str:
    if n_keywords <= 0:
        return "0.0"
    hits = " + ".join(
        f"(CASE WHEN strpos(lower(content), :kw{i}) > 0 THEN 1 ELSE 0 END)"
        for i in range(n_keywords)
    )
    return f"(({hits})::float / {n_keywords})"


def _ranked_select_sql(
    *, scope: str, clause: str, n_keywords: int, semantic: bool, exact: bool
) -> str:
    """The department-memory recall query (always tenant- and scope-filtered).

    Without a query vector: keyword share x confidence over the scope. With
    one: the nearest same-model vectors (exact for a small department, the
    halfvec HNSW index otherwise) and the best lexical matches are the
    candidates, ranked by the blended score; only ``:lim`` rows return.
    """
    where = f"tenant_id = :tid AND {scope}{clause}"
    lexical = _lexical_expr(n_keywords)
    if not semantic:
        order = (
            f"{lexical} * confidence DESC, created_at DESC" if n_keywords else "created_at DESC"
        )
        return (
            f"SELECT {_COLS} FROM department_memory_entries WHERE {where} "
            f"ORDER BY {order} LIMIT :lim"
        )
    similarity = (
        f"(CASE WHEN embedding IS NOT NULL AND embedding_model = :qmodel "
        f"THEN GREATEST(0.0, 1 - {_DIST}) ELSE 0.0 END)"
    )
    score = f"({_SEMANTIC_WEIGHT} * {similarity} + {_LEXICAL_WEIGHT} * {lexical}) * confidence"
    nearest = f"{_DIST}{' + 0' if exact else ''}"
    return (
        "WITH sem AS ("
        f"SELECT entry_id FROM department_memory_entries WHERE {where} "
        "AND embedding IS NOT NULL AND embedding_model = :qmodel "
        f"ORDER BY {nearest} LIMIT :cand), "
        "lex AS ("
        f"SELECT entry_id FROM department_memory_entries WHERE {where} "
        f"ORDER BY {lexical} * confidence DESC, created_at DESC LIMIT :cand) "
        f"SELECT {_COLS} FROM department_memory_entries WHERE tenant_id = :tid "
        "AND entry_id IN (SELECT entry_id FROM sem UNION SELECT entry_id FROM lex) "
        f"ORDER BY {score} DESC, created_at DESC LIMIT :lim"
    )


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return max(0.0, dot / (na * nb)) if na and nb else 0.0


def _vector_literal(vec: tuple[float, ...]) -> str:
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"


class DepartmentMemoryBlockedError(ValueError):
    """The memory-write gate refused the content (guardrail block or injection)."""


class DepartmentMemoryUnavailableError(RuntimeError):
    """The memory-write gate could not vet the content; nothing was stored."""


async def _screen_dept_text(text: str, *, tenant_id: str) -> str:
    """MEM-68: the shared memory-write gate for department memory.

    Entries are injected into the planner prompt of every goal the department
    dispatches, so PII/secrets and prompt-injection payloads are refused here.
    Returns the (possibly redacted) text to store.
    """
    from app.memory.screening import MemoryScreeningError, screen_memory_content

    try:
        screened = await screen_memory_content(text, tenant_id=tenant_id, store="department")
    except MemoryScreeningError as exc:
        raise DepartmentMemoryUnavailableError(
            "memory-write guardrail could not vet the content; nothing was stored"
        ) from exc
    if screened is None:
        raise DepartmentMemoryBlockedError(
            "department memory content rejected by the memory-write guardrail"
        )
    return screened


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
    # MEM-42: the content's vector and the model that produced it.
    embedding: tuple[float, ...] | None = None
    embedding_model: str | None = None

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
        # (tenant_id, dept_id) → [entries]. Keyed by tenant too: two tenants may
        # use the same dept_id and must never see each other's entries.
        self._store: dict[tuple[str, str], list[MemoryEntry]] = {}
        # Wired by the app lifespan; when set, entries persist to Postgres
        # (durable + cross-pod) instead of only this process's dict.
        self._db_factory: Any = None
        # ``(text) -> 2048-d tuple | None`` with a ``model_id`` (see
        # app.memory.embedding.memory_embedder_from_provider); None = lexical.
        self._embedder: Any = None
        self._iterative_scan: bool | None = None

    def set_db(self, db_factory: Any) -> None:
        self._db_factory = db_factory

    def set_embedder(self, embedder: Any) -> None:
        self._embedder = embedder

    @staticmethod
    async def _embed(
        embedder: Any, text: str, *, tenant_id: str
    ) -> tuple[tuple[float, ...] | None, str | None]:
        """``(vector, model)``; ``(None, None)`` without a usable vector.

        Non-fatal (the entry is still found lexically) but never silent.
        """
        if embedder is None:
            return None, None
        from app.memory.embedding import fit_memory_vector

        try:
            raw = await embedder(text)
        except Exception as exc:
            _log.warning(
                "dept_memory.embedding_failed", tenant_id=tenant_id, error=str(exc)[:200]
            )
            return None, None
        fitted = fit_memory_vector(raw) if raw else None
        model = str(getattr(embedder, "model_id", "") or "")[:128]
        if fitted is None or not model:
            return None, None
        return fitted, model

    async def _small_scope(self, session: Any, where: str, params: dict[str, Any]) -> bool:
        from sqlalchemy import text as _t

        count = (
            await session.execute(
                _t(
                    "SELECT count(*) FROM (SELECT 1 FROM department_memory_entries "
                    f"WHERE {where} LIMIT :cap) s"
                ),
                {**params, "cap": _EXACT_SEARCH_MAX_ROWS + 1},
            )
        ).scalar()
        return int(count or 0) <= _EXACT_SEARCH_MAX_ROWS

    async def _iterative_scan_supported(self, session: Any) -> bool:
        """pgvector >= 0.8 (hnsw.iterative_scan); probed once per store."""
        if self._iterative_scan is None:
            from sqlalchemy import text as _t

            version = (
                await session.execute(
                    _t("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                )
            ).scalar()
            try:
                parts = tuple(int(p) for p in str(version or "0").split(".")[:2])
            except ValueError:
                parts = (0, 0)
            self._iterative_scan = parts >= (0, 8)
        return self._iterative_scan

    async def _db_rows(
        self,
        dept_id: str,
        tenant_id: str,
        active_only: bool,
        *,
        keywords: list[str] | None = None,
        limit: int = 1000,
        org_id: str | None = None,
        query_vec: tuple[float, ...] | None = None,
        query_model: str | None = None,
    ) -> list[MemoryEntry]:
        """A department's entries from Postgres (RLS-scoped), best matches first.

        With ``keywords`` the ranking happens in SQL: the share of query keywords
        the content contains (case-insensitive substring, the same rule as the
        in-memory path) times the entry's confidence, ties newest first, and
        only ``limit`` rows leave the database (MEM-15). With a query vector the
        rank also blends cosine similarity to same-model entry vectors (MEM-42).
        """
        import json as _json

        from sqlalchemy import text as _t

        clause = " AND is_active IS TRUE" if active_only else ""
        params: dict[str, Any] = {"tid": tenant_id, "lim": max(1, limit)}
        # One department, or (org_id given) every department of an org.
        if org_id is not None:
            scope = "org_id = :oid"
            params["oid"] = org_id
        else:
            scope = "dept_id = :did"
            params["did"] = dept_id
        keywords = keywords or []
        params.update({f"kw{i}": kw for i, kw in enumerate(keywords)})
        semantic = query_vec is not None and bool(query_model)
        if semantic and query_vec is not None:
            params.update(
                {
                    "qvec": _vector_literal(query_vec),
                    "qmodel": query_model,
                    "cand": max(_CANDIDATES, limit * 4),
                }
            )
        async with self._db_factory() as s, s.begin():
            await s.execute(
                _t("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": tenant_id}
            )
            exact = True
            if semantic:
                exact = await self._small_scope(
                    s, f"tenant_id = :tid AND {scope}{clause}", params
                )
                if not exact and await self._iterative_scan_supported(s):
                    await s.execute(_t("SET LOCAL hnsw.iterative_scan = relaxed_order"))
                    await s.execute(_t(f"SET LOCAL hnsw.ef_search = {_HNSW_EF_SEARCH}"))
            sql = _ranked_select_sql(
                scope=scope,
                clause=clause,
                n_keywords=len(keywords),
                semantic=semantic,
                exact=exact,
            )
            rows = (await s.execute(_t(sql), params)).mappings().all()

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
        *,
        tenant_id: str,
        embedder: Any = None,
    ) -> list[MemoryEntry]:
        """Retrieve relevant department memories for a query (tenant required).

        Semantic + lexical (MEM-42): cosine similarity to entries embedded by
        the same model, blended with the share of query keywords an entry
        contains, weighted by its confidence. Without an embedder (the store's,
        or ``embedder`` for this call) the ranking is lexical. With Postgres the
        ranking runs in SQL and returns ``top_k`` rows.
        """
        with _tracer.start_as_current_span("dept_memory.retrieve") as span:
            span.set_attribute("dept_id", dept_id)
            span.set_attribute("query_len", len(query))
            span.set_attribute("top_k", top_k)
            qvec, qmodel = await self._embed(
                self._embedder or embedder, query, tenant_id=tenant_id
            )
            span.set_attribute("semantic", qvec is not None)
            if self._db_factory is not None:
                keywords = query.lower().split()[:_MAX_QUERY_KEYWORDS]
                results = await self._db_rows(
                    dept_id, tenant_id, active_only, keywords=keywords, limit=top_k,
                    query_vec=qvec, query_model=qmodel,
                )
            else:
                results = self._rank(
                    self._store.get((tenant_id, dept_id), []), query, top_k, active_only,
                    query_vec=qvec, query_model=qmodel,
                )
            span.set_attribute("results_count", len(results))
            return results

    async def retrieve_for_org(
        self,
        org_id: str,
        query: str,
        top_k: int = 5,
        active_only: bool = True,
        *,
        tenant_id: str,
        embedder: Any = None,
    ) -> list[MemoryEntry]:
        """Retrieve relevant memories across every department of one org."""
        with _tracer.start_as_current_span("dept_memory.retrieve_for_org") as span:
            span.set_attribute("org_id", org_id)
            qvec, qmodel = await self._embed(
                self._embedder or embedder, query, tenant_id=tenant_id
            )
            if self._db_factory is not None:
                keywords = query.lower().split()[:_MAX_QUERY_KEYWORDS]
                results = await self._db_rows(
                    "", tenant_id, active_only, keywords=keywords, limit=top_k, org_id=org_id,
                    query_vec=qvec, query_model=qmodel,
                )
            else:
                entries = [
                    e
                    for (tid, _did), rows in self._store.items()
                    if tid == tenant_id
                    for e in rows
                    if e.org_id == org_id
                ]
                results = self._rank(
                    entries, query, top_k, active_only, query_vec=qvec, query_model=qmodel
                )
            span.set_attribute("results_count", len(results))
            return results

    @staticmethod
    def _rank(
        entries: list[MemoryEntry],
        query: str,
        top_k: int,
        active_only: bool,
        *,
        query_vec: tuple[float, ...] | None = None,
        query_model: str | None = None,
    ) -> list[MemoryEntry]:
        """The SQL path's rule, in memory: keyword overlap x confidence, blended
        with cosine similarity to same-model vectors when there is a query vector."""
        if active_only:
            entries = [e for e in entries if e.is_active]
        keywords = query.lower().split()[:_MAX_QUERY_KEYWORDS]
        scored = []
        for entry in entries:
            content_lower = entry.content.lower()
            overlap = sum(1 for kw in keywords if kw in content_lower)
            score = overlap / max(1, len(keywords))
            if query_vec is not None and query_model:
                similarity = (
                    _cosine(query_vec, entry.embedding)
                    if entry.embedding is not None and entry.embedding_model == query_model
                    else 0.0
                )
                score = _SEMANTIC_WEIGHT * similarity + _LEXICAL_WEIGHT * score
            scored.append((score * entry.confidence, entry))
        return [e for _, e in sorted(scored, key=lambda x: x[0], reverse=True)[:top_k]]

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
            DepartmentMemoryBlockedError: the memory-write gate refused the content.
            DepartmentMemoryUnavailableError: the gate could not vet it (fail closed).
        """
        if confidence < MIN_CONFIDENCE_FOR_PROMOTION:
            raise ValueError(
                f"confidence {confidence:.2f} is below the promotion threshold "
                f"of {MIN_CONFIDENCE_FOR_PROMOTION:.2f}"
            )
        content = await _screen_dept_text(content, tenant_id=tenant_id)
        embedding, embedding_model = await self._embed(
            self._embedder, content, tenant_id=tenant_id
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
                embedding=embedding,
                embedding_model=embedding_model,
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
                            "org_id, tenant_id, content, source, confidence, tags, "
                            "embedding, embedding_model) VALUES "
                            "(:eid, :did, :oid, :tid, :c, :src, :conf, CAST(:tags AS jsonb), "
                            "CAST(:emb AS vector), :emodel)"
                        ),
                        {
                            "eid": entry.entry_id, "did": dept_id, "oid": org_id,
                            "tid": tenant_id, "c": content, "src": source,
                            "conf": confidence, "tags": _json.dumps(tags or []),
                            "emb": _vector_literal(embedding) if embedding else None,
                            "emodel": embedding_model,
                        },
                    )
            else:
                self._store.setdefault((tenant_id, dept_id), []).append(entry)
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
        *,
        tenant_id: str,
    ) -> MemoryEntry | None:
        """Append a correction to an existing entry (non-destructive).

        The original content is preserved; the correction is logged
        and the confidence is adjusted. The correction passes the memory-write
        gate first (see :meth:`add` for the errors it raises).
        """
        correction = await _screen_dept_text(correction, tenant_id=tenant_id)
        with _tracer.start_as_current_span("dept_memory.correct") as span:
            span.set_attribute("dept_id", dept_id)
            span.set_attribute("entry_id", entry_id)

            if self._db_factory is not None:
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

            for entry in self._store.get((tenant_id, dept_id), []):
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
        *,
        tenant_id: str,
    ) -> MemoryEntry | None:
        """Mark an entry as no longer valid."""
        with _tracer.start_as_current_span("dept_memory.deprecate") as span:
            span.set_attribute("dept_id", dept_id)
            span.set_attribute("entry_id", entry_id)

            if self._db_factory is not None:
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

            for entry in self._store.get((tenant_id, dept_id), []):
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
        *,
        tenant_id: str,
    ) -> list[dict[str, Any]]:
        """Return all entries for a tenant's department (DB-less store)."""
        entries = self._store.get((tenant_id, dept_id), [])
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
            return self.list_entries(
                dept_id, active_only=active_only, limit=limit, tenant_id=tenant_id
            )
        entries = await self._db_rows(dept_id, tenant_id, active_only, limit=limit)
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

    def dept_summary(self, dept_id: str, *, tenant_id: str) -> dict[str, Any]:
        """Return summary statistics for a tenant's department memory (DB-less)."""
        entries = self._store.get((tenant_id, dept_id), [])
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
