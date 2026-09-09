"""Tests for ToolTrustStore + ToolScorer + ToolRanker — 7 tests."""
from __future__ import annotations

import pytest

from app.tool_runtime.tool_ranker import ToolRanker
from app.tool_runtime.tool_score import ToolScorer
from app.tool_runtime.tool_trust_store import ToolTrustStore


def _make_scorer() -> tuple[ToolTrustStore, ToolScorer]:
    store = ToolTrustStore()
    scorer = ToolScorer(store)
    return store, scorer


def test_trust_store_records_and_retrieves() -> None:
    store = ToolTrustStore()
    store.record_outcome("search", success=True, latency_ms=120.0)
    store.record_outcome("search", success=False, latency_ms=500.0)
    history = store.get_history("search")
    assert len(history) == 2
    assert store.has_tool("search")


def test_tool_scorer_unknown_tool_returns_neutral() -> None:
    _, scorer = _make_scorer()
    profile = scorer.score("unknown_tool")
    assert profile.trust_score == 0.5
    assert profile.circuit_state == "closed"
    assert profile.call_count == 0


def test_tool_scorer_all_successes_high_trust() -> None:
    store, scorer = _make_scorer()
    for _ in range(10):
        store.record_outcome("api_call", success=True, latency_ms=100.0)
    profile = scorer.score("api_call")
    assert profile.success_rate == 1.0
    assert profile.trust_score > 0.7
    assert profile.circuit_state == "closed"


def test_tool_scorer_circuit_opens_on_five_consecutive_failures() -> None:
    store, scorer = _make_scorer()
    # 3 successes then 5 failures
    for _ in range(3):
        store.record_outcome("flaky_tool", success=True, latency_ms=200.0)
    for _ in range(5):
        store.record_outcome("flaky_tool", success=False, latency_ms=200.0)
    profile = scorer.score("flaky_tool")
    assert profile.circuit_state == "open"
    assert profile.trust_score <= 0.2


def test_tool_scorer_p95_latency_computed() -> None:
    store, scorer = _make_scorer()
    # Record 20 calls with varying latencies
    for i in range(20):
        store.record_outcome("slow_tool", success=True, latency_ms=float(i * 100))
    profile = scorer.score("slow_tool")
    # p95 of 0..1900 should be around 1900
    assert profile.p95_latency_ms >= 1800.0


def test_tool_ranker_orders_by_trust() -> None:
    store = ToolTrustStore()
    # good_tool: all successes, low latency
    for _ in range(10):
        store.record_outcome("good_tool", success=True, latency_ms=50.0)
    # bad_tool: all failures
    for _ in range(10):
        store.record_outcome("bad_tool", success=False, latency_ms=5000.0)
    scorer = ToolScorer(store)
    ranker = ToolRanker(scorer)
    ranked = ranker.rank(["bad_tool", "good_tool"])
    assert ranked[0] == "good_tool"


def test_tool_ranker_uses_context_relevance() -> None:
    store = ToolTrustStore()
    scorer = ToolScorer(store)
    ranker = ToolRanker(scorer)
    # Both tools are unknown (equal trust_score=0.5)
    # "search_web" matches context "search web documents"
    ranked = ranker.rank(["database_query", "search_web"], goal_context="search web documents")
    assert ranked[0] == "search_web"
