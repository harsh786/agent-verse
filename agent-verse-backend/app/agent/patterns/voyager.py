"""Bounded governed Voyager curriculum and skill synthesis."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.coordination.patterns.common import invoke
from app.memory.procedural_validator import ProcedureContract
from app.orchestration.strategy_adapters import ExecutionTier


class VoyagerState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    execution_id: str
    phase: Literal[
        "curriculum", "executing", "synthesizing", "completed", "failed", "cancelled"
    ] = "curriculum"
    task_index: int = Field(default=0, ge=0)
    evidence_refs: tuple[str, ...] = ()
    published_skill_id: str | None = None
    terminal_reason: str | None = None


@dataclass(frozen=True, slots=True)
class VoyagerAdapter:
    strategy_id: str = "voyager"
    execution_tier: ExecutionTier = ExecutionTier.DISTRIBUTED

    def create_runtime(self, **kwargs: Any) -> VoyagerRuntime:
        return VoyagerRuntime(**kwargs)


class VoyagerRuntime:
    def __init__(self, *, checkpoint_store: Any, skill_store: Any) -> None:
        self._checkpoints = checkpoint_store
        self._skills = skill_store

    async def execute(
        self,
        *,
        session_id: str,
        execution_id: str,
        tenant_id: str,
        capability_gaps: tuple[str, ...],
        run_task: Any,
        synthesize_skill: Any,
        maximum_tasks: int,
        publication_context: dict[str, Any],
        cancelled: asyncio.Event | None = None,
    ) -> VoyagerState:
        loaded = await self._checkpoints.load(session_id, execution_id)
        state = (
            loaded
            if isinstance(loaded, VoyagerState)
            else VoyagerState.model_validate(loaded.model_dump())
            if loaded is not None
            else VoyagerState(session_id=session_id, execution_id=execution_id)
        )
        if state.phase in {"completed", "failed", "cancelled"}:
            return state
        tasks = tuple(sorted(set(capability_gaps)))[:maximum_tasks]
        for index in range(state.task_index, len(tasks)):
            if cancelled is not None and cancelled.is_set():
                state = state.model_copy(
                    update={"phase": "cancelled", "terminal_reason": "cancelled"}
                )
                await self._checkpoints.save(state)
                return state
            result = dict(await invoke(run_task, tasks[index]))
            evidence = str(result.get("evidence_ref", ""))
            if not evidence:
                state = state.model_copy(
                    update={"phase": "failed", "terminal_reason": "missing_evidence"}
                )
                await self._checkpoints.save(state)
                return state
            state = state.model_copy(
                update={
                    "phase": "executing",
                    "task_index": index + 1,
                    "evidence_refs": (*state.evidence_refs, evidence),
                }
            )
            await self._checkpoints.save(state)
        raw_skill = dict(await invoke(synthesize_skill, tasks, state.evidence_refs))
        skill = ProcedureContract(tenant_id=tenant_id, **raw_skill)
        published = self._skills.publish(skill, **publication_context)
        state = state.model_copy(
            update={"phase": "completed", "published_skill_id": published.procedure_id}
        )
        await self._checkpoints.save(state)
        return state


__all__ = ["VoyagerAdapter", "VoyagerRuntime", "VoyagerState"]
