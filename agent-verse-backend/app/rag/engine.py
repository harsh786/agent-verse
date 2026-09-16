"""
World-Class RAG Retrieval Engine
=================================

Four retrieval legs fused with Reciprocal Rank Fusion (RRF):
  1. pgvector ANN — cosine similarity with HNSW index
  2. PostgreSQL FTS — tsvector + ts_rank_cd
  3. pg_trgm fuzzy — trigram similarity for typo tolerance
  4. Application Okapi BM25 over a bounded persisted corpus

After fusion: optional cross-encoder reranking of top-50 candidates.

Vector-DB-less mode: when a collection has no embeddings or no embedding
provider is configured, the engine degrades gracefully to FTS+trigram only.

Retrieval modes:
  - "hybrid" (default): all 3 legs + RRF
  - "lexical": FTS + trigram only (no embeddings needed)
  - "vector": ANN only
"""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, ClassVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.observability.logging import get_logger
from app.rag.bm25 import BM25CorpusScorer, BM25Hit
from app.rag.rerank_stage import apply_default_rerank

logger = get_logger(__name__)

# RRF constant (standard: 60)
_RRF_K = 60
# Must match app.rag.store.SUPPORTED_EMBEDDING_DIMENSIONS and the
# ck_knowledge_collections_embedding_dim DB constraint. 2048 (NVIDIA nemotron) and
# 3072 exceed pgvector's 2000-d cap for a plain `vector` HNSW index, so they have a
# halfvec HNSW index (idx_knowledge_chunks_<dim>_vector_halfvec) and MUST be queried
# with a matching halfvec cast (see below) to use it instead of an exact scan.
_SUPPORTED_EMBEDDING_DIMENSIONS = frozenset({768, 1024, 1536, 2048, 3072})
_BM25_PAGE_SIZE = 500
_MAX_HOPS = 5
_MAX_VARIANTS = 5


@dataclass
class RetrievalResult:
    chunk_id: str
    content: str
    score: float
    source_metadata: dict[str, Any]
    retrieval_legs: list[str] = field(default_factory=list)  # which legs contributed
    component_scores: dict[str, float] = field(default_factory=dict)
    rrf_score: float = 0.0


@dataclass(frozen=True, slots=True)
class ParentWindowCitation:
    parent_chunk_id: str
    parent_content: str


async def load_agentic_parent_citations(
    session: AsyncSession,
    *,
    child_chunk_ids: list[str],
    collection_id: str,
    tenant_id: str,
    embedding_dim: int,
) -> dict[str, ParentWindowCitation]:
    """Load parent windows for tenant-scoped persisted proposition hits."""
    if embedding_dim not in _SUPPORTED_EMBEDDING_DIMENSIONS:
        raise RetrievalStrategyExecutionError("agentic_chunking", "unsupported embedding dimension")
    table = f"knowledge_chunks_{embedding_dim}"
    rows = (
        await session.execute(
            text(f"""
                SELECT child.id, parent.id, parent.content
                FROM {table} AS child
                JOIN {table} AS parent
                  ON parent.id = child.parent_chunk_id
                 AND parent.collection_id = child.collection_id
                 AND parent.tenant_id = child.tenant_id
                WHERE child.id = ANY(:child_ids)
                  AND child.collection_id = :collection_id
                  AND child.tenant_id = :tenant_id
                  AND child.is_proposition IS TRUE
            """),
            {
                "child_ids": child_chunk_ids,
                "collection_id": collection_id,
                "tenant_id": tenant_id,
            },
        )
    ).fetchall()
    return {str(row[0]): ParentWindowCitation(str(row[1]), str(row[2])) for row in rows}


def expand_agentic_parent_results(
    results: list[RetrievalResult],
    parent_citations: dict[str, ParentWindowCitation],
    *,
    top_k: int,
) -> list[RetrievalResult]:
    """Expand propositions, preserve provenance, and dedupe before final top-k."""
    expanded: dict[str, RetrievalResult] = {}
    for result in results:
        parent = parent_citations.get(result.chunk_id)
        if parent is None:
            raise RetrievalStrategyExecutionError(
                "agentic_chunking",
                f"persisted proposition has no parent: {result.chunk_id}",
            )
        proposition = {
            "proposition_chunk_id": result.chunk_id,
            "proposition_content": result.content,
            "score": result.score,
        }
        existing = expanded.get(parent.parent_chunk_id)
        if existing is None:
            result.source_metadata = {
                **result.source_metadata,
                "strategy": "agentic_chunking",
                **proposition,
                "parent_chunk_id": parent.parent_chunk_id,
                "propositions": [proposition],
            }
            result.chunk_id = parent.parent_chunk_id
            result.content = parent.parent_content
            result.retrieval_legs = list(
                dict.fromkeys([*result.retrieval_legs, "agentic_chunking"])
            )
            expanded[parent.parent_chunk_id] = result
            continue
        existing.source_metadata["propositions"].append(proposition)
        existing.score = max(existing.score, result.score)
        existing.component_scores.update(result.component_scores)
        existing.retrieval_legs = list(
            dict.fromkeys([*existing.retrieval_legs, *result.retrieval_legs])
        )
    return sorted(
        expanded.values(),
        key=lambda result: (-result.score, result.chunk_id),
    )[:top_k]


def merge_grounding_results(
    result_groups: list[list[RetrievalResult]],
    *,
    top_k: int,
) -> list[RetrievalResult]:
    """Merge ranked evidence without discarding source provenance."""

    merged: dict[str, RetrievalResult] = {}
    provenance: dict[str, list[dict[str, Any]]] = {}
    for results in result_groups:
        for result in results:
            source_provenance = dict(result.source_metadata)
            existing = merged.get(result.chunk_id)
            if existing is None:
                result.source_metadata = dict(result.source_metadata)
                merged[result.chunk_id] = result
                provenance[result.chunk_id] = [source_provenance]
                continue
            if source_provenance not in provenance[result.chunk_id]:
                provenance[result.chunk_id].append(source_provenance)
            existing.score = max(existing.score, result.score)
            existing.rrf_score = max(existing.rrf_score, result.rrf_score)
            existing.retrieval_legs = list(
                dict.fromkeys([*existing.retrieval_legs, *result.retrieval_legs])
            )
            existing.component_scores.update(result.component_scores)

    for chunk_id, sources in provenance.items():
        if len(sources) > 1:
            merged[chunk_id].source_metadata["merged_provenance"] = sources
    return sorted(
        merged.values(),
        key=lambda result: (-result.score, result.chunk_id),
    )[:top_k]


@dataclass(slots=True)
class _BM25HeapEntry:
    score: float
    chunk_id: str
    content: str
    source_metadata: dict[str, Any]

    def __lt__(self, other: _BM25HeapEntry) -> bool:
        if self.score != other.score:
            return self.score < other.score
        return self.chunk_id > other.chunk_id


class RetrievalExecutionError(RuntimeError):
    """A canonical retrieval operation failed before producing valid evidence."""


class RetrievalLegExecutionError(RetrievalExecutionError):
    """An enabled, required retrieval leg failed."""

    def __init__(self, leg: str) -> None:
        super().__init__(f"Required retrieval leg failed: {leg}")
        self.leg = leg


