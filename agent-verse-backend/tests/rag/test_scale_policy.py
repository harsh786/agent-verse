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


class _FakeSession:
    def __init__(self, fail: bool = False) -> None:
        self.statements: list[str] = []
        self._fail = fail

    async def execute(self, statement, params=None):
        if self._fail:
            raise RuntimeError("SET failed")
        self.statements.append(str(statement))


async def test_apply_ef_search_sets_local_and_returns_ef() -> None:
    from app.rag.scale_policy import apply_ef_search

    s = _FakeSession()
    ef = await apply_ef_search(s, "high")
    assert ef == 128
    assert any("hnsw.ef_search = 128" in stmt for stmt in s.statements)


async def test_apply_ef_search_degrades_on_backend_error() -> None:
    from app.rag.scale_policy import apply_ef_search

    assert await apply_ef_search(_FakeSession(fail=True), "high") == 0
