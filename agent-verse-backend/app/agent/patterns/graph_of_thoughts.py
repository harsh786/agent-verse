"""Bounded Graph-of-Thoughts search with safe checkpoint metadata."""

from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from typing import Any

from app.agent.patterns.reasoning_contracts import (
    LocalReasoningResult,
    ReasoningContractError,
    ReasoningPhase,
    ThoughtEdgeState,
    ThoughtNodeState,
    validate_thought_graph,
)
from app.orchestration.strategy_adapters import ExecutionTier


@dataclass(frozen=True, slots=True)
class GraphOfThoughtsAdapter:
    strategy_id: str = "graph_of_thoughts"
    execution_tier: ExecutionTier = ExecutionTier.LOCAL

    def create_runtime(self, **kwargs: Any) -> GraphOfThoughtsRuntime:
        return GraphOfThoughtsRuntime(**kwargs)


class GraphOfThoughtsRuntime:
    def __init__(self, *, checkpoint_callback: Any = None) -> None:
        self._checkpoint = checkpoint_callback

    @staticmethod
    def select_frontier(
        nodes: tuple[ThoughtNodeState, ...], *, maximum: int = 4
    ) -> tuple[ThoughtNodeState, ...]:
        return tuple(
            sorted(
                (node for node in nodes if node.score >= 0.55 and node.status != "pruned"),
                key=lambda node: (-node.score, node.depth, node.node_id),
            )[:maximum]
        )

    @staticmethod
    def serialize_cursor(round_number: int, frontier: tuple[str, ...]) -> str:
        return json.dumps(
            {"round": round_number, "frontier_node_ids": list(frontier)},
            sort_keys=True,
            separators=(",", ":"),
        )

    async def _invoke(self, callback: Any, *args: Any) -> Any:
        value = callback(*args)
        return await value if inspect.isawaitable(value) else value

    async def _save(self, cursor: str) -> None:
        if self._checkpoint is not None:
            await self._invoke(self._checkpoint, cursor)

    async def execute(
        self,
        *,
        generate: Any,
        evaluate: Any,
        synthesize: Any,
        initial_nodes: tuple[ThoughtNodeState, ...] = (),
        initial_edges: tuple[ThoughtEdgeState, ...] = (),
        start_round: int = 0,
        cancelled: asyncio.Event | None = None,
        max_rounds: int = 6,
    ) -> LocalReasoningResult:
        nodes = list(initial_nodes)
        edges = list(initial_edges)
        calls = 0
        for round_number in range(start_round + 1, min(max_rounds, 6) + 1):
            if cancelled is not None and cancelled.is_set():
                return LocalReasoningResult(
                    phase=ReasoningPhase.CANCELLED,
                    terminal_reason="cancelled_between_rounds",
                    call_count=calls,
                    node_count=len(nodes),
                )
            frontier = self.select_frontier(tuple(nodes)) if nodes else ()
            generated_nodes, generated_edges = await self._invoke(
                generate, round_number, tuple(node.node_id for node in frontier)
            )
            calls += 1
            proposed_nodes = [*nodes, *generated_nodes]
            proposed_edges = [*edges, *generated_edges]
            try:
                validate_thought_graph(proposed_nodes, proposed_edges)
            except ReasoningContractError as exc:
                return LocalReasoningResult(
                    phase=ReasoningPhase.FAILED,
                    terminal_reason=f"invalid_thought_graph:{exc}",
                    call_count=calls,
                    node_count=len(nodes),
                )
            scored = tuple(await self._invoke(evaluate, tuple(generated_nodes)))
            calls += 1
            scored_by_id = {node.node_id: node for node in scored}
            nodes = [scored_by_id.get(node.node_id, node) for node in proposed_nodes]
            edges = proposed_edges
            frontier = self.select_frontier(tuple(nodes))
            cursor = self.serialize_cursor(round_number, tuple(node.node_id for node in frontier))
            await self._save(cursor)
            terminal = next(
                (node for node in frontier if node.status == "terminal" and node.score >= 0.85),
                None,
            )
            if terminal is not None:
                answer = str(await self._invoke(synthesize, (terminal.node_id,)))
                calls += 1
                return LocalReasoningResult(
                    phase=ReasoningPhase.COMPLETED,
                    answer=answer,
                    checkpoint_cursor=cursor,
                    call_count=calls,
                    node_count=len(nodes),
                    safe_evidence={
                        "selected_node_ids": [terminal.node_id],
                        "selected_scores": [terminal.score],
                        "pruned_node_ids": [
                            node.node_id
                            for node in nodes
                            if node.score < 0.55 or node.status == "pruned"
                        ],
                    },
                )
            if not frontier:
                return LocalReasoningResult(
                    phase=ReasoningPhase.FAILED,
                    terminal_reason="frontier_empty",
                    checkpoint_cursor=cursor,
                    call_count=calls,
                    node_count=len(nodes),
                )
        return LocalReasoningResult(
            phase=ReasoningPhase.LIMIT_EXCEEDED,
            terminal_reason="round_limit",
            checkpoint_cursor=self.serialize_cursor(
                min(max_rounds, 6),
                tuple(node.node_id for node in self.select_frontier(tuple(nodes))),
            ),
            call_count=calls,
            node_count=len(nodes),
        )


__all__ = ["GraphOfThoughtsAdapter", "GraphOfThoughtsRuntime"]
