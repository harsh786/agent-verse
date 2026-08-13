"""Shared immutable state and checkpoint helpers for durable coordination patterns."""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent.patterns.reasoning_contracts import topological_order


class DurableWorkItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    work_item_id: str = Field(min_length=1)
    safe_summary: str = Field(min_length=1, max_length=2_000)
    dependencies: tuple[str, ...] = ()
    state: Literal["pending", "running", "completed", "failed", "cancelled"] = "pending"
    child_goal_id: str | None = None
    result_reference: str | None = None
    evidence_references: tuple[str, ...] = ()
    attempt: int = Field(default=0, ge=0)


class DurablePatternState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    phase: str
    work_items: tuple[DurableWorkItem, ...]
    synthesis_reference: str | None = None
    synthesis_output: str | None = Field(default=None, max_length=8_000)
    terminal_reason: str | None = None
    checkpoint_version: int = 1


def ordered_work_items(items: tuple[DurableWorkItem, ...]) -> tuple[DurableWorkItem, ...]:
    return topological_order(
        items,
        id_of=lambda item: item.work_item_id,
        dependencies_of=lambda item: item.dependencies,
    )


class InMemoryPatternCheckpointStore:
    def __init__(self) -> None:
        self._states: dict[tuple[str, str], BaseModel] = {}
        self._lock = asyncio.Lock()

    async def save(self, state: Any) -> None:
        async with self._lock:
            session_id = str(state.session_id)
            execution_id = str(state.execution_id)
            self._states[(session_id, execution_id)] = state

    async def load(self, session_id: str, execution_id: str) -> BaseModel | None:
        return self._states.get((session_id, execution_id))


async def invoke(callback: Any, *args: Any, **kwargs: Any) -> Any:
    import inspect

    value = callback(*args, **kwargs)
    return await value if inspect.isawaitable(value) else value


__all__ = [
    "DurablePatternState",
    "DurableWorkItem",
    "InMemoryPatternCheckpointStore",
    "invoke",
    "ordered_work_items",
]
