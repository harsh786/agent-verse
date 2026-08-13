"""Validated LLM Compiler adapter backed by the canonical DAG executor."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from dataclasses import dataclass
from typing import Any

from app.agent.patterns.reasoning_contracts import (
    CompiledTask,
    LocalReasoningResult,
    ReasoningContractError,
    ReasoningPhase,
    canonical_json,
    validate_compiled_tasks,
)
from app.agent.structured_executor import ExecutionCheckpoint, StructuredPlanExecutor
from app.agent.structured_plan import StructuredPlan, StructuredStep
from app.orchestration.strategy_adapters import ExecutionTier
from app.orchestration.strategy_contracts import PatternLimits


@dataclass(frozen=True, slots=True)
class LLMCompilerAdapter:
    strategy_id: str = "llm_compiler"
    execution_tier: ExecutionTier = ExecutionTier.LOCAL

    def create_runtime(self, **kwargs: Any) -> LLMCompilerRuntime:
        return LLMCompilerRuntime(**kwargs)


class LLMCompilerRuntime:
    def __init__(
        self,
        *,
        governed_dispatcher: Any,
        tool_catalogue: dict[str, dict[str, Any]],
        checkpoint_callback: Any = None,
    ) -> None:
        self._dispatch = governed_dispatcher
        self._catalogue = dict(tool_catalogue)
        self._checkpoint = checkpoint_callback
        self._executor = StructuredPlanExecutor(loop_backoff_seconds=0)

    @staticmethod
    def _matches_type(value: Any, expected: str) -> bool:
        if expected == "string":
            return isinstance(value, str)
        if expected == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected == "number":
            return isinstance(value, int | float) and not isinstance(value, bool)
        if expected == "boolean":
            return isinstance(value, bool)
        if expected == "object":
            return isinstance(value, dict)
        if expected == "array":
            return isinstance(value, list)
        if expected == "null":
            return value is None
        return False

    @classmethod
    def _validate_schema(cls, value: Any, schema: dict[str, Any]) -> bool:
        expected = schema.get("type")
        if isinstance(expected, str) and not cls._matches_type(value, expected):
            return False
        if isinstance(value, dict):
            required = schema.get("required", [])
            if any(item not in value for item in required):
                return False
            properties = schema.get("properties", {})
            return all(
                key not in properties
                or cls._validate_schema(item, properties[key])
                for key, item in value.items()
            )
        return True

    def validate_compilation(
        self, tasks: tuple[CompiledTask, ...]
    ) -> tuple[CompiledTask, ...]:
        ordered = validate_compiled_tasks(tasks)
        if not ordered or len(ordered) > 16:
            raise ReasoningContractError("compiled task count must be between 1 and 16")
        for task in ordered:
            tool = self._catalogue.get(task.tool_name)
            if tool is None:
                raise ReasoningContractError(f"unknown compiled tool: {task.tool_name}")
            if not self._validate_schema(task.arguments, tool.get("input_schema", {})):
                raise ReasoningContractError(f"invalid arguments for task: {task.task_id}")
        return ordered

    async def _invoke(self, callback: Any, *args: Any, **kwargs: Any) -> Any:
        result = callback(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    async def execute(
        self,
        *,
        execution_id: str,
        tasks: tuple[CompiledTask, ...],
        limits: PatternLimits,
        cancelled: asyncio.Event,
        synthesize: Any,
        prior_checkpoint: ExecutionCheckpoint | None = None,
        completed_outputs: dict[str, Any] | None = None,
    ) -> LocalReasoningResult:
        ordered = self.validate_compilation(tasks)
        compiled_hash = hashlib.sha256(
            canonical_json([task.model_dump(mode="json") for task in ordered]).encode()
        ).hexdigest()
        plan = StructuredPlan(
            steps=[
                StructuredStep(
                    id=task.task_id,
                    description=f"compiled:{task.task_id}",
                    tool=task.tool_name,
                    arguments=dict(task.arguments),
                    depends_on=list(task.depends_on),
                    max_loop_iter=1,
                )
                for task in ordered
            ]
        )
        outputs = dict(completed_outputs or {})

        async def run_step(step: StructuredStep) -> Any:
            output = await self._invoke(
                self._dispatch,
                step.tool,
                step.arguments,
                idempotency_key=f"{execution_id}:{step.id}",
            )
            task = next(item for item in ordered if item.task_id == step.id)
            if not self._validate_schema(output, task.output_schema):
                raise ReasoningContractError(f"invalid output for task: {step.id}")
            outputs[step.id] = output
            return output

        async def save(checkpoint: ExecutionCheckpoint) -> None:
            if self._checkpoint is not None:
                await self._invoke(self._checkpoint, checkpoint, tuple(sorted(outputs)))

        try:
            result = await self._executor.execute(
                plan,
                run_step,
                limits=limits,
                cancelled=cancelled,
                checkpoint_writer=save,
                prior_checkpoint=prior_checkpoint,
            )
        except asyncio.CancelledError:
            return LocalReasoningResult(
                phase=ReasoningPhase.CANCELLED,
                terminal_reason="cancelled_during_compiled_wave",
                checkpoint_cursor=compiled_hash,
            )
        except Exception as exc:
            return LocalReasoningResult(
                phase=ReasoningPhase.FAILED,
                terminal_reason=f"compiled_execution_failed:{type(exc).__name__}",
                checkpoint_cursor=compiled_hash,
            )
        outputs.update(result.outputs)
        answer = str(await self._invoke(synthesize, outputs))
        return LocalReasoningResult(
            phase=ReasoningPhase.COMPLETED,
            answer=answer,
            checkpoint_cursor=compiled_hash,
            call_count=len(result.completed_step_ids) + 1,
            node_count=len(ordered),
            safe_evidence={
                "compilation_hash": compiled_hash,
                "completed_task_ids": list(result.completed_step_ids),
                "tool_names": [task.tool_name for task in ordered],
            },
        )


__all__ = ["LLMCompilerAdapter", "LLMCompilerRuntime"]
