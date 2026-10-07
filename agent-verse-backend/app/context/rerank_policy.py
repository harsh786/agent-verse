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

import asyncio
import enum
import math
import re
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any

from app.observability.logging import get_logger
from app.rag.score_calibration import CalibrationMethod, calibrate_scores, retrieval_confidence
from app.rag_platform.reranker_contract import RerankSkipped

if TYPE_CHECKING:
    from app.ai_router.resolve import RerankerResolution, Resolution

logger = get_logger(__name__)


class RerankStrategy(enum.StrEnum):
    SCORE = "score"
    RRF = "rrf"
    DIVERSITY = "diversity"
    CROSS_ENCODER = "cross_encoder"
    # TF-IDF weighted token overlap blended with the retrieval score: the local
    # lexical reranker. It is also what an explicit cross-encoder / hosted rerank
    # degrades to when that backend fails — ``last_strategy_used`` then says
    # TFIDF (it used to keep saying cross_encoder / hosted) and
    # ``last_degraded_reason`` says why.
    TFIDF = "tfidf"
    # There is no "llm" strategy (a04-F073-01): it was accepted and ran the
    # cross-encoder. An LLM reranker on the retrieval path would make tenant-
    # billed model calls per search; until one exists the name is refused.
    # HOSTED calls the rerank chain: the Model Registry rerank models (preference
    # order), then the env/settings endpoints. Within the rerank budget; on any
    # failure it degrades to TF-IDF, flagged. The sync path runs it too (off an
    # event loop) — on an event-loop thread it cannot block, and says so.
    HOSTED = "hosted"
    # AUTO resolves the reranker (app.ai_router.resolve.resolve_reranker): the
    # registry/env hosted chain → the local cross-encoder → SCORE flagged
    # ``last_degraded_reason``. The choice is recorded (last_reason,
    # last_resolution).
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
# RAG_RERANK_MAX_LENGTH tokens (~4 characters each); this only bounds pathological
# input.
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
        max_rerank_candidates: int | None = None,
    ) -> None:
        self._strategy = strategy
        self._deduplicate = deduplicate
        self._min_score = min_score
        self._max_per_source = max_per_source
        self._calibration_method = calibration_method
        # Candidates the cross-encoder scores (retrieval order); the rest follow
        # them in retrieval order. None = RAG_RERANK_MAX_CANDIDATES; 0 = all.
        self._max_rerank_candidates = max_rerank_candidates
        # Observability of the retrieval-strategy decision. These are set on
        # every rerank() call so callers (and tests) can see which path ran,
        # why it was chosen, and the aggregate calibrated confidence.
        self.last_strategy_used: RerankStrategy | None = None
        self.last_reason: str = ""
        self.last_retrieval_confidence: float | None = None
        # Set when the requested reranker failed and a fallback ran instead
        # ("cross_encoder_error", "hosted_reranker_error", ...); None otherwise.
        self.last_degraded_reason: str | None = None
        # Set when the cross-encoder was deliberately not run (load shedding:
        # "busy", "budget_exceeded", "warming_up"): the chunks keep their
        # retrieval order. None otherwise.
        self.last_skipped_reason: str | None = None
        # What ``auto`` resolved to (model + source) on the last call.
        self.last_resolution: Resolution | None = None
        self._auto_choice: RerankerResolution | None = None

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

        # 3. Resolve + apply strategy (AUTO: hosted chain → cross-encoder → SCORE).
        self.last_degraded_reason = None
        self.last_skipped_reason = None
        effective, reason = self._resolve_strategy()
        self.last_strategy_used = effective
        self.last_reason = reason

        if effective == RerankStrategy.SCORE:
            filtered = sorted(filtered, key=lambda c: c.get("score", 0.0), reverse=True)
        elif effective == RerankStrategy.HOSTED:
            filtered = self._hosted_rerank_sync(filtered, query)
        elif effective == RerankStrategy.DIVERSITY:
            filtered = self._diversity_rerank(filtered, query_embedding=query_embedding)
        elif effective == RerankStrategy.RRF:
            filtered = rrf_fuse([filtered], k=60)
        elif effective == RerankStrategy.CROSS_ENCODER:
            filtered = self._cross_encoder_rerank(filtered, query)
        elif effective == RerankStrategy.TFIDF:
            filtered = self._tfidf_rerank(filtered, query)

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

    def _resolve_auto(self) -> RerankerResolution | None:
        """The reranker ``auto`` uses (registry → env endpoint → local → degraded)."""
        try:
            from app.ai_router.resolve import resolve_reranker

            choice = resolve_reranker()
        except Exception as exc:  # never block retrieval on the resolver
            logger.warning(
                "rerank_resolve_failed", error_type=type(exc).__name__, error=str(exc)[:200]
            )
            choice = None
        self._auto_choice = choice
        self.last_resolution = choice.resolution if choice is not None else None
        return choice

    def _resolve_strategy(self) -> tuple[RerankStrategy, str]:
        """Resolve the concrete strategy, recording why it was chosen.

        Only AUTO is dynamic: the resolver's hosted chain (registry rerank models
        in preference order, then the env/settings endpoint), else the local
        cross-encoder when its model is loaded, else SCORE — flagged
        (``last_degraded_reason``) when no reranker is configured at all.
        Everything else is explicit.
        """
        self._auto_choice = None
        if self._strategy is not RerankStrategy.AUTO:
            return self._strategy, f"explicit:{self._strategy.value}"
        choice = self._resolve_auto()
        if choice is not None and choice.tier == "hosted":
            return RerankStrategy.HOSTED, f"auto:{choice.resolution.source}"
        if choice is not None and choice.tier == "degraded":
            self._record_degraded("no_reranker_configured")
            return RerankStrategy.SCORE, "auto:no_reranker_configured"
        if self._cross_encoder_ready():
            return RerankStrategy.CROSS_ENCODER, "auto:cross_encoder_available"
        return (
            RerankStrategy.SCORE,
            "auto:cross_encoder_unavailable_fallback_score",
        )

    @staticmethod
    def _cross_encoder_ready() -> bool:
        try:
            from app.rag.cross_encoder import is_cross_encoder_available

            return is_cross_encoder_available()
        except Exception:  # pragma: no cover - defensive import guard
            return False

    def _record_degraded(self, reason: str, exc: BaseException | None = None) -> None:
        """Record, count and log that the requested reranker could not run."""
        from app.observability.metrics import RERANK_DEGRADED_TOTAL

        self.last_degraded_reason = reason
        RERANK_DEGRADED_TOTAL.labels(reason=reason).inc()
        logger.warning(
            "rerank_degraded",
            requested=self._strategy.value,
            reason=reason,
            error_type=type(exc).__name__ if exc is not None else None,
            error=str(exc)[:200] if exc is not None else None,
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

    def _ce_candidate_window(
        self, chunks: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split chunks (retrieval order) into the cross-encoded head and the rest.

        Cross-encoder cost grows linearly with the candidates; only the top
        ``max_rerank_candidates`` are scored. The tail keeps its retrieval order
        and score, behind the reranked head, marked ``rerank_beyond_window``.
        """
        limit = self._max_rerank_candidates
        if limit is None:
            from app.rag.rerank_budget import rerank_limits

            limit = rerank_limits().max_candidates
        if limit <= 0 or len(chunks) <= limit:
            return list(chunks), []
        tail = [{**chunk, "rerank_beyond_window": True} for chunk in chunks[limit:]]
        return list(chunks[:limit]), tail

    @staticmethod
    def _ce_documents(chunks: list[dict[str, Any]]) -> list[str]:
        # P1c-3: the model truncates at its token limit itself; a 512-character
        # cut dropped most of a normal chunk (a record's distinguishing field
        # often comes last). Capped only against pathological input.
        return [str(c.get("content", ""))[:_CE_MAX_INPUT_CHARS] for c in chunks]

    @staticmethod
    def _ce_blend(
        chunks: list[dict[str, Any]], scores: list[float], query: str
    ) -> list[dict[str, Any]]:
        """Blend cross-encoder probabilities with the retrieval score and sort."""
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

    def _skip_rerank(
        self,
        chunks: list[dict[str, Any]],
        skip: RerankSkipped,
        strategy: RerankStrategy = RerankStrategy.CROSS_ENCODER,
    ) -> list[dict[str, Any]]:
        """Keep the retrieval order because the cross-encoder was not run — visibly.

        Load shedding, not a failure: no fallback ranker runs (TF-IDF would be a
        worse answer dressed up as a ranking). Recorded (``last_skipped_reason``),
        counted (``agentverse_rerank_degraded_total{reason}``) and logged.
        """
        from app.observability.metrics import RERANK_DEGRADED_TOTAL

        self.last_skipped_reason = skip.reason
        self.last_reason = f"skipped:{skip.reason}"
        RERANK_DEGRADED_TOTAL.labels(reason=skip.reason).inc()
        logger.info(
            "rerank_skipped",
            strategy=strategy.value,
            reason=skip.reason,
            detail=skip.detail[:200],
            candidates=len(chunks),
        )
        return list(chunks)

    def _cross_encoder_rerank(
        self,
        chunks: list[dict[str, Any]],
        query: str,
    ) -> list[dict[str, Any]]:
        """Real cross-encoder reranking via sentence-transformers (synchronous path).

        Scores the top candidates on the bounded inference lane within the
        rerank budget. A skip (busy / budget / warming up) keeps the retrieval
        order; a model failure degrades to TF-IDF.
        """
        if not chunks:
            return chunks
        head, tail = self._ce_candidate_window(chunks)
        try:
            from app.rag.cross_encoder import cross_encode

            scores = cross_encode(query, self._ce_documents(head))
            return self._ce_blend(head, scores, query) + tail
        except RerankSkipped as skip:
            return self._skip_rerank(chunks, skip)
        except Exception as exc:
            # Cross-encoder inference failed (missing lib, load error, backend
            # crash). Degrade to the lexical TF-IDF reranker rather than
            # propagating — and say so: the results are TF-IDF ranked, counted
            # and logged (a04-F073-03: they used to stay labelled cross_encoder,
            # with no metric).
            return self._degrade_to_tfidf(chunks, query, "cross_encoder_error", exc)

    async def _cross_encoder_rerank_async(
        self,
        chunks: list[dict[str, Any]],
        query: str,
        budget_seconds: float | None,
    ) -> list[dict[str, Any]]:
        """Async cross-encoder reranking: awaits the bounded lane, parks no thread."""
        if not chunks:
            return chunks
        head, tail = self._ce_candidate_window(chunks)
        try:
            from app.rag.cross_encoder import cross_encode_async

            scores = await cross_encode_async(
                query, self._ce_documents(head), budget_seconds=budget_seconds
            )
            return self._ce_blend(head, scores, query) + tail
        except RerankSkipped as skip:
            return self._skip_rerank(chunks, skip)
        except Exception as exc:
            return self._degrade_to_tfidf(chunks, query, "cross_encoder_error", exc)

    async def rerank_async(
        self,
        chunks: list[dict[str, Any]],
        query: str,
        strategy: RerankStrategy | None = None,
        query_embedding: list[float] | None = None,
        budget_seconds: float | None = None,
        resolution: RerankerResolution | None = None,
    ) -> list[dict[str, Any]]:
        """Async reranking — the cross-encoder runs on its bounded inference lane.

        ``budget_seconds`` bounds the cross-encoder AND the hosted chain (None =
        the configured budget, capped by the retrieval deadline). For AUTO,
        ``resolution`` is a reranker already resolved by the caller.
        """
        s = strategy or RerankStrategy.CROSS_ENCODER

        if s in (
            RerankStrategy.CROSS_ENCODER,
            RerankStrategy.DIVERSITY,
            RerankStrategy.HOSTED,
        ):
            self.last_strategy_used = s
            self.last_reason = f"explicit:{s.value}"
            self.last_degraded_reason = None
            self.last_skipped_reason = None

        if s == RerankStrategy.CROSS_ENCODER:
            # RERANK-BOUNDED: awaits the bounded lane directly. It used to park a
            # default-executor thread per search in a per-call executor behind
            # the inference lock — an unbounded queue that blew the deadline.
            return await self._cross_encoder_rerank_async(chunks, query, budget_seconds)

        if s == RerankStrategy.DIVERSITY:
            return self._diversity_rerank(chunks, query_embedding=query_embedding)

        if s == RerankStrategy.HOSTED:
            out = await self._hosted_rerank(chunks, query, budget_seconds=budget_seconds)
            return out if out is not None else chunks

        if s == RerankStrategy.AUTO:
            return await self._auto_rerank_async(chunks, query, budget_seconds, resolution)

        return self.rerank(chunks, query=query)

    async def _auto_rerank_async(
        self,
        chunks: list[dict[str, Any]],
        query: str,
        budget_seconds: float | None,
        resolution: RerankerResolution | None,
    ) -> list[dict[str, Any]]:
        """AUTO: the hosted chain → the local cross-encoder → SCORE, one budget.

        A hosted answer that does not come within the budget keeps the retrieval
        order (``last_skipped_reason='budget_exceeded'``): there is no time left
        for another tier. A hosted FAILURE falls through to the local tier (or
        score order) with ``last_degraded_reason`` saying why.
        """
        import time

        from app.rag.rerank_budget import rerank_budget_seconds

        self.last_degraded_reason = None
        self.last_skipped_reason = None
        if resolution is not None:
            self._auto_choice = resolution
            self.last_resolution = resolution.resolution
            choice: RerankerResolution | None = resolution
        else:
            choice = self._resolve_auto()
        budget = rerank_budget_seconds() if budget_seconds is None else budget_seconds
        deadline = time.monotonic() + max(budget, 0.0)

        hosted_failure: str | None = None
        if choice is not None and choice.tier == "hosted":
            self.last_strategy_used = RerankStrategy.HOSTED
            self.last_reason = f"auto:{choice.resolution.source}"
            out = await self._hosted_rerank(
                chunks,
                query,
                reranker=choice.reranker,
                budget_seconds=max(deadline - time.monotonic(), 0.0),
                fallback=None,
            )
            if out is not None:
                return out
            hosted_failure = self.last_degraded_reason
            if not choice.local_available:
                return self._score_order(chunks)

        if choice is None or choice.tier == "local" or choice.local_available:
            self.last_strategy_used = RerankStrategy.CROSS_ENCODER
            self.last_reason = (
                f"auto:{hosted_failure}_fallback_cross_encoder"
                if hosted_failure
                else "auto:cross_encoder"
            )
            out = await self._cross_encoder_rerank_async(
                chunks, query, max(deadline - time.monotonic(), 0.0)
            )
            if hosted_failure and self.last_degraded_reason is None:
                self.last_degraded_reason = hosted_failure
            return out

        self.last_reason = "auto:no_reranker_configured"
        self._record_degraded("no_reranker_configured")
        return self._score_order(chunks)

    def _score_order(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.last_strategy_used = RerankStrategy.SCORE
        return sorted(chunks, key=lambda c: c.get("score", 0.0), reverse=True)

    def _hosted_rerank_sync(
        self, chunks: list[dict[str, Any]], query: str
    ) -> list[dict[str, Any]]:
        """HOSTED on the synchronous path, never silently replaced.

        Off an event loop the chain runs here (its own loop, within the rerank
        budget). On an event-loop thread a blocking HTTP call would stall every
        request on it, so the hosted call is NOT made and that is recorded
        (``hosted_reranker_needs_async``): ``auto`` goes on to its next tier,
        an explicit ``hosted`` degrades to TF-IDF — both flagged and counted.
        """
        from app.rag.cross_encoder import _on_event_loop_thread
        from app.rag.rerank_budget import rerank_budget_seconds

        choice = self._auto_choice
        auto = self._strategy is RerankStrategy.AUTO and choice is not None
        fallback = None if auto else "tfidf"
        if _on_event_loop_thread():
            if fallback == "tfidf":
                return self._degrade_to_tfidf(chunks, query, "hosted_reranker_needs_async")
            self._record_degraded("hosted_reranker_needs_async")
            out: list[dict[str, Any]] | None = None
        else:
            out = asyncio.run(
                self._hosted_rerank(
                    chunks,
                    query,
                    reranker=choice.reranker if auto and choice is not None else None,
                    budget_seconds=rerank_budget_seconds(),
                    fallback=fallback,
                )
            )
        if out is not None:
            return out
        # auto, hosted failed: the local tier, else score order (flagged).
        failure = self.last_degraded_reason
        if choice is not None and choice.local_available and self._cross_encoder_ready():
            self.last_strategy_used = RerankStrategy.CROSS_ENCODER
            self.last_reason = f"auto:{failure}_fallback_cross_encoder"
            ranked = self._cross_encoder_rerank(chunks, query)
            if self.last_degraded_reason is None:
                self.last_degraded_reason = failure
            return ranked
        self.last_reason = f"auto:{failure}_fallback_score"
        return self._score_order(chunks)

    async def _hosted_rerank(
        self,
        chunks: list[dict[str, Any]],
        query: str,
        *,
        reranker: Any = None,
        budget_seconds: float | None = None,
        fallback: str | None = "tfidf",
    ) -> list[dict[str, Any]] | None:
        """Rerank via the hosted chain within the rerank budget.

        Reorders the top candidates (``RAG_RERANK_MAX_CANDIDATES``, like the
        cross-encoder) by the endpoint's relevance score and reflects that score
        (preserving the pre-rerank score). No answer within the budget keeps the
        retrieval order (``last_skipped_reason='budget_exceeded'``). A failure
        degrades to TF-IDF (``fallback='tfidf'``) or returns None with
        ``last_degraded_reason`` set (``fallback=None``: the caller picks the
        next tier) — never drops results.
        """
        if not chunks:
            return []
        from app.rag.rerank_budget import rerank_budget_seconds

        budget = rerank_budget_seconds() if budget_seconds is None else budget_seconds
        head, tail = self._ce_candidate_window(chunks)
        try:
            if reranker is None:
                from app.core.config import get_settings
                from app.rag_platform.registry_reranker import reranker_chain_from_settings

                # Model Registry rerank models (preference order, each on its own
                # provider's API or endpoint), then the env endpoints.
                reranker = reranker_chain_from_settings(get_settings())
            if reranker is None:
                return self._hosted_failed(
                    chunks, query, "hosted_reranker_unconfigured", None, fallback
                )
            if budget <= 0:
                raise TimeoutError
            pairs = await asyncio.wait_for(
                reranker.rerank(query, self._ce_documents(head)), timeout=budget
            )
        except TimeoutError:
            return self._skip_rerank(
                chunks,
                RerankSkipped(
                    "budget_exceeded", "the hosted reranker did not answer within the budget"
                ),
                strategy=RerankStrategy.HOSTED,
            )
        except Exception as exc:
            return self._hosted_failed(chunks, query, "hosted_reranker_error", exc, fallback)

        if len(pairs) != len(head):
            # Endpoint returned a different count than sent → don't trust it.
            return self._hosted_failed(chunks, query, "hosted_reranker_incomplete", None, fallback)
        model = str(getattr(reranker, "last_model", "") or "")
        reordered: list[dict[str, Any]] = []
        for original_index, score in pairs:
            chunk = head[original_index]
            reordered.append(
                {
                    **chunk,
                    "score": float(score),
                    "pre_rerank_score": float(chunk.get("score", 0.0)),
                    "hosted_rerank_score": float(score),
                    "hosted_rerank_model": model,
                }
            )
        self.last_strategy_used = RerankStrategy.HOSTED
        return reordered + tail

    def _hosted_failed(
        self,
        chunks: list[dict[str, Any]],
        query: str,
        reason: str,
        exc: BaseException | None,
        fallback: str | None,
    ) -> list[dict[str, Any]] | None:
        if fallback == "tfidf":
            return self._degrade_to_tfidf(chunks, query, reason, exc)
        self._record_degraded(reason, exc)
        return None

    def _degrade_to_tfidf(
        self,
        chunks: list[dict[str, Any]],
        query: str,
        reason: str,
        exc: BaseException | None = None,
    ) -> list[dict[str, Any]]:
        """Rank with TF-IDF because the requested reranker failed — visibly.

        Records the strategy that actually ran (TFIDF) and why, counts the
        degradation (``agentverse_rerank_degraded_total{reason}``) and logs it.
        """
        from app.observability.metrics import RERANK_DEGRADED_TOTAL

        requested = self.last_strategy_used
        self.last_strategy_used = RerankStrategy.TFIDF
        self.last_reason = f"{reason}_fallback_tfidf"
        self.last_degraded_reason = reason
        RERANK_DEGRADED_TOTAL.labels(reason=reason).inc()
        logger.warning(
            "rerank_degraded_to_tfidf",
            requested=requested.value if requested is not None else None,
            reason=reason,
            error_type=type(exc).__name__ if exc is not None else None,
            error=str(exc)[:200] if exc is not None else None,
        )
        return self._tfidf_rerank(chunks, query)

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
