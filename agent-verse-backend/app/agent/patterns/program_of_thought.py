"""Single-program governed Program-of-Thought strategy."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.execution_environment.code_validation import CodeWorkloadValidator
from app.execution_environment.models import CodeExecutionObservation, CodeExecutionWorkload
from app.orchestration.strategy_adapters import ExecutionTier


class ProgramOfThoughtPhase(StrEnum):
    CREATED = "created"
    GENERATING = "generating"
    VALIDATING = "validating"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    VALIDATING_OUTPUT = "validating_output"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProgramOfThoughtState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: ProgramOfThoughtPhase = ProgramOfThoughtPhase.CREATED
    program_ref: str | None = None
    source_sha256: str | None = None
    workload_id: str | None = None
    observation_ref: str | None = None
    output_ref: str | None = None
    checkpoint_version: int = 1


@dataclass(frozen=True, slots=True)
class ProgramOfThoughtAdapter:
    strategy_id: str = "program_of_thought"
    execution_tier: ExecutionTier = ExecutionTier.SANDBOX

    def create_runtime(self, **kwargs: Any) -> ProgramOfThoughtRuntime:
        return ProgramOfThoughtRuntime(**kwargs)


class ProgramOfThoughtRuntime:
    def __init__(self, *, governed_code_tool: Any, checkpoint_callback: Any = None) -> None:
        self._tool = governed_code_tool
        self._checkpoint = checkpoint_callback
        self._validator = CodeWorkloadValidator()

    @staticmethod
    async def _invoke(callback: Any, *args: Any, **kwargs: Any) -> Any:
        result = callback(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    async def _save(self, state: ProgramOfThoughtState) -> None:
        if self._checkpoint is not None:
            await self._invoke(self._checkpoint, state)

    async def execute(
        self,
        *,
        generate: Any,
        invocation: Any,
        synthesize: Any,
        prior_state: ProgramOfThoughtState | None = None,
        workload: CodeExecutionWorkload | None = None,
        observation: CodeExecutionObservation | None = None,
        cancelled: asyncio.Event | None = None,
    ) -> tuple[ProgramOfThoughtState, str | None]:
        state = prior_state or ProgramOfThoughtState()
        if cancelled is not None and cancelled.is_set():
            return state.model_copy(update={"phase": ProgramOfThoughtPhase.CANCELLED}), None
        if workload is None:
            workload = await self._invoke(generate)
            state = state.model_copy(
                update={
                    "phase": ProgramOfThoughtPhase.GENERATING,
                    "program_ref": f"artifact://source/{workload.workload_id}",
                    "source_sha256": workload.source_sha256,
                    "workload_id": workload.workload_id,
                }
            )
            await self._save(state)
        violations = self._validator.validate(workload)
        if violations:
            failed = state.model_copy(update={"phase": ProgramOfThoughtPhase.FAILED})
            await self._save(failed)
            return failed, None
        if observation is None:
            state = state.model_copy(update={"phase": ProgramOfThoughtPhase.EXECUTING})
            await self._save(state)
            try:
                observation = await self._tool.execute(
                    invocation=invocation, workload=workload
                )
            except Exception:
                failed = state.model_copy(update={"phase": ProgramOfThoughtPhase.FAILED})
                await self._save(failed)
                return failed, None
        if observation.terminal_state != "completed" or observation.result_json is None:
            failed = state.model_copy(update={"phase": ProgramOfThoughtPhase.FAILED})
            await self._save(failed)
            return failed, None
        state = state.model_copy(
            update={
                "phase": ProgramOfThoughtPhase.SYNTHESIZING,
                "observation_ref": f"observation://{observation.observation_sha256}",
                "output_ref": f"result://{observation.observation_sha256}",
            }
        )
        await self._save(state)
        answer = str(await self._invoke(synthesize, observation.result_json))
        completed = state.model_copy(update={"phase": ProgramOfThoughtPhase.COMPLETED})
        await self._save(completed)
        return completed, answer


__all__ = [
    "ProgramOfThoughtAdapter",
    "ProgramOfThoughtPhase",
    "ProgramOfThoughtRuntime",
    "ProgramOfThoughtState",
]
