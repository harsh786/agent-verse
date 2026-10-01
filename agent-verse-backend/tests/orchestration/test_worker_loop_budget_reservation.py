"""CORE-37 regression: the worker's distributed runner reserves budget before running.

build_worker_distributed_loop used to construct its StrategyRunner without
``reserve_budget``, so the always-true default admitted distributed runs for
tenants with no budget left (spend was only charged after each call).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.coordination.pattern_runs.goal_bridge import build_worker_distributed_loop
from app.orchestration.runtime_profile import StrategySelection
from app.orchestration.strategy_context_store import StrategyGoalContext
from app.orchestration.strategy_contracts import ExecutionTerminalState
from app.providers.fake import FakeProvider
from tests.orchestration.test_distributed_hitl_gates import CTX, _limits, _request


async def _run_with_budget(remaining: Any) -> tuple[Any, FakeProvider]:
    controller = SimpleNamespace(ahas_remaining_budget=remaining)
    provider = FakeProvider(responses=["proposal", "critique", "proposer-a"])
    loop = build_worker_distributed_loop(
        SimpleNamespace(primary_strategy=StrategySelection("debate", "1.0.0")),
        db_factory=object(),
        provider=provider,
        cost_controller=controller,
    )
    assert loop is not None
    await loop.context_store.put(
        "ctx-debate", StrategyGoalContext(goal_text="Pick a database", provider=provider,
                                          tenant_ctx=CTX)
    )
    return await loop.strategy_runner.run(_request("debate"), _limits()), provider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "remaining",
    [AsyncMock(return_value=False), AsyncMock(side_effect=ConnectionError("redis down"))],
)
async def test_exhausted_or_unverifiable_budget_is_denied_before_any_llm_call(
    remaining: Any,
) -> None:
    result, provider = await _run_with_budget(remaining)
    assert result.terminal_state is ExecutionTerminalState.POLICY_DENIED
    assert "budget_reservation_failed" in str(result.model_dump())
    assert provider.call_history == []
    remaining.assert_awaited_once()
