"""P1-6: ContextBudget.apply must not discard fitting chunks after an oversized one."""

from __future__ import annotations

from app.context.context_budget import ContextBudget


def test_oversized_chunk_does_not_discard_later_chunks():
    """FAILS TODAY: `break` on token overflow drops every chunk after the first oversized one."""
    budget = ContextBudget(max_tokens=100, max_chunks=10)
    chunks = [
        {"content": "a" * 40},  # ~10 tokens
        {"content": "b" * 8000},  # oversized (~2000 tokens)
        {"content": "c" * 40},  # ~10 tokens — must still be included
    ]
    result = budget.apply(chunks)
    contents = [c["content"][0] for c in result.included_chunks]
    assert "a" in contents
    assert "c" in contents, "later fitting chunks must not be dropped after an oversized one"
    assert "b" not in contents, "the oversized chunk itself must be skipped"


def test_max_chunks_still_hard_stops():
    """The max_chunks cap must remain a hard stop (not a skip)."""
    budget = ContextBudget(max_tokens=10_000, max_chunks=2)
    chunks = [{"content": "x" * 4} for _ in range(5)]
    result = budget.apply(chunks)
    assert len(result.included_chunks) == 2
