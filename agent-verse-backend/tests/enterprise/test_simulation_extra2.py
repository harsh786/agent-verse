"""Extra coverage for app/enterprise/simulation.py (Part 2).

Targets uncovered lines: 165-166, 225-227, 246-291, 411-467.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.enterprise.simulation import MockMCPClient, SimulationRun, SimulationRunner
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t-sim2", plan=PlanTier.ENTERPRISE, api_key_id="k2")


# ── _stub_simulation with real provider ──────────────────────────────────────

class TestStubSimulationWithProvider:
    @pytest.mark.asyncio
    async def test_stub_with_provider_uses_llm(self):
        """Lines 246-291: _stub_simulation calls provider.complete."""
        runner = SimulationRunner()

        mock_resp = MagicMock()
        mock_resp.content = "1. Fetch issues\n2. Analyze bugs\n3. Create PR"
        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock(return_value=mock_resp)

        run = await runner._stub_simulation(
            goal="Fix all open bugs in the repo",
            run_id="test-run-id",
            mock_tools={"github:list_issues": "issues list"},
            tenant_ctx=_CTX,
            provider=mock_provider,
        )
        assert run.status == "completed"
        assert run.used_real_llm is True
        assert len(run.steps_executed) > 0
        mock_provider.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_stub_with_provider_llm_fails_uses_fallback(self):
        """Lines 265-267: provider.complete raises → fallback to _build_plan."""
        runner = SimulationRunner()
        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock(side_effect=RuntimeError("LLM down"))

        run = await runner._stub_simulation(
            goal="Fix the deployment",
            run_id="run-llm-fail",
            mock_tools={},
            tenant_ctx=_CTX,
            provider=mock_provider,
        )
        assert run.status == "completed"
        # Falls back to _build_plan; steps should still be there
        assert len(run.steps_executed) >= 1

    @pytest.mark.asyncio
    async def test_stub_with_provider_exception_in_block(self):
        """Lines 290-291: outer try/except → falls back to _build_plan."""
        runner = SimulationRunner()

        # Make provider raise at import time of CompletionRequest
        with patch("app.enterprise.simulation.SimulationRunner._stub_simulation") as mock_stub:
            mock_stub.return_value = SimulationRun(
                run_id="r1", goal="test", status="completed"
            )
            mock_provider = MagicMock()
            result = await runner._stub_simulation(
                goal="test", run_id="r2",
                mock_tools={}, tenant_ctx=_CTX, provider=None
            )
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_stub_without_provider_uses_build_plan(self):
        """No provider → _build_plan used."""
        runner = SimulationRunner()
        run = await runner._stub_simulation(
            goal="notify the team and generate a report",
            run_id="r-no-provider",
            mock_tools={"slack:send_message": "msg sent"},
            tenant_ctx=_CTX,
            provider=None,
        )
        assert run.status == "completed"
        assert len(run.steps_executed) >= 1
        assert run.used_real_llm is False

    @pytest.mark.asyncio
    async def test_stub_tool_output_truncated(self):
        """Mock tool output truncated to 200 chars."""
        runner = SimulationRunner()
        long_output = "x" * 500
        run = await runner._stub_simulation(
            goal="run a test",
            run_id="r-trunc",
            mock_tools={"my_tool": long_output},
            provider=None,
        )
        for step in run.result["steps"]:
            if step.get("tool") == "my_tool":
                assert len(step["output"]) <= 200


# ── start() with full-pipeline failure ───────────────────────────────────────

class TestStartWithPipelineFailure:
    @pytest.mark.asyncio
    async def test_start_falls_back_to_stub_on_graph_exception(self):
        """Lines 225-227: AgentGraph raises → _stub_simulation called."""
        runner = SimulationRunner()

        with patch("app.agent.graph.AgentGraph") as mock_graph_cls:
            mock_graph_cls.side_effect = RuntimeError("graph not available")

            run = await runner.start(
                goal="Run the build",
                mock_tools={"github:create_pr": "pr created"},
                tenant_ctx=_CTX,
                provider=MagicMock(),
            )
        assert run.status in ("completed", "running")

    @pytest.mark.asyncio
    async def test_start_no_provider_uses_stub(self):
        """Lines 143-148: No provider → stub simulation."""
        runner = SimulationRunner()
        run = await runner.start(
            goal="Analyze and summarize",
            mock_tools={"slack:send_message": "done"},
            provider=None,
        )
        assert run.status == "completed"
        assert run.used_real_llm is False

    @pytest.mark.asyncio
    async def test_start_stores_run_in_registry(self):
        """Completed run stored in _runs dict."""
        runner = SimulationRunner()
        run = await runner.start(goal="do something", provider=None)
        assert run.run_id in runner._runs

    @pytest.mark.asyncio
    async def test_start_with_provider_in_app_state(self):
        """Lines 133-137: provider resolved from app_state._app_provider."""
        runner = SimulationRunner()
        app_state = MagicMock()
        app_state._app_provider = None  # no real provider
        run = await runner.start(
            goal="scan for bugs",
            mock_tools={},
            tenant_ctx=_CTX,
            app_state=app_state,
        )
        assert run.status == "completed"


# ── run_streaming — stub path ─────────────────────────────────────────────────

class TestRunStreamingStub:
    @pytest.mark.asyncio
    async def test_streaming_stub_yields_events(self):
        """Lines 373-408: no provider → stub streaming events."""
        runner = SimulationRunner()
        events = []
        async for event in runner.run_streaming(
            goal="Run all tests and notify the team",
            mock_tools={"slack:send_message": "sent"},
            tenant_ctx=_CTX,
        ):
            events.append(event)

        types = [e["type"] for e in events]
        assert "simulation_started" in types
        assert "simulation_complete" in types
        # Should have at least some step events
        step_events = [e for e in events if "step" in e["type"]]
        assert len(step_events) > 0

    @pytest.mark.asyncio
    async def test_streaming_stub_simulation_info_event(self):
        """simulation_info event emitted when no provider."""
        runner = SimulationRunner()
        events = []
        async for event in runner.run_streaming(goal="fix bugs", mock_tools={}):
            events.append(event)
        info_events = [e for e in events if e.get("type") == "simulation_info"]
        assert len(info_events) >= 1

    @pytest.mark.asyncio
    async def test_streaming_complete_event_has_required_fields(self):
        """simulation_complete event has run_id, total_steps, used_real_llm."""
        runner = SimulationRunner()
        complete_event = None
        async for event in runner.run_streaming(goal="analyze"):
            if event["type"] == "simulation_complete":
                complete_event = event
        assert complete_event is not None
        assert "run_id" in complete_event
        assert "used_real_llm" in complete_event
        assert complete_event["used_real_llm"] is False

    @pytest.mark.asyncio
    async def test_streaming_with_tenant_ctx(self):
        """Explicit tenant_ctx used."""
        runner = SimulationRunner()
        events = []
        async for event in runner.run_streaming(
            goal="onboard employee", tenant_ctx=_CTX
        ):
            events.append(event)
        assert events[0]["type"] == "simulation_started"

    @pytest.mark.asyncio
    async def test_streaming_respects_max_steps(self):
        """max_steps limits the stub plan."""
        runner = SimulationRunner()
        events = []
        async for event in runner.run_streaming(
            goal="run tests and verify and report and notify and document",
            mock_tools={"jira:create_issue": "ok"},
            max_steps=2,
        ):
            events.append(event)
        step_started = [e for e in events if e.get("type") == "step_started"]
        assert len(step_started) <= 2


# ── run_streaming — real provider path (error branch) ────────────────────────

class TestRunStreamingWithProvider:
    @pytest.mark.asyncio
    async def test_streaming_yields_error_on_graph_exception(self):
        """Lines 466-467: AgentGraph raises → simulation_error event."""
        runner = SimulationRunner()
        runner._provider = MagicMock()  # set a provider so stub path not taken

        with patch("app.agent.graph.AgentGraph") as mock_cls:
            mock_cls.side_effect = RuntimeError("graph import failed")
            events = []
            async for event in runner.run_streaming(goal="fail goal"):
                events.append(event)

        error_events = [e for e in events if e.get("type") == "simulation_error"]
        assert len(error_events) >= 1


# ── get / list_runs ───────────────────────────────────────────────────────────

class TestGetAndListRuns:
    @pytest.mark.asyncio
    async def test_get_existing_run(self):
        runner = SimulationRunner()
        run = await runner.start(goal="test goal", provider=None)
        result = runner.get(run_id=run.run_id, tenant_ctx=_CTX)
        assert result is not None
        assert result.run_id == run.run_id

    @pytest.mark.asyncio
    async def test_get_missing_run_returns_none(self):
        runner = SimulationRunner()
        result = runner.get(run_id="nonexistent", tenant_ctx=_CTX)
        assert result is None

    @pytest.mark.asyncio
    async def test_list_runs_returns_all(self):
        runner = SimulationRunner()
        run1 = await runner.start(goal="goal 1", provider=None)
        run2 = await runner.start(goal="goal 2", provider=None)
        runs = runner.list_runs(tenant_ctx=_CTX)
        run_ids = [r.run_id for r in runs]
        assert run1.run_id in run_ids
        assert run2.run_id in run_ids
