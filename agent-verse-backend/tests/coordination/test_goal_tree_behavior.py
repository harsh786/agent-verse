"""Behavioral tests proving the Goal Tree adapter enforces DAG dependency waves."""

from __future__ import annotations

import asyncio

import pytest

from app.coordination.patterns.common import (
    DurableWorkItem,
    InMemoryPatternCheckpointStore,
)
from app.coordination.patterns.goal_tree_adapter import DurableGoalTreeRuntime


def _child_result(item: DurableWorkItem) -> dict[str, object]:
    return {
        "child_goal_id": f"goal-{item.work_item_id}",
        "result_reference": f"result://{item.work_item_id}",
        "evidence_references": (),
    }


@pytest.mark.asyncio
async def test_goal_tree_respects_multi_level_dependency_waves() -> None:
    # root -> {left, right}; left -> leaf_l; right -> leaf_r; sink depends on both leaves.
    items = (
        DurableWorkItem(work_item_id="root", safe_summary="root"),
        DurableWorkItem(work_item_id="left", safe_summary="left", dependencies=("root",)),
        DurableWorkItem(work_item_id="right", safe_summary="right", dependencies=("root",)),
        DurableWorkItem(work_item_id="leaf_l", safe_summary="ll", dependencies=("left",)),
        DurableWorkItem(work_item_id="leaf_r", safe_summary="lr", dependencies=("right",)),
        DurableWorkItem(
            work_item_id="sink", safe_summary="sink", dependencies=("leaf_l", "leaf_r")
        ),
    )
    completed_before: dict[str, set[str]] = {}
    done: set[str] = set()

    async def child(item: DurableWorkItem) -> dict[str, object]:
        # Snapshot which dependencies are already complete when this item is dispatched.
        completed_before[item.work_item_id] = set(done)
        await asyncio.sleep(0)
        done.add(item.work_item_id)
        return _child_result(item)

    runtime = DurableGoalTreeRuntime(checkpoint_store=InMemoryPatternCheckpointStore())
    state, _ = await runtime.execute(
        session_id="s",
        execution_id="tree",
        goal="g",
        decompose=lambda _goal: items,
        run_child=child,
        synthesize=lambda finished: "done",
    )
    assert state.phase == "completed"
    # Each item is only dispatched once all of its declared dependencies have completed.
    by_id = {item.work_item_id: item for item in items}
    for work_item in items:
        for dependency in work_item.dependencies:
            assert dependency in completed_before[work_item.work_item_id], (
                f"{work_item.work_item_id} ran before dependency {dependency}"
            )
        assert set(work_item.dependencies).issubset(completed_before[work_item.work_item_id])
    # sink is strictly last since it transitively depends on everything.
    assert completed_before["sink"] >= {"leaf_l", "leaf_r"}
    assert by_id["sink"].dependencies == ("leaf_l", "leaf_r")


@pytest.mark.asyncio
async def test_goal_tree_parallelizes_independent_sibling_branches() -> None:
    items = (
        DurableWorkItem(work_item_id="root", safe_summary="root"),
        DurableWorkItem(work_item_id="left", safe_summary="left", dependencies=("root",)),
        DurableWorkItem(work_item_id="right", safe_summary="right", dependencies=("root",)),
    )
    concurrent = 0
    peak = 0

    async def child(item: DurableWorkItem) -> dict[str, object]:
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        concurrent -= 1
        return _child_result(item)

    runtime = DurableGoalTreeRuntime(checkpoint_store=InMemoryPatternCheckpointStore())
    state, _ = await runtime.execute(
        session_id="s",
        execution_id="siblings",
        goal="g",
        decompose=lambda _goal: items,
        run_child=child,
        synthesize=lambda finished: "done",
    )
    assert state.phase == "completed"
    # left and right have no dependency on each other and must run in the same wave.
    assert peak == 2
