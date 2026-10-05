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

from app.observability.logging import get_logger
from app.rag.score_calibration import CalibrationMethod, calibrate_scores, retrieval_confidence

logger = get_logger(__name__)


class RerankStrategy(enum.StrEnum):
    SCORE = "score"
    RRF = "rrf"
    DIVERSITY = "diversity"
    CROSS_ENCODER = "cross_encoder"
    LLM = "llm"
    # HOSTED calls a managed rerank API (Cohere/Voyage/Jina-compatible). It is an
    # async strategy: on the sync path it degrades to SCORE; on rerank_async it
    # calls the endpoint and falls back to the local path on any failure.
    HOSTED = "hosted"
    # AUTO uses the cross-encoder when the optional sentence-transformers lib +
    # model are present, otherwise degrades to SCORE. SCORE stays the safe
    # default; AUTO makes the choice explicit and records it (last_reason).
    AUTO = "auto"


# ---------------------------------------------------------------------------
# Value scoring for value-based context packing (Phase-2, task 2.2).
#
# A chunk's *predicted value* combines the signals that make a chunk worth its
# tokens: how relevant retrieval judged it, how much we trust its source, how
# fresh it is, and (when available) how useful it has historically proven.
# ContextBudget uses this to pack for maximum total value under a token budget.
# This is additive to (and independent of) the reranking strategies above.
# ---------------------------------------------------------------------------

# Trust priors per source type. Overridable per chunk via a "source_trust" field.
_SOURCE_TRUST: dict[str, float] = {
    "curated": 1.0,
    "internal": 0.95,
    "internal_doc": 0.95,
    "documentation": 0.9,
    "docs": 0.9,
    "knowledge_base": 0.9,
    "code": 0.85,
    "web": 0.6,
    "search": 0.6,
    "user": 0.5,
    "unknown": 0.7,
}

# Recency half-life in days for the exponential freshness decay.
_RECENCY_HALFLIFE_DAYS = 30.0



# Characters of a chunk handed to the cross-encoder. Its tokenizer truncates at
# 512 tokens (~2,000+ characters of English); this only bounds pathological input.
_CE_MAX_INPUT_CHARS = 4096
# Weight of the cross-encoder probability against the retrieval score (OI-5:
# equal trust; the cross-encoder's say now scales with its own confidence).
_CE_WEIGHT = 0.5


def _ce_probabilities(scores: list[float], count: int) -> list[float]:
    """Cross-encoder scores as relevance probabilities in 0..1.

    The ms-marco cross-encoder returns raw logits (about -11 .. +11): they go
    through a sigmoid. Scores already on a 0..1 scale (the TF-IDF fallback) are
    used as they are.
    """
    if not scores:
        return [0.5] * count
    values = [float(s) for s in scores]
    if all(0.0 <= v <= 1.0 for v in values):
        return values
    out: list[float] = []
    for v in values:
        if v >= 0:
            out.append(1.0 / (1.0 + math.exp(-v)))
        else:
            e = math.exp(v)
            out.append(e / (1.0 + e))
    return out


def _query_identifier_patterns(query: str) -> list[re.Pattern[str]]:
    """Word-bounded, separator-tolerant patterns of the query's identifiers.

    "TJ-5531" matches "TJ-5531", "tj 5531" and "TJ5531" but not "TJ-55310".
    Only code-like tokens count (letters mixed with digits, or joined parts).
    """
    try:
        from app.rag.lexical_query import analyze_query, identifier_parts
    except ImportError:  # pragma: no cover - rag package always present
        return []
    analysed = analyze_query(query or "")
    patterns: list[re.Pattern[str]] = []
    seen: set[str] = set()
    for ident in (*analysed.identifiers, *analysed.derived_identifiers):
        parts = identifier_parts(ident)
        if not parts or sum(len(p) for p in parts) < 3:
            continue
        key = "-".join(parts)
        if key in seen:
            continue
        seen.add(key)
        body = r"[-_./\s]?".join(re.escape(p) for p in parts)
        patterns.append(re.compile(rf"(?<![^\W_]){body}(?![^\W_])", re.IGNORECASE))
        if len(patterns) >= 8:
            break
    return patterns

def source_trust(chunk: dict[str, Any]) -> float:
    """Trust multiplier in (0, 1] for a chunk's source.

    Honours an explicit ``source_trust`` float when present, else looks up the
    ``source_type`` prior, else falls back to the neutral ``unknown`` prior.
    """
    explicit = chunk.get("source_trust")
    if explicit is not None:
        return max(0.0, float(explicit))
    stype = str(chunk.get("source_type", "unknown")).lower()
    return _SOURCE_TRUST.get(stype, _SOURCE_TRUST["unknown"])


def recency_weight(chunk: dict[str, Any]) -> float:
    """Freshness multiplier in (0, 1]. 1.0 when age is unknown.

    Honours an explicit ``recency`` float; otherwise applies an exponential
    half-life decay over ``age_days`` (older chunks are worth less).
    """
    explicit = chunk.get("recency")
    if explicit is not None:
        return max(0.0, float(explicit))
    age_days = chunk.get("age_days")
    if age_days is None:
        return 1.0
    return float(0.5 ** (max(0.0, float(age_days)) / _RECENCY_HALFLIFE_DAYS))


