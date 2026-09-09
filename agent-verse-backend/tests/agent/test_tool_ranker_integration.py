# tests/agent/test_tool_ranker_integration.py
"""ToolRanker + ToolTrustStore must influence tool selection order."""
from __future__ import annotations

import pytest

from app.tool_runtime.tool_ranker import ToolRanker
from app.tool_runtime.tool_score import ToolScorer
from app.tool_runtime.tool_trust_store import ToolTrustStore


def test_tool_selection_uses_trust_scores():
    store = ToolTrustStore()
    # Reliable tool — 10/10 success
    for _ in range(10):
        store.record_outcome("jira.search_issues", success=True, latency_ms=200)
    # Unreliable — 3 failures
    for _ in range(3):
        store.record_outcome("github.search_issues", success=False, latency_ms=5000)
    for _ in range(7):
        store.record_outcome("github.search_issues", success=True, latency_ms=400)
    scorer = ToolScorer(trust_store=store)
    ranker = ToolRanker(scorer=scorer)
    ranked = ranker.rank(
        ["github.search_issues", "jira.search_issues"],
        goal_context="search for open issues",
    )
    assert ranked[0] == "jira.search_issues"  # must rank higher due to trust


def test_circuit_open_tool_ranked_last():
    store = ToolTrustStore()
    for _ in range(6):
        store.record_outcome("broken_tool", success=False, latency_ms=10000)
    for _ in range(5):
        store.record_outcome("good_tool", success=True, latency_ms=200)
    scorer = ToolScorer(trust_store=store)
    ranker = ToolRanker(scorer=scorer)
    ranked = ranker.rank(["broken_tool", "good_tool"], goal_context="any")
    assert ranked[-1] == "broken_tool"  # broken tool must be last
