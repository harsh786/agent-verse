"""ContextBudgetManager: dedup + token cap + step query relevance."""
from __future__ import annotations

import pytest
from app.rag.context_manager import ContextBudgetManager, ManagedContext


@pytest.fixture
def manager():
    return ContextBudgetManager(max_tokens=500)


@pytest.fixture
def chunks():
    return [
        {"chunk_id": "c1", "content": "Chunk one about orchestration.", "score": 0.9,
         "source_url": "https://docs.example.com/1"},
        {"chunk_id": "c2", "content": "Chunk two about agents.", "score": 0.8,
         "source_url": "https://docs.example.com/2"},
        {"chunk_id": "c3", "content": "Chunk one about orchestration.", "score": 0.7,  # duplicate
         "source_url": "https://docs.example.com/1"},
        {"chunk_id": "c4", "content": "Chunk four about memory.", "score": 0.6,
         "source_url": "https://docs.example.com/3"},
    ]


def test_dedup_removes_same_content(manager, chunks):
    result = manager.prepare(chunks, step_query="orchestration")
    contents = [c["content"] for c in result.chunks]
    assert len(contents) == len(set(contents))


def test_token_cap_applied(manager, chunks):
    result = manager.prepare(chunks, step_query="any")
    assert result.total_tokens <= manager.max_tokens


def test_ranking_by_step_query(manager, chunks):
    result = manager.prepare(chunks, step_query="orchestration")
    if result.chunks:
        assert "orchestration" in result.chunks[0]["content"].lower()


def test_at_least_one_chunk_returned(manager, chunks):
    result = manager.prepare(chunks, step_query="any")
    assert len(result.chunks) >= 1


def test_empty_input_returns_empty(manager):
    result = manager.prepare([], step_query="any")
    assert result.chunks == []
    assert result.total_tokens == 0


def test_managed_context_has_metadata(manager, chunks):
    result = manager.prepare(chunks, step_query="agents")
    assert isinstance(result, ManagedContext)
    assert result.total_tokens >= 0
    assert result.dedup_removed >= 0


def test_context_dedup_across_iterations(manager):
    chunks = [{"chunk_id": "c1", "content": "Same chunk", "score": 0.9}]
    manager.mark_seen("c1")
    result = manager.prepare(chunks, step_query="any")
    assert not any(c["chunk_id"] == "c1" for c in result.chunks)
