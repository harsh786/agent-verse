"""Bounded AutoGPT controller with mandatory pre-execution gates."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.coordination.patterns.common import invoke
from app.orchestration.strategy_adapters import ExecutionTier


class AutoGPTState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    execution_id: str
    phase: Literal[
        "planning", "executing", "completed", "awaiting_human", "failed", "cancelled"
    ] = "planning"
    action_count: int = Field(default=0, ge=0)
    stagnation_count: int = Field(default=0, ge=0)
    last_result_digest: str = ""
    safe_output: str | None = Field(default=None, max_length=8_000)
    terminal_reason: str | None = None
    checkpoint_version: int = 1


@dataclass(frozen=True, slots=True)
class AutoGPTAdapter:
    strategy_id: str = "autogpt"
    execution_tier: ExecutionTier = ExecutionTier.DISTRIBUTED

    def create_runtime(self, **kwargs: Any) -> AutoGPTRuntime:
        return AutoGPTRuntime(**kwargs)


class AutoGPTRuntime:
    def __init__(self, *, checkpoint_store: Any) -> None:
        self._checkpoints = checkpoint_store

    async def execute(
        self,
        *,
        session_id: str,
        execution_id: str,
        objective: str,
        plan_action: Any,
        pre_execution_gate: Any,
        execute_action: Any,
        maximum_actions: int,
        maximum_stagnation: int,
        cancelled: asyncio.Event | None = None,
    ) -> AutoGPTState:
        loaded = await self._checkpoints.load(session_id, execution_id)
        state = (
            loaded
            if isinstance(loaded, AutoGPTState)
            else AutoGPTState.model_validate(loaded.model_dump())
            if loaded is not None
            else AutoGPTState(session_id=session_id, execution_id=execution_id)
        )
        if state.phase in {"completed", "failed", "cancelled", "awaiting_human"}:
            return state
        while state.action_count < maximum_actions:
            if cancelled is not None and cancelled.is_set():
                state = state.model_copy(
                    update={"phase": "cancelled", "terminal_reason": "cancelled"}
                )
                await self._checkpoints.save(state)
                return state
            action = dict(await invoke(plan_action, objective, state.action_count))
            if not await invoke(pre_execution_gate, action):
                state = state.model_copy(
                    update={"phase": "failed", "terminal_reason": "pre_execution_denied"}
                )
                await self._checkpoints.save(state)
                return state
            result = dict(await invoke(execute_action, action))
            safe_output = str(result.get("safe_output", ""))[:8_000]
            digest = hashlib.sha256(safe_output.encode()).hexdigest()
            stagnation = state.stagnation_count + 1 if digest == state.last_result_digest else 0
            state = state.model_copy(
                update={
                    "phase": "executing",
                    "action_count": state.action_count + 1,
                    "stagnation_count": stagnation,
                    "last_result_digest": digest,
                    "safe_output": safe_output or state.safe_output,
                }
            )
            await self._checkpoints.save(state)
            if result.get("completed"):
                state = state.model_copy(update={"phase": "completed"})
                await self._checkpoints.save(state)
                return state
            if stagnation >= maximum_stagnation:
                state = state.model_copy(
                    update={"phase": "awaiting_human", "terminal_reason": "stagnation"}
                )
                await self._checkpoints.save(state)
                return state
        state = state.model_copy(update={"phase": "failed", "terminal_reason": "action_limit"})
        await self._checkpoints.save(state)
        return state


__all__ = ["AutoGPTAdapter", "AutoGPTRuntime", "AutoGPTState"]
