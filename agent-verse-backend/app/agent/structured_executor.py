"""Bounded, cancellable, checkpointable execution for validated structured plans."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agent.structured_plan import (
    StructuredPlan,
    StructuredStep,
    _safe_eval_condition,
)
from app.orchestration.strategy_contracts import PatternLimits

StepRunner = Callable[[StructuredStep], Awaitable[Any]]
CheckpointWriter = Callable[["ExecutionCheckpoint"], Awaitable[None]]


class ResumeMismatchError(ValueError):
    """A checkpoint cannot safely resume the supplied plan/runtime schema."""


class LoopExhaustedError(RuntimeError):
    """A loop reached its bounded iteration ceiling without succeeding."""


class StepExecutionError(RuntimeError):
    """A structured step failed and its sibling work was cancelled."""


class ExecutionCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_hash: str
    state_schema_version: int = Field(gt=0)
    wave_index: int = Field(ge=0)
    completed_step_ids: tuple[str, ...]
    loop_iterations: dict[str, int]


class StructuredExecutionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_hash: str
    completed_step_ids: tuple[str, ...]
    outputs: dict[str, Any]
    terminal_state: str = "succeeded"


class StructuredPlanExecutor:
    """Execute one validated DAG within profile-owned limits."""

    state_schema_version = 1

    def __init__(self, *, loop_backoff_seconds: float = 0.1) -> None:
        self._loop_backoff_seconds = loop_backoff_seconds

    @staticmethod
    def plan_hash(plan: StructuredPlan) -> str:
        payload = [
            {
                "id": step.id,
                "description": step.description,
                "tool": step.tool,
                "arguments": step.arguments,
                "depends_on": step.depends_on,
                "condition": step.condition,
                "loop_until": step.loop_until,
                "max_loop_iter": step.max_loop_iter,
            }
            for step in plan.steps
        ]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    async def execute(
        self,
        plan: StructuredPlan,
        run_step: StepRunner,
        *,
        limits: PatternLimits,
        cancelled: asyncio.Event,
        checkpoint_writer: CheckpointWriter | None = None,
        prior_checkpoint: ExecutionCheckpoint | None = None,
    ) -> StructuredExecutionResult:
        plan.validate()
        digest = self.plan_hash(plan)
        if prior_checkpoint is not None:
            if prior_checkpoint.plan_hash != digest:
                raise ResumeMismatchError("checkpoint plan hash does not match")
            if prior_checkpoint.state_schema_version != self.state_schema_version:
                raise ResumeMismatchError("checkpoint state schema version does not match")

        completed = set(prior_checkpoint.completed_step_ids if prior_checkpoint is not None else ())
        loop_iterations = dict(
            prior_checkpoint.loop_iterations if prior_checkpoint is not None else {}
        )
        outputs: dict[str, Any] = {}
        semaphore = asyncio.Semaphore(limits.fan_out)
        call_count = 0

        async def checkpoint(wave_index: int) -> None:
            if checkpoint_writer is None:
                return
            await checkpoint_writer(
                ExecutionCheckpoint(
                    plan_hash=digest,
                    state_schema_version=self.state_schema_version,
                    wave_index=wave_index,
                    completed_step_ids=tuple(
                        step.id for step in plan.steps if step.id in completed
                    ),
                    loop_iterations=dict(loop_iterations),
                )
            )

        async def execute_step(step: StructuredStep, wave_index: int) -> None:
            nonlocal call_count
            if step.id in completed:
                return
            if cancelled.is_set():
                raise asyncio.CancelledError
            async with semaphore:
                maximum_attempts = min(step.max_loop_iter, limits.rounds)
                while True:
                    if cancelled.is_set():
                        raise asyncio.CancelledError
                    if call_count >= limits.calls:
                        raise StepExecutionError("profile call limit exhausted")
                    call_count += 1
                    try:
                        output = await run_step(step)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        raise StepExecutionError(f"step failed: {step.id}") from exc
                    outputs[step.id] = output
                    loop_iterations[step.id] = loop_iterations.get(step.id, 0) + 1
                    await checkpoint(wave_index)
                    if step.loop_until is None or _safe_eval_condition(
                        step.loop_until, {"output": output}
                    ):
                        completed.add(step.id)
                        await checkpoint(wave_index)
                        return
                    if loop_iterations[step.id] >= maximum_attempts:
                        raise LoopExhaustedError(
                            f"loop exhausted for step {step.id} after {maximum_attempts} attempts"
                        )
                    if self._loop_backoff_seconds > 0:
                        with contextlib.suppress(TimeoutError):
                            await asyncio.wait_for(
                                cancelled.wait(), timeout=self._loop_backoff_seconds
                            )
                        if cancelled.is_set():
                            raise asyncio.CancelledError

        async with asyncio.timeout(limits.duration_seconds):
            for wave_index, wave in enumerate(plan.execution_waves()):
                tasks = [
                    asyncio.create_task(execute_step(step, wave_index))
                    for step in wave
                    if step.id not in completed
                ]
                if not tasks:
                    continue
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
                if any(task.cancelled() for task in done):
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    raise asyncio.CancelledError
                failure = next(
                    (
                        task.exception()
                        for task in done
                        if not task.cancelled() and task.exception() is not None
                    ),
                    None,
                )
                if failure is not None:
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    raise failure
                await asyncio.gather(*pending)

        ordered_completed = tuple(step.id for step in plan.steps if step.id in completed)
        return StructuredExecutionResult(
            plan_hash=digest,
            completed_step_ids=ordered_completed,
            outputs=outputs,
        )


__all__ = [
    "ExecutionCheckpoint",
    "LoopExhaustedError",
    "ResumeMismatchError",
    "StepExecutionError",
    "StructuredExecutionResult",
    "StructuredPlanExecutor",
]
