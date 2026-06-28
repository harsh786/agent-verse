"""Federated search across multiple knowledge collections.

Runs per-collection searches in parallel, normalises scores within each
collection (so results from collections with different embedding models are
comparable), deduplicates by content hash, and returns the global top-k.

Usage::

    from app.knowledge.federated_search import federated_search

    results = await federated_search(
        query="breach of contract",
        collection_ids=["uuid1", "uuid2"],
        store=knowledge_store,
        top_k=10,
    )
"""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

__all__ = ["federated_search"]


def _normalize_scores(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Min-max normalise ``score`` within a single collection result list.

    When *results* has fewer than two entries, every score is set to 1.0 so
    the single/zero result is not discarded by downstream min-score filters.
    """
    if not results:
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


async def federated_search(
    query: str,
    collection_ids: list[str],
    store: Any,
    top_k: int = 10,
    *,
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
        store: A ``KnowledgeStore`` instance (or any object with a
            ``search(query, collection_id, top_k)`` coroutine).
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
        try:
            return await store.search(query, cid, top_k=fetch_k)
        except Exception as exc:
            _log.warning(
                "federated_search_collection_error",
                collection_id=cid,
                error=str(exc),
            )
            return []

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
    all_results.sort(key=lambda r: r.get("normalized_score", 0.0), reverse=True)

    # ------------------------------------------------------------------ #
    # Deduplicate by content                                              #
    # ------------------------------------------------------------------ #
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in all_results:
        key = _content_key(r)
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    return deduped[:top_k]
