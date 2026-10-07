"""a10-F241-03/04/05, a10-F254-03: simulation outcome, LLM flag and cost are real.

* F241-04: any AgentGraph exception (budget refusal included) silently re-ran the
  goal through the keyword stub, which reported completed / "success (simulated)".
* F241-05: the stub's ``used_real_llm`` was ``provider is not None`` — True even
  when the planning completion failed and the keyword plan was used.
* F241-03 / F254-03: cost was fabricated as ``len(steps) * 0.001`` (and the stub
  stream emitted ``cost_increment: 0.001``); the real stream read a context key
  (``last_step_cost``) nothing ever set, so it was always 0.0.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.enterprise.simulation import SimulationRunner
from app.intelligence.cost_tracker import calculate_cost
from app.providers.base import CompletionResponse
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t-sim", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _RealLLM:
    async def complete(self, req: Any) -> Any:  # pragma: no cover - patched out
        raise AssertionError


class _RaisingGraph:
    def __init__(self, **_: Any) -> None:
        pass

    async def run(self, **_: Any) -> Any:
        raise RuntimeError("tenant daily budget exhausted")


def _graph_returning(result: Any) -> type:
    class _G:
        def __init__(self, **_: Any) -> None:
            pass

        async def run(self, **_: Any) -> Any:
            return result

    return _G


# ── F241-04 ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_exception_is_a_failed_run_not_a_stub_success() -> None:
    r = SimulationRunner()
    with patch("app.agent.graph.AgentGraph", _RaisingGraph):
        run = await r.start(
            goal="Fetch GitHub issues", mock_tools={"github:list_issues": "[]"},
            tenant_ctx=_CTX, provider=_RealLLM(),
        )
    assert run.status == "failed"
    assert run.result["status"] == "failed"
    assert run.result["outcome"] == "failed (simulated)"
    assert "budget exhausted" in run.result["error"]
    # The keyword stub's fabricated plan is not substituted.
    assert run.steps_executed == []
    assert run.result["steps"] == []
    assert run.used_real_llm is False
    # The failure is persisted like any other run.
    assert r.get(run_id=run.run_id, tenant_ctx=_CTX) is run


# ── F241-03 (full pipeline cost) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_pipeline_cost_is_the_metered_spend_not_steps_times_0001() -> None:
    steps = [SimpleNamespace(description=f"s{i}", tool="", output="ok") for i in range(3)]
    result = SimpleNamespace(
        status="complete", steps=steps, error_message="",
        context={"total_cost_usd": 0.01234},
    )
    r = SimulationRunner()
    with patch("app.agent.graph.AgentGraph", _graph_returning(result)):
        run = await r.start(goal="g", tenant_ctx=_CTX, provider=_RealLLM())
    assert run.status == "complete"
    assert run.cost_estimate == pytest.approx(0.01234)
    assert run.result["cost_usd"] == pytest.approx(0.01234)


@pytest.mark.asyncio
async def test_full_pipeline_with_no_metered_spend_reports_zero() -> None:
    steps = [SimpleNamespace(description=f"s{i}", tool="", output="ok") for i in range(4)]
    result = SimpleNamespace(status="complete", steps=steps, error_message="", context={})
    r = SimulationRunner()
    with patch("app.agent.graph.AgentGraph", _graph_returning(result)):
        run = await r.start(goal="g", tenant_ctx=_CTX, provider=_RealLLM())
    assert run.cost_estimate == 0.0
    assert run.result["cost_usd"] == 0.0


# ── F241-05 + F241-03 (stub) ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stub_failed_llm_plan_is_not_reported_as_real_llm() -> None:
    r = SimulationRunner()
    with patch(
        "app.providers.guarded_completion.complete_decision",
        AsyncMock(side_effect=RuntimeError("provider down")),
    ):
        run = await r._stub_simulation(
            goal="Send a Slack message", run_id="r1",
            mock_tools={"slack:send_message": "ok"}, tenant_ctx=_CTX, provider=_RealLLM(),
        )
    assert run.used_real_llm is False
    assert run.result["used_real_llm"] is False
    assert run.result["planner"] == "keyword_stub"
    assert run.cost_estimate == 0.0
    assert run.result["cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_stub_llm_plan_costs_the_one_planning_completion() -> None:
    resp = CompletionResponse(
        content="1. Search Jira issues for the sprint\n2. Summarise the findings",
        model="claude-sonnet-4-5", input_tokens=1200, output_tokens=300,
    )
    r = SimulationRunner()
    with patch(
        "app.providers.guarded_completion.complete_decision", AsyncMock(return_value=resp)
    ):
        run = await r._stub_simulation(
            goal="Summarise Jira", run_id="r2",
            mock_tools={"jira:search_issues": "[]"}, tenant_ctx=_CTX, provider=_RealLLM(),
        )
    expected = round(calculate_cost("claude-sonnet-4-5", 1200, 300), 6)
    assert run.used_real_llm is True
    assert run.result["planner"] == "llm"
    assert run.cost_estimate == pytest.approx(expected)
    assert run.result["cost_usd"] == pytest.approx(expected)


@pytest.mark.asyncio
async def test_keyword_stub_without_llm_costs_nothing() -> None:
    r = SimulationRunner()
    run = await r.start(
        goal="Check and report", mock_tools={"a": "1", "b": "2"}, tenant_ctx=_CTX
    )
    assert len(run.steps_executed) >= 3
    assert run.cost_estimate == 0.0
    assert run.result["cost_usd"] == 0.0
    assert run.result["planner"] == "keyword_stub"


# ── F254-03 (stream cost) ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stub_stream_emits_no_fabricated_cost() -> None:
    r = SimulationRunner()
    with patch("app.enterprise.simulation.asyncio.sleep", AsyncMock()):
        events = [e async for e in r.run_streaming(goal="g", mock_tools={"x": "y"})]
    completed = [e for e in events if e["type"] == "step_completed"]
    assert completed
    assert all(e["cost_increment"] == 0.0 for e in completed)
    assert events[-1]["type"] == "simulation_complete"
    assert events[-1]["total_cost"] == 0.0


@pytest.mark.asyncio
async def test_real_stream_cost_increments_are_deltas_of_the_metered_spend() -> None:
    final = SimpleNamespace(status="complete", context={"total_cost_usd": 0.05})

    class _CbGraph:
        def __init__(self, **kwargs: Any) -> None:
            self._cb = kwargs["step_callback"]

        async def run(self, **_: Any) -> Any:
            await self._cb("step_completed", {"tool_called": None, "total_cost_usd": 0.01})
            await self._cb("step_completed", {"tool_called": None, "total_cost_usd": 0.03})
            return final

    r = SimulationRunner()
    r._provider = _RealLLM()
    with patch("app.agent.graph.AgentGraph", _CbGraph):
        events = [e async for e in r.run_streaming(goal="g", tenant_ctx=_CTX)]
    completed = [e for e in events if e["type"] == "step_completed"]
    assert [e["cost_increment"] for e in completed] == [pytest.approx(0.01), pytest.approx(0.02)]
    assert [e["step_number"] for e in completed] == [1, 2]
    assert all("total_cost_usd" not in e for e in completed)
    done = events[-1]
    assert done["type"] == "simulation_complete"
    assert done["total_steps"] == 2
    # Spend metered after the last step (e.g. the verifier) is included.
    assert done["total_cost"] == pytest.approx(0.05)


def test_executor_step_callback_reports_the_metered_running_spend() -> None:
    """The executor's step_completed payload carries context["total_cost_usd"] (which
    llm_cost accumulates), not the never-set "last_step_cost"."""
    import inspect

    from app.agent.nodes import executor_mixin

    src = inspect.getsource(executor_mixin)
    assert 'get("last_step_cost"' not in src
    assert '"total_cost_usd": (' in src
