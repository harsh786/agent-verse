"""Bounded Language Agent Tree Search (LATS) adapter."""

from __future__ import annotations

import asyncio
import inspect
import math
from dataclasses import dataclass
from typing import Any

from app.agent.patterns.reasoning_contracts import (
    LocalReasoningResult,
    ReasoningPhase,
    SearchNodeState,
)
from app.orchestration.strategy_adapters import ExecutionTier

UCT_EXPLORATION = 1.41421356237


@dataclass(frozen=True, slots=True)
class LATSAdapter:
    strategy_id: str = "lats"
    execution_tier: ExecutionTier = ExecutionTier.LOCAL

    def create_runtime(self, **kwargs: Any) -> LATSRuntime:
        return LATSRuntime(**kwargs)


class LATSRuntime:
    def __init__(self, *, checkpoint_callback: Any = None) -> None:
        self._checkpoint = checkpoint_callback

    @staticmethod
    def uct_score(parent_visits: int, child: SearchNodeState) -> float:
        if child.visits == 0:
            return math.inf
        mean = child.value_sum / child.visits
        exploration = UCT_EXPLORATION * math.sqrt(
            math.log(max(parent_visits, 1)) / child.visits
        )
        return mean + exploration

    @classmethod
    def select_child(
        cls, parent: SearchNodeState, children: tuple[SearchNodeState, ...]
    ) -> SearchNodeState:
        if not children:
            raise ValueError("selection requires at least one child")
        return sorted(
            children,
            key=lambda child: (-cls.uct_score(parent.visits, child), child.node_id),
        )[0]

    @staticmethod
    def backpropagate(
        nodes: dict[str, SearchNodeState], selected_id: str, reward: float
    ) -> dict[str, SearchNodeState]:
        clamped = max(0.0, min(1.0, reward))
        updated = dict(nodes)
        current_id: str | None = selected_id
        while current_id is not None:
            current = updated[current_id]
            updated[current_id] = current.model_copy(
                update={
                    "visits": current.visits + 1,
                    "value_sum": current.value_sum + clamped,
                }
            )
            current_id = current.parent_id
        return updated

    async def _invoke(self, callback: Any, *args: Any) -> Any:
        value = callback(*args)
        return await value if inspect.isawaitable(value) else value

    async def execute(
        self,
        *,
        root: SearchNodeState,
        expand: Any,
        rollout: Any,
        evaluate: Any,
        synthesize: Any,
        nodes: dict[str, SearchNodeState] | None = None,
        completed_simulations: int = 0,
        cancelled: asyncio.Event | None = None,
        max_simulations: int = 24,
    ) -> LocalReasoningResult:
        search_nodes = dict(nodes or {root.node_id: root})
        calls = 0
        selected = root
        for simulation in range(completed_simulations + 1, min(max_simulations, 24) + 1):
            if cancelled is not None and cancelled.is_set():
                return LocalReasoningResult(
                    phase=ReasoningPhase.CANCELLED,
                    terminal_reason="cancelled_between_simulations",
                    checkpoint_cursor=f"{simulation - 1}:{selected.node_id}:complete",
                    call_count=calls,
                    node_count=len(search_nodes),
                )
            children = tuple(
                node for node in search_nodes.values() if node.parent_id == selected.node_id
            )
            if not children:
                expanded = tuple(await self._invoke(expand, selected))[:4]
                calls += 1
                for child in expanded:
                    if child.node_id in search_nodes:
                        raise ValueError(f"duplicate search node: {child.node_id}")
                    if child.depth > 6 or len(search_nodes) >= 32:
                        return LocalReasoningResult(
                            phase=ReasoningPhase.LIMIT_EXCEEDED,
                            terminal_reason="node_or_depth_limit",
                            call_count=calls,
                            node_count=len(search_nodes),
                        )
                    search_nodes[child.node_id] = child
                children = expanded
            if not children:
                return LocalReasoningResult(
                    phase=ReasoningPhase.FAILED,
                    terminal_reason="expansion_empty",
                    call_count=calls,
                    node_count=len(search_nodes),
                )
            selected = self.select_child(search_nodes[selected.node_id], children)
            observation = await self._invoke(rollout, selected)
            calls += 1
            reward = float(await self._invoke(evaluate, selected, observation))
            calls += 1
            search_nodes = self.backpropagate(search_nodes, selected.node_id, reward)
            cursor = f"{simulation}:{selected.node_id}:complete"
            if self._checkpoint is not None:
                await self._invoke(self._checkpoint, cursor, search_nodes)
            if max(0.0, min(1.0, reward)) >= 0.90:
                answer = str(await self._invoke(synthesize, selected, observation))
                calls += 1
                return LocalReasoningResult(
                    phase=ReasoningPhase.COMPLETED,
                    answer=answer,
                    checkpoint_cursor=cursor,
                    call_count=calls,
                    node_count=len(search_nodes),
                    safe_evidence={
                        "selected_node_id": selected.node_id,
                        "reward": max(0.0, min(1.0, reward)),
                        "simulations": simulation,
                    },
                )
            selected = root
        return LocalReasoningResult(
            phase=ReasoningPhase.LIMIT_EXCEEDED,
            terminal_reason="simulation_limit",
            checkpoint_cursor=f"{min(max_simulations, 24)}:{selected.node_id}:complete",
            call_count=calls,
            node_count=len(search_nodes),
        )


__all__ = ["UCT_EXPLORATION", "LATSAdapter", "LATSRuntime"]
