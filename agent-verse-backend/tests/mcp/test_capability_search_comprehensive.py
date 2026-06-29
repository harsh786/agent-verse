"""Comprehensive tests for app/mcp/capability_search.py — adds coverage beyond test_capability_search.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.mcp.capability_search import CapabilitySearch, ToolMatch
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="cap-t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")

_SAMPLE_TOOLS = [
    {"server_id": "github", "name": "list_repos", "description": "List GitHub repositories"},
    {"server_id": "github", "name": "create_pr", "description": "Create a pull request on GitHub"},
    {"server_id": "jira", "name": "search_issues", "description": "Search for JIRA issues"},
    {"server_id": "slack", "name": "send_message", "description": "Send a message in Slack"},
    {"server_id": "datadog", "name": "get_metrics", "description": "Fetch Datadog metrics"},
]


# ── ToolMatch ─────────────────────────────────────────────────────────────────


def test_tool_match_to_dict() -> None:
    match = ToolMatch(
        server_id="github",
        tool_name="list_repos",
        description="List GitHub repos",
        score=0.876543,
    )
    d = match.to_dict()
    assert d["server_id"] == "github"
    assert d["tool_name"] == "list_repos"
    assert d["description"] == "List GitHub repos"
    # Score should be rounded to 4 decimal places
    assert d["score"] == round(0.876543, 4)


def test_tool_match_score_rounded_to_4_decimals() -> None:
    match = ToolMatch(server_id="s", tool_name="t", description="d", score=0.123456789)
    assert match.to_dict()["score"] == 0.1235


# ── CapabilitySearch._normalize_tools ────────────────────────────────────────


def test_normalize_tools_with_dicts() -> None:
    tools = [{"name": "t1", "description": "d1", "server_id": "s1"}]
    result = CapabilitySearch._normalize_tools(tools)
    assert result[0]["name"] == "t1"


def test_normalize_tools_with_objects() -> None:
    class FakeTool:
        name = "tool_x"
        description = "does something"
        server_id = "srv-y"
        input_schema = {"type": "object"}

    result = CapabilitySearch._normalize_tools([FakeTool()])
    assert result[0]["name"] == "tool_x"
    assert result[0]["description"] == "does something"
    assert result[0]["server_id"] == "srv-y"


def test_normalize_tools_mixed_list() -> None:
    class ObjTool:
        name = "obj_tool"
        description = "desc"
        server_id = "s"
        input_schema = {}

    tools = [
        {"name": "dict_tool", "description": "d", "server_id": "s"},
        ObjTool(),
    ]
    result = CapabilitySearch._normalize_tools(tools)
    assert len(result) == 2
    assert result[0]["name"] == "dict_tool"
    assert result[1]["name"] == "obj_tool"


# ── CapabilitySearch._cosine ──────────────────────────────────────────────────


def test_cosine_mismatched_lengths_returns_zero() -> None:
    assert CapabilitySearch._cosine([1.0, 2.0], [1.0]) == 0.0


def test_cosine_empty_vector_returns_zero() -> None:
    assert CapabilitySearch._cosine([], []) == 0.0


def test_cosine_similar_vectors() -> None:
    import math

    v = [1.0, 1.0, 0.0]
    w = [1.0, 0.5, 0.0]
    result = CapabilitySearch._cosine(v, w)
    # Both vectors have positive components in same direction
    assert result > 0.9


# ── CapabilitySearch._keyword_score ──────────────────────────────────────────


def test_keyword_score_exact_match() -> None:
    tool = {"name": "github", "description": "github repositories"}
    score = CapabilitySearch._keyword_score("github", tool)
    assert score > 0.0


def test_keyword_score_no_match_returns_zero() -> None:
    tool = {"name": "slack", "description": "send messages"}
    score = CapabilitySearch._keyword_score("kubernetes", tool)
    assert score == 0.0


def test_keyword_score_empty_query_returns_zero() -> None:
    tool = {"name": "tool", "description": "desc"}
    assert CapabilitySearch._keyword_score("", tool) == 0.0


def test_keyword_score_empty_tool_returns_zero() -> None:
    assert CapabilitySearch._keyword_score("query", {"name": "", "description": ""}) == 0.0


def test_keyword_score_partial_overlap() -> None:
    tool = {"name": "jira", "description": "create issues in jira project"}
    score = CapabilitySearch._keyword_score("create jira issue", tool)
    assert 0 < score < 1.0


# ── CapabilitySearch.search — keyword mode ────────────────────────────────────


async def test_search_keyword_returns_ranked_results() -> None:
    search = CapabilitySearch()
    results = await search.search("github repos", _SAMPLE_TOOLS, tenant_ctx=CTX)
    assert len(results) > 0
    assert all(isinstance(r, ToolMatch) for r in results)
    # Results should be sorted by score descending
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


async def test_search_keyword_top_k_respected() -> None:
    search = CapabilitySearch()
    results = await search.search("slack message", _SAMPLE_TOOLS, tenant_ctx=CTX, top_k=1)
    assert len(results) <= 1


async def test_search_keyword_threshold_filters_low_scores() -> None:
    search = CapabilitySearch()
    results = await search.search("xyzzy nonsense", _SAMPLE_TOOLS, tenant_ctx=CTX, threshold=0.5)
    assert all(r.score > 0.5 for r in results)


async def test_search_with_preloaded_tools() -> None:
    """Tools passed to __init__ are used when search() gets no explicit tools."""
    search = CapabilitySearch(tools=_SAMPLE_TOOLS)
    results = await search.search("github", tenant_ctx=CTX)
    assert any(r.server_id == "github" for r in results)


async def test_search_explicit_tools_override_init_tools() -> None:
    """Explicit tools arg to search() takes precedence over __init__ tools."""
    init_tools = [{"server_id": "init", "name": "init_tool", "description": "init tool"}]
    override_tools = [{"server_id": "override", "name": "new_tool", "description": "new tool"}]
    search = CapabilitySearch(tools=init_tools)
    results = await search.search("new tool", override_tools, tenant_ctx=CTX)
    assert all(r.server_id == "override" for r in results)


async def test_search_no_tools_in_init_or_call_returns_empty() -> None:
    search = CapabilitySearch()
    results = await search.search("anything", tenant_ctx=CTX)
    assert results == []


# ── CapabilitySearch.search — semantic mode ───────────────────────────────────


async def test_search_semantic_with_embedder() -> None:
    """When an embedder is available, semantic cosine scoring is used."""
    mock_embedder = MagicMock()
    mock_embedder.embed = AsyncMock()

    # Query embedding
    query_embedding = [1.0, 0.0, 0.0]
    # Tool embeddings: first tool is very similar, others are orthogonal
    tool_embeddings = [
        [0.99, 0.01, 0.0],   # github/list_repos — close match
        [0.0, 1.0, 0.0],     # github/create_pr — orthogonal
        [0.0, 0.0, 1.0],     # jira/search_issues — orthogonal
        [-1.0, 0.0, 0.0],    # slack/send_message — opposite
        [0.5, 0.5, 0.0],     # datadog/get_metrics — partial match
    ]

    call_count = 0

    async def mock_embed(request):
        nonlocal call_count
        call_count += 1
        from app.providers.base import EmbedResponse

        if len(request.texts) == 1:
            return EmbedResponse(embeddings=[query_embedding])
        return EmbedResponse(embeddings=tool_embeddings)

    mock_embedder.embed = mock_embed

    search = CapabilitySearch(embedder=mock_embedder)
    results = await search.search("repos", _SAMPLE_TOOLS, tenant_ctx=CTX, top_k=3)
    assert len(results) <= 3
    assert all(isinstance(r, ToolMatch) for r in results)
    # The closest match (first tool) should rank highest
    assert results[0].tool_name == "list_repos"


async def test_search_semantic_fallback_on_empty_embedding() -> None:
    """If embedder returns empty embedding, falls back to keyword search."""
    mock_embedder = MagicMock()

    async def mock_embed(request):
        from app.providers.base import EmbedResponse

        if len(request.texts) == 1:
            return EmbedResponse(embeddings=[])  # Empty = no query vector
        return EmbedResponse(embeddings=[])

    mock_embedder.embed = mock_embed
    search = CapabilitySearch(embedder=mock_embedder)

    # Should fall back to keyword search without raising
    results = await search.search("github repos", _SAMPLE_TOOLS, tenant_ctx=CTX)
    # Keyword fallback should still return some results
    assert isinstance(results, list)


async def test_search_result_has_correct_server_id() -> None:
    search = CapabilitySearch()
    tools = [{"server_id": "custom-srv", "name": "my_tool", "description": "custom tool"}]
    results = await search.search("custom tool", tools, tenant_ctx=CTX)
    assert results[0].server_id == "custom-srv"


async def test_search_result_to_dict_is_serializable() -> None:
    search = CapabilitySearch()
    results = await search.search("metrics", _SAMPLE_TOOLS, tenant_ctx=CTX)
    if results:
        d = results[0].to_dict()
        import json

        json.dumps(d)  # Must not raise