class RetrievalStrategyExecutionError(RetrievalExecutionError):
    """The requested strategy cannot complete without substitution."""

    def __init__(self, strategy: str, reason: str) -> None:
        super().__init__(f"RAG strategy execution failed: {strategy} ({reason})")
        self.strategy = strategy
        self.reason = reason


def first_task_group_exception(group: BaseExceptionGroup[BaseException]) -> BaseException:
    """Return the first concrete TaskGroup failure for canonical error propagation."""

    first = group.exceptions[0]
    return first_task_group_exception(first) if isinstance(first, BaseExceptionGroup) else first


async def _run_parallel_searches(
    requests: list[tuple[str, list[float] | None]],
    operation: Callable[[str, list[float] | None], Awaitable[list[RetrievalResult]]],
) -> list[list[RetrievalResult]]:
    """Run searches with sibling cancellation and deterministic result ordering."""

    results: list[list[RetrievalResult] | None] = [None] * len(requests)

    async def run_one(
        index: int,
        query: str,
        embedding: list[float] | None,
    ) -> None:
        results[index] = await operation(query, embedding)

    try:
        async with asyncio.TaskGroup() as group:
            for index, (query, embedding) in enumerate(requests):
                group.create_task(run_one(index, query, embedding))
    except BaseExceptionGroup as exc:
        raise first_task_group_exception(exc) from None
    return [result if result is not None else [] for result in results]


def _rrf_score(ranks: list[int]) -> float:
    """Reciprocal Rank Fusion score from multiple ranked lists."""
    return sum(1.0 / (_RRF_K + r) for r in ranks)


async def hybrid_search(
    session: AsyncSession,
    *,
    query: str,
    query_embedding: list[float] | None,
    collection_id: str,
    top_k: int = 10,
    ef_search: int = 200,
    retrieval_mode: str = "hybrid",
    embedding_dim: int | None = None,
    metadata_filter: dict[str, Any] | None = None,
    strict: bool = False,
    evidence: list[dict[str, Any]] | None = None,
) -> list[RetrievalResult]:
    """
    Four-leg retrieval with RRF fusion.

    Args:
        query: Natural language query for FTS/trigram
        query_embedding: Pre-computed embedding vector (None → skip vector leg)
        collection_id: Knowledge collection ID
        top_k: Number of results to return
        ef_search: pgvector HNSW ef_search parameter
        retrieval_mode: "hybrid" | "lexical" | "vector"
        embedding_dim: Embedding dimension for the dynamic table

    Returns:
        Fused and sorted list of RetrievalResult
    """
    # Determine table name — query collection's embedding_dim when not provided (C6 fix)
    if embedding_dim is None:
        try:
            row = (
                await session.execute(
                    text("SELECT embedding_dim FROM knowledge_collections WHERE id = :cid LIMIT 1"),
                    {"cid": collection_id},
                )
            ).fetchone()
            embedding_dim = int(row[0]) if row and row[0] else 1536
        except Exception as exc:
            if strict:
                raise RetrievalLegExecutionError("collection_metadata") from exc
            embedding_dim = 1536
    if embedding_dim not in _SUPPORTED_EMBEDDING_DIMENSIONS:
        if strict:
            raise RetrievalLegExecutionError("collection_metadata")
        logger.warning("unsupported_embedding_dimension", embedding_dim=embedding_dim)
        return []
    table = f"knowledge_chunks_{embedding_dim}"
    # Dimensions above pgvector's 2000-dim cap for a plain `vector` HNSW index are
    # stored/queried as halfvec, which is what the idx_knowledge_chunks_<dim>_vector_halfvec
    # HNSW index is built on. Both 2048 (NVIDIA nemotron) and 3072 need the halfvec
    # cast so the query actually USES that index instead of a full sequential scan.
    _use_halfvec = embedding_dim in (2048, 3072)
    vector_expression = f"embedding::halfvec({embedding_dim})" if _use_halfvec else "embedding"
    query_vector_expression = (
        f"CAST(:emb AS halfvec({embedding_dim}))" if _use_halfvec else "CAST(:emb AS vector)"
    )
    metadata_clause = " AND metadata @> CAST(:metadata_filter AS jsonb)" if metadata_filter else ""
    live_chunk_clause = " AND (expires_at IS NULL OR expires_at > now())"
    metadata_params = {"metadata_filter": json.dumps(metadata_filter)} if metadata_filter else {}

    # Per-leg result dicts: chunk_id → (content, metadata, rank)
    vector_ranks: dict[str, tuple[str, dict[str, Any], int, float]] = {}
    fts_ranks: dict[str, tuple[str, dict[str, Any], int, float]] = {}
    trgm_ranks: dict[str, tuple[str, dict[str, Any], int, float]] = {}
    bm25_ranks: dict[str, tuple[str, dict[str, Any], int, float]] = {}

    if strict and retrieval_mode in ("hybrid", "vector") and not query_embedding:
        raise RetrievalLegExecutionError("vector")

    # Leg 1: pgvector ANN
    vector_started = time.perf_counter()
    if query_embedding and retrieval_mode in ("hybrid", "vector"):
        try:
            await session.execute(
                text("SELECT set_config('hnsw.ef_search', :ef, true)"),
                {"ef": str(ef_search)},
            )
            vec_sql = text(f"""
                SELECT id, content, metadata,
                       1 - ({vector_expression} <=> {query_vector_expression}) AS score
                FROM {table}
                WHERE collection_id = :cid
                  {metadata_clause}
                  {live_chunk_clause}
                 ORDER BY {vector_expression} <=> {query_vector_expression}, id ASC
                LIMIT :limit
            """)
            rows = await session.execute(
                vec_sql,
                {
                    "emb": str(query_embedding),
                    "cid": collection_id,
                    "limit": top_k * 3,
                    **metadata_params,
                },
            )
            for i, row in enumerate(rows.fetchall()):
                vector_ranks[row[0]] = (row[1], row[2] or {}, i + 1, float(row[3]))
        except Exception as e:
            if strict:
                raise RetrievalLegExecutionError("vector") from e
            logger.debug("vector_leg_failed", error=str(e)[:80])

    if retrieval_mode in ("hybrid", "vector"):
        _record_leg_evidence(
            evidence,
            "vector",
            vector_ranks,
            detail={"latency_ms": (time.perf_counter() - vector_started) * 1000},
        )

    # Leg 2: PostgreSQL Full-Text Search
    fts_started = time.perf_counter()
    if retrieval_mode in ("hybrid", "lexical"):
        try:
            fts_sql = text(f"""
                SELECT id, content, metadata,
                       ts_rank_cd(to_tsvector('english', content),
                                  plainto_tsquery('english', :q)) AS score
                FROM {table}
                WHERE collection_id = :cid
                  {metadata_clause}
                  {live_chunk_clause}
                  AND to_tsvector('english', content) @@ plainto_tsquery('english', :q)
                 ORDER BY score DESC, id ASC
                LIMIT :limit
            """)
            rows = await session.execute(
                fts_sql,
                {
                    "q": query[:500],
                    "cid": collection_id,
                    "limit": top_k * 3,
                    **metadata_params,
                },
            )
            for i, row in enumerate(rows.fetchall()):
                fts_ranks[row[0]] = (row[1], row[2] or {}, i + 1, float(row[3]))
        except Exception as e:
            if strict:
                raise RetrievalLegExecutionError("fts") from e
            logger.debug("fts_leg_failed", error=str(e)[:80])
    fts_latency_ms = (time.perf_counter() - fts_started) * 1000

    # Leg 3: pg_trgm fuzzy
    trgm_started = time.perf_counter()
    if retrieval_mode in ("hybrid", "lexical"):
        try:
            trgm_sql = text(f"""
                SELECT id, content, metadata,
                       similarity(content, :q) AS score
                FROM {table}
                WHERE collection_id = :cid
                  {metadata_clause}
                  {live_chunk_clause}
                  AND content % :q
                 ORDER BY score DESC, id ASC
                LIMIT :limit
            """)
            rows = await session.execute(
                trgm_sql,
                {
                    "q": query[:500],
                    "cid": collection_id,
                    "limit": top_k * 2,
                    **metadata_params,
                },
            )
            for i, row in enumerate(rows.fetchall()):
                trgm_ranks[row[0]] = (row[1], row[2] or {}, i + 1, float(row[3]))
        except Exception as e:
            if strict:
                raise RetrievalLegExecutionError("trgm") from e
            logger.debug("trgm_leg_failed", error=str(e)[:80])
    trgm_latency_ms = (time.perf_counter() - trgm_started) * 1000

    if retrieval_mode in ("hybrid", "lexical"):
        _record_leg_evidence(
            evidence,
            "fts",
            fts_ranks,
            detail={"latency_ms": fts_latency_ms},
        )
        _record_leg_evidence(
            evidence,
            "trigram",
            trgm_ranks,
            detail={"latency_ms": trgm_latency_ms},
        )

    # Leg 4: bounded application-side Okapi BM25 over the persisted corpus.
    if retrieval_mode == "hybrid":
        bm25_started = time.perf_counter()
        try:
            bm25_hits, bm25_trace = await _bm25_search_persisted(
                session,
                table=table,
                query=query,
                collection_id=collection_id,
                result_limit=top_k * 3,
                metadata_clause=metadata_clause,
                live_chunk_clause=live_chunk_clause,
                metadata_params=metadata_params,
            )
            for rank, hit in enumerate(bm25_hits, start=1):
                bm25_ranks[hit.chunk_id] = (
                    hit.content,
                    hit.source_metadata,
                    rank,
                    hit.score,
                )
        except Exception as exc:
            if strict:
                raise RetrievalLegExecutionError("bm25") from exc
            logger.debug("bm25_leg_failed", error=str(exc)[:80])
            bm25_trace = {
                "corpus_size": 0,
                "pages_scanned": 0,
                "scoring_mode": "application_okapi_bm25_two_pass_keyset",
            }
        _record_leg_evidence(
            evidence,
            "bm25",
            bm25_ranks,
            detail={
                **bm25_trace,
                "latency_ms": (time.perf_counter() - bm25_started) * 1000,
            },
        )

    # Collect all unique chunk IDs
    all_ids = set(vector_ranks) | set(fts_ranks) | set(trgm_ranks) | set(bm25_ranks)
    if not all_ids:
        return []

    # Compute RRF scores
    fused: list[tuple[str, float, str, dict[str, Any], list[str], dict[str, float]]] = []
    for chunk_id in all_ids:
        ranks: list[int] = []
        legs: list[str] = []
        content: str = ""
        metadata: dict[str, Any] = {}
        component_scores: dict[str, float] = {}

        if chunk_id in vector_ranks:
            content, metadata, r, component_score = vector_ranks[chunk_id]
            ranks.append(r)
            legs.append("vector")
            component_scores["vector"] = component_score
        if chunk_id in fts_ranks:
            c, m, r, component_score = fts_ranks[chunk_id]
            if not content:
                content, metadata = c, m
            ranks.append(r)
            legs.append("fts")
            component_scores["fts"] = component_score
        if chunk_id in trgm_ranks:
            c, m, r, component_score = trgm_ranks[chunk_id]
            if not content:
                content, metadata = c, m
            ranks.append(r)
            legs.append("trigram")
            component_scores["trigram"] = component_score
        if chunk_id in bm25_ranks:
            c, m, r, component_score = bm25_ranks[chunk_id]
            if not content:
                content, metadata = c, m
            ranks.append(r)
            legs.append("bm25")
            component_scores["bm25"] = component_score

        score = _rrf_score(ranks)
        fused.append((chunk_id, score, content, metadata, legs, component_scores))

    # Sort by RRF score descending
    fused.sort(key=lambda item: (-item[1], item[0]))

    results = [
        RetrievalResult(
            chunk_id=cid,
            content=content,
            score=score,
            source_metadata=meta,
            retrieval_legs=legs,
            component_scores=component_scores,
            rrf_score=score,
        )
        for cid, score, content, meta, legs, component_scores in fused[:top_k]
    ]

    logger.debug(
        "rrf_retrieval_complete",
        collection_id=collection_id,
        top_k=top_k,
        mode=retrieval_mode,
        candidates=len(all_ids),
        vector_hits=len(vector_ranks),
        fts_hits=len(fts_ranks),
        trgm_hits=len(trgm_ranks),
        bm25_hits=len(bm25_ranks),
    )

    return results


