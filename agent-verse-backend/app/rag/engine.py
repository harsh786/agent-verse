"""
World-Class RAG Retrieval Engine
=================================

Three retrieval legs fused with Reciprocal Rank Fusion (RRF):
  1. pgvector ANN — cosine similarity with HNSW index
  2. PostgreSQL FTS — tsvector + ts_rank_cd (BM25-like)
  3. pg_trgm fuzzy — trigram similarity for typo tolerance

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
import json
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, ClassVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.logging import get_logger

logger = get_logger(__name__)

# RRF constant (standard: 60)
_RRF_K = 60
_SUPPORTED_EMBEDDING_DIMENSIONS = frozenset({768, 1024, 1536, 3072})


@dataclass
class RetrievalResult:
    chunk_id: str
    content: str
    score: float
    source_metadata: dict[str, Any]
    retrieval_legs: list[str] = field(default_factory=list)  # which legs contributed


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
) -> list[RetrievalResult]:
    """
    Tri-leg retrieval with RRF fusion.

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
            row = (await session.execute(
                text("SELECT embedding_dim FROM knowledge_collections WHERE id = :cid LIMIT 1"),
                {"cid": collection_id},
            )).fetchone()
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
    vector_expression = (
        "embedding::halfvec(3072)" if embedding_dim == 3072 else "embedding"
    )
    query_vector_expression = (
        "CAST(:emb AS halfvec(3072))"
        if embedding_dim == 3072
        else "CAST(:emb AS vector)"
    )
    metadata_clause = (
        " AND metadata @> CAST(:metadata_filter AS jsonb)" if metadata_filter else ""
    )
    metadata_params = (
        {"metadata_filter": json.dumps(metadata_filter)} if metadata_filter else {}
    )

    # Per-leg result dicts: chunk_id → (content, metadata, rank)
    vector_ranks: dict[str, tuple[str, dict[str, Any], int]] = {}
    fts_ranks: dict[str, tuple[str, dict[str, Any], int]] = {}
    trgm_ranks: dict[str, tuple[str, dict[str, Any], int]] = {}

    # Leg 1: pgvector ANN
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
                ORDER BY {vector_expression} <=> {query_vector_expression}
                LIMIT :limit
            """)
            rows = await session.execute(vec_sql, {
                "emb": str(query_embedding),
                "cid": collection_id,
                "limit": top_k * 3,
                **metadata_params,
            })
            for i, row in enumerate(rows.fetchall()):
                vector_ranks[row[0]] = (row[1], row[2] or {}, i + 1)
        except Exception as e:
            if strict:
                raise RetrievalLegExecutionError("vector") from e
            logger.debug("vector_leg_failed", error=str(e)[:80])

    # Leg 2: PostgreSQL Full-Text Search (BM25-like)
    if retrieval_mode in ("hybrid", "lexical"):
        try:
            fts_sql = text(f"""
                SELECT id, content, metadata,
                       ts_rank_cd(to_tsvector('english', content),
                                  plainto_tsquery('english', :q)) AS score
                FROM {table}
                WHERE collection_id = :cid
                  {metadata_clause}
                  AND to_tsvector('english', content) @@ plainto_tsquery('english', :q)
                ORDER BY score DESC
                LIMIT :limit
            """)
            rows = await session.execute(fts_sql, {
                "q": query[:500],
                "cid": collection_id,
                "limit": top_k * 3,
                **metadata_params,
            })
            for i, row in enumerate(rows.fetchall()):
                fts_ranks[row[0]] = (row[1], row[2] or {}, i + 1)
        except Exception as e:
            if strict:
                raise RetrievalLegExecutionError("fts") from e
            logger.debug("fts_leg_failed", error=str(e)[:80])

    # Leg 3: pg_trgm fuzzy
    if retrieval_mode in ("hybrid", "lexical"):
        try:
            trgm_sql = text(f"""
                SELECT id, content, metadata,
                       similarity(content, :q) AS score
                FROM {table}
                WHERE collection_id = :cid
                  {metadata_clause}
                  AND content % :q
                ORDER BY score DESC
                LIMIT :limit
            """)
            rows = await session.execute(trgm_sql, {
                "q": query[:500],
                "cid": collection_id,
                "limit": top_k * 2,
                **metadata_params,
            })
            for i, row in enumerate(rows.fetchall()):
                trgm_ranks[row[0]] = (row[1], row[2] or {}, i + 1)
        except Exception as e:
            if strict:
                raise RetrievalLegExecutionError("trgm") from e
            logger.debug("trgm_leg_failed", error=str(e)[:80])

    # Collect all unique chunk IDs
    all_ids = set(vector_ranks) | set(fts_ranks) | set(trgm_ranks)
    if not all_ids:
        return []

    # Compute RRF scores
    fused: list[tuple[str, float, str, dict[str, Any], list[str]]] = []
    for chunk_id in all_ids:
        ranks: list[int] = []
        legs: list[str] = []
        content: str = ""
        metadata: dict[str, Any] = {}

        if chunk_id in vector_ranks:
            content, metadata, r = vector_ranks[chunk_id]
            ranks.append(r)
            legs.append("vector")
        if chunk_id in fts_ranks:
            c, m, r = fts_ranks[chunk_id]
            if not content:
                content, metadata = c, m
            ranks.append(r)
            legs.append("fts")
        if chunk_id in trgm_ranks:
            c, m, r = trgm_ranks[chunk_id]
            if not content:
                content, metadata = c, m
            ranks.append(r)
            legs.append("trgm")

        score = _rrf_score(ranks)
        fused.append((chunk_id, score, content, metadata, legs))

    # Sort by RRF score descending
    fused.sort(key=lambda x: x[1], reverse=True)

    results = [
        RetrievalResult(
            chunk_id=cid,
            content=content,
            score=score,
            source_metadata=meta,
            retrieval_legs=legs,
        )
        for cid, score, content, meta, legs in fused[:top_k]
    ]

    # Post-filter by metadata if requested
    if metadata_filter:
        results = [
            r for r in results
            if all(r.source_metadata.get(k) == v for k, v in metadata_filter.items())
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
    )

    return results


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
        r"[A-Z][A-Z0-9]+-\d+",         # JIRA/GitHub IDs
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

    candidates = results[:min(20, len(results))]
    if not candidates:
        return results[:top_k] if top_k else results

    try:
        import json as _json

        from app.providers.base import CompletionRequest, Message

        # Build a batch relevance scoring prompt
        passages_text = "\n".join(
            f"[{i}] {r.content[:300]}" for i, r in enumerate(candidates)
        )
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
    strict: bool = False,
) -> list[RetrievalResult]:
    """HyDE: generate a hypothetical answer, search with it. Falls back to hybrid."""
    if provider is None:
        if strict:
            raise RetrievalStrategyExecutionError("hyde", "LLM provider is required")
        return await hybrid_search(
            session, query=query, query_embedding=query_embedding,
            collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
            metadata_filter=metadata_filter,
        )
    try:
        from app.providers.base import CompletionRequest, Message
        req = CompletionRequest(
            messages=[
                Message(role="system", content=(
                    "Write a 2-3 sentence hypothetical document that would perfectly "
                    "answer the following question. Write only the document text."
                )),
                Message(role="user", content=f"Question: {query}"),
            ],
            model=model,
            max_tokens=200,
        )
        resp = await provider.complete(req)
        hyp_doc = resp.content.strip()
        return await hybrid_search(
            session, query=hyp_doc, query_embedding=query_embedding,
            collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
            metadata_filter=metadata_filter,
            strict=strict,
        )
    except Exception as exc:
        if strict:
            raise RetrievalStrategyExecutionError("hyde", "algorithm failed") from exc
        logger.warning("hyde_failed_falling_back", error=str(exc)[:80])
        return await hybrid_search(
            session, query=query, query_embedding=query_embedding,
            collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
            metadata_filter=metadata_filter,
        )


async def retrieve_multi_hop(
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
    strict: bool = False,
) -> list[RetrievalResult]:
    """Multi-hop: decompose query, search each sub-query, merge results."""
    if provider is None:
        if strict:
            raise RetrievalStrategyExecutionError("multi_hop", "LLM provider is required")
        return await hybrid_search(
            session, query=query, query_embedding=query_embedding,
            collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
            metadata_filter=metadata_filter,
        )
    try:
        import json as _json

        from app.providers.base import CompletionRequest, Message
        req = CompletionRequest(
            messages=[
                Message(role="system", content=(
                    "Decompose this query into 2-3 specific sub-queries. "
                    'Return ONLY a JSON array: ["sub-query 1", "sub-query 2"]'
                )),
                Message(role="user", content=f"Query: {query}"),
            ],
            model=model,
            max_tokens=150,
        )
        resp = await provider.complete(req)
        sub_queries: list[str] = _json.loads(resp.content.strip())
        if not isinstance(sub_queries, list):
            sub_queries = [query]
        sub_queries = [str(q) for q in sub_queries[:3]]
    except Exception as exc:
        if strict:
            raise RetrievalStrategyExecutionError("multi_hop", "decomposition failed") from exc
        sub_queries = [query]

    seen: set[str] = set()
    all_results: list[RetrievalResult] = []
    per_hop = max(top_k // max(len(sub_queries), 1), 3)
    for sub_q in sub_queries:
        try:
            hop = await hybrid_search(
                session, query=sub_q, query_embedding=query_embedding,
                collection_id=collection_id, top_k=per_hop, embedding_dim=embedding_dim,
                metadata_filter=metadata_filter,
                strict=strict,
            )
            for r in hop:
                if r.chunk_id not in seen:
                    seen.add(r.chunk_id)
                    all_results.append(r)
        except Exception as exc:
            if strict:
                raise RetrievalStrategyExecutionError("multi_hop", "retrieval hop failed") from exc
            pass
    all_results.sort(key=lambda r: r.score, reverse=True)
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
) -> list[RetrievalResult]:
    """Fusion RAG: expand query into N variants, retrieve, and RRF-merge.

    A gateway supplies ``search_operation`` to run variants concurrently with a
    fresh tenant-scoped session per call. Legacy direct callers are serialized
    because an ``AsyncSession`` cannot safely be shared by concurrent tasks.
    """
    from app.context.rerank_policy import rrf_fuse
    from app.rag.agentic.query_expander import QueryExpander

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
        if strict:
            raise RetrievalStrategyExecutionError(
                "fusion", "provider query expansion failed"
            ) from exc
        variants = expander.expand_for_fusion(query, max_variants=max_variants)

    # Use original embedding for all variants (best-effort: embed each if embedder available)
    variant_embeddings: list[list[float] | None] = []
    for v in variants:
        if embedder is not None and v != query:
            try:
                from app.providers.base import EmbedRequest
                resp = await embedder.embed(EmbedRequest(texts=[v]))
                variant_embeddings.append(
                    resp.embeddings[0] if resp.embeddings else query_embedding
                )
            except Exception as exc:
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
                session=session, query=q, query_embedding=emb,
                collection_id=collection_id, top_k=top_k,
                ef_search=ef_search, embedding_dim=embedding_dim,
                metadata_filter=metadata_filter, strict=strict,
            )
        except Exception as exc:
            if strict:
                raise
            logger.warning("fusion_rag_variant_failed", query=q[:60], error=str(exc)[:80])
            return []

    if search_operation is not None:
        per_variant_results = await asyncio.gather(
            *[
                search_operation(q, emb)
                for q, emb in zip(variants, variant_embeddings, strict=True)
            ]
        )
    else:
        per_variant_results = []
        for q, emb in zip(variants, variant_embeddings, strict=True):
            per_variant_results.append(await _retrieve_legacy(q, emb))

    # Build ranked lists for rrf_fuse
    ranked_lists: list[list[dict[str, Any]]] = []
    for variant_results in per_variant_results:
        ranked_list = [
            {
                "chunk_id": r.chunk_id,
                "content": r.content,
                "score": r.score,
                "source_metadata": r.source_metadata,
                "retrieval_legs": r.retrieval_legs,
            }
            for r in variant_results
        ]
        if ranked_list:
            ranked_lists.append(ranked_list)

    if not ranked_lists:
        return []

    fused = rrf_fuse(ranked_lists, k=60)

    # Deduplicate by chunk_id
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in fused:
        cid = item.get("chunk_id", "")
        if cid not in seen:
            seen.add(cid)
            deduped.append(item)

    return [
        RetrievalResult(
            chunk_id=d["chunk_id"],
            content=d["content"],
            score=d.get("score", 0.0),
            source_metadata=d.get("source_metadata", {}),
            retrieval_legs=d.get("retrieval_legs", ["fusion"]),
        )
        for d in deduped[:top_k]
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
                session, query=query, query_embedding=query_embedding,
                collection_id=collection_id, provider=provider,
                model=model, top_k=top_k, embedding_dim=embedding_dim,
                metadata_filter=metadata_filter,
                strict=strict,
            )
        if strategy == "multi_hop":
            return await retrieve_multi_hop(
                session, query=query, query_embedding=query_embedding,
                collection_id=collection_id, provider=provider,
                model=model, top_k=top_k, embedding_dim=embedding_dim,
                metadata_filter=metadata_filter,
                strict=strict,
            )
        if strategy == "fusion":
            return await retrieve_fusion(
                session, query=query, query_embedding=query_embedding,
                collection_id=collection_id, top_k=top_k,
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
                    session, query=query, query_embedding=query_embedding,
                    collection_id=collection_id, top_k=top_k,
                    retrieval_mode="hybrid", embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter, strict=strict,
                )
                # CRAG correction: if low confidence, the caller (RetrieverTool.retrieve_corrective)
                # handles web fallback. At engine level, return base results + confidence metadata.
                avg_conf = (
                    sum(r.score for r in base_results) / len(base_results)
                    if base_results
                    else 0.0
                )
                if base_results and avg_conf < 0.5:
                    # Tag results as low-confidence for CRAG layer to act on
                    for r in base_results:
                        r.source_metadata["corrective_flagged"] = True
                        r.source_metadata["avg_confidence"] = avg_conf
                return base_results
            except Exception as exc:
                if strict:
                    raise RetrievalStrategyExecutionError(
                        "corrective", "algorithm failed"
                    ) from exc
                logger.warning("corrective_rag_failed", error=str(exc)[:80])
                return await hybrid_search(
                    session, query=query, query_embedding=query_embedding,
                    collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                )

        if strategy in ("flare", "self_rag"):
            # FLARE and Self-RAG use the provider for generation
            # When no provider given, fall back to hybrid
            if provider is None:
                if strict:
                    raise RetrievalStrategyExecutionError(
                        strategy, "LLM provider is required"
                    )
                return await hybrid_search(
                    session, query=query, query_embedding=query_embedding,
                    collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                )
            try:
                base_results = await hybrid_search(
                    session, query=query, query_embedding=query_embedding,
                    collection_id=collection_id, top_k=top_k,
                    retrieval_mode="hybrid", embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter, strict=strict,
                )
                if not base_results:
                    return base_results
                # Use context from base retrieval as FLARE/Self-RAG context
                context_text = "\n".join(r.content[:300] for r in base_results[:5])
                if strategy == "flare":
                    from app.rag.agentic.patterns.flare import FLAREPattern
                    pattern = FLAREPattern()

                    async def _flare_retrieve(q: str, **kw: Any) -> str:
                        extra = await hybrid_search(
                            session, query=q, query_embedding=query_embedding,
                            collection_id=collection_id, top_k=3,
                            embedding_dim=embedding_dim, metadata_filter=metadata_filter,
                            strict=strict,
                        )
                        return "\n".join(r.content[:300] for r in extra)

                    refined = await pattern.execute(
                        query=query,
                        provider=provider,
                        retrieve_fn=_flare_retrieve,
                        model=model,
                        strict=strict,
                    )
                elif strategy == "self_rag":
                    from app.rag.agentic.patterns.self_rag import SelfRAGPattern
                    pattern = SelfRAGPattern()

                    async def _self_rag_retrieve(q: str, **kw: Any) -> str:
                        return context_text

                    refined = await pattern.execute(
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
                    session, query=query, query_embedding=query_embedding,
                    collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                )

        if strategy == "speculative":
            try:
                if strict and provider is None:
                    raise RetrievalStrategyExecutionError(
                        "speculative", "LLM provider is required"
                    )
                base_results = await hybrid_search(
                    session, query=query, query_embedding=query_embedding,
                    collection_id=collection_id, top_k=top_k,
                    embedding_dim=embedding_dim, metadata_filter=metadata_filter, strict=strict,
                )
                if not base_results or provider is None:
                    return base_results

                async def _spec_retrieve(q: str, **kw: Any) -> str:
                    r = await hybrid_search(
                        session, query=q, query_embedding=query_embedding,
                        collection_id=collection_id, top_k=3,
                        embedding_dim=embedding_dim, metadata_filter=metadata_filter,
                        strict=strict,
                    )
                    return "\n".join(x.content[:300] for x in r)

                from app.rag.agentic.patterns.speculative import SpeculativeRAGPattern
                pattern = SpeculativeRAGPattern(n_candidates=2)
                best = await pattern.execute(
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
                    session, query=query, query_embedding=query_embedding,
                    collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                )

        if strategy == "raptor":
            try:
                if strict and provider is None:
                    raise RetrievalStrategyExecutionError(
                        "raptor", "LLM provider is required"
                    )
                base_results = await hybrid_search(
                    session, query=query, query_embedding=query_embedding,
                    collection_id=collection_id, top_k=min(top_k * 2, 20),
                    embedding_dim=embedding_dim, metadata_filter=metadata_filter, strict=strict,
                )
                if not base_results or provider is None:
                    return base_results[:top_k]
                chunks = [
                    {"content": r.content, "chunk_id": r.chunk_id, "score": r.score}
                    for r in base_results
                ]
                from app.rag.agentic.patterns.raptor import RAPTORPattern
                pattern = RAPTORPattern(cluster_size=4, max_levels=2)
                answer = await pattern.execute(
                    query=query,
                    chunks=chunks,
                    provider=provider,
                    model=model,
                    strict=strict,
                )
                if answer and base_results:
                    summary = RetrievalResult(
                        chunk_id="raptor_summary",
                        content=answer[:3000],
                        score=0.95,
                        source_metadata={"strategy": "raptor", "source_count": len(base_results)},
                        retrieval_legs=["raptor"],
                    )
                    return [summary, *base_results[:top_k - 1]]
                return base_results[:top_k]
            except Exception as exc:
                if strict:
                    if isinstance(exc, RetrievalStrategyExecutionError):
                        raise
                    raise RetrievalStrategyExecutionError("raptor", "algorithm failed") from exc
                logger.warning("raptor_failed", error=str(exc)[:80])
                return await hybrid_search(
                    session, query=query, query_embedding=query_embedding,
                    collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                )

        if strategy == "colbert":
            try:
                base_results = await hybrid_search(
                    session, query=query, query_embedding=query_embedding,
                    collection_id=collection_id, top_k=top_k * 2,
                    embedding_dim=embedding_dim, metadata_filter=metadata_filter, strict=strict,
                )
                if not base_results:
                    return base_results
                chunks = [
                    {"content": r.content, "chunk_id": r.chunk_id, "score": r.score,
                     "source_metadata": r.source_metadata}
                    for r in base_results
                ]
                from app.rag.agentic.patterns.colbert import ColBERTPattern
                pattern = ColBERTPattern(alpha=0.5)
                reranked = pattern.rerank(query=query, chunks=chunks, top_k=top_k)
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
                    session, query=query, query_embedding=query_embedding,
                    collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                )

        if strategy == "agentic_chunking":
            try:
                if strict and provider is None:
                    raise RetrievalStrategyExecutionError(
                        "agentic_chunking", "LLM provider is required"
                    )
                from app.rag.agentic.patterns.agentic_chunking import AgenticChunkingPattern
                base_results = await hybrid_search(
                    session, query=query, query_embedding=query_embedding,
                    collection_id=collection_id, top_k=top_k * 2,
                    embedding_dim=embedding_dim, metadata_filter=metadata_filter, strict=strict,
                )
                if not base_results or provider is None:
                    return base_results[:top_k]
                chunks = [
                    {"content": r.content, "chunk_id": r.chunk_id, "score": r.score}
                    for r in base_results
                ]
                pattern = AgenticChunkingPattern(max_propositions=5)
                proposition_chunks = await pattern.execute(
                    chunks=chunks,
                    provider=provider,
                    query=query,
                    top_k=top_k,
                    model=model,
                    strict=strict,
                )
                return [
                    RetrievalResult(
                        chunk_id=c.get("chunk_id", f"prop_{i}"),
                        content=c.get("content", ""),
                        score=c.get("score", 0.7),
                        source_metadata={
                            "strategy": "agentic_chunking",
                            "source_chunk": c.get("source_chunk_id", ""),
                        },
                        retrieval_legs=["agentic_chunking"],
                    )
                    for i, c in enumerate(proposition_chunks[:top_k])
                ]
            except Exception as exc:
                if strict:
                    if isinstance(exc, RetrievalStrategyExecutionError):
                        raise
                    raise RetrievalStrategyExecutionError(
                        "agentic_chunking", "algorithm failed"
                    ) from exc
                logger.warning("agentic_chunking_failed", error=str(exc)[:80])
                return await hybrid_search(
                    session, query=query, query_embedding=query_embedding,
                    collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                )

        if strategy == "parametric":
            # Skip retrieval entirely — LLM uses its own knowledge
            return []

        if strategy == "memory":
            # Memory retrieval — LTM semantic recall using pgvector cosine similarity
            if long_term_memory is not None and tenant_ctx is not None:
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
                    if ltm_results:
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
                except Exception as exc:
                    logger.warning("memory_strategy_recall_failed", error=str(exc)[:80])
            # Fallback: empty (LTM not available in this context)
            return []

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
                    session, query=query, query_embedding=query_embedding,
                    collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                )
            except Exception as exc:
                logger.warning("graph_strategy_failed", error=str(exc)[:80])
                return await hybrid_search(
                    session, query=query, query_embedding=query_embedding,
                    collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
                    metadata_filter=metadata_filter,
                )

        mode = "lexical" if strategy == "lexical" else retrieval_mode
        return await hybrid_search(
            session, query=query, query_embedding=query_embedding,
            collection_id=collection_id, top_k=top_k,
            retrieval_mode=mode, embedding_dim=embedding_dim, strict=strict,
            metadata_filter=metadata_filter,
        )
    except Exception as exc:
        if strict:
            raise
        logger.warning("retrieve_dispatch_failed", strategy=strategy, error=str(exc)[:80])
        return await hybrid_search(
            session, query=query, query_embedding=query_embedding,
            collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
            metadata_filter=metadata_filter,
        )
