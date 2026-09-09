from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.orchestration.strategy_contracts import (
    ExecutionTerminalState,
    StrategyExecutionRequest,
)
from app.orchestration.strategy_registry import LOCAL_REASONING_LIMITS, build_default_registry
from app.orchestration.strategy_runner import (
    ExecutionMetrics,
    StrategyRunner,
    StrategyRunOutput,
)

STRATEGIES = tuple(LOCAL_REASONING_LIMITS)


def _request(strategy_id: str) -> StrategyExecutionRequest:
    return StrategyExecutionRequest(
        tenant_id="tenant",
        goal_id=f"goal-{strategy_id}",
        strategy_id=strategy_id,
        adapter_version="1.0.0",
        state_schema_version=1,
        agent_id="agent",
        runtime_profile_ref="profile:v1",
        context_snapshot_ref="context:v1",
        policy_ref="policy:v1",
        budget_ref="budget:v1",
        cancellation_token=f"cancel-{strategy_id}",
        deadline=datetime.now(UTC) + timedelta(minutes=5),
        idempotency_key=f"key-{strategy_id}",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_id", STRATEGIES)
async def test_all_local_reasoning_adapters_resolve_through_runner(strategy_id: str) -> None:
    observed: list[tuple[str, str]] = []

    async def execute(request: StrategyExecutionRequest, factory: Any, *_args: Any):
        adapter_module = factory.__self__.__class__.__module__
        observed.append((request.strategy_id, adapter_module))
        return StrategyRunOutput(
            answer="safe answer",
            metrics=ExecutionMetrics(
                calls=1,
                nodes=min(1, LOCAL_REASONING_LIMITS[request.strategy_id].nodes),
            ),
            checkpoint_cursor="phase-1",
            safe_rationale_summary="bounded execution completed",
        )

    checkpoints: list[object] = []
    result = await StrategyRunner(
        build_default_registry(),
        executor=execute,
        checkpoint_callback=checkpoints.append,
    ).run(_request(strategy_id), LOCAL_REASONING_LIMITS[strategy_id])

    assert result.terminal_state is ExecutionTerminalState.SUCCEEDED
    assert result.answer == "safe answer"
    assert result.safe_rationale_summary == "bounded execution completed"
    assert observed[0][0] == strategy_id
    assert observed[0][1].startswith("app.agent.patterns.")
    assert len(checkpoints) == 1


@pytest.mark.asyncio
async def test_unconfigured_runner_fails_closed() -> None:
    result = await StrategyRunner(build_default_registry()).run(
        _request("least_to_most"), LOCAL_REASONING_LIMITS["least_to_most"]
    )
    assert result.terminal_state is ExecutionTerminalState.FAILED
    assert result.trace_summary.reason_codes == ("execution_failed",)
