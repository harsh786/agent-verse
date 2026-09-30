"""CORE-18: StrategyRunner caches are bounded, failures are not cached, a cancelled
run stops its execution, and the budget reservation is real."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.orchestration.strategy_contracts import ExecutionTerminalState
from app.orchestration.strategy_registry import build_default_registry
from app.orchestration.strategy_runner import ExecutionMetrics, StrategyRunner, StrategyRunOutput
from tests.orchestration.test_strategy_runner import limits, request

pytestmark = pytest.mark.asyncio


async def test_failures_are_not_cached_so_a_retry_runs_again() -> None:
    calls = 0

    async def execute(*_: object) -> StrategyRunOutput:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient")
        return StrategyRunOutput(answer="done", metrics=ExecutionMetrics(calls=1))

    runner = StrategyRunner(build_default_registry(), executor=execute)
    first = await runner.run(request(), limits())
    second = await runner.run(request(), limits())

    assert first.terminal_state is ExecutionTerminalState.FAILED
    assert second.terminal_state is ExecutionTerminalState.SUCCEEDED
    assert calls == 2


async def test_caches_are_bounded_and_locks_and_cancel_events_are_evicted() -> None:
    async def execute(*_: object) -> StrategyRunOutput:
        return StrategyRunOutput(answer="ok", metrics=ExecutionMetrics(calls=1))

    runner = StrategyRunner(build_default_registry(), executor=execute, max_cached_results=2)
    for i in range(5):
        await runner.run(
            request(idempotency_key=f"k-{i}", cancellation_token=f"c-{i}"), limits()
        )

    assert len(runner._results) == 2
    assert runner._locks == {}
    assert runner._cancel_events == {}


async def test_cancelling_the_run_cancels_the_execution() -> None:
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def execute(*_: object) -> StrategyRunOutput:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            stopped.set()
            raise
        return StrategyRunOutput(answer="late")

    runner = StrategyRunner(build_default_registry(), executor=execute)
    task = asyncio.create_task(runner.run(request(), limits(duration_seconds=60)))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(stopped.wait(), 2)


async def test_async_budget_reservation_denial_rejects_execution() -> None:
    executed = False
    released: list[Any] = []

    async def execute(*_: object) -> StrategyRunOutput:
        nonlocal executed
        executed = True
        return StrategyRunOutput(answer="x")

    async def reserve(_req: Any, _limits: Any) -> bool:
        return False

    runner = StrategyRunner(
        build_default_registry(),
        executor=execute,
        reserve_budget=reserve,
        release_budget=lambda req: released.append(req),
    )
    result = await runner.run(request(), limits())

    assert result.terminal_state is ExecutionTerminalState.POLICY_DENIED
    assert "budget_reservation_failed" in result.trace_summary.reason_codes
    assert executed is False
    assert released == []  # nothing was reserved, nothing to release


async def test_app_runner_reserves_against_the_cost_controller() -> None:
    from types import SimpleNamespace

    from app.main import _strategy_budget_reserver

    class _CC:
        def __init__(self, ok: bool) -> None:
            self.ok = ok

        async def ahas_remaining_budget(self, *, tenant_ctx: Any) -> bool:
            assert tenant_ctx.tenant_id == "tenant-1"
            return self.ok

    state = SimpleNamespace(cost_controller=_CC(False))
    assert await _strategy_budget_reserver(state)(request(), limits()) is False
    state.cost_controller = _CC(True)
    assert await _strategy_budget_reserver(state)(request(), limits()) is True
    # No cost controller wired: fail closed.
    assert await _strategy_budget_reserver(SimpleNamespace())(request(), limits()) is False


async def test_goal_cancel_reaches_the_strategy_runner() -> None:
    from types import SimpleNamespace

    from app.services.goal_service import GoalService

    cancelled: list[str] = []
    svc = GoalService()
    svc._app_state = SimpleNamespace(strategy_runner=SimpleNamespace(cancel=cancelled.append))
    svc._cancel_local_strategy_run("goal-9")
    assert cancelled == ["goal-9"]


async def test_executor_checkpoint_stores_are_bounded() -> None:
    from app.orchestration.strategy_executor import DistributedStrategyExecutor

    ex = DistributedStrategyExecutor(context_store=None)  # type: ignore[arg-type]
    ex._MAX_CHECKPOINT_STORES = 3  # type: ignore[misc]
    for i in range(10):
        ex._checkpoint_store_for(request(goal_id=f"g-{i}"))
    assert len(ex._checkpoint_stores) == 3
