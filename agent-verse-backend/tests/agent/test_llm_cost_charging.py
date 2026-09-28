"""Every LLM call must reach the cost path (audit item 5).

Before: planner / verifier / reasoning spend logged ``cost=0.0`` and was never charged
to the budget; streamed executor calls carried no ``usage`` so tool-less steps skipped
the ledger; the distributed strategy tier sent ``model='fake-model'`` to real providers.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.graph import AgentGraph
from app.providers.base import CompletionRequest, CompletionResponse, Message
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.asyncio

T = TenantContext(tenant_id="cost-charge-t", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _RecordingController:
    def __init__(self, allow: bool = True) -> None:
        self.charges: list[float] = []
        self._allow = allow

    async def check_and_record(self, *, goal_id: str, cost_usd: float, tenant_ctx: Any, **_: Any):
        self.charges.append(cost_usd)
        return self._allow

    async def get_cost_tier(self, *_a: Any, **_k: Any) -> str:
        return "premium"


async def test_planner_verifier_and_executor_calls_all_reach_the_ledger() -> None:
    provider = FakeProvider(
        responses=['{"steps": ["answer the question"]}', "the answer", '{"success": true}']
    )
    tracker = SimpleNamespace(record_llm_usage=AsyncMock(return_value=0.0))
    controller = _RecordingController()
    graph = AgentGraph(
        planner=provider,
        executor=provider,
        verifier=provider,
        cost_controller=controller,
        cost_tracker=tracker,
    )

    state = await graph.run(goal="answer the question", tenant_ctx=T)

    roles = [c.kwargs["role"] for c in tracker.record_llm_usage.await_args_list]
    assert "planner" in roles
    assert "verifier" in roles
    assert "executor" in roles
    # planner + executor + verifier are all charged to the budget, not just the executor
    assert len(controller.charges) >= 3
    assert all(c > 0.0 for c in controller.charges)
    assert state.context["total_cost_usd"] == pytest.approx(
        sum(controller.charges), rel=0.5
    )


async def test_planner_spend_denied_latches_budget_exhausted() -> None:
    from app.agent.nodes.llm_cost import charge_llm_call
    from app.agent.state import AgentState

    graph = SimpleNamespace(_cost_controller=_RecordingController(allow=False))
    state = AgentState(goal="g", tenant_ctx=T)
    resp = CompletionResponse(content="x", model="gpt-4o", input_tokens=100, output_tokens=50)

    cost = await charge_llm_call(
        graph, resp=resp, role="planner", model="gpt-4o", agent_state=state, tenant_ctx=T
    )

    assert cost > 0.0
    assert state.context["_budget_exhausted"] is True


async def test_reasoning_pattern_calls_are_charged() -> None:
    from app.agent.nodes.llm_cost import ChargingProvider
    from app.agent.state import AgentState

    controller = _RecordingController()
    graph = SimpleNamespace(_cost_controller=controller)
    state = AgentState(goal="g", tenant_ctx=T)
    wrapped = ChargingProvider(
        FakeProvider(responses=["thought"]),
        graph=graph,
        role="tree_of_thoughts",
        agent_state=state,
        tenant_ctx=T,
    )

    await wrapped.complete(
        CompletionRequest(messages=[Message(role="user", content="hi")], model="gpt-4o")
    )

    assert len(controller.charges) == 1
    assert state.context["total_cost_usd"] > 0.0


async def test_tool_less_streamed_executor_step_reaches_ledger_without_usage() -> None:
    """FakeProvider.stream_tokens returns token totals but no ``usage`` object."""
    tracker = SimpleNamespace(record_llm_usage=AsyncMock(return_value=0.0))
    graph = AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=FakeProvider(responses=["streamed answer with several words"]),
        verifier=FakeProvider(responses=['{"success": true}']),
        cost_tracker=tracker,
    )
    from app.agent.state import AgentState, StepResult, StepStatus

    state = AgentState(goal="g", tenant_ctx=T)
    state.steps.append(StepResult(description="answer", status=StepStatus.RUNNING))

    await graph._execute_step("answer", state, T)

    roles = [c.kwargs["role"] for c in tracker.record_llm_usage.await_args_list]
    assert roles == ["executor"]


async def test_openai_stream_tokens_requests_and_parses_usage() -> None:
    mock_openai = MagicMock()
    mock_client = MagicMock()
    mock_openai.AsyncOpenAI.return_value = mock_client

    def _chunk(content: str | None, usage: Any = None) -> Any:
        choices = [SimpleNamespace(delta=SimpleNamespace(content=content))] if content else []
        return SimpleNamespace(choices=choices, usage=usage, model="gpt-4o-2024")

    async def _stream() -> Any:
        yield _chunk("Hello ")
        yield _chunk("world")
        yield _chunk(None, SimpleNamespace(prompt_tokens=12, completion_tokens=3))

    mock_client.chat.completions.create = AsyncMock(return_value=_stream())

    with patch.dict(sys.modules, {"openai": mock_openai}):
        from app.providers.openai_compatible import OpenAICompatibleProvider

        provider = OpenAICompatibleProvider(api_key="sk-test")
        tokens: list[str] = []

        async def _on_token(t: str) -> None:
            tokens.append(t)

        resp = await provider.stream_tokens(
            CompletionRequest(messages=[Message(role="user", content="hi")], model="gpt-4o"),
            _on_token,
        )

    kwargs = mock_client.chat.completions.create.await_args.kwargs
    assert kwargs["stream_options"] == {"include_usage": True}
    assert resp.content == "Hello world"
    assert resp.input_tokens == 12
    assert resp.output_tokens == 3
    assert resp.usage is not None
    assert resp.usage.prompt_tokens == 12


async def test_distributed_strategy_uses_real_model_and_charges_budget() -> None:
    from datetime import UTC, datetime, timedelta

    from app.orchestration.strategy_context_store import (
        StrategyGoalContext,
        StrategyGoalContextStore,
    )
    from app.orchestration.strategy_contracts import (
        ExecutionTerminalState,
        PatternLimits,
        StrategyExecutionRequest,
    )
    from app.orchestration.strategy_executor import (
        DistributedStrategyExecutor,
        default_distributed_admission,
    )
    from app.orchestration.strategy_registry import build_default_registry
    from app.orchestration.strategy_runner import StrategyRunner

    provider = FakeProvider(
        responses=[
            '{"steps": [{"id": "s1", "summary": "do it"}]}',
            "child result",
            "final answer",
        ]
    )
    provider._default_model = "gpt-4o"  # type: ignore[attr-defined]
    store = StrategyGoalContextStore()
    await store.put(
        "ctx-1", StrategyGoalContext(goal_text="goal", provider=provider, tenant_ctx=T)
    )
    controller = _RecordingController()
    runner = StrategyRunner(
        build_default_registry(),
        executor=DistributedStrategyExecutor(context_store=store, cost_controller=controller),
        admission=default_distributed_admission,
    )
    request = StrategyExecutionRequest.model_validate(
        {
            "tenant_id": T.tenant_id,
            "goal_id": "goal-1",
            "strategy_id": "supervisor",
            "adapter_version": "1.0.0",
            "state_schema_version": 1,
            "agent_id": "agent-1",
            "runtime_profile_ref": "profile-1",
            "context_snapshot_ref": "ctx-1",
            "policy_ref": "policy-1",
            "budget_ref": "budget-1",
            "cancellation_token": "cancel-1",
            "deadline": datetime.now(UTC) + timedelta(minutes=1),
            "idempotency_key": "idem-1",
        }
    )
    limits = PatternLimits.model_validate(
        {
            "calls": 32,
            "nodes": 32,
            "edges": 32,
            "depth": 8,
            "fan_out": 8,
            "rounds": 16,
            "tokens": 20_000,
            "duration_seconds": 30,
            "cost_usd": 5.0,
        }
    )

    result = await runner.run(request, limits)

    assert result.terminal_state is ExecutionTerminalState.SUCCEEDED
    assert provider.call_history
    assert {r.model for r in provider.call_history} == {"gpt-4o"}
    assert "fake-model" not in {r.model for r in provider.call_history}
    assert controller.charges and all(c > 0.0 for c in controller.charges)
    assert result.cost_usd > 0.0


async def test_planner_spend_controller_error_fails_closed() -> None:
    """A cost-controller exception latches the goal instead of ok=True."""
    from app.agent.nodes.llm_cost import charge_llm_call
    from app.agent.nodes.routing_mixin import RoutingMixin
    from app.agent.state import AgentState, GoalStatus

    controller = SimpleNamespace(
        check_and_record=AsyncMock(side_effect=ConnectionError("redis down"))
    )
    graph = SimpleNamespace(_cost_controller=controller)
    state = AgentState(goal="g", tenant_ctx=T)
    resp = CompletionResponse(content="x", model="gpt-4o", input_tokens=100, output_tokens=50)

    await charge_llm_call(
        graph, resp=resp, role="planner", model="gpt-4o", agent_state=state, tenant_ctx=T
    )

    assert state.context["_budget_exhausted"] is True
    assert "ConnectionError" in state.context["_budget_check_error"]
    assert RoutingMixin._fail_if_budget_exhausted(state) is True
    assert state.status is GoalStatus.FAILED
    assert "could not be verified" in (state.error_message or "")
