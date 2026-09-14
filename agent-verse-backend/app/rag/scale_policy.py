"""Retrieval scale policy — decisions that keep recall fast at 10M+ chunks.

Pure decision functions (no I/O) so they are cheap to test and reuse:

* whether to run the two-stage binary prefilter (coarse Hamming shortlist →
  exact cosine rerank) — worth its overhead only once a collection is large;
* how big the Stage-1 shortlist should be for a requested ``top_k``;
* the HNSW ``ef_search`` to use for a goal's precision need (higher = better
  recall, slower).

The store reads these instead of hard-coding thresholds, so scaling behaviour is
one tested place, not scattered magic numbers.
"""

from __future__ import annotations

from typing import Literal

Precision = Literal["low", "standard", "high", "max"]

# Above this many chunks, direct HNSW candidate scans get expensive enough that
# the binary prefilter's coarse shortlist pays for itself.
_DEFAULT_PREFILTER_THRESHOLD = 50_000

_EF_BY_PRECISION: dict[str, int] = {
    "low": 40,
    "standard": 64,
    "high": 128,
    "max": 256,
}


def should_use_binary_prefilter(
    collection_size: int,
    *,
    enabled: bool = True,
    prefilter_available: bool = True,
    threshold: int = _DEFAULT_PREFILTER_THRESHOLD,
) -> bool:
    """True when the two-stage binary prefilter should run for this collection.

    Requires the feature enabled, the binary index available (pgvector>=0.7 +
    migration 0120), and the collection large enough to benefit.
    """
    if not enabled or not prefilter_available:
        return False
    return collection_size >= threshold


def shortlist_size_for(top_k: int, *, multiplier: int = 40, cap: int = 1000) -> int:
    """Stage-1 shortlist size: enough candidates that Stage-2 exact rerank keeps
    recall high, bounded so the rerank stays cheap. Always >= top_k.
    """
    if top_k < 1:
        raise ValueError("top_k must be >= 1")
    return max(top_k, min(top_k * multiplier, cap))


def ef_search_for(precision: Precision | str, *, minimum: int = 16) -> int:
    """HNSW ef_search for a goal's precision tier (higher recall ⇒ higher ef)."""
    return max(minimum, _EF_BY_PRECISION.get(str(precision).lower(), _EF_BY_PRECISION["standard"]))
