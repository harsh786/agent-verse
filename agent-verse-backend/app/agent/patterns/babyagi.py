"""Bounded durable BabyAGI controller over canonical work items."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.coordination.patterns.common import DurableWorkItem, invoke
from app.orchestration.strategy_adapters import ExecutionTier


class BabyAGIState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    execution_id: str
    phase: Literal["creating", "executing", "completed", "failed", "cancelled"] = "creating"
    work_items: tuple[DurableWorkItem, ...] = ()
    current_index: int = Field(default=0, ge=0)
    terminal_reason: str | None = None
    checkpoint_version: int = 1


@dataclass(frozen=True, slots=True)
class BabyAGIAdapter:
    strategy_id: str = "babyagi"
    execution_tier: ExecutionTier = ExecutionTier.DISTRIBUTED

    def create_runtime(self, **kwargs: Any) -> BabyAGIRuntime:
        return BabyAGIRuntime(**kwargs)


class BabyAGIRuntime:
    def __init__(self, *, checkpoint_store: Any) -> None:
        self._checkpoints = checkpoint_store

    async def execute(
        self,
        *,
        session_id: str,
        execution_id: str,
        objective: str,
        create_tasks: Any,
        execute_task: Any,
        maximum_tasks: int,
        cancelled: asyncio.Event | None = None,
    ) -> BabyAGIState:
        loaded = await self._checkpoints.load(session_id, execution_id)
        state = (
            loaded
            if isinstance(loaded, BabyAGIState)
            else BabyAGIState.model_validate(loaded.model_dump())
            if loaded is not None
            else BabyAGIState(session_id=session_id, execution_id=execution_id)
        )
        if state.phase in {"completed", "failed", "cancelled"}:
            return state
        if not state.work_items:
            raw = tuple(await invoke(create_tasks, objective))
            unique: dict[str, dict[str, Any]] = {}
            for item in raw:
                identifier = str(item["work_item_id"])
                unique.setdefault(identifier, dict(item))
            ordered = sorted(
                unique.values(),
                key=lambda item: (int(item.get("priority", 0)), str(item["work_item_id"])),
            )[:maximum_tasks]
            state = state.model_copy(
                update={
                    "work_items": tuple(
                        DurableWorkItem(
                            work_item_id=str(item["work_item_id"]),
                            safe_summary=str(item["safe_summary"]),
                            dependencies=tuple(item.get("dependencies", ())),
                        )
                        for item in ordered
                    )
                }
            )
            await self._checkpoints.save(state)
        for index in range(state.current_index, len(state.work_items)):
            if cancelled is not None and cancelled.is_set():
                state = state.model_copy(
                    update={"phase": "cancelled", "terminal_reason": "cancelled"}
                )
                await self._checkpoints.save(state)
                return state
            item = state.work_items[index].model_copy(update={"state": "running", "attempt": 1})
            result = dict(await invoke(execute_task, item))
            completed = item.model_copy(
                update={"state": "completed", "result_reference": str(result["result_reference"])}
            )
            items = list(state.work_items)
            items[index] = completed
            state = state.model_copy(
                update={
                    "phase": "executing",
                    "work_items": tuple(items),
                    "current_index": index + 1,
                }
            )
            await self._checkpoints.save(state)
            if result.get("objective_complete"):
                state = state.model_copy(update={"phase": "completed"})
                await self._checkpoints.save(state)
                return state
        state = state.model_copy(update={"phase": "failed", "terminal_reason": "task_limit"})
        await self._checkpoints.save(state)
        return state


__all__ = ["BabyAGIAdapter", "BabyAGIRuntime", "BabyAGIState"]