def _record_leg_evidence(
    evidence: list[dict[str, Any]] | None,
    component: str,
    ranks: dict[str, tuple[str, dict[str, Any], int, float]],
    *,
    detail: dict[str, Any] | None = None,
) -> None:
    if evidence is None:
        return
    evidence.append(
        {
            "component": component,
            "result_count": len(ranks),
            "component_scores": {chunk_id: item[3] for chunk_id, item in sorted(ranks.items())},
            **(detail or {}),
        }
    )


async def _bm25_search_persisted(
    session: AsyncSession,
    *,
    table: str,
    query: str,
    collection_id: str,
    result_limit: int,
    metadata_clause: str,
    live_chunk_clause: str,
    metadata_params: dict[str, Any],
) -> tuple[list[BM25Hit], dict[str, Any]]:
    scorer = BM25CorpusScorer(query)
    pages_scanned = 0
    max_heap_size = 0

    async def read_pages(*, include_metadata: bool) -> AsyncIterator[list[Any]]:
        nonlocal pages_scanned
        after_id: Any = None
        columns = "id, content, metadata" if include_metadata else "id, content"
        while True:
            after_clause = " AND id > :after_id" if after_id is not None else ""
            page_sql = text(f"""
                SELECT {columns}
                FROM {table}
                WHERE collection_id = :cid
                  {metadata_clause}
                  {live_chunk_clause}
                  {after_clause}
                ORDER BY id ASC
                LIMIT :page_size
            """)
            params = {
                "cid": collection_id,
                "page_size": _BM25_PAGE_SIZE,
                **metadata_params,
            }
            if after_id is not None:
                params["after_id"] = after_id
            rows = (await session.execute(page_sql, params)).fetchall()
            if not rows:
                break
            pages_scanned += 1
            yield list(rows)
            after_id = rows[-1][0]
            if len(rows) < _BM25_PAGE_SIZE:
                break

    async for page in read_pages(include_metadata=False):
        for row in page:
            scorer.observe(str(row[1] or ""))

    heap: list[_BM25HeapEntry] = []
    if result_limit > 0:
        async for page in read_pages(include_metadata=True):
            for row in page:
                score = scorer.score(str(row[1] or ""))
                if score <= 0:
                    continue
                candidate = _BM25HeapEntry(
                    score=score,
                    chunk_id=str(row[0]),
                    content=str(row[1] or ""),
                    source_metadata=dict(row[2] or {}),
                )
                if len(heap) < result_limit:
                    heapq.heappush(heap, candidate)
                elif heap[0] < candidate:
                    heapq.heapreplace(heap, candidate)
                max_heap_size = max(max_heap_size, len(heap))

    ranked = sorted(heap, key=lambda item: (-item.score, item.chunk_id))
    return (
        [
            BM25Hit(
                chunk_id=item.chunk_id,
                content=item.content,
                score=item.score,
                source_metadata=item.source_metadata,
            )
            for item in ranked
        ],
        {
            "corpus_size": scorer.document_count,
            "pages_scanned": pages_scanned,
            "page_size": _BM25_PAGE_SIZE,
            "tracked_term_count": scorer.tracked_term_count,
            "heap_capacity": max(result_limit, 0),
            "max_heap_size": max_heap_size,
            "scoring_mode": "application_okapi_bm25_two_pass_keyset",
        },
    )


