"""Million-doc RAG — scale-decision policy (pure)."""
from __future__ import annotations

import pytest

from app.rag.scale_policy import (
    ef_search_for,
    shortlist_size_for,
    should_use_binary_prefilter,
)


def test_prefilter_on_only_for_large_collections() -> None:
    assert should_use_binary_prefilter(10_000) is False  # small → direct HNSW
    assert should_use_binary_prefilter(50_000) is True  # at threshold
    assert should_use_binary_prefilter(5_000_000) is True


def test_prefilter_respects_feature_flags() -> None:
    assert should_use_binary_prefilter(10_000_000, enabled=False) is False
    assert should_use_binary_prefilter(10_000_000, prefilter_available=False) is False


def test_shortlist_size_bounds() -> None:
    assert shortlist_size_for(5) == 200  # 5 * 40
    assert shortlist_size_for(100) == 1000  # capped
    assert shortlist_size_for(2000) == 2000  # never below top_k
    with pytest.raises(ValueError):
        shortlist_size_for(0)


def test_ef_search_scales_with_precision() -> None:
    assert ef_search_for("low") < ef_search_for("standard") < ef_search_for("high")
    assert ef_search_for("max") == 256
    assert ef_search_for("unknown") == ef_search_for("standard")  # safe default
