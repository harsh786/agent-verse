"""Federated search across multiple knowledge collections.

Runs per-collection searches in parallel, normalises scores within each
collection (so results from collections with different embedding models are
comparable), deduplicates by content hash, and returns the global top-k.

Usage::

    from app.knowledge.federated_search import federated_search

    results = await federated_search(
        query="breach of contract",
        collection_ids=["uuid1", "uuid2"],
        gateway=retrieval_gateway,
        strategy="hybrid",
        top_k=10,
        tenant_ctx=tenant_ctx,
    )
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from app.rag.contracts import RAGExecutionResult, RAGStrategy
from app.tenancy.context import TenantContext

__all__ = ["federated_search"]


def _normalize_scores(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Min-max normalise ``score`` within a single collection result list.

    When *results* has fewer than two entries, every score is set to 1.0 so
    the single/zero result is not discarded by downstream min-score filters.
    """
    if not results:
        return results
    if len(results) == 1:
        results[0]["normalized_score"] = 1.0
        return results

    scores = [r.get("score", 0.0) for r in results]
    min_s = min(scores)
    max_s = max(scores)
    score_range = max_s - min_s if max_s != min_s else 1.0

    for r in results:
        raw = r.get("score", 0.0)
        r["normalized_score"] = (raw - min_s) / score_range

    return results


def _content_key(result: dict[str, Any]) -> str:
    """Stable deduplication key — content_hash preferred, SHA-256 fallback."""
    if result.get("content_hash"):
        return str(result["content_hash"])
    content = result.get("content", "")
    return hashlib.sha256(content[:512].encode()).hexdigest()


def _merge_unique_dicts(
    first: list[dict[str, Any]],
    second: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_value = {
        json.dumps(item, sort_keys=True, separators=(",", ":")): item
        for item in [*first, *second]
    }
    return [by_value[key] for key in sorted(by_value)]


async def federated_search(
    query: str,
    collection_ids: list[str],
    gateway: Any,
    top_k: int = 10,
    *,
    tenant_ctx: TenantContext,
    strategy: str | RAGStrategy = RAGStrategy.HYBRID,
    filters: dict[str, Any] | None = None,
    per_collection_k: int | None = None,
) -> list[dict[str, Any]]:
    """Search *query* across every collection in *collection_ids* in parallel.

    Scores from different collections may use different embedding models and
    score scales.  This function normalises each collection's result set to
    [0, 1] before merging so that a high-relevance result from a 768-dim
    Voyage collection is directly comparable to one from a 1536-dim OpenAI
    collection.

    Args:
        query: Natural-language search query.
        collection_ids: UUIDs of collections to search.
        gateway: The tenant-aware retrieval gateway.
        top_k: Final number of results to return after merging.
        per_collection_k: How many results to fetch per collection before
            merging (defaults to ``top_k * 2`` for better recall).

    Returns:
        Deduplicated results sorted by ``normalized_score`` descending,
        truncated to ``top_k``.  Each result dict carries both the original
        ``score`` (from the provider) and the ``normalized_score``.
    """
    if not collection_ids:
        return []

    fetch_k = per_collection_k if per_collection_k is not None else top_k * 2

    # ------------------------------------------------------------------ #
    # Parallel fetch — one coroutine per collection                       #
    # ------------------------------------------------------------------ #
    async def _search_one(cid: str) -> list[dict[str, Any]]:
        result = await gateway.execute(
            tenant_ctx,
            query=query,
            collection_id=cid,
            strategy_id=strategy,
            top_k=fetch_k,
            filters=filters or {},
        )
        if not isinstance(result, RAGExecutionResult):
            raise TypeError("Retrieval gateway must return RAGExecutionResult")
        retrieval_legs = [leg.model_dump(mode="json") for leg in result.retrieval_legs]
        strategy_trace = [trace.model_dump(mode="json") for trace in result.strategy_trace]
        return [
            {
                "collection_id": cid,
                "citation_id": f"{cid}:{citation.citation_id}",
                "original_citation_id": citation.citation_id,
                "chunk_id": citation.chunk_id,
                "content": citation.content,
                "score": citation.score,
                "source": citation.source,
                "content_hash": citation.metadata.get("content_hash"),
                "metadata": dict(citation.metadata),
                "requested_strategy_id": result.requested_strategy_id,
                "resolved_strategy_id": result.resolved_strategy_id.value,
                "retrieval_legs": retrieval_legs,
                "strategy_trace": strategy_trace,
            }
            for citation in result.citations
        ]

    per_collection: list[list[dict[str, Any]]] = list(
        await asyncio.gather(*[_search_one(cid) for cid in collection_ids])
    )

    # ------------------------------------------------------------------ #
    # Normalise + flatten                                                 #
    # ------------------------------------------------------------------ #
    all_results: list[dict[str, Any]] = []
    for _cid, results in zip(collection_ids, per_collection, strict=False):
        if not results:
            continue
        normalised = _normalize_scores(list(results))  # operates on copies
        all_results.extend(normalised)

    # ------------------------------------------------------------------ #
    # Sort by normalised score (descending)                               #
    # ------------------------------------------------------------------ #
    all_results.sort(
        key=lambda result: (
            -float(result.get("normalized_score", 0.0)),
            -float(result.get("score", 0.0)),
            _content_key(result),
            str(result.get("citation_id", "")),
        )
    )

    # ------------------------------------------------------------------ #
    # Deduplicate by content                                              #
    # ------------------------------------------------------------------ #
    merged_by_content: dict[str, dict[str, Any]] = {}
    for result in all_results:
        key = _content_key(result)
        existing = merged_by_content.get(key)
        if existing is None:
            result["collection_ids"] = [str(result["collection_id"])]
            result["sources"] = [str(result["source"])]
            result["citation_refs"] = [str(result["citation_id"])]
            merged_by_content[key] = result
            continue
        existing["collection_ids"] = sorted(
            {*existing["collection_ids"], str(result["collection_id"])}
        )
        existing["sources"] = sorted(
            {*existing["sources"], str(result["source"])}
        )
        existing["citation_refs"] = sorted(
            {*existing["citation_refs"], str(result["citation_id"])}
        )
        existing["retrieval_legs"] = _merge_unique_dicts(
            list(existing["retrieval_legs"]),
            list(result["retrieval_legs"]),
        )
        existing["strategy_trace"] = _merge_unique_dicts(
            list(existing["strategy_trace"]),
            list(result["strategy_trace"]),
        )

    return list(merged_by_content.values())[:top_k]
