"""Deepened coverage for app/embedding/vector_index_policy.py.

Adds strategy-threshold boundary tests (EXACT/HNSW/IVF) and explicit
unsupported-dimension rejection tests, per the audit's finding that this
module previously had no tests at all.
"""
from __future__ import annotations

import pytest

from app.embedding.vector_index_policy import IndexStrategy, VectorIndexPolicy


@pytest.fixture
def policy() -> VectorIndexPolicy:
    return VectorIndexPolicy()


# ── select() strategy thresholds ──────────────────────────────────────────────


class TestIndexStrategyThresholds:
    def test_zero_collection_is_exact(self, policy: VectorIndexPolicy) -> None:
        assert policy.select(0, 1536) == IndexStrategy.EXACT

    def test_just_below_hnsw_threshold_is_exact(self, policy: VectorIndexPolicy) -> None:
        assert policy.select(999, 1536) == IndexStrategy.EXACT

    def test_at_hnsw_threshold_is_hnsw(self, policy: VectorIndexPolicy) -> None:
        """collection_size == 1000 crosses into HNSW (select uses '<', not '<=')."""
        assert policy.select(1000, 1536) == IndexStrategy.HNSW

    def test_just_above_hnsw_threshold_is_hnsw(self, policy: VectorIndexPolicy) -> None:
        assert policy.select(1001, 1536) == IndexStrategy.HNSW

    def test_just_below_ivf_threshold_is_hnsw(self, policy: VectorIndexPolicy) -> None:
        assert policy.select(99_999, 1536) == IndexStrategy.HNSW

    def test_at_ivf_threshold_is_ivf(self, policy: VectorIndexPolicy) -> None:
        """collection_size == 100_000 crosses into IVF."""
        assert policy.select(100_000, 1536) == IndexStrategy.IVF

    def test_well_above_ivf_threshold_is_ivf(self, policy: VectorIndexPolicy) -> None:
        assert policy.select(10_000_000, 1536) == IndexStrategy.IVF

    def test_strategy_choice_is_independent_of_dimension(
        self, policy: VectorIndexPolicy
    ) -> None:
        """The strategy only depends on collection_size; dimension is accepted
        but must not change the outcome for a fixed size."""
        for dim in (768, 1024, 1536, 3072, 99999):
            assert policy.select(50, dim) == IndexStrategy.EXACT
            assert policy.select(5_000, dim) == IndexStrategy.HNSW
            assert policy.select(500_000, dim) == IndexStrategy.IVF


# ── is_dimension_compatible() ─────────────────────────────────────────────────


class TestDimensionCompatible:
    def test_equal_dims_compatible(self, policy: VectorIndexPolicy) -> None:
        assert policy.is_dimension_compatible(1536, 1536) is True

    def test_different_dims_incompatible(self, policy: VectorIndexPolicy) -> None:
        assert policy.is_dimension_compatible(1536, 3072) is False

    def test_zero_vs_zero_is_compatible(self, policy: VectorIndexPolicy) -> None:
        assert policy.is_dimension_compatible(0, 0) is True


# ── is_supported_dimension() — explicit unsupported-dimension rejection ──────


class TestSupportedDimension:
    @pytest.mark.parametrize("dim", [768, 1024, 1536, 3072])
    def test_supported_dimensions_accepted(self, policy: VectorIndexPolicy, dim: int) -> None:
        assert policy.is_supported_dimension(dim) is True

    @pytest.mark.parametrize("dim", [0, -1, -1536, 1, 512, 100, 2048, 4096, 999_999])
    def test_unsupported_dimensions_rejected(self, policy: VectorIndexPolicy, dim: int) -> None:
        assert policy.is_supported_dimension(dim) is False

    def test_negative_dimension_explicitly_rejected(self, policy: VectorIndexPolicy) -> None:
        """A negative dimension is nonsensical for a vector column and must
        never be treated as supported."""
        assert policy.is_supported_dimension(-768) is False
