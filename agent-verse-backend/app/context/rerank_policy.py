"""RerankPolicy — deduplication, score-based and diversity reranking.

TODO(row6-wiring, gateway): the default gateway RAG path never cross-encoder
reranks or calibrates its results. ``app.rag.gateway.execute_core_strategy``
builds citations via ``app.rag.gateway._canonical_result`` (~line 723 onward)
straight from raw engine scores. That is the seam where a ``RerankPolicy`` with
``strategy=RerankStrategy.AUTO`` should reorder the retrieved results and where
``_apply_calibration``'s ``calibrated_confidence`` should be surfaced onto each
``RAGCitation`` (do NOT edit gateway.py here — recently changed; wire it in a
follow-up).

TODO(row6-wiring, scorecard): the aggregate ``last_retrieval_confidence`` (see
``app.rag.score_calibration.retrieval_confidence``) should feed the
``retrieval_confidence`` dimension in
``app.evals.runtime_scorecard.RuntimeScorecard.score`` (~line 140), which today
reads ``retrieval_result.confidence`` and gets nothing calibrated.
"""

from __future__ import annotations

import enum
import math
import re
from collections import Counter, defaultdict
from typing import Any

from app.rag.score_calibration import CalibrationMethod, calibrate_scores, retrieval_confidence


class RerankStrategy(enum.StrEnum):
    SCORE = "score"
    RRF = "rrf"
    DIVERSITY = "diversity"
    CROSS_ENCODER = "cross_encoder"
    LLM = "llm"
    # AUTO uses the cross-encoder when the optional sentence-transformers lib +
    # model are present, otherwise degrades to SCORE. SCORE stays the safe
    # default; AUTO makes the choice explicit and records it (last_reason).
    AUTO = "auto"


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
    return [{**docs[cid], "rrf_score": scores[cid], "score": scores[cid]} for cid in sorted_ids]