class RetrievalPlanner:
    """
    Selects retrieval strategy per goal step.

    Strategies:
    - "direct": standard top-k (most queries)
    - "multi_hop": query decomposition for comparative/analytical questions
    - "hyde": HyDE for short/abstract queries
    - "lexical": keyword-only for structured IDs (e.g. "Find ticket JIRA-123")
    """

    _LEXICAL_PATTERNS: ClassVar[list[str]] = [
        r"[A-Z][A-Z0-9]+-\d+",  # JIRA/GitHub IDs
        r"\bticket\b|\bissue\b|\bpr\b",  # ticket/issue references
    ]

    _MULTI_HOP_KEYWORDS: ClassVar[tuple[str, ...]] = (
        "compare",
        "analyze",
        "contrast",
        "relationship",
        "difference between",
        "across all",
        "summarize all",
    )

    _HYDE_TRIGGERS: ClassVar[tuple[str, ...]] = (
        "what is",
        "explain",
        "how does",
        "describe",
        "tell me about",
        "overview of",
    )

    def select_strategy(self, query: str) -> str:
        """Select retrieval strategy based on query heuristics."""
        q_lower = query.lower()

        # Lexical: specific IDs
        if any(re.search(p, query) for p in self._LEXICAL_PATTERNS):
            return "lexical"

        # Multi-hop: comparative / analytical
        if any(kw in q_lower for kw in self._MULTI_HOP_KEYWORDS):
            return "multi_hop"

        # HyDE: short/abstract queries
        if any(q_lower.startswith(kw) for kw in self._HYDE_TRIGGERS) and len(query) < 80:
            return "hyde"

        return "direct"


async def rerank_results(
    results: list[RetrievalResult],
    query: str,
    *,
    provider: Any = None,
    model: str = "",
    top_k: int | None = None,
) -> list[RetrievalResult]:
    """Cross-encoder reranking of top retrieval results.

    Uses an LLM to score each (query, passage) pair for relevance.
    Falls back to the original RRF order when provider is None.
    Scores top_k results (default: min(20, len(results))).
    """
    if not results or provider is None:
        return results[:top_k] if top_k else results

    candidates = results[: min(20, len(results))]
    if not candidates:
        return results[:top_k] if top_k else results

    try:
        import json as _json

        from app.providers.base import CompletionRequest, Message

        # Build a batch relevance scoring prompt
        passages_text = "\n".join(f"[{i}] {r.content[:300]}" for i, r in enumerate(candidates))
        prompt = (
            f"Query: {query}\n\n"
            f"Rate each passage for relevance to the query (0=irrelevant, 10=highly relevant).\n"
            f"Return ONLY a JSON array of integers, one score per passage, e.g. [8, 3, 7, ...]:\n\n"
            f"{passages_text}"
        )
        req = CompletionRequest(
            messages=[Message(role="user", content=prompt)],
            model=model,
            max_tokens=100,
        )
        resp = await provider.complete(req)
        scores = _json.loads(resp.content.strip())
        if isinstance(scores, list) and len(scores) == len(candidates):
            for i, r in enumerate(candidates):
                with suppress(TypeError, ValueError, IndexError):
                    r.score = float(scores[i]) / 10.0
            candidates.sort(key=lambda r: r.score, reverse=True)
    except Exception as exc:
        logger.debug("rerank_failed_falling_back", error=str(exc)[:80])

    final = candidates + [r for r in results if r not in candidates]
    return final[:top_k] if top_k else final


async def retrieve_hyde(
    session: AsyncSession,
    *,
    query: str,
    query_embedding: list[float] | None,
    collection_id: str,
    provider: Any = None,
    model: str = "",
    top_k: int = 10,
    embedding_dim: int | None = None,
    metadata_filter: dict[str, Any] | None = None,
    embedder: Any = None,
    strict: bool = False,
    strategy_evidence: dict[str, Any] | None = None,
) -> list[RetrievalResult]:
    """Generate, embed, and vector-search a hypothetical document."""
    if provider is None:
        if strict:
            raise RetrievalStrategyExecutionError("hyde", "LLM provider is required")
        return await hybrid_search(
            session,
            query=query,
            query_embedding=query_embedding,
            collection_id=collection_id,
            top_k=top_k,
            embedding_dim=embedding_dim,
            metadata_filter=metadata_filter,
        )
    if strict and not model.strip():
        raise RetrievalStrategyExecutionError("hyde", "LLM model is required")
    if strict and embedder is None:
        raise RetrievalStrategyExecutionError("hyde", "embedding provider is required")
    try:
        from app.providers.base import CompletionRequest, Message

        req = CompletionRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "Write a 2-3 sentence hypothetical document that would perfectly "
                        "answer the following question. Write only the document text."
                    ),
                ),
                Message(role="user", content=f"Question: {query}"),
            ],
            model=model,
            max_tokens=200,
        )
        try:
            resp = await provider.complete(req)
        except Exception as exc:
            if isinstance(exc, RetrievalStrategyExecutionError):
                raise
            raise RetrievalStrategyExecutionError("hyde", "generation failed") from exc
        hyp_doc = resp.content.strip()
        if not hyp_doc:
            raise RetrievalStrategyExecutionError("hyde", "generated document is empty")
        if embedder is None:
            generated_embedding = query_embedding
        else:
            from app.providers.base import EmbedRequest

            try:
                embedding_response = await embedder.embed(
                    EmbedRequest(texts=[hyp_doc], input_type="document")
                )
                generated_embedding = (
                    embedding_response.embeddings[0] if embedding_response.embeddings else None
                )
                if not generated_embedding:
                    raise ValueError("embedding response was empty")
            except Exception as exc:
                if isinstance(exc, RetrievalStrategyExecutionError):
                    raise
                raise RetrievalStrategyExecutionError("hyde", "embedding failed") from exc
        if strategy_evidence is not None:
            strategy_evidence.update(
                {
                    "generated_text_sha256": hashlib.sha256(hyp_doc.encode("utf-8")).hexdigest(),
                    "model": model,
                    "generation": "hypothetical_document",
                }
            )
        return await hybrid_search(
            session,
            query=hyp_doc,
            query_embedding=generated_embedding,
            collection_id=collection_id,
            top_k=top_k,
            embedding_dim=embedding_dim,
            metadata_filter=metadata_filter,
            retrieval_mode="vector",
            strict=strict,
        )
    except Exception as exc:
        if strict:
            if isinstance(exc, RetrievalStrategyExecutionError):
                raise
            raise RetrievalStrategyExecutionError("hyde", "algorithm failed") from exc
        logger.warning("hyde_failed_falling_back", error=str(exc)[:80])
        return await hybrid_search(
            session,
            query=query,
            query_embedding=query_embedding,
            collection_id=collection_id,
            top_k=top_k,
            embedding_dim=embedding_dim,
            metadata_filter=metadata_filter,
        )


