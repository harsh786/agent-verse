"""D-1: StrategyRunner must actually execute DISTRIBUTED-tier strategies.

Before this change, ``StrategyRunner`` was built with the module-default executor which
unconditionally raised ``RuntimeError("strategy executor is not configured")``, and nothing in
``app/`` ever called ``StrategyRunner.run()``. These tests pin down: (1) the inert behaviour
that existed before the fix, (2) that the wired ``DistributedStrategyExecutor`` genuinely
drives a real adapter to completion, and (3) that unsupported DISTRIBUTED strategies are
denied rather than faked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.orchestration.strategy_context_store import StrategyGoalContext, StrategyGoalContextStore
from app.orchestration.strategy_contracts import (
    ExecutionTerminalState,
    PatternLimits,
    StrategyExecutionRequest,
)
from app.orchestration.strategy_executor import (
    SUPPORTED_DISTRIBUTED_STRATEGIES,
    DistributedStrategyExecutor,
    default_distributed_admission,
)
from app.orchestration.strategy_registry import build_default_registry
from app.orchestration.strategy_runner import StrategyRunner
from app.providers.fake import FakeProvider


def _request(**overrides: object) -> StrategyExecutionRequest:
    values: dict[str, object] = {
        "tenant_id": "tenant-1",
        "goal_id": "goal-1",
        "strategy_id": "supervisor",
        "adapter_version": "1.0.0",
        "state_schema_version": 1,
        "agent_id": "agent-1",
        "runtime_profile_ref": "profile-1",
        "context_snapshot_ref": "context-1",
        "policy_ref": "policy-1",
        "budget_ref": "budget-1",
        "cancellation_token": "cancel-1",
        "deadline": datetime.now(UTC) + timedelta(minutes=1),
        "idempotency_key": "idempotency-1",
    }
    values.update(overrides)
    return StrategyExecutionRequest.model_validate(values)


def _limits(**overrides: int | float) -> PatternLimits:
    values: dict[str, int | float] = {
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
    values.update(overrides)
    return PatternLimits.model_validate(values)


# ── 1. Pin down the inert behaviour (failing-first characterisation) ──────────────────────────


@pytest.mark.asyncio
async def test_default_strategy_runner_cannot_execute_anything() -> None:
    """A StrategyRunner built with no executor override can never succeed.

    This is the D-1 bug: app/main.py built exactly this kind of runner, so calling .run()
    on it (which nothing in app/ did) always resolves to FAILED with reason "execution_failed"
    — the default executor's RuntimeError("strategy executor is not configured") is caught by
    StrategyRunner's own exception handling and converted into this terminal result.
    """
    runner = StrategyRunner(build_default_registry())  # no executor= override: the old default

    result = await runner.run(_request(strategy_id="react"), _limits())

    assert result.terminal_state is ExecutionTerminalState.FAILED
    assert "execution_failed" in result.trace_summary.reason_codes


# ── 2. The wired executor genuinely drives a real adapter to SUCCEEDED ─────────────────────────


@pytest.mark.asyncio
async def test_wired_executor_drives_supervisor_adapter_to_success_with_metrics() -> None:
    context_store = StrategyGoalContextStore()
    provider = FakeProvider(
        responses=[
            '{"steps": [{"id": "gather", "summary": "gather the facts"}, '
            '{"id": "write", "summary": "write the answer"}]}',
            "The capital of France is Paris.",
            "Roses are best planted in early spring.",
            "Final answer: Paris is the capital of France; plant roses in early spring.",
        ]
    )
    await context_store.put(
        "context-1",
        StrategyGoalContext(goal_text="Answer two trivia questions", provider=provider),
    )
    runner = StrategyRunner(
        build_default_registry(),
        executor=DistributedStrategyExecutor(context_store=context_store),
        admission=default_distributed_admission,
    )

    result = await runner.run(_request(strategy_id="supervisor"), _limits())

    assert result.terminal_state is ExecutionTerminalState.SUCCEEDED
    assert result.answer
    assert "Final answer" in result.answer
    assert result.trace_summary.counts  # calls/tokens were recorded, not zeroed out
    call_counts = {counter.key: counter.value for counter in result.trace_summary.counts}
    assert call_counts.get("calls", 0) >= 3  # decompose + >=1 child + synthesize


@pytest.mark.asyncio
async def test_wired_executor_drives_debate_adapter_to_success() -> None:
    context_store = StrategyGoalContextStore()
    provider = FakeProvider(
        responses=[
            "proposal text",
            "critique text",
            "proposer-a",
        ]
    )
    await context_store.put(
        "context-1",
        StrategyGoalContext(
            goal_text="Pick the best database for this workload", provider=provider
        ),
    )
    runner = StrategyRunner(
        build_default_registry(),
        executor=DistributedStrategyExecutor(context_store=context_store),
        admission=default_distributed_admission,
    )

    result = await runner.run(_request(strategy_id="debate"), _limits())

    assert result.terminal_state is ExecutionTerminalState.SUCCEEDED
    assert result.answer


@pytest.mark.asyncio
async def test_wired_executor_is_idempotent_per_goal() -> None:
    """StrategyRunner's own idempotency cache means the LLM is only driven once."""
    context_store = StrategyGoalContextStore()
    provider = FakeProvider(
        responses=['{"steps": [{"id": "s1", "summary": "do it"}]}', "done", "final answer"]
    )
    await context_store.put(
        "context-1", StrategyGoalContext(goal_text="Do one thing", provider=provider)
    )
    runner = StrategyRunner(
        build_default_registry(),
        executor=DistributedStrategyExecutor(context_store=context_store),
        admission=default_distributed_admission,
    )

    first = await runner.run(_request(strategy_id="supervisor"), _limits())
    second = await runner.run(_request(strategy_id="supervisor"), _limits())

    assert first == second
    # FakeProvider only cycles responses on new .complete() calls; a second run would advance
    # the cursor if it re-executed, which it must not because of StrategyRunner's cache.
    assert len(provider.call_history) == 3


# ── 3. Unsupported DISTRIBUTED strategies are denied, never faked ─────────────────────────────


@pytest.mark.parametrize(
    "strategy_id",
    [
        "autogpt",
        "babyagi",
        "camel",
        "group_chat",
        "magentic",
        "mixture_of_agents",
        "market_auction",
    ],
)
@pytest.mark.asyncio
async def test_unsupported_distributed_strategies_are_denied_not_faked(strategy_id: str) -> None:
    assert strategy_id not in SUPPORTED_DISTRIBUTED_STRATEGIES
    admitted, reason = default_distributed_admission(_request(strategy_id=strategy_id))
    assert not admitted
    assert reason == "strategy_execution_not_implemented"

    runner = StrategyRunner(
        build_default_registry(),
        executor=DistributedStrategyExecutor(context_store=StrategyGoalContextStore()),
        admission=default_distributed_admission,
    )
    req = _request(strategy_id=strategy_id, idempotency_key=strategy_id)
    result = await runner.run(req, _limits())

    assert result.terminal_state is ExecutionTerminalState.POLICY_DENIED
    assert "strategy_execution_not_implemented" in result.trace_summary.reason_codes


@pytest.mark.asyncio
async def test_missing_goal_context_fails_rather_than_fabricating_an_answer() -> None:
    runner = StrategyRunner(
        build_default_registry(),
        executor=DistributedStrategyExecutor(context_store=StrategyGoalContextStore()),
        admission=default_distributed_admission,
    )

    result = await runner.run(_request(strategy_id="supervisor"), _limits())

    assert result.terminal_state is ExecutionTerminalState.FAILED