class RerankPolicy:
    def __init__(
        self,
        strategy: RerankStrategy = RerankStrategy.SCORE,
        deduplicate: bool = True,
        min_score: float = 0.0,
        max_per_source: int = 5,
        calibration_method: CalibrationMethod | None = "minmax",
    ) -> None:
        self._strategy = strategy
        self._deduplicate = deduplicate
        self._min_score = min_score
        self._max_per_source = max_per_source
        self._calibration_method = calibration_method
        # Observability of the retrieval-strategy decision. These are set on
        # every rerank() call so callers (and tests) can see which path ran,
        # why it was chosen, and the aggregate calibrated confidence.
        self.last_strategy_used: RerankStrategy | None = None
        self.last_reason: str = ""
        self.last_retrieval_confidence: float | None = None

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

        # 3. Resolve + apply strategy (AUTO chooses cross-encoder vs SCORE).
        effective, reason = self._resolve_strategy()
        self.last_strategy_used = effective
        self.last_reason = reason

        if effective == RerankStrategy.SCORE:
            filtered = sorted(filtered, key=lambda c: c.get("score", 0.0), reverse=True)
        elif effective == RerankStrategy.DIVERSITY:
            filtered = self._diversity_rerank(filtered, query_embedding=query_embedding)
        elif effective == RerankStrategy.RRF:
            filtered = rrf_fuse([filtered], k=60)
        elif effective == RerankStrategy.CROSS_ENCODER:
            filtered = self._cross_encoder_rerank(filtered, query)
        elif effective == RerankStrategy.LLM:
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

        # 5. Attach calibrated confidence (additive — never mutates `score`).
        return self._apply_calibration(filtered)

    def _resolve_strategy(self) -> tuple[RerankStrategy, str]:
        """Resolve the concrete strategy, recording why it was chosen.

        Only AUTO is dynamic: it prefers the cross-encoder when the optional
        sentence-transformers lib + model can actually be loaded, and otherwise
        degrades to the safe SCORE default. Everything else is explicit.
        """
        if self._strategy is not RerankStrategy.AUTO:
            return self._strategy, f"explicit:{self._strategy.value}"
        try:
            from app.rag.cross_encoder import is_cross_encoder_available

            available = is_cross_encoder_available()
        except Exception:  # pragma: no cover - defensive import guard
            available = False
        if available:
            return RerankStrategy.CROSS_ENCODER, "auto:cross_encoder_available"
        return (
            RerankStrategy.SCORE,
            "auto:cross_encoder_unavailable_fallback_score",
        )

    def _apply_calibration(
        self, chunks: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Attach a calibrated [0,1] confidence to each reranked chunk.

        Additive: it adds a ``calibrated_confidence`` field and records the
        aggregate ``last_retrieval_confidence`` without touching the raw
        ``score``/``ce_score`` fields downstream code already relies on.
        """
        method = self._calibration_method
        if method is None or not chunks:
            return chunks
        raw = [float(c.get("score", 0.0)) for c in chunks]
        calibrated = calibrate_scores(raw, method=method)
        self.last_retrieval_confidence = retrieval_confidence(raw)
        return [
            {**chunk, "calibrated_confidence": confidence}
            for chunk, confidence in zip(chunks, calibrated, strict=False)
        ]

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
        dot = sum(x * y for x, y in zip(a, b, strict=False))
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
                    max_sim = max(self._cosine(emb, s.get("embedding", [])) for s in selected)
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
        tokens_a = set(re.findall(r"\b\w+\b", text_a.lower()))
        tokens_b = set(re.findall(r"\b\w+\b", text_b.lower()))
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
        self,
        chunks: list[dict[str, Any]],
        query: str,
    ) -> list[dict[str, Any]]:
        """Real cross-encoder reranking via sentence-transformers.

        Falls back to TF-IDF when model unavailable.
        """
        if not chunks:
            return chunks
        try:
            from app.rag.cross_encoder import cross_encode

            documents = [c.get("content", "")[:512] for c in chunks]
            scores = cross_encode(query, documents)
            # Normalize scores to 0-1 range
            if scores:
                min_s, max_s = min(scores), max(scores)
                span = max_s - min_s or 1.0
                norm_scores = [(s - min_s) / span for s in scores]
            else:
                norm_scores = [0.5] * len(chunks)
            # Blend cross-encoder score with original retrieval score
            scored = []
            for chunk, ce_score, orig_score in zip(
                chunks,
                norm_scores,
                [float(c.get("score", 0.5)) for c in chunks], strict=False,
            ):
                blended = 0.6 * ce_score + 0.4 * orig_score
                scored.append({**chunk, "score": blended, "ce_score": ce_score})
            scored.sort(key=lambda c: c["score"], reverse=True)
            return scored
        except Exception:
            # Cross-encoder inference failed (missing lib, load error, backend
            # crash). Degrade cleanly to the lexical TF-IDF fallback rather than
            # propagating — and record that the CE path did not run.
            self.last_reason = "cross_encoder_error_fallback_tfidf"
            return self._tfidf_rerank(chunks, query)

    async def rerank_async(
        self,
        chunks: list[dict[str, Any]],
        query: str,
        strategy: RerankStrategy | None = None,
        query_embedding: list[float] | None = None,
    ) -> list[dict[str, Any]]:
        """Async reranking — cross-encoder via thread pool for blocking inference."""
        import asyncio

        s = strategy or RerankStrategy.CROSS_ENCODER

        if s == RerankStrategy.CROSS_ENCODER:
            # Run blocking cross-encoder in thread pool
            loop = asyncio.get_event_loop()
            try:
                result = await loop.run_in_executor(None, self._cross_encoder_rerank, chunks, query)
                return result
            except Exception:
                return self._tfidf_rerank(chunks, query)

        if s == RerankStrategy.DIVERSITY:
            return self._diversity_rerank(chunks, query_embedding=query_embedding)

        return self.rerank(chunks, query=query)

    def _tfidf_rerank(self, chunks: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
        """TF-IDF weighted token overlap reranking (improved fallback)."""
        query_tokens = set(re.findall(r"\b\w+\b", query.lower()))
        all_doc_tokens = [re.findall(r"\b\w+\b", c.get("content", "").lower()) for c in chunks]
        doc_count = len(chunks)

        def idf(token: str) -> float:
            df = sum(1 for tokens in all_doc_tokens if token in tokens)
            return math.log((doc_count + 1) / (df + 1)) + 1 if df > 0 else 1.0

        scored = []
        for i, chunk in enumerate(chunks):
            doc_tokens = all_doc_tokens[i]
            doc_freq = Counter(doc_tokens)
            total = len(doc_tokens) or 1
            tfidf_score = sum((doc_freq.get(t, 0) / total) * idf(t) for t in query_tokens)
            original_score = float(chunk.get("score", 0.5))
            final = 0.4 * original_score + 0.6 * min(tfidf_score, 1.0)
            scored.append({**chunk, "score": final})

        scored.sort(key=lambda c: c["score"], reverse=True)
        return scored

    def _llm_rerank_sync(self, chunks: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
        """Cross-encoder reranking (uses sentence-transformers when available, else TF-IDF)."""
        return self._cross_encoder_rerank(chunks, query)
