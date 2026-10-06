"""a04-F068-01: the ingestion-time indexing vocabulary is exactly what needs one.

``IndexingStrategy`` (POST /knowledge/collections/{id}/documents) accepts only
``raptor`` and ``agentic_chunking`` — and every other value is an honest 422.
That is not a gap: those are the only RAG strategies whose retrieval reads a
PRECOMPUTED per-collection index (RAPTOR summary tree, agentic-chunking
propositions). Every other strategy runs at query time over the base chunk
index. This test keeps the three lists in step, so a new precomputed-index
strategy cannot be added to the catalogue without an ingestion path.
"""

from __future__ import annotations

from typing import get_args

from app.api.knowledge import IndexingStrategy
from app.rag.catalogue import RAG_CAPABILITY_CATALOGUE, RAGRuntimeDependency
from app.rag.indexing import _SUPPORTED_INDEXING_STRATEGIES


def test_indexing_vocabulary_matches_the_strategies_that_need_a_precomputed_index() -> None:
    needs_index = {
        strategy.value
        for strategy, entry in RAG_CAPABILITY_CATALOGUE.items()
        if RAGRuntimeDependency.PRECOMPUTED_INDEX in entry.required_dependencies
    }
    assert set(get_args(IndexingStrategy)) == needs_index == {"raptor", "agentic_chunking"}
    assert {s.value for s in _SUPPORTED_INDEXING_STRATEGIES} == needs_index
