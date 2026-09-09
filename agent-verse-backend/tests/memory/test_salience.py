"""Tests for memory salience scoring and decay."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.memory.salience import SalienceScorer, rank_memories


class TestSalienceScorer:
    def setup_method(self) -> None:
        self.scorer = SalienceScorer()

    def test_higher_score_for_recent_memory(self) -> None:
        now = datetime.now(UTC)
        recent = self.scorer.score("cats and dogs", "cats", last_accessed_at=now)
        old = self.scorer.score("cats and dogs", "cats", last_accessed_at=now - timedelta(days=30))
        assert recent > old

    def test_higher_score_for_relevant_query(self) -> None:
        now = datetime.now(UTC)
        relevant = self.scorer.score("python programming language", "python language", last_accessed_at=now)
        irrelevant = self.scorer.score("python programming language", "cooking recipes", last_accessed_at=now)
        assert relevant > irrelevant

    def test_higher_score_for_frequent_access(self) -> None:
        now = datetime.now(UTC)
        freq = self.scorer.score("content", "query", access_count=100, last_accessed_at=now)
        rare = self.scorer.score("content", "query", access_count=1, last_accessed_at=now)
        assert freq > rare

    def test_score_in_range(self) -> None:
        now = datetime.now(UTC)
        s = self.scorer.score("any text", "any query", last_accessed_at=now)
        assert 0.0 <= s <= 1.0

    def test_apply_decay_reduces_score(self) -> None:
        initial = 1.0
        decayed = self.scorer.apply_decay(initial, hours_since_last_use=168)  # one half-life
        assert decayed < initial
        assert decayed == pytest.approx(0.5, abs=0.01)

    def test_rank_memories_returns_top_k(self) -> None:
        mems = [
            {"content": f"memory {i}", "access_count": i, "last_accessed_at": None}
            for i in range(20)
        ]
        ranked = rank_memories(mems, "memory", top_k=5)
        assert len(ranked) == 5

    def test_rank_memories_sorted_by_salience(self) -> None:
        now = datetime.now(UTC)
        mems = [
            {"content": "python code", "access_count": 10, "last_accessed_at": now},
            {"content": "cooking recipes", "access_count": 1, "last_accessed_at": now - timedelta(days=7)},
        ]
        ranked = rank_memories(mems, "python", top_k=2)
        # Python-related memory should rank higher
        assert "python" in ranked[0]["content"].lower()
