from __future__ import annotations

import asyncio
import math

import pytest

from app.agent.patterns.lats import LATSRuntime
from app.agent.patterns.reasoning_contracts import SearchNodeState


def _node(
    identifier: str,
    *,
    parent: str | None = None,
    visits: int = 0,
    value: float = 0.0,
) -> SearchNodeState:
    return SearchNodeState(
        node_id=identifier,
        parent_id=parent,
        action_ref=f"action://{identifier}",
        observation_ref=None,
        visits=visits,
        value_sum=value,
        depth=0 if parent is None else 1,
        is_terminal=False,
    )


def test_uct_unvisited_priority_ties_and_backpropagation_clamp() -> None:
    root = _node("root", visits=10)
    unvisited_a = _node("a", parent="root")
    unvisited_b = _node("b", parent="root")
    assert math.isinf(LATSRuntime.uct_score(10, unvisited_a))
    assert LATSRuntime.select_child(root, (unvisited_b, unvisited_a)).node_id == "a"
    updated = LATSRuntime.backpropagate(
        {"root": root, "a": unvisited_a}, "a", 4.0
    )
    assert updated["a"].visits == 1 and updated["a"].value_sum == 1.0
    assert updated["root"].visits == 11 and updated["root"].value_sum == 1.0


@pytest.mark.asyncio
async def test_completed_simulation_checkpoints_once_and_resume_number() -> None:
    root = _node("root")
    child = _node("child", parent="root")
    checkpoints: list[str] = []
    rollouts: list[str] = []
    result = await LATSRuntime(
        checkpoint_callback=lambda cursor, _nodes: checkpoints.append(cursor)
    ).execute(
        root=root,
        expand=lambda _selected: (child,),
        rollout=lambda selected: rollouts.append(selected.node_id) or "observation",
        evaluate=lambda _selected, _observation: 0.95,
        synthesize=lambda selected, _observation: selected.node_id,
        completed_simulations=3,
    )
    assert result.phase == "completed"
    assert checkpoints == ["4:child:complete"]
    assert rollouts == ["child"]


@pytest.mark.asyncio
async def test_cancellation_and_simulation_limit_are_typed() -> None:
    root = _node("root")
    cancelled = asyncio.Event()
    cancelled.set()
    stopped = await LATSRuntime().execute(
        root=root,
        expand=lambda _node: (),
        rollout=lambda _node: "x",
        evaluate=lambda *_args: 0.0,
        synthesize=lambda *_args: "x",
        cancelled=cancelled,
    )
    assert stopped.phase == "cancelled"
    limited = await LATSRuntime().execute(
        root=root,
        expand=lambda _selected: (_node("child", parent="root"),),
        rollout=lambda _node: "x",
        evaluate=lambda *_args: 0.1,
        synthesize=lambda *_args: "x",
        max_simulations=1,
    )
    assert limited.phase == "limit_exceeded"
