"""Extra coverage for app/enterprise/simulation.py — MockMCPClient and SimulationRunner."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.enterprise.simulation import MockMCPClient, SimulationRun, SimulationRunner
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t-sim", plan=PlanTier.ENTERPRISE, api_key_id="k1")


class TestMockMCPClient:
    def test_init_with_mock_tools(self):
        client = MockMCPClient(mock_tools={"jira.create_issue": "issue created"})
        assert "jira.create_issue" in client._mocks

    def test_init_with_mock_responses(self):
        client = MockMCPClient(mock_responses={"tool": "response"})
        assert "tool" in client._mocks

    def test_init_empty(self):
        client = MockMCPClient()
        assert client._mocks == {}

    @pytest.mark.asyncio
    async def test_discover_tools_returns_empty(self):
        client = MockMCPClient()
        result = await client.discover_tools("srv1", MagicMock())
        assert result == []

    @pytest.mark.asyncio
    async def test_discover_all_tools_returns_empty(self):
        client = MockMCPClient()
        result = await client.discover_all_tools(MagicMock())
        assert result == []

    @pytest.mark.asyncio
    async def test_call_tool_full_key_match(self):
        client = MockMCPClient(mock_tools={"myserver.my_tool": {"ok": True}})
        result = await client.call_tool("myserver", "my_tool", {}, MagicMock())
        assert result["simulated"] is True
        assert "ok" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_call_tool_short_key_match(self):
        client = MockMCPClient(mock_tools={"my_tool": "tool output"})
        result = await client.call_tool("server1", "my_tool", {}, MagicMock())
        assert result["simulated"] is True
        assert "tool output" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_call_tool_no_mock(self):
        client = MockMCPClient()
        result = await client.call_tool("s", "unknown_tool", {}, MagicMock())
        assert result["simulated"] is True
        assert "unknown_tool" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_call_tool_dict_mock_serialized_to_json(self):
        mock_data = {"field": "value", "count": 42}
        client = MockMCPClient(mock_tools={"srv.tool": mock_data})
        result = await client.call_tool("srv", "tool", {}, MagicMock())
        import json
        text = result["content"][0]["text"]
        parsed = json.loads(text)
        assert parsed["field"] == "value"

    @pytest.mark.asyncio
    async def test_call_tool_string_mock_returned_as_is(self):
        client = MockMCPClient(mock_tools={"srv.t": "plain text response"})
        result = await client.call_tool("srv", "t", {}, MagicMock())
        assert "plain text response" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_was_hit_true_for_called_tool(self):
        client = MockMCPClient(mock_tools={"srv.my_tool": "ok"})
        await client.call_tool("srv", "my_tool", {}, MagicMock())
        assert client.was_hit("my_tool") is True

    def test_was_hit_false_for_uncalled_tool(self):
        client = MockMCPClient()
        assert client.was_hit("never_called") is False

    def test_was_hit_none_returns_false(self):
        client = MockMCPClient()
        assert client.was_hit(None) is False

    @pytest.mark.asyncio
    async def test_was_hit_full_key_match(self):
        client = MockMCPClient(mock_tools={"s.t": "r"})
        await client.call_tool("s", "t", {}, MagicMock())
        assert client.was_hit("s.t") is True


class TestSimulationRun:
    def test_default_fields(self):
        run = SimulationRun()
        assert run.status == "pending"
        assert run.goal == ""
        assert run.run_id
        assert run.steps_executed == []
        assert run.cost_estimate == 0.0
        assert run.used_real_llm is False


class TestSimulationRunnerBuildPlan:
    def test_always_includes_analyse_step(self):
        runner = SimulationRunner()
        plan = runner._build_plan("fix bug", {})
        descriptions = [s["description"] for s in plan]
        assert any("nalyse" in d for d in descriptions)

    def test_includes_tool_steps(self):
        runner = SimulationRunner()
        plan = runner._build_plan("create issue", {"jira.create_issue": "ok"})
        tools = [s.get("tool") for s in plan]
        assert "jira.create_issue" in tools

    def test_adds_verify_step_for_test_goal(self):
        runner = SimulationRunner()
        plan = runner._build_plan("test the deployment", {})
        descriptions = [s["description"].lower() for s in plan]
        assert any("verif" in d for d in descriptions)

    def test_adds_report_step_for_summary_goal(self):
        runner = SimulationRunner()
        plan = runner._build_plan("generate a summary report", {})
        descriptions = [s["description"].lower() for s in plan]
        assert any("report" in d or "summary" in d for d in descriptions)

    def test_adds_notify_step_for_notify_goal(self):
        runner = SimulationRunner()
        plan = runner._build_plan("notify the team", {})
        descriptions = [s["description"].lower() for s in plan]
        assert any("notif" in d for d in descriptions)

    def test_always_ends_with_verify_goal(self):
        runner = SimulationRunner()
        plan = runner._build_plan("do something", {})
        assert len(plan) >= 2
        last = plan[-1]["description"].lower()
        assert "verif" in last or "goal" in last


class TestSimulationRunnerStart:
    @pytest.mark.asyncio
    async def test_stub_simulation_no_provider(self):
        runner = SimulationRunner()
        run = await runner.start(
            goal="list github issues",
            mock_tools={"github.list_issues": "issue list"},
            tenant_ctx=_CTX,
            provider=None,
        )
        assert run.status in ("completed", "complete", "running")
        assert run.goal == "list github issues"
        assert run.run_id

    @pytest.mark.asyncio
    async def test_stub_simulation_uses_mock_tools(self):
        runner = SimulationRunner()
        run = await runner.start(
            goal="create jira issue",
            mock_tools={"jira.create_issue": "PROJ-1 created"},
            tenant_ctx=_CTX,
        )
        assert run.mock_tools_used is not None

    @pytest.mark.asyncio
    async def test_result_has_required_keys(self):
        runner = SimulationRunner()
        run = await runner.start(goal="deploy app", tenant_ctx=_CTX)
        assert "goal" in run.result
        assert "status" in run.result
        assert "steps" in run.result

    @pytest.mark.asyncio
    async def test_tenant_ctx_created_if_none(self):
        """SimulationRunner creates a default tenant context when none provided."""
        runner = SimulationRunner()
        run = await runner.start(goal="test goal", mock_tools={}, tenant_ctx=None)
        assert run.status in ("completed", "complete", "running")

    @pytest.mark.asyncio
    async def test_stub_with_provider_llm_fails(self):
        """If LLM call fails, falls back to keyword-based planning."""
        runner = SimulationRunner()
        mock_provider = MagicMock()
        mock_provider.complete = AsyncMock(side_effect=RuntimeError("no model"))
        run = await runner.start(
            goal="analyze data",
            mock_tools={"datadog.get_metrics": "metric data"},
            tenant_ctx=_CTX,
            provider=mock_provider,
        )
        assert run.run_id


class TestSimulationRunnerGetAndList:
    @pytest.mark.asyncio
    async def test_get_returns_run_after_start(self):
        runner = SimulationRunner()
        run = await runner.start(goal="test", tenant_ctx=_CTX)
        retrieved = runner.get(run_id=run.run_id, tenant_ctx=_CTX)
        assert retrieved is not None
        assert retrieved.run_id == run.run_id

    @pytest.mark.asyncio
    async def test_get_returns_none_for_unknown(self):
        runner = SimulationRunner()
        result = runner.get(run_id="nonexistent", tenant_ctx=_CTX)
        assert result is None

    @pytest.mark.asyncio
    async def test_list_runs(self):
        runner = SimulationRunner()
        await runner.start(goal="r1", tenant_ctx=_CTX)
        await runner.start(goal="r2", tenant_ctx=_CTX)
        runs = runner.list_runs(tenant_ctx=_CTX)
        assert len(runs) >= 2


class TestSimulationRunnerStreaming:
    @pytest.mark.asyncio
    async def test_streaming_stub_yields_events(self):
        runner = SimulationRunner()
        # No provider → stub mode
        events = []
        async for evt in runner.run_streaming(goal="test goal", mock_tools={}):
            events.append(evt)
            if len(events) > 20:
                break

        types = [e.get("type") for e in events]
        assert "simulation_started" in types
        # Should end with simulation_complete
        assert any("simulation_complete" in t for t in types if t)

    @pytest.mark.asyncio
    async def test_streaming_with_mock_tools(self):
        runner = SimulationRunner()
        events = []
        async for evt in runner.run_streaming(
            goal="create jira issue and notify slack",
            mock_tools={"jira.create_issue": "done", "slack.send_message": "sent"},
            max_steps=5,
        ):
            events.append(evt)
            if len(events) > 30:
                break

        assert len(events) > 0
        assert events[0]["type"] == "simulation_started"
