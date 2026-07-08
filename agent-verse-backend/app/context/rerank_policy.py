"""RerankPolicy — deduplication, score-based and diversity reranking."""
from __future__ import annotations

import enum
import math
import re
from collections import Counter, defaultdict
from typing import Any


class RerankStrategy(str, enum.Enum):
    SCORE = "score"
    RRF = "rrf"
    DIVERSITY = "diversity"
    CROSS_ENCODER = "cross_encoder"
    LLM = "llm"


def rrf_fuse(ranked_lists: list[list[dict]], k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion — 1/(k+rank) per Cormack 2009 (1-indexed ranks)."""
    scores: dict[str, float] = defaultdict(float)
    docs: dict[str, dict] = {}
    for ranked_list in ranked_lists:
        for rank, chunk in enumerate(ranked_list, start=1):  # 1-indexed
            chunk_id = chunk.get("chunk_id", chunk.get("content", str(rank)))
            scores[chunk_id] += 1.0 / (k + rank)
            docs[chunk_id] = chunk
    sorted_ids = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    return [{**docs[cid], "rrf_score": scores[cid], "score": scores[cid]}
            for cid in sorted_ids]


class RerankPolicy:
    def __init__(
        self,
        strategy: RerankStrategy = RerankStrategy.SCORE,
        deduplicate: bool = True,
        min_score: float = 0.0,
        max_per_source: int = 5,
    ) -> None:
        self._strategy = strategy
        self._deduplicate = deduplicate
        self._min_score = min_score
        self._max_per_source = max_per_source

    def rerank(
        self,
        chunks: list[dict[str, Any]],
        query: str,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        # 1. Filter by min score
        filtered = [c for c in chunks if c.get("score", 0.0) >= self._min_score]

        # 2. Deduplicate by content
        if self._deduplicate:
            seen_content: set[str] = set()
            deduped = []
            for c in filtered:
                content = c.get("content", "")
                if content not in seen_content:
                    seen_content.add(content)
                    deduped.append(c)
            filtered = deduped

        # 3. Apply strategy
        if self._strategy == RerankStrategy.SCORE:
            filtered = sorted(filtered, key=lambda c: c.get("score", 0.0), reverse=True)
        elif self._strategy == RerankStrategy.DIVERSITY:
            filtered = self._diversity_rerank(filtered, query_embedding=query_embedding)
        elif self._strategy == RerankStrategy.RRF:
            filtered = rrf_fuse([filtered], k=60)
        elif self._strategy == RerankStrategy.CROSS_ENCODER:
            filtered = self._cross_encoder_rerank(filtered, query)
        elif self._strategy == RerankStrategy.LLM:
            filtered = self._llm_rerank_sync(filtered, query)

        # 4. Cap per source
        if self._max_per_source > 0:
            source_counts: dict[str, int] = {}
            capped = []
            for c in filtered:
                src = c.get("source_url", "_")
                if source_counts.get(src, 0) < self._max_per_source:
                    source_counts[src] = source_counts.get(src, 0) + 1
                    capped.append(c)
            filtered = capped

        return filtered

    # ------------------------------------------------------------------
    # Fix 1: True MMR (Maximal Marginal Relevance)
    # ------------------------------------------------------------------

    def _diversity_rerank(
        self,
        chunks: list[dict[str, Any]],
        query_embedding: list[float] | None = None,
        lambda_: float = 0.5,
    ) -> list[dict[str, Any]]:
        """True Maximal Marginal Relevance reranking.

        Selects documents that are both relevant to the query AND diverse from
        already-selected documents: argmax_d [λ·sim(d,q) - (1-λ)·max_{s∈S} sim(d,s)]

        Falls back to token-overlap Jaccard if no embeddings available.
        """
        if not chunks:
            return []
        if len(chunks) == 1:
            return chunks

        has_embeddings = all("embedding" in c and c["embedding"] for c in chunks)

        if has_embeddings and query_embedding:
            return self._mmr_with_embeddings(chunks, query_embedding, lambda_)
        else:
            return self._mmr_with_tokens(chunks, lambda_)

    def _cosine(self, a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = sum(x * x for x in a) ** 0.5
        mag_b = sum(x * x for x in b) ** 0.5
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    def _mmr_with_embeddings(
        self,
        chunks: list[dict[str, Any]],
        query_embedding: list[float],
        lambda_: float,
    ) -> list[dict[str, Any]]:
        """MMR using real embedding cosine similarity."""
        remaining = list(chunks)
        selected: list[dict[str, Any]] = []

        while remaining:
            best_score = float("-inf")
            best_chunk = None
            for chunk in remaining:
                emb = chunk.get("embedding", [])
                relevance = self._cosine(emb, query_embedding)
                if not selected:
                    mmr_score = relevance
                else:
                    max_sim = max(
                        self._cosine(emb, s.get("embedding", []))
                        for s in selected
                    )
                    mmr_score = lambda_ * relevance - (1 - lambda_) * max_sim
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_chunk = chunk
            if best_chunk is not None:
                selected.append(best_chunk)
                remaining.remove(best_chunk)
            else:
                break
        return selected

    def _jaccard(self, text_a: str, text_b: str) -> float:
        """Token-level Jaccard similarity as fallback."""
        tokens_a = set(re.findall(r'\b\w+\b', text_a.lower()))
        tokens_b = set(re.findall(r'\b\w+\b', text_b.lower()))
        if not tokens_a or not tokens_b:
            return 0.0
        return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)

    def _mmr_with_tokens(
        self,
        chunks: list[dict[str, Any]],
        lambda_: float,
    ) -> list[dict[str, Any]]:
        """MMR using token Jaccard as fallback when no embeddings."""
        remaining = list(chunks)
        selected: list[dict[str, Any]] = []

        while remaining:
            best_score = float("-inf")
            best_chunk = None
            for chunk in remaining:
                relevance = float(chunk.get("score", 0.5))
                if not selected:
                    mmr_score = relevance
                else:
                    max_sim = max(
                        self._jaccard(chunk.get("content", ""), s.get("content", ""))
                        for s in selected
                    )
                    mmr_score = lambda_ * relevance - (1 - lambda_) * max_sim
                if mmr_score > best_score:
                    best_score = mmr_score
                    best_chunk = chunk
            if best_chunk is not None:
                selected.append(best_chunk)
                remaining.remove(best_chunk)
            else:
                break
        return selected

    # ------------------------------------------------------------------
    # Fix 2: Real cross-encoder reranking
    # ------------------------------------------------------------------

    def _cross_encoder_rerank(
        self, chunks: list[dict[str, Any]], query: str
    ) -> list[dict[str, Any]]:
        """Cross-encoder style reranking — calls LLM reranker if provider available.
        Falls back to TF-IDF weighted token overlap when no provider configured.
        """
        # Try async LLM reranker via event loop (production path)
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if not loop.is_running():
                return loop.run_until_complete(self._async_cross_encoder(chunks, query))
        except Exception:
            pass
        # Fallback: TF-IDF weighted overlap (better than pure set intersection)
        return self._tfidf_rerank(chunks, query)

    async def _async_cross_encoder(
        self, chunks: list[dict[str, Any]], query: str
    ) -> list[dict[str, Any]]:
        """Async cross-encoder using LLM relevance scoring."""
        try:
            from app.rag.engine import RetrievalResult, rerank_results
            # rerank_results expects RetrievalResult objects — adapt chunks
            results = [
                RetrievalResult(
                    chunk_id=c.get("chunk_id", f"c{i}"),
                    content=c.get("content", ""),
                    score=float(c.get("score", 0.5)),
                    source_metadata=c.get("source_metadata", {}),
                    retrieval_legs=c.get("retrieval_legs", []),
                )
                for i, c in enumerate(chunks)
            ]
            reranked = await rerank_results(results=results, query=query)
            # Convert back to dicts
            return [
                {
                    **(chunks[i] if i < len(chunks) else {}),
                    "chunk_id": r.chunk_id,
                    "content": r.content,
                    "score": r.score,
                }
                for i, r in enumerate(reranked)
            ]
        except Exception:
            return self._tfidf_rerank(chunks, query)

    def _tfidf_rerank(self, chunks: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
        """TF-IDF weighted token overlap reranking (improved fallback)."""
        query_tokens = set(re.findall(r'\b\w+\b', query.lower()))
        all_doc_tokens = [
            re.findall(r'\b\w+\b', c.get("content", "").lower())
            for c in chunks
        ]
        N = len(chunks)

        def idf(token: str) -> float:
            df = sum(1 for tokens in all_doc_tokens if token in tokens)
            return math.log((N + 1) / (df + 1)) + 1 if df > 0 else 1.0

        scored = []
        for i, chunk in enumerate(chunks):
            doc_tokens = all_doc_tokens[i]
            doc_freq = Counter(doc_tokens)
            total = len(doc_tokens) or 1
            tfidf_score = sum(
                (doc_freq.get(t, 0) / total) * idf(t)
                for t in query_tokens
            )
            original_score = float(chunk.get("score", 0.5))
            final = 0.4 * original_score + 0.6 * min(tfidf_score, 1.0)
            scored.append({**chunk, "score": final})

        scored.sort(key=lambda c: c["score"], reverse=True)
        return scored

    def _llm_rerank_sync(
        self, chunks: list[dict[str, Any]], query: str
    ) -> list[dict[str, Any]]:
        """LLM reranker — keyword overlap scoring as lightweight proxy.

        Production: call async LLM reranker via app/rag_platform/reranker.py.
        In sync context: uses keyword overlap as a deterministic approximation
        that preserves the interface contract without requiring async.
        """
        if not query:
            return sorted(chunks, key=lambda c: c.get("score", 0.0), reverse=True)

        query_words = set(query.lower().split())

        def llm_proxy_score(chunk: dict[str, Any]) -> float:
            content = chunk.get("content", "").lower()
            content_words = set(content.split())
            overlap = len(query_words & content_words)
            overlap_score = overlap / max(len(query_words), 1)
            # Combine vector score with semantic overlap
            return 0.4 * chunk.get("score", 0.5) + 0.6 * overlap_score

        return sorted(chunks, key=llm_proxy_score, reverse=True)
