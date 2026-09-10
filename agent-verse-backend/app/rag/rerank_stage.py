"""Default-path reranking STAGE (WS-10 item 1).

Historically cross-encoder / MMR / ColBERT reranking fired only on explicit RAG
pattern branches (``colbert``, ``modular`` …). The *default* hybrid retrieval
path — ``app.rag.engine.retrieve`` fallthrough and the gateway ``HYBRID``/
``NAIVE`` strategies — returned raw fused scores with no reranking.

This module adds ONE reachable reranking stage, driven by the ONE reranking
registry (:class:`app.context.rerank_policy.RerankPolicy`, whose ``RerankStrategy``
enum is the registry of strategies: ``score``/``rrf``/``diversity``/
``cross_encoder``/``llm``/``auto``). Any strategy registered there becomes
selectable on the default path via config alone — no new call sites.

Design guarantees:
  * **Config-gated** — off leaves the path byte-for-byte unchanged.
  * **Honest passthrough** — when the reranker dependency is missing or the
    backend errors, results are returned in their original order, never dropped.
    ``auto`` degrades to ``score`` when the cross-encoder model is unavailable.
  * **Additive** — reordering only; never fabricates scores. When a strategy
    computes a genuine relevance score (cross-encoder / TF-IDF fallback) the new
    blended score is reflected onto the result and the raw score is preserved in
    ``source_metadata['pre_rerank_score']`` for auditability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.observability.logging import get_logger

if TYPE_CHECKING:
    from app.context.rerank_policy import RerankStrategy
    from app.rag.engine import RetrievalResult

logger = get_logger(__name__)

# Strategies that emit a genuine per-(query, passage) relevance score we should
# reflect onto the result (rather than a pure reordering).
_SCORING_STRATEGIES = frozenset({"cross_encoder", "llm", "hosted", "auto"})


def is_enabled(settings: Any) -> bool:
    """True when the default-path reranking stage is switched on in config."""
    return bool(getattr(settings, "rag_default_rerank_enabled", False))


def _resolve_strategy(name: str) -> RerankStrategy:
    from app.context.rerank_policy import RerankStrategy

    try:
        return RerankStrategy(name.lower())
    except ValueError:
        return RerankStrategy.AUTO


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
    # Pure reranker: no dedup, no min-score filter, no per-source cap, and no
    # calibration mutation — the stage's contract is ordering only.
    policy = RerankPolicy(
        strategy=strategy,
        deduplicate=False,
        min_score=0.0,
        max_per_source=0,
        calibration_method=None,
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
        # Async strategies (blocking cross-encoder / HTTP hosted reranker) run via
        # rerank_async; everything else on the synchronous path.
        if strategy in (RerankStrategy.CROSS_ENCODER, RerankStrategy.HOSTED):
            reranked = await policy.rerank_async(
                chunk_dicts,
                query=query,
                strategy=strategy,
                query_embedding=query_embedding,
            )
        else:
            reranked = policy.rerank(chunk_dicts, query, query_embedding=query_embedding)
    except Exception as exc:  # pragma: no cover - defensive; honest passthrough
        logger.debug("default_rerank_failed_passthrough", error=str(exc)[:120])
        return results

    effective = (policy.last_strategy_used or strategy).value
    ordered: list[RetrievalResult] = []
    seen: set[int] = set()
    for chunk in reranked:
        idx = chunk.get("_idx")
        if not isinstance(idx, int) or not (0 <= idx < len(results)) or idx in seen:
            continue
        seen.add(idx)
        result = results[idx]
        result.source_metadata = {**(result.source_metadata or {}), "rerank_strategy": effective}
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
