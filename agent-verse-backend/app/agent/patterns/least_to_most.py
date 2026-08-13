"""Deterministic, resumable Least-to-Most reasoning adapter."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any

from app.agent.patterns.reasoning_contracts import (
    LocalReasoningResult,
    ReasoningPhase,
    SubproblemState,
    validate_subproblems,
)
from app.orchestration.strategy_adapters import ExecutionTier


@dataclass(frozen=True, slots=True)
class LeastToMostAdapter:
    strategy_id: str = "least_to_most"
    execution_tier: ExecutionTier = ExecutionTier.LOCAL

    def create_runtime(self, **kwargs: Any) -> LeastToMostRuntime:
        return LeastToMostRuntime(**kwargs)


class LeastToMostRuntime:
    def __init__(self, *, checkpoint_callback: Any = None) -> None:
        self._checkpoint = checkpoint_callback

    async def _invoke(self, callback: Any, *args: Any) -> Any:
        result = callback(*args)
        return await result if inspect.isawaitable(result) else result

    async def _save(self, cursor: str, completed_ids: tuple[str, ...]) -> None:
        if self._checkpoint is not None:
            await self._invoke(self._checkpoint, cursor, completed_ids)

    async def execute(
        self,
        *,
        subproblems: tuple[SubproblemState, ...],
        solve: Any,
        synthesize: Any,
        completed_answers: dict[str, str] | None = None,
        cancelled: asyncio.Event | None = None,
    ) -> LocalReasoningResult:
        ordered = validate_subproblems(subproblems)
        answers = dict(completed_answers or {})
        await self._save("decomposition_validated", tuple(sorted(answers)))
        calls = 0
        for subproblem in ordered:
            if subproblem.subproblem_id in answers:
                continue
            if cancelled is not None and cancelled.is_set():
                return LocalReasoningResult(
                    phase=ReasoningPhase.CANCELLED,
                    terminal_reason="cancelled_between_subproblems",
                    checkpoint_cursor=subproblem.subproblem_id,
                    call_count=calls,
                )
            dependency_answers = {
                dependency: answers[dependency]
                for dependency in subproblem.depends_on
                if dependency in answers
            }
            if len(dependency_answers) != len(subproblem.depends_on):
                return LocalReasoningResult(
                    phase=ReasoningPhase.FAILED,
                    terminal_reason="dependency_incomplete",
                    checkpoint_cursor=subproblem.subproblem_id,
                    call_count=calls,
                )
            cumulative_summary = " | ".join(
                f"{item_id}:{answers[item_id][:240]}" for item_id in tuple(answers)[-4:]
            )[:1_200]
            try:
                answer = await self._invoke(
                    solve,
                    subproblem,
                    dependency_answers,
                    cumulative_summary,
                )
            except Exception as exc:
                return LocalReasoningResult(
                    phase=ReasoningPhase.FAILED,
                    terminal_reason=f"subproblem_failed:{type(exc).__name__}",
                    checkpoint_cursor=subproblem.subproblem_id,
                    call_count=calls + 1,
                )
            calls += 1
            answers[subproblem.subproblem_id] = str(answer)
            await self._save(subproblem.subproblem_id, tuple(answers))
        final_answer = str(await self._invoke(synthesize, answers))
        calls += 1
        return LocalReasoningResult(
            phase=ReasoningPhase.COMPLETED,
            answer=final_answer,
            checkpoint_cursor=ordered[-1].subproblem_id,
            call_count=calls,
            node_count=len(ordered),
            safe_evidence={
                "subproblem_ids": [item.subproblem_id for item in ordered],
                "completed_ids": list(answers),
            },
        )


__all__ = ["LeastToMostAdapter", "LeastToMostRuntime"]