async def retrieve_multi_hop(
    session: AsyncSession | None,
    *,
    query: str,
    query_embedding: list[float] | None,
    collection_id: str,
    provider: Any = None,
    model: str = "",
    top_k: int = 10,
    embedding_dim: int | None = None,
    metadata_filter: dict[str, Any] | None = None,
    embedder: Any = None,
    strict: bool = False,
    max_hops: int = 3,
    search_operation: Callable[[str, list[float] | None], Awaitable[list[RetrievalResult]]]
    | None = None,
    strategy_evidence: list[dict[str, Any]] | None = None,
) -> list[RetrievalResult]:
    """Multi-hop: decompose query, search each sub-query, merge results."""
    if not 1 <= max_hops <= _MAX_HOPS:
        raise RetrievalStrategyExecutionError(
            "multi_hop", f"max_hops must be between 1 and {_MAX_HOPS}"
        )
    if provider is None:
        if strict:
            raise RetrievalStrategyExecutionError("multi_hop", "LLM provider is required")
        if session is None:
            return []
        return await hybrid_search(
            session,
            query=query,
            query_embedding=query_embedding,
            collection_id=collection_id,
            top_k=top_k,
            embedding_dim=embedding_dim,
            metadata_filter=metadata_filter,
        )
    if strict and not model.strip():
        raise RetrievalStrategyExecutionError("multi_hop", "LLM model is required")
    if strict and embedder is None:
        raise RetrievalStrategyExecutionError("multi_hop", "embedding provider is required")
    try:
        import json as _json

        from app.providers.base import CompletionRequest, Message

        req = CompletionRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "Decompose this query into 2-3 specific sub-queries. "
                        'Return ONLY a JSON array: ["sub-query 1", "sub-query 2"]'
                    ),
                ),
                Message(role="user", content=f"Query: {query}"),
            ],
            model=model,
            max_tokens=150,
        )
        resp = await provider.complete(req)
        sub_queries: list[str] = _json.loads(resp.content.strip())
        if not isinstance(sub_queries, list):
            raise ValueError("decomposition was not a list")
        sub_queries = list(
            dict.fromkeys(str(item).strip() for item in sub_queries if str(item).strip())
        )[:max_hops]
        if not sub_queries or all(item == query for item in sub_queries):
            raise ValueError("decomposition did not produce an independent hop")
    except Exception as exc:
        if isinstance(exc, RetrievalStrategyExecutionError):
            raise
        if strict:
            raise RetrievalStrategyExecutionError("multi_hop", "decomposition failed") from exc
        sub_queries = [query]

    hop_embeddings: list[list[float] | None] = []
    for sub_query in sub_queries:
        if embedder is None:
            hop_embeddings.append(query_embedding)
            continue
        try:
            from app.providers.base import EmbedRequest

            response = await embedder.embed(EmbedRequest(texts=[sub_query], input_type="query"))
            embedding = response.embeddings[0] if response.embeddings else None
            if not embedding:
                raise ValueError("embedding response was empty")
            hop_embeddings.append(embedding)
        except Exception as exc:
            if isinstance(exc, RetrievalStrategyExecutionError):
                raise
            if strict:
                raise RetrievalStrategyExecutionError("multi_hop", "hop embedding failed") from exc
            hop_embeddings.append(query_embedding)

    per_hop = max(top_k // max(len(sub_queries), 1), 3)

    async def search(sub_query: str, embedding: list[float] | None) -> list[RetrievalResult]:
        if search_operation is not None:
            return await search_operation(sub_query, embedding)
        if session is None:
            raise ValueError("session is required when search_operation is not supplied")
        return await hybrid_search(
            session,
            query=sub_query,
            query_embedding=embedding,
            collection_id=collection_id,
            top_k=per_hop,
            embedding_dim=embedding_dim,
            metadata_filter=metadata_filter,
            strict=strict,
        )

    if search_operation is not None:
        try:
            per_hop_results = await _run_parallel_searches(
                list(zip(sub_queries, hop_embeddings, strict=True)),
                search,
            )
        except Exception as exc:
            if strict:
                raise RetrievalStrategyExecutionError("multi_hop", "retrieval hop failed") from exc
            per_hop_results = []
    else:
        per_hop_results = []
        for sub_query, embedding in zip(sub_queries, hop_embeddings, strict=True):
            try:
                per_hop_results.append(await search(sub_query, embedding))
            except Exception as exc:
                if isinstance(exc, RetrievalStrategyExecutionError):
                    raise
                if strict:
                    raise RetrievalStrategyExecutionError(
                        "multi_hop", "retrieval hop failed"
                    ) from exc

    if strategy_evidence is not None:
        strategy_evidence.extend(
            {
                "hop": index,
                "query": sub_query,
                "result_count": len(hop_results),
                "component_scores": {result.chunk_id: result.score for result in hop_results},
            }
            for index, (sub_query, hop_results) in enumerate(
                zip(sub_queries, per_hop_results, strict=True), start=1
            )
        )

    merged: dict[str, RetrievalResult] = {}
    for sub_query, hop_results in zip(sub_queries, per_hop_results, strict=True):
        for result in hop_results:
            existing = merged.get(result.chunk_id)
            if existing is None:
                result.source_metadata = {
                    **result.source_metadata,
                    "hop_queries": [sub_query],
                }
                merged[result.chunk_id] = result
            else:
                hop_queries = existing.source_metadata.setdefault("hop_queries", [])
                if sub_query not in hop_queries:
                    hop_queries.append(sub_query)
                existing.score = max(existing.score, result.score)
                existing.retrieval_legs = list(
                    dict.fromkeys([*existing.retrieval_legs, *result.retrieval_legs])
                )
    all_results = sorted(merged.values(), key=lambda result: (-result.score, result.chunk_id))
    return all_results[:top_k]


async def retrieve_fusion(
    session: AsyncSession | None,
    *,
    query: str,
    query_embedding: list[float] | None,
    collection_id: str,
    top_k: int = 10,
    max_variants: int = 3,
    ef_search: int = 200,
    embedding_dim: int | None = None,
    embedder: Any = None,
    provider: Any = None,
    model: str = "",
    metadata_filter: dict[str, Any] | None = None,
    strict: bool = False,
    search_operation: Callable[
        [str, list[float] | None],
        Awaitable[list[RetrievalResult]],
    ]
    | None = None,
    strategy_evidence: list[dict[str, Any]] | None = None,
) -> list[RetrievalResult]:
    """Fusion RAG: expand query into N variants, retrieve, and RRF-merge.

    A gateway supplies ``search_operation`` to run variants concurrently with a
    fresh tenant-scoped session per call. Legacy direct callers are serialized
    because an ``AsyncSession`` cannot safely be shared by concurrent tasks.
    """
    from app.rag.agentic.query_expander import QueryExpander

    if not 2 <= max_variants <= _MAX_VARIANTS:
        raise RetrievalStrategyExecutionError(
            "fusion", f"max_variants must be between 2 and {_MAX_VARIANTS}"
        )
    if strict and provider is not None and not model.strip():
        raise RetrievalStrategyExecutionError("fusion", "LLM model is required")
    if strict and embedder is None and query_embedding is None:
        raise RetrievalStrategyExecutionError("fusion", "embedding provider is required")

    expander = QueryExpander()
    try:
        if provider is not None and hasattr(expander, "expand_for_fusion_async"):
            variants = await expander.expand_for_fusion_async(
                query,
                max_variants=max_variants,
                provider=provider,
                model=model,
                strict=strict,
            )
        else:
            variants = expander.expand_for_fusion(query, max_variants=max_variants)
    except Exception as exc:
        if isinstance(exc, RetrievalStrategyExecutionError):
            raise
        if strict:
            raise RetrievalStrategyExecutionError(
                "fusion", "provider query expansion failed"
            ) from exc
        variants = expander.expand_for_fusion(query, max_variants=max_variants)

    if strict and len(variants) < 2:
        raise RetrievalStrategyExecutionError("fusion", "query expansion produced one variant")

    # Embed every variant, including the original query.
    variant_embeddings: list[list[float] | None] = []
    for v in variants:
        if embedder is not None:
            try:
                from app.providers.base import EmbedRequest

                resp = await embedder.embed(EmbedRequest(texts=[v], input_type="query"))
                embedding = resp.embeddings[0] if resp.embeddings else None
                if not embedding:
                    raise ValueError("embedding response was empty")
                variant_embeddings.append(embedding)
            except Exception as exc:
                if isinstance(exc, RetrievalStrategyExecutionError):
                    raise
                if strict:
                    raise RetrievalStrategyExecutionError(
                        "fusion", "variant embedding failed"
                    ) from exc
                variant_embeddings.append(query_embedding)
        else:
            variant_embeddings.append(query_embedding)

    async def _retrieve_legacy(q: str, emb: list[float] | None) -> list[RetrievalResult]:
        if session is None:
            raise ValueError("session is required when search_operation is not supplied")
        try:
            return await hybrid_search(
                session=session,
                query=q,
                query_embedding=emb,
                collection_id=collection_id,
                top_k=top_k,
                ef_search=ef_search,
                embedding_dim=embedding_dim,
                metadata_filter=metadata_filter,
                strict=strict,
            )
        except Exception as exc:
            if strict:
                raise
            logger.warning("fusion_rag_variant_failed", query=q[:60], error=str(exc)[:80])
            return []

    if search_operation is not None:
        per_variant_results = await _run_parallel_searches(
            list(zip(variants, variant_embeddings, strict=True)),
            search_operation,
        )
    else:
        per_variant_results = []
        for q, emb in zip(variants, variant_embeddings, strict=True):
            per_variant_results.append(await _retrieve_legacy(q, emb))

    merged: dict[str, RetrievalResult] = {}
    rrf_scores: dict[str, float] = {}
    for variant, variant_results in zip(variants, per_variant_results, strict=True):
        for rank, result in enumerate(variant_results, start=1):
            rrf_scores[result.chunk_id] = rrf_scores.get(result.chunk_id, 0.0) + 1.0 / (
                _RRF_K + rank
            )
            existing = merged.get(result.chunk_id)
            if existing is None:
                result.source_metadata = {
                    **result.source_metadata,
                    "fusion_queries": [variant],
                }
                merged[result.chunk_id] = result
            else:
                provenance = existing.source_metadata.setdefault("fusion_queries", [])
                if variant not in provenance:
                    provenance.append(variant)
                existing.retrieval_legs = list(
                    dict.fromkeys([*existing.retrieval_legs, *result.retrieval_legs])
                )

    fused_results = list(merged.values())
    if strategy_evidence is not None:
        strategy_evidence.extend(
            {
                "variant": index,
                "query": variant,
                "result_count": len(variant_results),
                "component_scores": {result.chunk_id: result.score for result in variant_results},
            }
            for index, (variant, variant_results) in enumerate(
                zip(variants, per_variant_results, strict=True), start=1
            )
        )
    for result in fused_results:
        result.score = rrf_scores[result.chunk_id]
        result.rrf_score = result.score
    fused_results.sort(key=lambda result: (-result.score, result.chunk_id))
    return fused_results[:top_k]


async def recall_long_term_memory(
    long_term_memory: Any,
    *,
    query: str,
    tenant_ctx: Any,
    top_k: int,
    embedder: Any = None,
    provider: Any = None,
    strict: bool = False,
) -> list[RetrievalResult]:
    """Recall cross-session long-term-memory entries as retrieval evidence.

    Shared by the legacy ``strategy="memory"`` engine dispatch (``strict=False``,
    degrades to an empty list on recall failure) and the canonical
    ``RAGStrategy.MEMORY_AUGMENTED`` gateway adapter (``strict=True``, surfaces a
    recall failure as ``RetrievalStrategyExecutionError`` instead of swallowing it).
    """
    if long_term_memory is None or tenant_ctx is None:
        return []
    try:
        ltm_results = await long_term_memory.recall_async(
            query=query,
            tenant_ctx=tenant_ctx,
            top_k=top_k,
            db=(
                getattr(long_term_memory, "_db_factory", None)
                or getattr(long_term_memory, "_db", None)
            ),
            embedder=embedder or provider,
        )
    except Exception as exc:
        if strict:
            raise RetrievalStrategyExecutionError(
                "memory_augmented", "long-term memory recall failed"
            ) from exc
        logger.warning("memory_strategy_recall_failed", error=str(exc)[:80])
        return []
    if not ltm_results:
        return []
    return [
        RetrievalResult(
            chunk_id=f"ltm_{getattr(m, 'memory_id', str(i))}",
            content=getattr(m, "content", str(m)),
            score=float(getattr(m, "confidence", 0.7)),
            source_metadata={
                "memory_type": getattr(m, "memory_type", "ltm"),
                "source": "long_term_memory",
                "source_goal_id": getattr(m, "source_goal_id", ""),
            },
            retrieval_legs=["long_term_memory"],
        )
        for i, m in enumerate(ltm_results)
    ]


async def retrieve(
    session: AsyncSession,
    *,
    query: str,
    query_embedding: list[float] | None,
    collection_id: str,
    top_k: int = 10,
    strategy: str | None = None,
    provider: Any = None,
    model: str = "",
    embedding_dim: int | None = None,
    retrieval_mode: str = "hybrid",
    metadata_filter: dict[str, Any] | None = None,
    long_term_memory: Any = None,
    tenant_ctx: Any = None,
    embedder: Any = None,
    strict: bool = False,
) -> list[RetrievalResult]:
    """Dispatch retrieval, optionally failing closed for canonical gateway calls.

    ``strict=False`` preserves historical product entry points until they are
    rerouted through the gateway. Canonical adapters use only ``strict=True``.
    """
    if strategy is None:
        strategy = RetrievalPlanner().select_strategy(query)
    try:
        if strategy == "hyde":
            return await retrieve_hyde(
                session,
                query=query,
                query_embedding=query_embedding,
                collection_id=collection_id,
                provider=provider,
                model=model,
                top_k=top_k,
                embedding_dim=embedding_dim,
                metadata_filter=metadata_filter,
                embedder=embedder,
                strict=strict,
            )
        if strategy == "multi_hop":
            return await retrieve_multi_hop(
                session,
                query=query,
                query_embedding=query_embedding,
                collection_id=collection_id,
                provider=provider,
                model=model,
                top_k=top_k,
                embedding_dim=embedding_dim,
                metadata_filter=metadata_filter,
                embedder=embedder,
                strict=strict,
            )
        if strategy == "fusion":
            return await retrieve_fusion(
                session,
                query=query,
                query_embedding=query_embedding,
                collection_id=collection_id,
                top_k=top_k,
                embedding_dim=embedding_dim,
                embedder=embedder or provider,
                provider=provider,
                model=model,
                metadata_filter=metadata_filter,
                strict=strict,
            )
        if strategy == "corrective":
            # CRAG: run hybrid search, check confidence, web fallback via RetrieverTool
            try:
                base_results = await hybrid_search(
                    session,
                    query=query,
                    query_embedding=query_embedding,
                    collection_id=collection_id,
                    top_k=top_k,
                    retrieval_mode="hybrid",
                    embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                    strict=strict,
                )
                # CRAG correction: if low confidence, the caller (RetrieverTool.retrieve_corrective)
                # handles web fallback. At engine level, return base results + confidence metadata.
                avg_conf = (
                    sum(r.score for r in base_results) / len(base_results) if base_results else 0.0
                )
                if base_results and avg_conf < 0.5:
                    # Tag results as low-confidence for CRAG layer to act on
                    for r in base_results:
                        r.source_metadata["corrective_flagged"] = True
                        r.source_metadata["avg_confidence"] = avg_conf
                return base_results
            except Exception as exc:
                if strict:
                    raise RetrievalStrategyExecutionError("corrective", "algorithm failed") from exc
                logger.warning("corrective_rag_failed", error=str(exc)[:80])
                return await hybrid_search(
                    session,
                    query=query,
                    query_embedding=query_embedding,
                    collection_id=collection_id,
                    top_k=top_k,
                    embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                )

        if strategy in ("flare", "self_rag"):
            # FLARE and Self-RAG use the provider for generation
            # When no provider given, fall back to hybrid
            if provider is None:
                if strict:
                    raise RetrievalStrategyExecutionError(strategy, "LLM provider is required")
                return await hybrid_search(
                    session,
                    query=query,
                    query_embedding=query_embedding,
                    collection_id=collection_id,
                    top_k=top_k,
                    embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                )
            try:
                base_results = await hybrid_search(
                    session,
                    query=query,
                    query_embedding=query_embedding,
                    collection_id=collection_id,
                    top_k=top_k,
                    retrieval_mode="hybrid",
                    embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                    strict=strict,
                )
                if not base_results:
                    return base_results
                # Use context from base retrieval as FLARE/Self-RAG context
                context_text = "\n".join(r.content[:300] for r in base_results[:5])
                if strategy == "flare":
                    from app.rag.agentic.patterns.flare import FLAREPattern

                    flare_pattern = FLAREPattern()

                    async def _flare_retrieve(q: str, **kw: Any) -> str:
                        extra = await hybrid_search(
                            session,
                            query=q,
                            query_embedding=query_embedding,
                            collection_id=collection_id,
                            top_k=3,
                            embedding_dim=embedding_dim,
                            metadata_filter=metadata_filter,
                            strict=strict,
                        )
                        return "\n".join(r.content[:300] for r in extra)

                    refined = await flare_pattern.execute(
                        query=query,
                        provider=provider,
                        retrieve_fn=_flare_retrieve,
                        model=model,
                        strict=strict,
                    )
                elif strategy == "self_rag":
                    from app.rag.agentic.patterns.self_rag import SelfRAGPattern

                    self_rag_pattern = SelfRAGPattern()

                    async def _self_rag_retrieve(q: str, **kw: Any) -> str:
                        return context_text

                    refined = await self_rag_pattern.execute(
                        query=query,
                        provider=provider,
                        retrieve_fn=_self_rag_retrieve,
                        model=model,
                        strict=strict,
                    )
                else:
                    refined = ""
                # Return base results enriched with refined answer in first result
                if refined and base_results:
                    base_results[0] = RetrievalResult(
                        chunk_id=base_results[0].chunk_id,
                        content=refined[:2000] or base_results[0].content,
                        score=base_results[0].score,
                        source_metadata={**base_results[0].source_metadata, "strategy": strategy},
                        retrieval_legs=[*base_results[0].retrieval_legs, strategy],
                    )
                return base_results
            except Exception as exc:
                if strict:
                    raise RetrievalStrategyExecutionError(strategy, "algorithm failed") from exc
                logger.warning(f"{strategy}_rag_failed", error=str(exc)[:80])
                return await hybrid_search(
                    session,
                    query=query,
                    query_embedding=query_embedding,
                    collection_id=collection_id,
                    top_k=top_k,
                    embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                )

        if strategy == "speculative":
            try:
                if strict and provider is None:
                    raise RetrievalStrategyExecutionError("speculative", "LLM provider is required")
                base_results = await hybrid_search(
                    session,
                    query=query,
                    query_embedding=query_embedding,
                    collection_id=collection_id,
                    top_k=top_k,
                    embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                    strict=strict,
                )
                if not base_results or provider is None:
                    return base_results

                async def _spec_retrieve(q: str, **kw: Any) -> str:
                    r = await hybrid_search(
                        session,
                        query=q,
                        query_embedding=query_embedding,
                        collection_id=collection_id,
                        top_k=3,
                        embedding_dim=embedding_dim,
                        metadata_filter=metadata_filter,
                        strict=strict,
                    )
                    return "\n".join(x.content[:300] for x in r)

                from app.rag.agentic.patterns.speculative import SpeculativeRAGPattern

                speculative_pattern = SpeculativeRAGPattern(n_candidates=2)
                best = await speculative_pattern.execute(
                    query=query,
                    provider=provider,
                    retrieve_fn=_spec_retrieve,
                    model=model,
                    strict=strict,
                )
                if best and base_results:
                    base_results[0] = RetrievalResult(
                        chunk_id=base_results[0].chunk_id,
                        content=best[:2000] or base_results[0].content,
                        score=0.9,
                        source_metadata={
                            **base_results[0].source_metadata,
                            "strategy": "speculative",
                        },
                        retrieval_legs=["speculative"],
                    )
                return base_results
            except Exception as exc:
                if strict:
                    if isinstance(exc, RetrievalStrategyExecutionError):
                        raise
                    raise RetrievalStrategyExecutionError(
                        "speculative", "algorithm failed"
                    ) from exc
                logger.warning("speculative_rag_failed", error=str(exc)[:80])
                return await hybrid_search(
                    session,
                    query=query,
                    query_embedding=query_embedding,
                    collection_id=collection_id,
                    top_k=top_k,
                    embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                )

        if strategy == "raptor":
            try:
                results = await hybrid_search(
                    session,
                    query=query,
                    query_embedding=query_embedding,
                    collection_id=collection_id,
                    top_k=top_k,
                    embedding_dim=embedding_dim,
                    metadata_filter={
                        **(metadata_filter or {}),
                        "rag_strategy": "raptor",
                    },
                    strict=True,
                )
                for result in results:
                    result.retrieval_legs = list(dict.fromkeys([*result.retrieval_legs, "raptor"]))
                    result.source_metadata["strategy"] = "raptor"
                return results
            except Exception as exc:
                raise RetrievalStrategyExecutionError(
                    "raptor", "precomputed index search failed"
                ) from exc

        if strategy == "colbert":
            try:
                base_results = await hybrid_search(
                    session,
                    query=query,
                    query_embedding=query_embedding,
                    collection_id=collection_id,
                    top_k=top_k * 2,
                    embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                    strict=strict,
                )
                if not base_results:
                    return base_results
                chunks = [
                    {
                        "content": r.content,
                        "chunk_id": r.chunk_id,
                        "score": r.score,
                        "source_metadata": r.source_metadata,
                    }
                    for r in base_results
                ]
                from app.rag.agentic.patterns.colbert import ColBERTPattern

                colbert_pattern = ColBERTPattern(alpha=0.5)
                try:
                    reranked = await colbert_pattern.rerank_async(
                        query=query,
                        chunks=chunks,
                        top_k=top_k,
                    )
                finally:
                    await colbert_pattern.aclose()
                return [
                    RetrievalResult(
                        chunk_id=c["chunk_id"],
                        content=c["content"],
                        score=c["score"],
                        source_metadata={
                            **c.get("source_metadata", {}),
                            "colbert_score": c.get("colbert_score", 0.0),
                        },
                        retrieval_legs=["colbert"],
                    )
                    for c in reranked
                ]
            except Exception as exc:
                if strict:
                    raise RetrievalStrategyExecutionError("colbert", "algorithm failed") from exc
                logger.warning("colbert_failed", error=str(exc)[:80])
                return await hybrid_search(
                    session,
                    query=query,
                    query_embedding=query_embedding,
                    collection_id=collection_id,
                    top_k=top_k,
                    embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                )

        if strategy == "agentic_chunking":
            try:
                results = await hybrid_search(
                    session,
                    query=query,
                    query_embedding=query_embedding,
                    collection_id=collection_id,
                    top_k=min(top_k * 4, 100),
                    embedding_dim=embedding_dim,
                    metadata_filter={
                        **(metadata_filter or {}),
                        "rag_strategy": "agentic_chunking",
                        "is_proposition": True,
                    },
                    strict=True,
                )
                if not results:
                    return []
                resolved_embedding_dim = embedding_dim or (
                    len(query_embedding) if query_embedding else None
                )
                if resolved_embedding_dim is None or tenant_ctx is None:
                    raise RetrievalStrategyExecutionError(
                        "agentic_chunking",
                        "tenant-scoped persistence context is required",
                    )
                parents = await load_agentic_parent_citations(
                    session,
                    child_chunk_ids=[result.chunk_id for result in results],
                    collection_id=collection_id,
                    tenant_id=tenant_ctx.tenant_id,
                    embedding_dim=resolved_embedding_dim,
                )
                return expand_agentic_parent_results(results, parents, top_k=top_k)
            except Exception as exc:
                if isinstance(exc, RetrievalStrategyExecutionError):
                    raise
                raise RetrievalStrategyExecutionError(
                    "agentic_chunking", "precomputed index search failed"
                ) from exc

        if strategy == "parametric":
            # Skip retrieval entirely — LLM uses its own knowledge
            return []

        if strategy == "memory":
            # Memory retrieval — LTM semantic recall using pgvector cosine similarity
            return await recall_long_term_memory(
                long_term_memory,
                query=query,
                tenant_ctx=tenant_ctx,
                top_k=top_k,
                embedder=embedder,
                provider=provider,
                strict=False,
            )

        if strategy == "graph":
            if strict:
                raise RetrievalStrategyExecutionError(
                    "graph", "graph capability is not wired into the legacy engine"
                )
            # Graph retrieval via KnowledgeGraphStore
            try:
                from app.state_runtime.kg_query_engine import KGQueryEngine  # noqa: F401

                # KGQueryEngine needs a kg_store — pass provider as proxy if no store available
                # For now return empty with a log — real wiring needs kg_store injection
                logger.info("graph_strategy_requested_no_store_fallback", query=query[:60])
                return await hybrid_search(
                    session,
                    query=query,
                    query_embedding=query_embedding,
                    collection_id=collection_id,
                    top_k=top_k,
                    embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                )
            except Exception as exc:
                logger.warning("graph_strategy_failed", error=str(exc)[:80])
                return await hybrid_search(
                    session,
                    query=query,
                    query_embedding=query_embedding,
                    collection_id=collection_id,
                    top_k=top_k,
                    embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                )

        mode = (
            "lexical"
            if strategy == "lexical"
            else "vector"
            if strategy == "naive"
            else retrieval_mode
        )
        base_results = await hybrid_search(
            session,
            query=query,
            query_embedding=query_embedding,
            collection_id=collection_id,
            top_k=top_k,
            retrieval_mode=mode,
            embedding_dim=embedding_dim,
            strict=strict,
            metadata_filter=metadata_filter,
        )
        # WS-10: reranking STAGE on the DEFAULT retrieval path (config-gated,
        # honest passthrough when disabled or when the reranker is unavailable).
        return await apply_default_rerank(
            base_results,
            query=query,
            query_embedding=query_embedding,
            settings=get_settings(),
            top_k=top_k,
        )
    except Exception as exc:
        if strict:
            raise
        logger.warning("retrieve_dispatch_failed", strategy=strategy, error=str(exc)[:80])
        return await hybrid_search(
            session,
            query=query,
            query_embedding=query_embedding,
            collection_id=collection_id,
            top_k=top_k,
            embedding_dim=embedding_dim,
            metadata_filter=metadata_filter,
        )
