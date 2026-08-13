"""Checkpointed bounded generative-agent simulation runtime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.coordination.generative.models import GenerativeState, Persona
from app.coordination.generative.simulation_clock import SimulationClock
from app.coordination.patterns.common import invoke
from app.orchestration.strategy_adapters import ExecutionTier


@dataclass(frozen=True, slots=True)
class GenerativeAgentsAdapter:
    strategy_id: str = "generative_agents"
    execution_tier: ExecutionTier = ExecutionTier.DISTRIBUTED

    def create_runtime(self, **kwargs: Any) -> GenerativeAgentRuntime:
        return GenerativeAgentRuntime(**kwargs)


class GenerativeAgentRuntime:
    def __init__(self, *, checkpoint_store: Any) -> None:
        self._checkpoints = checkpoint_store

    async def execute(
        self,
        *,
        tenant_id: str,
        session_id: str,
        execution_id: str,
        persona: Persona,
        start: datetime,
        step: timedelta,
        maximum_events: int,
        horizon: timedelta,
        run_action: Any,
        cancelled: asyncio.Event | None = None,
    ) -> GenerativeState:
        loaded = await self._checkpoints.load(session_id, execution_id)
        state = (
            loaded
            if isinstance(loaded, GenerativeState)
            else GenerativeState.model_validate(loaded.model_dump())
            if loaded is not None
            else GenerativeState(
                tenant_id=tenant_id,
                session_id=session_id,
                execution_id=execution_id,
                simulation_time=start,
            )
        )
        if state.phase in {"completed", "failed", "cancelled"}:
            return state
        clock = SimulationClock(
            start=state.simulation_time, horizon=start + horizon, maximum_events=maximum_events
        )
        for index in range(state.event_count, maximum_events):
            if cancelled is not None and cancelled.is_set():
                state = state.model_copy(
                    update={"phase": "cancelled", "terminal_reason": "cancelled"}
                )
                await self._checkpoints.save(state)
                return state
            state = state.model_copy(update={"phase": "acting"})
            await self._checkpoints.save(state)
            result = dict(await invoke(run_action, index, persona))
            advanced = clock.advance(step, event_id=f"{execution_id}:{index}")
            state = state.model_copy(
                update={
                    "phase": "advancing_time",
                    "event_count": index + 1,
                    "simulation_time": advanced,
                    "safe_output": str(result.get("safe_output"))[:8_000]
                    if result.get("safe_output") is not None
                    else state.safe_output,
                }
            )
            await self._checkpoints.save(state)
            if result.get("completed"):
                state = state.model_copy(update={"phase": "completed"})
                await self._checkpoints.save(state)
                return state
        state = state.model_copy(update={"phase": "failed", "terminal_reason": "event_limit"})
        await self._checkpoints.save(state)
        return state


__all__ = ["GenerativeAgentRuntime", "GenerativeAgentsAdapter"]
