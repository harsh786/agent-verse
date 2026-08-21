"""Restartable Supervisor adapter backed by durable work-item checkpoints."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.coordination.patterns.common import (
    DurablePatternState,
    DurableWorkItem,
    invoke,
    ordered_work_items,
)
from app.orchestration.strategy_adapters import ExecutionTier


@dataclass(frozen=True, slots=True)
class DurableSupervisorAdapter:
    strategy_id: str = "supervisor"
    execution_tier: ExecutionTier = ExecutionTier.DISTRIBUTED

    def create_runtime(self, **kwargs: Any) -> DurableSupervisorRuntime:
        return DurableSupervisorRuntime(**kwargs)


class DurableSupervisorRuntime:
    def __init__(self, *, checkpoint_store: Any, maximum_parallel: int = 5) -> None:
        self._store = checkpoint_store
        self._maximum_parallel = max(1, min(maximum_parallel, 8))

    async def execute(
        self,
        *,
        session_id: str,
        execution_id: str,
        goal: str,
        decompose: Any,
        run_child: Any,
        synthesize: Any,
        cancelled: asyncio.Event | None = None,
    ) -> tuple[DurablePatternState, str | None]:
        state = await self._store.load(session_id, execution_id)
        if state is not None and state.phase == "completed":
            return state, state.synthesis_output
        if state is None:
            raw_items = tuple(await invoke(decompose, goal))
            items = ordered_work_items(
                tuple(
                    item
                    if isinstance(item, DurableWorkItem)
                    else DurableWorkItem.model_validate(item)
                    for item in raw_items
                )
            )
            state = DurablePatternState(
                session_id=session_id,
                execution_id=execution_id,
                phase="decomposed",
                work_items=items,
            )
            await self._store.save(state)
        completed = {item.work_item_id for item in state.work_items if item.state == "completed"}
        items_by_id = {item.work_item_id: item for item in state.work_items}
        while len(completed) < len(items_by_id):
            if cancelled is not None and cancelled.is_set():
                stopped = state.model_copy(
                    update={"phase": "cancelled", "terminal_reason": "cancelled"}
                )
                await self._store.save(stopped)
                return stopped, None
            ready = [
                item
                for item in state.work_items
                if item.state == "pending" and set(item.dependencies).issubset(completed)
            ][: self._maximum_parallel]
            if not ready:
                failed = state.model_copy(
                    update={"phase": "failed", "terminal_reason": "dependency_deadlock"}
                )
                await self._store.save(failed)
                return failed, None

            async def execute_one(item: DurableWorkItem) -> tuple[str, Any]:
                return item.work_item_id, await invoke(run_child, item)

            results = await asyncio.gather(
                *(execute_one(item) for item in ready), return_exceptions=True
            )
            for item, result in zip(ready, results, strict=True):
                if isinstance(result, BaseException):
                    updated = item.model_copy(
                        update={"state": "failed", "attempt": item.attempt + 1}
                    )
                else:
                    _, child = result
                    updated = item.model_copy(
                        update={
                            "state": "completed",
                            "attempt": item.attempt + 1,
                            "child_goal_id": str(child["child_goal_id"]),
                            "result_reference": str(child["result_reference"]),
                            "evidence_references": tuple(child.get("evidence_references", ())),
                        }
                    )
                    completed.add(item.work_item_id)
                items_by_id[item.work_item_id] = updated
            state = state.model_copy(
                update={
                    "phase": "executing",
                    "work_items": tuple(
                        items_by_id[item.work_item_id] for item in state.work_items
                    ),
                }
            )
            await self._store.save(state)
            if any(item.state == "failed" for item in state.work_items):
                failed = state.model_copy(
                    update={"phase": "failed", "terminal_reason": "child_failed"}
                )
                await self._store.save(failed)
                return failed, None
        answer = str(await invoke(synthesize, state.work_items))
        completed_state = state.model_copy(
            update={
                "phase": "completed",
                "synthesis_reference": f"result://{execution_id}",
                "synthesis_output": answer[:8_000],
            }
        )
        await self._store.save(completed_state)
        return completed_state, answer


__all__ = ["DurableSupervisorAdapter", "DurableSupervisorRuntime"]
