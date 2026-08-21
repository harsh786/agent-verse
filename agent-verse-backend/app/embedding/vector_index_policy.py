"""VectorIndexPolicy — selects vector index strategy per collection size and dimension."""

from __future__ import annotations

import enum


class IndexStrategy(enum.StrEnum):
    EXACT = "exact"
    HNSW = "hnsw"
    IVF = "ivf"


_HNSW_THRESHOLD = 1_000
_IVF_THRESHOLD = 100_000
_SUPPORTED_DIMS = {768, 1024, 1536, 3072}


class VectorIndexPolicy:
    def select(self, collection_size: int, dimension: int) -> IndexStrategy:
        if collection_size < _HNSW_THRESHOLD:
            return IndexStrategy.EXACT
        elif collection_size < _IVF_THRESHOLD:
            return IndexStrategy.HNSW
        else:
            return IndexStrategy.IVF

    def is_dimension_compatible(self, old_dim: int, new_dim: int) -> bool:
        return old_dim == new_dim

    def is_supported_dimension(self, dimension: int) -> bool:
        return dimension in _SUPPORTED_DIMS