def predict_chunk_value(chunk: dict[str, Any]) -> float:
    """Predicted value of a chunk = relevance x trust x recency x usefulness.

    - relevance: the retrieval ``score`` (neutral 0.5 when absent);
    - trust: :func:`source_trust`;
    - recency: :func:`recency_weight`;
    - usefulness: optional ``historical_usefulness`` multiplier (1.0 default).

    Always returns a small positive floor so a zero-scored chunk can still fill
    leftover budget rather than being silently un-packable.
    """
    relevance = chunk.get("score")
    relevance = 0.5 if relevance is None else float(relevance)
    usefulness = chunk.get("historical_usefulness")
    usefulness = 1.0 if usefulness is None else float(usefulness)
    # Optional salience multiplier (Phase 4): SalienceScorer output enriched onto
    # the chunk by ``app.context.salience_enrichment``. 1.0 default keeps existing
    # value packing unchanged when no salience signal is present.
    salience = chunk.get("salience")
    salience = 1.0 if salience is None else float(salience)
    value = relevance * source_trust(chunk) * recency_weight(chunk) * usefulness * salience
    return max(value, 1e-6)


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
        if effective == RerankStrategy.LLM:
            # There is no LLM reranker: 'llm' runs the cross-encoder. Report the
            # reranker that actually ran (it used to be reported as 'llm').
            effective = RerankStrategy.CROSS_ENCODER
            reason = "llm reranking is not implemented; the cross-encoder was used"
        self.last_strategy_used = effective
        self.last_reason = reason

        if effective == RerankStrategy.SCORE or effective == RerankStrategy.HOSTED:
            # HOSTED is async-only (an HTTP call); on the sync path degrade to a
            # deterministic score-sort. Use rerank_async for the real hosted call.
            filtered = sorted(filtered, key=lambda c: c.get("score", 0.0), reverse=True)
        elif effective == RerankStrategy.DIVERSITY:
            filtered = self._diversity_rerank(filtered, query_embedding=query_embedding)
        elif effective == RerankStrategy.RRF:
            filtered = rrf_fuse([filtered], k=60)
        elif effective == RerankStrategy.CROSS_ENCODER:
            filtered = self._cross_encoder_rerank(filtered, query)

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

            # P1c-3: the model truncates at 512 *tokens* itself; a 512-character
            # cut dropped most of a normal chunk (a record's distinguishing
            # field often comes last). Capped only against pathological input.
            documents = [str(c.get("content", ""))[:_CE_MAX_INPUT_CHARS] for c in chunks]
            scores = cross_encode(query, documents)
            # OI-5: the cross-encoder's relevance as a PROBABILITY, not min-max
            # normalised over the candidates. Min-max stretched an indiscriminate
            # cross-encoder's noise (every logit ~ -10 for a one-word proper noun,
            # every structured record ~ +6) to a full 0..1 span that outvoted the
            # retrieval evidence; a probability adds the same to every chunk it
            # cannot tell apart, and still separates the ones it is sure about.
            ce_probs = _ce_probabilities(scores, len(chunks))
            # The retrieval (weighted RRF) score on a 0..1 scale (P1c-4), relative
            # to the best candidate: a chunk every lexical leg and the exact-match
            # leg (P2-3) agreed on keeps its lead; near-equal RRF scores stay
            # near-equal (min-max stretched a 0.015 vs 0.017 tie to 0 vs 1).
            raw = [max(float(c.get("score", 0.5)), 0.0) for c in chunks]
            top = max(raw) if raw else 0.0
            orig_rel = [r / top for r in raw] if top > 0 else [0.5] * len(raw)
            # An exact identifier of the query ("RTO-5531", "TJ-5531") in a chunk
            # is decisive: such chunks rank ahead of chunks without it.
            ident_patterns = _query_identifier_patterns(query)
            scored = []
            for chunk, ce_score, orig_score in zip(chunks, ce_probs, orig_rel, strict=False):
                blended = _CE_WEIGHT * ce_score + (1.0 - _CE_WEIGHT) * orig_score
                exact = bool(ident_patterns) and any(
                    p.search(str(chunk.get("content", ""))) for p in ident_patterns
                )
                scored.append(
                    {**chunk, "score": blended, "ce_score": ce_score, "exact_identifier": exact}
                )
            scored.sort(key=lambda c: (c["exact_identifier"], c["score"]), reverse=True)
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

        if s == RerankStrategy.HOSTED:
            return await self._hosted_rerank(chunks, query)

        return self.rerank(chunks, query=query)

    async def _hosted_rerank(
        self, chunks: list[dict[str, Any]], query: str
    ) -> list[dict[str, Any]]:
        """Rerank via the managed hosted reranker; honest fallback on any failure.

        Reorders chunks by the endpoint's relevance score and reflects that score
        (preserving the pre-rerank score). If the endpoint is unconfigured or
        errors, degrades to the local TF-IDF/cross-encoder path — never drops
        results.
        """
        if not chunks:
            return []
        try:
            from app.core.config import get_settings
            from app.rag_platform.hosted_reranker import (
                HostedRerankerError,
                hosted_reranker_from_settings,
            )

            reranker = hosted_reranker_from_settings(get_settings())
            if reranker is None:
                return self._tfidf_rerank(chunks, query)
            documents = [str(c.get("content", "")) for c in chunks]
            pairs = await reranker.rerank(query, documents)
        except HostedRerankerError as exc:
            logger.debug("hosted_rerank_failed_fallback", error=str(exc)[:120])
            return self._tfidf_rerank(chunks, query)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("hosted_rerank_error_fallback", error=str(exc)[:120])
            return self._tfidf_rerank(chunks, query)

        if len(pairs) != len(chunks):
            # Endpoint returned a different count than sent → don't trust it.
            return self._tfidf_rerank(chunks, query)
        reordered: list[dict[str, Any]] = []
        for original_index, score in pairs:
            chunk = chunks[original_index]
            reordered.append(
                {
                    **chunk,
                    "score": float(score),
                    "pre_rerank_score": float(chunk.get("score", 0.0)),
                    "hosted_rerank_score": float(score),
                }
            )
        return reordered

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
