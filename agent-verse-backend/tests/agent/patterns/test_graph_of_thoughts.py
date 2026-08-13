from __future__ import annotations

import asyncio

import pytest

from app.agent.patterns.graph_of_thoughts import GraphOfThoughtsRuntime
from app.agent.patterns.reasoning_contracts import ThoughtEdgeState, ThoughtNodeState


def _node(
    identifier: str,
    score: float,
    *,
    status: str = "candidate",
    depth: int = 0,
) -> ThoughtNodeState:
    return ThoughtNodeState(
        node_id=identifier,
        content_ref=f"artifact://{identifier}",
        safe_summary=f"summary {identifier}",
        score=score,
        depth=depth,
        status=status,
        parent_ids=(),
    )


def test_frontier_order_is_deterministic_and_prunes_low_scores() -> None:
    selected = GraphOfThoughtsRuntime.select_frontier(
        (_node("b", 0.8), _node("a", 0.8), _node("low", 0.54))
    )
    assert [node.node_id for node in selected] == ["a", "b"]


@pytest.mark.asyncio
async def test_terminal_path_completes_and_checkpoints_safe_cursor() -> None:
    checkpoints: list[str] = []

    async def generate(_round: int, _frontier: tuple[str, ...]):
        return (_node("winner", 0.1, status="terminal"),), ()

    async def evaluate(nodes: tuple[ThoughtNodeState, ...]):
        return (nodes[0].model_copy(update={"score": 0.9}),)

    result = await GraphOfThoughtsRuntime(checkpoint_callback=checkpoints.append).execute(
        generate=generate,
        evaluate=evaluate,
        synthesize=lambda selected: f"selected:{selected[0]}",
    )
    assert result.phase == "completed"
    assert result.answer == "selected:winner"
    assert checkpoints and "winner" in checkpoints[0]


@pytest.mark.asyncio
async def test_cycle_fails_closed_and_round_limit_is_typed() -> None:
    a = _node("a", 0.7)
    b = _node("b", 0.7)

    async def cycle(_round: int, _frontier: tuple[str, ...]):
        return (a, b), (
            ThoughtEdgeState(source_id="a", target_id="b", relation="expands"),
            ThoughtEdgeState(source_id="b", target_id="a", relation="expands"),
        )

    failed = await GraphOfThoughtsRuntime().execute(
        generate=cycle, evaluate=lambda nodes: nodes, synthesize=lambda _nodes: "x"
    )
    assert failed.phase == "failed"
    assert "cycle" in str(failed.terminal_reason)

    rounds = 0

    async def generate_one(round_number: int, _frontier: tuple[str, ...]):
        nonlocal rounds
        rounds += 1
        return (_node(f"n{round_number}", 0.7),), ()

    limited = await GraphOfThoughtsRuntime().execute(
        generate=generate_one,
        evaluate=lambda nodes: nodes,
        synthesize=lambda _nodes: "x",
        max_rounds=2,
    )
    assert limited.phase == "limit_exceeded"
    assert rounds == 2


@pytest.mark.asyncio
async def test_cancellation_prevents_generation() -> None:
    cancelled = asyncio.Event()
    cancelled.set()
    result = await GraphOfThoughtsRuntime().execute(
        generate=lambda *_args: pytest.fail("must not generate"),
        evaluate=lambda nodes: nodes,
        synthesize=lambda _nodes: "x",
        cancelled=cancelled,
    )
    assert result.phase == "cancelled"
