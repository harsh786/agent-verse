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
