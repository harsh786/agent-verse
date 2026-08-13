"""Frozen-plan ReWOO adapter using a governed tool dispatcher."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
from dataclasses import dataclass
from typing import Any

from app.agent.patterns.reasoning_contracts import (
    LocalReasoningResult,
    ReasoningContractError,
    ReasoningPhase,
    ToolPlanStep,
    canonical_json,
    validate_tool_plan,
)
from app.orchestration.strategy_adapters import ExecutionTier

_VARIABLE = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


@dataclass(frozen=True, slots=True)
class ReWOOAdapter:
    strategy_id: str = "rewoo"
    execution_tier: ExecutionTier = ExecutionTier.LOCAL

    def create_runtime(self, **kwargs: Any) -> ReWOORuntime:
        return ReWOORuntime(**kwargs)


class ReWOORuntime:
    def __init__(self, *, governed_dispatcher: Any, checkpoint_callback: Any = None) -> None:
        self._dispatch = governed_dispatcher
        self._checkpoint = checkpoint_callback

    @staticmethod
    def freeze_plan(plan: tuple[ToolPlanStep, ...]) -> tuple[tuple[ToolPlanStep, ...], str]:
        ordered = validate_tool_plan(plan)
        serialized = canonical_json([item.model_dump(mode="json") for item in ordered])
        return ordered, hashlib.sha256(serialized.encode()).hexdigest()

    @staticmethod
    def validate_variables(plan: tuple[ToolPlanStep, ...]) -> None:
        producer_by_variable = {item.output_variable: item.step_id for item in plan}
        by_id = {item.step_id: item for item in plan}

        def ancestors(step_id: str) -> set[str]:
            found: set[str] = set()
            pending = list(by_id[step_id].depends_on)
            while pending:
                dependency = pending.pop()
                if dependency not in found:
                    found.add(dependency)
                    pending.extend(by_id[dependency].depends_on)
            return found

        for step in plan:
            serialized = canonical_json(step.arguments)
            for variable in _VARIABLE.findall(serialized):
                producer = producer_by_variable.get(variable)
                if producer is None:
                    raise ReasoningContractError(f"unknown variable: {variable}")
                if producer not in ancestors(step.step_id):
                    raise ReasoningContractError(f"forward variable reference: {variable}")

    @staticmethod
    def _resolve(value: Any, outputs: dict[str, Any]) -> Any:
        if isinstance(value, str):
            exact = _VARIABLE.fullmatch(value)
            if exact:
                return outputs[exact.group(1)]
            return _VARIABLE.sub(lambda match: str(outputs[match.group(1)]), value)
        if isinstance(value, list):
            return [ReWOORuntime._resolve(item, outputs) for item in value]
        if isinstance(value, dict):
            return {key: ReWOORuntime._resolve(item, outputs) for key, item in value.items()}
        return value

    async def _invoke(self, callback: Any, *args: Any, **kwargs: Any) -> Any:
        result = callback(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    async def execute(
        self,
        *,
        execution_id: str,
        plan: tuple[ToolPlanStep, ...],
        synthesize: Any,
        completed_outputs: dict[str, Any] | None = None,
        expected_plan_hash: str | None = None,
        cancelled: asyncio.Event | None = None,
    ) -> LocalReasoningResult:
        ordered, plan_hash = self.freeze_plan(plan)
        self.validate_variables(ordered)
        if expected_plan_hash is not None and expected_plan_hash != plan_hash:
            return LocalReasoningResult(
                phase=ReasoningPhase.FAILED, terminal_reason="plan_hash_mismatch"
            )
        outputs = dict(completed_outputs or {})
        completed_ids = {
            item.step_id for item in ordered if item.output_variable in outputs
        }
        calls = 0
        while len(completed_ids) < len(ordered):
            if cancelled is not None and cancelled.is_set():
                return LocalReasoningResult(
                    phase=ReasoningPhase.CANCELLED,
                    terminal_reason="cancelled_between_waves",
                    checkpoint_cursor=plan_hash,
                    call_count=calls,
                )
            ready = [
                item
                for item in ordered
                if item.step_id not in completed_ids
                and set(item.depends_on).issubset(completed_ids)
            ][:4]
            if not ready:
                return LocalReasoningResult(
                    phase=ReasoningPhase.FAILED,
                    terminal_reason="dependency_deadlock",
                    checkpoint_cursor=plan_hash,
                    call_count=calls,
                )

            async def dispatch(step: ToolPlanStep) -> tuple[ToolPlanStep, Any]:
                arguments = self._resolve(step.arguments, outputs)
                result = await self._invoke(
                    self._dispatch,
                    step.tool_name,
                    arguments,
                    idempotency_key=f"{execution_id}:{step.step_id}",
                )
                return step, result

            try:
                wave_results = await asyncio.gather(*(dispatch(item) for item in ready))
            except Exception as exc:
                return LocalReasoningResult(
                    phase=ReasoningPhase.FAILED,
                    terminal_reason=f"tool_step_failed:{type(exc).__name__}",
                    checkpoint_cursor=plan_hash,
                    call_count=calls + len(ready),
                    safe_evidence={"cancelled_dependency_ids": [
                        item.step_id
                        for item in ordered
                        if item.step_id not in completed_ids
                    ]},
                )
            calls += len(wave_results)
            for step, output in wave_results:
                outputs[step.output_variable] = output
                completed_ids.add(step.step_id)
            if self._checkpoint is not None:
                await self._invoke(
                    self._checkpoint,
                    plan_hash,
                    tuple(sorted(completed_ids)),
                    tuple(sorted(outputs)),
                )
        answer = str(await self._invoke(synthesize, outputs))
        calls += 1
        return LocalReasoningResult(
            phase=ReasoningPhase.COMPLETED,
            answer=answer,
            checkpoint_cursor=plan_hash,
            call_count=calls,
            node_count=len(ordered),
            safe_evidence={
                "plan_hash": plan_hash,
                "completed_step_ids": [item.step_id for item in ordered],
                "output_variables": [item.output_variable for item in ordered],
            },
        )


__all__ = ["ReWOOAdapter", "ReWOORuntime"]
