from __future__ import annotations

import asyncio

import pytest

from app.agent.structured_executor import (
    ExecutionCheckpoint,
    LoopExhaustedError,
    ResumeMismatchError,
    StructuredPlanExecutor,
)
from app.agent.structured_plan import StructuredPlan, StructuredStep
from app.orchestration.runtime_profile import default_pattern_limits


def limits(*, fan_out: int = 2, rounds: int = 3):
    return default_pattern_limits().model_copy(
        update={"fan_out": fan_out, "rounds": rounds}
    )


async def test_wave_executor_never_exceeds_max_concurrency() -> None:
    active = 0
    peak = 0

    async def run(step: StructuredStep) -> str:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return step.id

    plan = StructuredPlan(
        [StructuredStep(id=str(index), description="work") for index in range(6)]
    )
    result = await StructuredPlanExecutor().execute(
        plan, run, limits=limits(fan_out=2), cancelled=asyncio.Event()
    )

    assert peak == 2
    assert result.completed_step_ids == tuple(str(index) for index in range(6))


async def test_wave_executor_preserves_dependency_order() -> None:
    order: list[str] = []

    async def run(step: StructuredStep) -> str:
        order.append(step.id)
        return step.id

    plan = StructuredPlan(
        [
            StructuredStep(id="a", description="first"),
            StructuredStep(id="b", description="second", depends_on=["a"]),
        ]
    )
    await StructuredPlanExecutor().execute(
        plan, run, limits=limits(), cancelled=asyncio.Event()
    )

    assert order == ["a", "b"]


async def test_resume_skips_completed_steps_and_validates_plan_hash() -> None:
    calls: list[str] = []

    async def run(step: StructuredStep) -> str:
        calls.append(step.id)
        return step.id

    plan = StructuredPlan(
        [
            StructuredStep(id="a", description="first"),
            StructuredStep(id="b", description="second", depends_on=["a"]),
        ]
    )
    executor = StructuredPlanExecutor()
    prior = ExecutionCheckpoint(
        plan_hash=executor.plan_hash(plan),
        state_schema_version=1,
        wave_index=1,
        completed_step_ids=("a",),
        loop_iterations={},
    )
    await executor.execute(
        plan,
        run,
        limits=limits(),
        cancelled=asyncio.Event(),
        prior_checkpoint=prior,
    )
    assert calls == ["b"]

    with pytest.raises(ResumeMismatchError, match="plan hash"):
        await executor.execute(
            plan,
            run,
            limits=limits(),
            cancelled=asyncio.Event(),
            prior_checkpoint=prior.model_copy(update={"plan_hash": "wrong"}),
        )


async def test_loop_until_stops_and_exhaustion_is_explicit() -> None:
    attempts = 0

    async def succeeds(step: StructuredStep) -> str:
        nonlocal attempts
        attempts += 1
        return "done" if attempts == 2 else "pending"

    plan = StructuredPlan(
        [
            StructuredStep(
                id="a",
                description="poll",
                loop_until="output == 'done'",
                max_loop_iter=3,
            )
        ]
    )
    await StructuredPlanExecutor(loop_backoff_seconds=0).execute(
        plan, succeeds, limits=limits(rounds=3), cancelled=asyncio.Event()
    )
    assert attempts == 2

    async def never(_: StructuredStep) -> str:
        return "pending"

    with pytest.raises(LoopExhaustedError, match="a"):
        await StructuredPlanExecutor(loop_backoff_seconds=0).execute(
            plan, never, limits=limits(rounds=2), cancelled=asyncio.Event()
        )


async def test_loop_honors_cancellation_during_backoff() -> None:
    cancelled = asyncio.Event()

    async def run(_: StructuredStep) -> str:
        cancelled.set()
        return "pending"

    plan = StructuredPlan(
        [
            StructuredStep(
                id="a", description="poll", loop_until="output == 'done'"
            )
        ]
    )
    with pytest.raises(asyncio.CancelledError):
        await StructuredPlanExecutor(loop_backoff_seconds=30).execute(
            plan, run, limits=limits(), cancelled=cancelled
        )
