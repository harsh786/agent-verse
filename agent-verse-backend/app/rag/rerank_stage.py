"""Default-path reranking STAGE (WS-10 item 1).

Historically cross-encoder / MMR / ColBERT reranking fired only on explicit RAG
pattern branches (``colbert``, ``modular`` …). The *default* hybrid retrieval
path — ``app.rag.engine.retrieve`` fallthrough and the gateway ``HYBRID``/
``NAIVE`` strategies — returned raw fused scores with no reranking.

This module adds ONE reachable reranking stage, driven by the ONE reranking
registry (:class:`app.context.rerank_policy.RerankPolicy`, whose ``RerankStrategy``
enum is the registry of strategies: ``score``/``rrf``/``diversity``/
``cross_encoder``/``tfidf``/``hosted``/``auto``). Any strategy registered there becomes
selectable on the default path via config alone — no new call sites.

Design guarantees:
  * **Config-gated** — off leaves the path byte-for-byte unchanged.
  * **Honest passthrough** — when the reranker dependency is missing or the
    backend errors, results are returned in their original order, never dropped.
  * **``auto`` follows the Model Registry** — it resolves the reranker
    (:func:`app.ai_router.resolve.resolve_reranker`): the registry ``rerank``
    models in preference order, then the env/settings hosted endpoint
    (``RAG_HOSTED_RERANKER_URL``, ``ONPREM_RERANKER_URL``), then the local
    cross-encoder (``RAG_CROSS_ENCODER_MODEL``), then score order flagged
    ``rerank_degraded``. A hosted failure falls through to the next tier,
    flagged ``rerank_degraded: <reason>`` on every result.
  * **Additive** — reordering only; never fabricates scores. When a strategy
    computes a genuine relevance score (cross-encoder / TF-IDF fallback) the new
    blended score is reflected onto the result and the raw score is preserved in
    ``source_metadata['pre_rerank_score']`` for auditability.
  * **Bounded (RERANK-BOUNDED)** — the stage never takes longer than
    ``RAG_RERANK_BUDGET_MS`` (warm-up wait + queue wait + inference), capped by
    what is left of the retrieval deadline. When the cross-encoder is busy or
    out of budget the results keep their retrieval order, each flagged
    ``rerank_skipped: busy|budget_exceeded`` (like ``reranker_warming_up``) and
    counted in ``agentverse_rerank_degraded_total{reason}``. Only the top
    ``RAG_RERANK_MAX_CANDIDATES`` are cross-encoded; the rest follow them in
    retrieval order, flagged ``rerank_beyond_window``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from app.observability.logging import get_logger
from app.rag.rerank_budget import rerank_budget_seconds, rerank_limits

if TYPE_CHECKING:
    from app.ai_router.resolve import RerankerResolution
    from app.context.rerank_policy import RerankStrategy
    from app.rag.engine import RetrievalResult

logger = get_logger(__name__)

# Strategies that emit a genuine per-(query, passage) relevance score we should
# reflect onto the result (rather than a pure reordering).
_SCORING_STRATEGIES = frozenset({"cross_encoder", "tfidf", "hosted", "auto"})


def is_enabled(settings: Any) -> bool:
    """True when the default-path reranking stage is switched on in config."""
    return bool(getattr(settings, "rag_default_rerank_enabled", False))


async def _cross_encoder_status(settings: Any) -> str:
    """Readiness of the cross-encoder, waiting at most the warm-up wait.

    The wait is also capped by the rerank budget: the warm-up wait and the
    inference share ONE stage budget.
    """
    from app.rag.cross_encoder import ensure_default_cross_encoder_ready

    wait = float(getattr(settings, "rag_rerank_warmup_wait_seconds", 2.0))
    return await ensure_default_cross_encoder_ready(min(wait, rerank_budget_seconds(settings)))


def _flag_skipped(
    results: list[RetrievalResult], reason: str, top_k: int | None
) -> list[RetrievalResult]:
    """Retrieval order, nothing dropped, every result saying why it is unranked."""
    for result in results:
        result.source_metadata = {**(result.source_metadata or {}), "rerank_skipped": reason}
    return results[:top_k] if top_k else results


def _resolve_strategy(name: str) -> RerankStrategy:
    from app.context.rerank_policy import RerankStrategy

    try:
        return RerankStrategy(name.lower())
    except ValueError:
        # Settings refuses unknown names at load; a value that slipped past it
        # (a hand-built settings object) runs ``auto`` — loudly.
        logger.warning("default_rerank_unknown_strategy", strategy=name, used="auto")
        return RerankStrategy.AUTO


def _resolve_auto_reranker(settings: Any) -> RerankerResolution | None:
    """The reranker ``auto`` resolves to, or None (the local tier) when the
    resolver itself fails — loudly."""
    try:
        from app.ai_router.resolve import resolve_reranker

        return resolve_reranker(settings)
    except Exception as exc:
        logger.warning(
            "default_rerank_resolve_failed", error_type=type(exc).__name__, error=str(exc)[:200]
        )
        return None


async def apply_default_rerank(
    results: list[RetrievalResult],
    *,
    query: str,
    query_embedding: list[float] | None,
    settings: Any,
    top_k: int | None = None,
) -> list[RetrievalResult]:
    """Rerank ``results`` for the default retrieval path.

    Returns the reordered list. On any failure, or when disabled, returns the
    input order unchanged (honest passthrough).
    """
    if not results or not is_enabled(settings):
        return results

    strategy_name = str(getattr(settings, "rag_default_rerank_strategy", "auto")).lower()
    from app.context.rerank_policy import RerankPolicy, RerankStrategy

    strategy = _resolve_strategy(strategy_name)
    requested_auto = strategy is RerankStrategy.AUTO
    # ONE budget for the whole stage (warm-up wait + queue + inference, or the
    # hosted call), capped by the retrieval deadline.
    stage_deadline = time.monotonic() + rerank_budget_seconds(settings)
    resolution = _resolve_auto_reranker(settings) if requested_auto else None
    # RERANK-PRELOAD: the cross-encoder model is warmed in the background at
    # startup. Until it is loaded, a search waits for it only within a small
    # budget and then skips it honestly (marked on every result) — it used to
    # load the model inline (``auto`` even on the event loop) and so the first
    # search after a restart burned the whole retrieval deadline and 503'd.
    skipped_reason: str | None = None
    local_only = resolution is None or resolution.tier == "local"
    if strategy is RerankStrategy.CROSS_ENCODER or (
        strategy is RerankStrategy.AUTO and local_only
    ):
        status = await _cross_encoder_status(settings)
        if status == "ready":
            if strategy is RerankStrategy.AUTO:
                strategy = RerankStrategy.CROSS_ENCODER
        else:
            skipped_reason = (
                "reranker_warming_up" if status == "warming_up" else "reranker_unavailable"
            )
            from app.observability.metrics import RERANK_DEGRADED_TOTAL

            RERANK_DEGRADED_TOTAL.labels(reason=skipped_reason).inc()
            logger.info(
                "default_rerank_skipped", strategy=strategy.value, reason=skipped_reason
            )
            if strategy is not RerankStrategy.AUTO:
                # An explicitly requested cross-encoder is not silently replaced:
                # original order, flagged.
                return _flag_skipped(results, skipped_reason, top_k)
            # ``auto`` keeps its documented degradation: deterministic score order.
            strategy = RerankStrategy.SCORE
    # Pure reranker: no dedup, no min-score filter, no per-source cap, and no
    # calibration mutation — the stage's contract is ordering only.
    policy = RerankPolicy(
        strategy=strategy,
        deduplicate=False,
        min_score=0.0,
        max_per_source=0,
        calibration_method=None,
        max_rerank_candidates=rerank_limits(settings).max_candidates,
    )

    chunk_dicts: list[dict[str, Any]] = []
    for index, result in enumerate(results):
        meta = result.source_metadata or {}
        chunk: dict[str, Any] = {
            "_idx": index,
            "chunk_id": result.chunk_id,
            "content": result.content,
            "score": float(result.score),
            "source_url": meta.get("source_url", meta.get("source", "_")),
        }
        embedding = meta.get("embedding")
        if embedding:
            chunk["embedding"] = embedding
        chunk_dicts.append(chunk)

    try:
        # Async strategies (blocking cross-encoder / HTTP hosted reranker / the
        # resolved ``auto`` cascade) run via rerank_async; everything else on the
        # synchronous path.
        if strategy in (RerankStrategy.CROSS_ENCODER, RerankStrategy.HOSTED, RerankStrategy.AUTO):
            reranked = await policy.rerank_async(
                chunk_dicts,
                query=query,
                strategy=strategy,
                query_embedding=query_embedding,
                budget_seconds=max(stage_deadline - time.monotonic(), 0.0),
                resolution=resolution,
            )
        else:
            reranked = policy.rerank(chunk_dicts, query, query_embedding=query_embedding)
    except Exception as exc:
        # Honest passthrough, but visible: this used to be a DEBUG line only, so
        # a broken reranker silently degraded every retrieval.
        from app.observability.metrics import RERANK_DEGRADED_TOTAL

        RERANK_DEGRADED_TOTAL.labels(reason="rerank_error").inc()
        logger.warning(
            "default_rerank_failed_passthrough",
            strategy=strategy.value,
            error_type=type(exc).__name__,
            error=str(exc)[:200],
        )
        for result in results:
            result.source_metadata = {**(result.source_metadata or {}), "rerank_degraded": True}
        return results

    if policy.last_skipped_reason is not None:
        # RERANK-BOUNDED: the cross-encoder was busy / out of budget (the policy
        # counted and logged it). Never a long wait: an explicit cross-encoder
        # keeps the retrieval order, flagged; ``auto`` keeps its documented
        # degradation (deterministic score order, labelled ``score``), flagged —
        # exactly like the warm-up skip above.
        if not requested_auto:
            return _flag_skipped(results, policy.last_skipped_reason, top_k)
        by_score = sorted(results, key=lambda result: float(result.score), reverse=True)
        for result in by_score:
            result.source_metadata = {
                **(result.source_metadata or {}),
                "rerank_strategy": RerankStrategy.SCORE.value,
            }
        return _flag_skipped(by_score, policy.last_skipped_reason, top_k)

    effective = (policy.last_strategy_used or strategy).value
    # a04-F073-03: the requested reranker failed and TF-IDF ranked instead; the
    # policy counted it, every result says so.
    degraded_reason = policy.last_degraded_reason
    ordered: list[RetrievalResult] = []
    seen: set[int] = set()
    for chunk in reranked:
        idx = chunk.get("_idx")
        if not isinstance(idx, int) or not (0 <= idx < len(results)) or idx in seen:
            continue
        seen.add(idx)
        result = results[idx]
        result.source_metadata = {**(result.source_metadata or {}), "rerank_strategy": effective}
        if skipped_reason is not None:
            result.source_metadata["rerank_skipped"] = skipped_reason
        if degraded_reason is not None:
            result.source_metadata["rerank_degraded"] = degraded_reason
        if chunk.get("rerank_beyond_window"):
            result.source_metadata["rerank_beyond_window"] = True
        # Reflect a genuine reranker score when one was computed.
        new_score = chunk.get("score")
        ce_score = chunk.get("ce_score")
        if (
            effective in _SCORING_STRATEGIES
            and isinstance(new_score, int | float)
            and float(new_score) != result.score
        ):
            result.source_metadata["pre_rerank_score"] = result.score
            if ce_score is not None:
                result.source_metadata["ce_score"] = float(ce_score)
            result.score = float(new_score)
        ordered.append(result)

    # Safety net: never drop a result the reranker failed to echo back.
    for index, result in enumerate(results):
        if index not in seen:
            ordered.append(result)

    return ordered[:top_k] if top_k else ordered
