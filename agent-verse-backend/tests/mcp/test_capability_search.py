"""Tests for CapabilitySearch — semantic and keyword tool matching."""
from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.mcp.capability_search import CapabilitySearch, ToolMatch
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tenant-test", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
_CTX2 = TenantContext(tenant_id="tenant-other", plan=PlanTier.FREE, api_key_id="k2")


def _tools(server_id: str = "jira") -> list[dict]:
    return [
        {"server_id": server_id, "name": "search_issues", "description": "search for jira issues"},
        {"server_id": server_id, "name": "create_issue", "description": "create a new jira issue"},
        {"server_id": server_id, "name": "get_issue", "description": "fetch details of a jira issue"},
        {"server_id": "github", "name": "list_repos", "description": "list github repositories"},
        {"server_id": "slack", "name": "send_message", "description": "send a message in slack"},
    ]


# ── empty inputs ──────────────────────────────────────────────────────────────


async def test_search_empty_tools_returns_empty() -> None:
    """Empty tool list must always yield empty results — with or without embedder."""
    search = CapabilitySearch()
    results = await search.search("find a jira issue", [], tenant_ctx=_CTX)
    assert results == []


async def test_search_empty_tools_with_embedder_returns_empty() -> None:
    """Even with an embedder, empty tools → empty results without calling embedder."""
    mock_embedder = MagicMock()
    mock_embedder.embed = AsyncMock()
    search = CapabilitySearch(embedder=mock_embedder)
    results = await search.search("find issue", [], tenant_ctx=_CTX)
    assert results == []
    mock_embedder.embed.assert_not_called()


# ── cosine similarity ─────────────────────────────────────────────────────────


def test_cosine_identical_vectors() -> None:
    """Cosine of identical vectors must be 1.0."""
    v = [1.0, 0.0, 0.0]
    assert CapabilitySearch._cosine(v, v) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors() -> None:
    """Cosine of orthogonal vectors must be 0.0."""
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert CapabilitySearch._cosine(a, b) == pytest.approx(0.0)


def test_cosine_zero_vector_returns_zero() -> None:
    """Zero-magnitude vector must return 0.0 without dividing by zero."""
    assert CapabilitySearch._cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_opposite_vectors() -> None:
    """Cosine of exactly opposite unit vectors is -1.0."""
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert CapabilitySearch._cosine(a, b) == pytest.approx(-1.0)


# ── keyword fallback ──────────────────────────────────────────────────────────


async def test_keyword_search_returns_relevant_results() -> None:
    """Keyword search must return tools whose name/description overlaps the query."""
    search = CapabilitySearch()  # no embedder → keyword mode
    tools = _tools()
    results = await search.search("search jira issues", tools, tenant_ctx=_CTX)
    assert len(results) > 0
    top = results[0]
    assert isinstance(top, ToolMatch)
    assert "search" in top.tool_name or "search" in top.description.lower()


async def test_keyword_search_respects_top_k() -> None:
    """top_k parameter must cap the number of results."""
    search = CapabilitySearch()
    results = await search.search("jira issue", _tools(), tenant_ctx=_CTX, top_k=2)
    assert len(results) <= 2


async def test_keyword_search_sorted_by_score() -> None:
    """Results must be sorted in descending score order."""
    search = CapabilitySearch()
    results = await search.search("jira issue", _tools(), tenant_ctx=_CTX)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


# ── tenant isolation ──────────────────────────────────────────────────────────


async def test_tenant_isolation_different_tools_per_tenant() -> None:
    """Results are scoped to the tools the caller supplies per tenant."""
    search = CapabilitySearch()

    # tenant-test gets jira tools
    jira_tools = [
        {"server_id": "jira", "name": "create_issue", "description": "create jira issue"}
    ]
    # tenant-other gets slack tools
    slack_tools = [
        {"server_id": "slack", "name": "send_message", "description": "send slack message"}
    ]

    results_a = await search.search("create jira issue", jira_tools, tenant_ctx=_CTX)
    results_b = await search.search("create jira issue", slack_tools, tenant_ctx=_CTX2)

    # Tenant-a gets a jira match; tenant-b gets no jira match
    assert any(r.server_id == "jira" for r in results_a)
    assert not any(r.server_id == "jira" for r in results_b)
