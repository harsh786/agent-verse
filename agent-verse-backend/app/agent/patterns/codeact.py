"""Bounded governed CodeAct strategy with deterministic stall detection."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.execution_environment.code_validation import CodeWorkloadValidator
from app.execution_environment.models import CodeExecutionObservation, CodeExecutionWorkload
from app.orchestration.strategy_adapters import ExecutionTier


class CodeActPhase(StrEnum):
    CREATED = "created"
    GENERATING_ACTION = "generating_action"
    VALIDATING_ACTION = "validating_action"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING_ACTION = "executing_action"
    OBSERVING = "observing"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    STALLED = "stalled"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CodeActionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_number: int = Field(ge=1, le=8)
    action_id: str = Field(min_length=1)
    source_ref: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    workload_id: str = Field(min_length=1)
    observation_ref: str | None = None
    observation_sha256: str | None = None
    status: Literal["generated", "approved", "executing", "observed", "failed", "cancelled"]


class CodeActState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: CodeActPhase = CodeActPhase.CREATED
    actions: tuple[CodeActionState, ...] = ()
    consecutive_no_progress: int = Field(default=0, ge=0, le=2)
    result_ref: str | None = None
    checkpoint_version: int = 1


@dataclass(frozen=True, slots=True)
class CodeActAdapter:
    strategy_id: str = "codeact"
    execution_tier: ExecutionTier = ExecutionTier.SANDBOX

    def create_runtime(self, **kwargs: Any) -> CodeActRuntime:
        return CodeActRuntime(**kwargs)


class CodeActRuntime:
    def __init__(self, *, governed_code_tool: Any, checkpoint_callback: Any = None) -> None:
        self._tool = governed_code_tool
        self._checkpoint = checkpoint_callback
        self._validator = CodeWorkloadValidator()

    @staticmethod
    async def _invoke(callback: Any, *args: Any, **kwargs: Any) -> Any:
        result = callback(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    async def _save(self, state: CodeActState) -> None:
        if self._checkpoint is not None:
            await self._invoke(self._checkpoint, state)

    async def execute(
        self,
        *,
        goal: str,
        invocation: Any,
        generate: Any,
        evaluate: Any,
        synthesize: Any,
        prior_state: CodeActState | None = None,
        cancelled: asyncio.Event | None = None,
        maximum_actions: int = 6,
    ) -> tuple[CodeActState, str | None]:
        state = prior_state or CodeActState()
        maximum_actions = max(1, min(maximum_actions, 8))
        observations: list[CodeExecutionObservation] = []
        prior_sources = {item.source_sha256 for item in state.actions}
        prior_observations = {
            item.observation_sha256 for item in state.actions if item.observation_sha256 is not None
        }
        for number in range(len(state.actions) + 1, maximum_actions + 1):
            if cancelled is not None and cancelled.is_set():
                stopped = state.model_copy(update={"phase": CodeActPhase.CANCELLED})
                await self._save(stopped)
                return stopped, None
            bounded_context = tuple(
                {
                    "observation_sha256": item.observation_sha256,
                    "terminal_state": item.terminal_state,
                    "result_json": item.result_json,
                }
                for item in observations[-3:]
            )
            workload: CodeExecutionWorkload = await self._invoke(
                generate, goal, number, bounded_context
            )
            action = CodeActionState(
                action_number=number,
                action_id=f"action-{number}",
                source_ref=f"artifact://source/{workload.workload_id}",
                source_sha256=workload.source_sha256,
                workload_id=workload.workload_id,
                status="generated",
            )
            state = state.model_copy(
                update={
                    "phase": CodeActPhase.VALIDATING_ACTION,
                    "actions": (*state.actions, action),
                }
            )
            await self._save(state)
            if self._validator.validate(workload):
                failed_action = action.model_copy(update={"status": "failed"})
                failed = state.model_copy(
                    update={
                        "phase": CodeActPhase.FAILED,
                        "actions": (*state.actions[:-1], failed_action),
                    }
                )
                await self._save(failed)
                return failed, None
            executing = action.model_copy(update={"status": "executing"})
            state = state.model_copy(
                update={
                    "phase": CodeActPhase.EXECUTING_ACTION,
                    "actions": (*state.actions[:-1], executing),
                }
            )
            await self._save(state)
            try:
                observation = await self._tool.execute(invocation=invocation, workload=workload)
            except Exception:
                failed = state.model_copy(update={"phase": CodeActPhase.FAILED})
                await self._save(failed)
                return failed, None
            if observation.terminal_state != "completed":
                failed = state.model_copy(update={"phase": CodeActPhase.FAILED})
                await self._save(failed)
                return failed, None
            evaluation = await self._invoke(evaluate, observation)
            no_progress = (
                workload.source_sha256 in prior_sources
                or observation.observation_sha256 in prior_observations
                or not bool(evaluation.get("progress", False))
            )
            count = state.consecutive_no_progress + 1 if no_progress else 0
            observed = executing.model_copy(
                update={
                    "status": "observed",
                    "observation_ref": f"observation://{observation.observation_sha256}",
                    "observation_sha256": observation.observation_sha256,
                }
            )
            state = state.model_copy(
                update={
                    "phase": CodeActPhase.OBSERVING,
                    "actions": (*state.actions[:-1], observed),
                    "consecutive_no_progress": min(count, 2),
                }
            )
            await self._save(state)
            observations.append(observation)
            prior_sources.add(workload.source_sha256)
            prior_observations.add(observation.observation_sha256)
            if count >= 2:
                stalled = state.model_copy(update={"phase": CodeActPhase.STALLED})
                await self._save(stalled)
                return stalled, None
            if bool(evaluation.get("complete", False)):
                answer = str(await self._invoke(synthesize, observation.result_json))
                completed = state.model_copy(
                    update={
                        "phase": CodeActPhase.COMPLETED,
                        "result_ref": f"result://{observation.observation_sha256}",
                    }
                )
                await self._save(completed)
                return completed, answer
        failed = state.model_copy(update={"phase": CodeActPhase.FAILED})
        await self._save(failed)
        return failed, None


__all__ = [
    "CodeActAdapter",
    "CodeActPhase",
    "CodeActRuntime",
    "CodeActState",
    "CodeActionState",
]
