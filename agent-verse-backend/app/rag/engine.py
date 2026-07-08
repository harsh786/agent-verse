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
import re
from dataclasses import dataclass, field
from typing import Any, ClassVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.logging import get_logger

logger = get_logger(__name__)

# RRF constant (standard: 60)
_RRF_K = 60


@dataclass
class RetrievalResult:
    chunk_id: str
    content: str
    score: float
    source_metadata: dict[str, Any]
    retrieval_legs: list[str] = field(default_factory=list)  # which legs contributed


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
    # Determine table name
    table = f"knowledge_chunks_{embedding_dim}" if embedding_dim else "knowledge_chunks_1536"

    # Per-leg result dicts: chunk_id → (content, metadata, rank)
    vector_ranks: dict[str, tuple[str, dict[str, Any], int]] = {}
    fts_ranks: dict[str, tuple[str, dict[str, Any], int]] = {}
    trgm_ranks: dict[str, tuple[str, dict[str, Any], int]] = {}

    # Leg 1: pgvector ANN
    if query_embedding and retrieval_mode in ("hybrid", "vector"):
        try:
            await session.execute(
                text("SET LOCAL hnsw.ef_search = :ef"),
                {"ef": ef_search},
            )
            vec_sql = text(f"""
                SELECT id, content, metadata,
                       1 - (embedding <=> :emb::vector) AS score
                FROM {table}
                WHERE collection_id = :cid
                ORDER BY embedding <=> :emb::vector
                LIMIT :limit
            """)
            rows = await session.execute(vec_sql, {
                "emb": str(query_embedding),
                "cid": collection_id,
                "limit": top_k * 3,
            })
            for i, row in enumerate(rows.fetchall()):
                vector_ranks[row[0]] = (row[1], row[2] or {}, i + 1)
        except Exception as e:
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
                  AND to_tsvector('english', content) @@ plainto_tsquery('english', :q)
                ORDER BY score DESC
                LIMIT :limit
            """)
            rows = await session.execute(fts_sql, {
                "q": query[:500],
                "cid": collection_id,
                "limit": top_k * 3,
            })
            for i, row in enumerate(rows.fetchall()):
                fts_ranks[row[0]] = (row[1], row[2] or {}, i + 1)
        except Exception as e:
            logger.debug("fts_leg_failed", error=str(e)[:80])

    # Leg 3: pg_trgm fuzzy
    if retrieval_mode in ("hybrid", "lexical"):
        try:
            trgm_sql = text(f"""
                SELECT id, content, metadata,
                       similarity(content, :q) AS score
                FROM {table}
                WHERE collection_id = :cid
                  AND content % :q
                ORDER BY score DESC
                LIMIT :limit
            """)
            rows = await session.execute(trgm_sql, {
                "q": query[:500],
                "cid": collection_id,
                "limit": top_k * 2,
            })
            for i, row in enumerate(rows.fetchall()):
                trgm_ranks[row[0]] = (row[1], row[2] or {}, i + 1)
        except Exception as e:
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
        from app.providers.base import CompletionRequest, Message
        import json as _json

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
            max_tokens=100,
        )
        resp = await provider.complete(req)
        scores = _json.loads(resp.content.strip())
        if isinstance(scores, list) and len(scores) == len(candidates):
            for i, r in enumerate(candidates):
                try:
                    r.score = float(scores[i]) / 10.0
                except (TypeError, ValueError, IndexError):
                    pass
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
    top_k: int = 10,
    embedding_dim: int | None = None,
) -> list[RetrievalResult]:
    """HyDE: generate a hypothetical answer, search with it. Falls back to hybrid."""
    if provider is None:
        return await hybrid_search(
            session, query=query, query_embedding=query_embedding,
            collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
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
            max_tokens=200,
        )
        resp = await provider.complete(req)
        hyp_doc = resp.content.strip()
        return await hybrid_search(
            session, query=hyp_doc, query_embedding=query_embedding,
            collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
        )
    except Exception as exc:
        logger.warning("hyde_failed_falling_back", error=str(exc)[:80])
        return await hybrid_search(
            session, query=query, query_embedding=query_embedding,
            collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
        )


async def retrieve_multi_hop(
    session: AsyncSession,
    *,
    query: str,
    query_embedding: list[float] | None,
    collection_id: str,
    provider: Any = None,
    top_k: int = 10,
    embedding_dim: int | None = None,
) -> list[RetrievalResult]:
    """Multi-hop: decompose query, search each sub-query, merge results."""
    if provider is None:
        return await hybrid_search(
            session, query=query, query_embedding=query_embedding,
            collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
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
            max_tokens=150,
        )
        resp = await provider.complete(req)
        sub_queries: list[str] = _json.loads(resp.content.strip())
        if not isinstance(sub_queries, list):
            sub_queries = [query]
        sub_queries = [str(q) for q in sub_queries[:3]]
    except Exception:
        sub_queries = [query]

    seen: set[str] = set()
    all_results: list[RetrievalResult] = []
    per_hop = max(top_k // max(len(sub_queries), 1), 3)
    for sub_q in sub_queries:
        try:
            hop = await hybrid_search(
                session, query=sub_q, query_embedding=query_embedding,
                collection_id=collection_id, top_k=per_hop, embedding_dim=embedding_dim,
            )
            for r in hop:
                if r.chunk_id not in seen:
                    seen.add(r.chunk_id)
                    all_results.append(r)
        except Exception:
            pass
    all_results.sort(key=lambda r: r.score, reverse=True)
    return all_results[:top_k]


async def retrieve_fusion(
    session: AsyncSession,
    *,
    query: str,
    query_embedding: list[float] | None,
    collection_id: str,
    top_k: int = 10,
    max_variants: int = 3,
    ef_search: int = 200,
    embedding_dim: int | None = None,
    embedder: Any = None,
) -> list[RetrievalResult]:
    """Fusion RAG: expand query into N variants, retrieve in parallel, RRF-merge."""
    from app.rag.agentic.query_expander import QueryExpander
    from app.context.rerank_policy import rrf_fuse

    expander = QueryExpander()
    variants: list[str] = expander.expand_for_fusion(query, max_variants=max_variants)

    # Use original embedding for all variants (best-effort: embed each if embedder available)
    variant_embeddings: list[list[float] | None] = []
    for v in variants:
        if embedder is not None and v != query:
            try:
                from app.providers.base import EmbedRequest
                resp = await embedder.embed(EmbedRequest(texts=[v]))
                variant_embeddings.append(resp.embeddings[0] if resp.embeddings else query_embedding)
            except Exception:
                variant_embeddings.append(query_embedding)
        else:
            variant_embeddings.append(query_embedding)

    async def _retrieve_one(q: str, emb: list[float] | None) -> list[RetrievalResult]:
        try:
            return await hybrid_search(
                session=session, query=q, query_embedding=emb,
                collection_id=collection_id, top_k=top_k,
                ef_search=ef_search, embedding_dim=embedding_dim,
            )
        except Exception as exc:
            logger.warning("fusion_rag_variant_failed", query=q[:60], error=str(exc)[:80])
            return []

    per_variant_results = await asyncio.gather(
        *[_retrieve_one(q, emb) for q, emb in zip(variants, variant_embeddings)]
    )

    # Build ranked lists for rrf_fuse
    ranked_lists: list[list[dict]] = []
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
    deduped: list[dict] = []
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
    embedding_dim: int | None = None,
    retrieval_mode: str = "hybrid",
) -> list[RetrievalResult]:
    """Strategy-dispatching entry point. Selects strategy via RetrievalPlanner if not given."""
    if strategy is None:
        strategy = RetrievalPlanner().select_strategy(query)
    try:
        if strategy == "hyde":
            return await retrieve_hyde(
                session, query=query, query_embedding=query_embedding,
                collection_id=collection_id, provider=provider,
                top_k=top_k, embedding_dim=embedding_dim,
            )
        if strategy == "multi_hop":
            return await retrieve_multi_hop(
                session, query=query, query_embedding=query_embedding,
                collection_id=collection_id, provider=provider,
                top_k=top_k, embedding_dim=embedding_dim,
            )
        if strategy == "fusion":
            return await retrieve_fusion(
                session, query=query, query_embedding=query_embedding,
                collection_id=collection_id, top_k=top_k,
                embedding_dim=embedding_dim,
            )
        mode = "lexical" if strategy == "lexical" else retrieval_mode
        return await hybrid_search(
            session, query=query, query_embedding=query_embedding,
            collection_id=collection_id, top_k=top_k,
            retrieval_mode=mode, embedding_dim=embedding_dim,
        )
    except Exception as exc:
        logger.warning("retrieve_dispatch_failed", strategy=strategy, error=str(exc)[:80])
        return await hybrid_search(
            session, query=query, query_embedding=query_embedding,
            collection_id=collection_id, top_k=top_k, embedding_dim=embedding_dim,
        )
