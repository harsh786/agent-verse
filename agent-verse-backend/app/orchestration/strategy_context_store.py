"""Bridges free-form goal text/context to the durable StrategyExecutionRequest contract.

``StrategyExecutionRequest`` (see ``strategy_contracts.py``) intentionally carries only
stable references (``context_snapshot_ref``, ``policy_ref``, ``budget_ref``, ...) rather than
raw content, because the contract is designed to be persisted, replayed, and checkpointed.
The literal goal text and the tenant's resolved LLM provider are not safe to embed in that
frozen, audited record, so this in-memory store lets the caller (``GoalService``) hand them to
the wired ``Executor`` out-of-band, keyed by the same ``context_snapshot_ref`` the request
carries.

This store is intentionally process-local and non-durable: it exists only to pass live Python
objects (a provider instance, goal text) across the boundary between "build a strategy
execution request" and "the executor that runs it", both of which happen in the same process
for a single goal. It is not a substitute for the checkpoint store used for adapter state.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class StrategyGoalContext:
    """Everything the wired Executor needs to actually run a strategy for one goal."""

    goal_text: str
    provider: Any
    initial_context: dict[str, Any] = field(default_factory=dict)


class StrategyGoalContextStore:
    """Process-local registry of :class:`StrategyGoalContext` keyed by context_snapshot_ref."""

    def __init__(self) -> None:
        self._entries: dict[str, StrategyGoalContext] = {}
        self._lock = asyncio.Lock()

    async def put(self, context_snapshot_ref: str, context: StrategyGoalContext) -> None:
        async with self._lock:
            self._entries[context_snapshot_ref] = context

    def get(self, context_snapshot_ref: str) -> StrategyGoalContext | None:
        return self._entries.get(context_snapshot_ref)

    async def discard(self, context_snapshot_ref: str) -> None:
        async with self._lock:
            self._entries.pop(context_snapshot_ref, None)


__all__ = ["StrategyGoalContext", "StrategyGoalContextStore"]
