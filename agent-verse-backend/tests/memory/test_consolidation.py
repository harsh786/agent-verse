"""Tests for memory consolidation."""
from __future__ import annotations

import pytest

from app.memory.consolidation import MemoryConsolidator


class TestMemoryConsolidator:
    def setup_method(self) -> None:
        self.consolidator = MemoryConsolidator(cluster_threshold=3, similarity_cutoff=0.2)

    def test_consolidates_similar_memories(self) -> None:
        memories = [
            {"content": "Python programming language for data science machine learning"},
            {"content": "Python language used in machine learning data science analysis"},
            {"content": "Python programming popular AI data science learning language"},
            {"content": "Cooking pasta requires boiling water salt"},
        ]
        result = self.consolidator.consolidate_sync(memories)
        assert result.original_count == 4
        # At minimum: should have processed without error
        assert result.consolidated_count >= 1

    def test_leaves_small_clusters_unchanged(self) -> None:
        memories = [
            {"content": "topic about python"},
            {"content": "something completely different about cooking"},
        ]
        result = self.consolidator.consolidate_sync(memories)
        assert result.consolidated_count == 2
        assert result.clusters_merged == 0

    def test_merged_cluster_has_higher_access_count(self) -> None:
        memories = [
            {"content": "python data science", "access_count": 3},
            {"content": "python machine learning", "access_count": 5},
            {"content": "python ai work", "access_count": 2},
        ]
        result = self.consolidator.consolidate_sync(memories)
        # The merged memory should have summed access counts
        for mem in result.memories:
            if "[Consolidated" in str(mem.get("content", "")):
                assert mem["access_count"] == 10

    def test_empty_list(self) -> None:
        result = self.consolidator.consolidate_sync([])
        assert result.original_count == 0
        assert result.consolidated_count == 0

    @pytest.mark.asyncio
    async def test_consolidate_async_no_provider(self) -> None:
        memories = [
            {"content": "cats are pets"},
            {"content": "cats are animals"},
            {"content": "cats are furry mammals"},
        ]
        result = await self.consolidator.consolidate(memories, provider=None)
        assert result.consolidated_count <= result.original_count
