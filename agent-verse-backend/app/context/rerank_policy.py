"""RerankPolicy — deduplication, score-based and diversity reranking."""
from __future__ import annotations

import enum
from collections import defaultdict
from typing import Any


class RerankStrategy(str, enum.Enum):
    SCORE = "score"
    RRF = "rrf"
    DIVERSITY = "diversity"
    CROSS_ENCODER = "cross_encoder"
    LLM = "llm"


def rrf_fuse(ranked_lists: list[list[dict]], k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion — combines multiple ranked lists.

    Formula: RRF_score(d) = sum over lists of 1 / (k + rank(d, list))
    """
    scores: dict[str, float] = defaultdict(float)
    docs: dict[str, dict] = {}
    for ranked_list in ranked_lists:
        for rank, chunk in enumerate(ranked_list):
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

    def rerank(self, chunks: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
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
            filtered = self._diversity_rerank(filtered)
        elif self._strategy == RerankStrategy.RRF:
            filtered = rrf_fuse([filtered], k=60)
        elif self._strategy == RerankStrategy.CROSS_ENCODER:
            filtered = self._cross_encoder_rerank(filtered, query)
        elif self._strategy == RerankStrategy.LLM:
            filtered = sorted(filtered, key=lambda c: c.get("score", 0.0), reverse=True)

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

    def _diversity_rerank(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """MMR-like diversity: alternate between high-score and different-source."""
        if not chunks:
            return []
        result = []
        remaining = sorted(chunks, key=lambda c: c.get("score", 0.0), reverse=True)
        used_sources: set[str] = set()
        while remaining:
            # Pick highest score from a new source if possible
            for i, c in enumerate(remaining):
                src = c.get("source_url", "_")
                if src not in used_sources or len(used_sources) >= 3:
                    result.append(remaining.pop(i))
                    used_sources.add(src)
                    break
            else:
                result.append(remaining.pop(0))
        return result

    def _cross_encoder_rerank(
        self, chunks: list[dict[str, Any]], query: str
    ) -> list[dict[str, Any]]:
        """Lightweight cross-encoder proxy using keyword overlap."""
        if not query:
            return sorted(chunks, key=lambda c: c.get("score", 0.0), reverse=True)
        query_words = set(query.lower().split())

        def cross_score(chunk: dict[str, Any]) -> float:
            content = chunk.get("content", "").lower()
            content_words = set(content.split())
            overlap = len(query_words & content_words)
            overlap_score = overlap / max(len(query_words), 1)
            return 0.4 * chunk.get("score", 0.5) + 0.6 * overlap_score

        return sorted(chunks, key=cross_score, reverse=True)
