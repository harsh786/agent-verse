from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.orchestration.strategy_contracts import (
    ExecutionTerminalState,
    PatternLimits,
    StrategyCheckpoint,
    StrategyExecutionRequest,
)
from app.orchestration.strategy_registry import build_default_registry
from app.orchestration.strategy_runner import (
    ExecutionMetrics,
    StrategyRunner,
    StrategyRunOutput,
)


def request(**overrides: object) -> StrategyExecutionRequest:
    values: dict[str, object] = {
        "tenant_id": "tenant-1",
        "goal_id": "goal-1",
        "strategy_id": "react",
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


def limits(**overrides: int | float) -> PatternLimits:
    values: dict[str, int | float] = {
        "calls": 2,
        "nodes": 2,
        "edges": 2,
        "depth": 2,
        "fan_out": 2,
        "rounds": 2,
        "tokens": 20,
        "duration_seconds": 2,
        "cost_usd": 2.0,
    }
    values.update(overrides)
    return PatternLimits.model_validate(values)


@pytest.mark.asyncio
async def test_runner_resolves_pinned_adapter_and_is_idempotent() -> None:
    calls = 0

    async def execute(*_: object) -> StrategyRunOutput:
        nonlocal calls
        calls += 1
        return StrategyRunOutput(answer="done", metrics=ExecutionMetrics(calls=1))

    runner = StrategyRunner(build_default_registry(), executor=execute)
    first = await runner.run(request(), limits())
    duplicate = await runner.run(request(), limits())

    assert first == duplicate
    assert first.terminal_state is ExecutionTerminalState.SUCCEEDED
    assert calls == 1


@pytest.mark.asyncio
async def test_admission_failure_prevents_adapter_execution() -> None:
    executed = False

    async def execute(*_: object) -> StrategyRunOutput:
        nonlocal executed
        executed = True
        return StrategyRunOutput(answer="bad")

    runner = StrategyRunner(
        build_default_registry(),
        executor=execute,
        admission=lambda _: (False, "dependency_not_ready"),
    )
    result = await runner.run(request(), limits())

    assert result.terminal_state is ExecutionTerminalState.POLICY_DENIED
    assert not executed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metric", "value"),
    [
        ("calls", 3),
        ("nodes", 3),
        ("edges", 3),
        ("depth", 3),
        ("fan_out", 3),
        ("rounds", 3),
        ("tokens", 21),
        ("duration_seconds", 3.0),
        ("cost_usd", 3.0),
    ],
)
async def test_every_bounded_dimension_terminates_execution(metric: str, value: float) -> None:
    async def execute(*_: object) -> StrategyRunOutput:
        return StrategyRunOutput(metrics=ExecutionMetrics(**{metric: value}))

    result = await StrategyRunner(
        build_default_registry(), executor=execute
    ).run(request(), limits())

    assert result.terminal_state is ExecutionTerminalState.LIMIT_EXCEEDED
    assert result.trace_summary.limit_type == metric


@pytest.mark.asyncio
async def test_cancellation_before_and_during_execution() -> None:
    runner = StrategyRunner(build_default_registry())
    runner.cancel("cancel-1")
    before = await runner.run(request(), limits())
    assert before.terminal_state is ExecutionTerminalState.CANCELLED

    started = asyncio.Event()

    async def execute(*_: object) -> StrategyRunOutput:
        started.set()
        await asyncio.sleep(10)
        return StrategyRunOutput(answer="late")

    runner = StrategyRunner(build_default_registry(), executor=execute)
    task = asyncio.create_task(runner.run(request(idempotency_key="during"), limits()))
    await started.wait()
    runner.cancel("cancel-1")
    during = await task
    assert during.terminal_state is ExecutionTerminalState.CANCELLED


@pytest.mark.asyncio
async def test_deadline_and_checkpoint_version_fail_safely() -> None:
    runner = StrategyRunner(build_default_registry())
    expired = request(deadline=datetime.now(UTC) + timedelta(milliseconds=10))
    await asyncio.sleep(0.02)
    deadline_result = await runner.run(expired, limits())
    assert deadline_result.terminal_state is ExecutionTerminalState.DEADLINE_EXCEEDED

    checkpoint = StrategyCheckpoint(
        strategy_id="react",
        adapter_version="2.0.0",
        state_schema_version=2,
        cursor="phase-1",
        state_ref="state-1",
        created_at=datetime.now(UTC),
    )
    resume = await runner.run(
        request(idempotency_key="resume"), limits(), checkpoint=checkpoint
    )
    assert resume.terminal_state is ExecutionTerminalState.FAILED
    assert "resume_blocked" in resume.trace_summary.reason_codes


@pytest.mark.asyncio
async def test_checkpoint_callback_and_budget_release_happen_once() -> None:
    checkpoints: list[StrategyCheckpoint] = []
    reservations: list[str] = []
    releases: list[str] = []

    async def execute(*_: object) -> StrategyRunOutput:
        return StrategyRunOutput(answer="done", checkpoint_cursor="phase-1")

    runner = StrategyRunner(
        build_default_registry(),
        executor=execute,
        checkpoint_callback=checkpoints.append,
        reserve_budget=lambda req, _: reservations.append(req.idempotency_key) or True,
        release_budget=lambda req: releases.append(req.idempotency_key),
    )
    result = await runner.run(request(), limits())

    assert result.terminal_state is ExecutionTerminalState.SUCCEEDED
    assert checkpoints[0].adapter_version == "1.0.0"
    assert reservations == ["idempotency-1"]
    assert releases == ["idempotency-1"]
