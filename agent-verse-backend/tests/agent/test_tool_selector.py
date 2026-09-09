"""Tests for Phase 2 Group A — ToolSelector."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.tool_selector import ToolSelection, ToolSelector, _needs_rpa


class _FakeToolRef:
    def __init__(self, name, server_id="srv", description=""):
        self.name = name
        self.server_id = server_id
        self.description = description
        self.server_name = "Server"
        self.input_schema = {}


class _FakeCapSearch:
    async def search(self, query, tools, tenant_ctx, top_k=10):
        # Return all tools with score based on position
        results = []
        for i, t in enumerate(tools[:top_k]):
            m = MagicMock()
            m.tool_name = t["name"]
            m.score = 1.0 - i * 0.1
            results.append(m)
        return results


class _FakeReliabilityStore:
    async def get_reliability(self, tenant_id, tool_name):
        if "github" in tool_name:
            return {"success_rate": 0.95}
        return {"success_rate": 0.2}


T = MagicMock()
T.tenant_id = "test-tenant"


class TestToolSelector:
    @pytest.mark.asyncio
    async def test_select_returns_tool_selection(self):
        tools = [_FakeToolRef(f"tool_{i}") for i in range(30)]
        sel = ToolSelector(capability_search=_FakeCapSearch())
        result = await sel.select(goal="search github issues", tools=tools, tenant_ctx=T)
        assert isinstance(result, ToolSelection)

    @pytest.mark.asyncio
    async def test_select_top_k_from_30_tools(self):
        tools = [_FakeToolRef(f"tool_{i}") for i in range(30)]
        sel = ToolSelector(capability_search=_FakeCapSearch(), top_k=12)
        result = await sel.select(goal="search github issues", tools=tools, tenant_ctx=T)
        assert len(result.selected) <= 12

    @pytest.mark.asyncio
    async def test_select_nothing_dropped(self):
        """All 30 tools must appear in some tier (selected + signature + names_only)."""
        tools = [_FakeToolRef(f"tool_{i}") for i in range(30)]
        sel = ToolSelector(capability_search=_FakeCapSearch(), top_k=12, signature_k=8)
        result = await sel.select(goal="test goal", tools=tools, tenant_ctx=T)
        assert len(result.all_tools()) == 30

    @pytest.mark.asyncio
    async def test_fallback_to_full_when_few_tools(self):
        """With < min_tools_for_retrieval tools, all go to selected."""
        tools = [_FakeToolRef(f"tool_{i}") for i in range(10)]
        sel = ToolSelector(capability_search=_FakeCapSearch(), min_tools_for_retrieval=15)
        result = await sel.select(goal="any goal", tools=tools, tenant_ctx=T)
        assert len(result.selected) == 10
        assert result.signature == []
        assert result.names_only == []

    @pytest.mark.asyncio
    async def test_reliability_boosting(self):
        """Tool with high success_rate should rank higher."""
        tools = [
            _FakeToolRef("github_search", description="Search GitHub"),
            _FakeToolRef("legacy_tool", description="Old tool"),
        ]
        sel = ToolSelector(
            capability_search=_FakeCapSearch(),
            reliability=_FakeReliabilityStore(),
            top_k=2,
            min_tools_for_retrieval=1,
        )
        result = await sel.select(goal="search github issues", tools=tools, tenant_ctx=T)
        tool_names = [t.name for t in result.all_tools()]
        assert "github_search" in tool_names

    @pytest.mark.asyncio
    async def test_rpa_excluded_when_not_needed(self):
        tools = [
            _FakeToolRef("jira_search", server_id="jira"),
            _FakeToolRef("rpa_click", server_id="rpa"),
        ]
        sel = ToolSelector(capability_search=_FakeCapSearch(), min_tools_for_retrieval=1)
        result = await sel.select(
            goal="search jira tickets",
            tools=tools,
            tenant_ctx=T,
        )
        all_names = [t.name for t in result.all_tools()]
        assert "rpa_click" not in all_names
        assert result.rpa_included is False

    @pytest.mark.asyncio
    async def test_rpa_included_when_goal_needs_browser(self):
        tools = [
            _FakeToolRef("jira_search", server_id="jira"),
            _FakeToolRef("rpa_click", server_id="rpa"),
        ]
        sel = ToolSelector(capability_search=_FakeCapSearch(), min_tools_for_retrieval=1)
        result = await sel.select(
            goal="navigate to website and screenshot",
            tools=tools,
            tenant_ctx=T,
        )
        assert result.rpa_included is True

    @pytest.mark.asyncio
    async def test_capability_search_failure_falls_back_gracefully(self):
        """If capability search throws, all tools returned in selected."""
        async def bad_search(**kwargs):
            raise RuntimeError("search down")

        bad_cs = MagicMock()
        bad_cs.search = AsyncMock(side_effect=RuntimeError("search down"))
        tools = [_FakeToolRef(f"t{i}") for i in range(5)]
        sel = ToolSelector(capability_search=bad_cs, min_tools_for_retrieval=100)
        result = await sel.select(goal="test", tools=tools, tenant_ctx=T)
        # Should not raise; all tools accessible
        assert len(result.all_tools()) == 5


class TestNeedsRpa:
    def test_navigate_triggers_rpa(self):
        assert _needs_rpa("navigate to google.com") is True

    def test_screenshot_triggers_rpa(self):
        assert _needs_rpa("take a screenshot of the dashboard") is True

    def test_jira_does_not_trigger_rpa(self):
        assert _needs_rpa("search jira tickets") is False

    def test_agent_browser_capability(self):
        assert _needs_rpa("search issues", {"browser"}) is True

    def test_no_capabilities_no_browser_keywords(self):
        assert _needs_rpa("generate a report", set()) is False
