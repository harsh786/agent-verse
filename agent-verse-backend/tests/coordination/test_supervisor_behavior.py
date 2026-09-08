"""Behavioral tests for the durable Supervisor adapter's parallel wave semantics."""

from __future__ import annotations

import asyncio

import pytest

from app.coordination.patterns.common import (
    DurableWorkItem,
    InMemoryPatternCheckpointStore,
)
from app.coordination.patterns.supervisor_adapter import DurableSupervisorRuntime


def _child_result(item: DurableWorkItem) -> dict[str, object]:
    return {
        "child_goal_id": f"goal-{item.work_item_id}",
        "result_reference": f"result://{item.work_item_id}",
        "evidence_references": (f"evidence://{item.work_item_id}",),
    }


@pytest.mark.asyncio
async def test_supervisor_dispatches_independent_items_in_one_parallel_wave() -> None:
    store = InMemoryPatternCheckpointStore()
    concurrent = 0
    peak = 0
    dispatched: list[str] = []
    items = (
        DurableWorkItem(work_item_id="a", safe_summary="a"),
        DurableWorkItem(work_item_id="b", safe_summary="b"),
        DurableWorkItem(work_item_id="c", safe_summary="c"),
    )

    async def child(item: DurableWorkItem) -> dict[str, object]:
        nonlocal concurrent, peak
        dispatched.append(item.work_item_id)
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        concurrent -= 1
        return _child_result(item)

    runtime = DurableSupervisorRuntime(checkpoint_store=store, maximum_parallel=5)
    state, answer = await runtime.execute(
        session_id="s",
        execution_id="parallel",
        goal="g",
        decompose=lambda _goal: items,
        run_child=child,
        synthesize=lambda done: "+".join(i.work_item_id for i in done),
    )
    # All three items are independent, so they must be in flight simultaneously.
    assert peak == 3
    assert set(dispatched) == {"a", "b", "c"}
    assert state.phase == "completed" and answer == "a+b+c"
    assert all(item.state == "completed" for item in state.work_items)


@pytest.mark.asyncio
async def test_supervisor_bounds_wave_width_by_maximum_parallel() -> None:
    store = InMemoryPatternCheckpointStore()
    concurrent = 0
    peak = 0
    items = tuple(DurableWorkItem(work_item_id=str(n), safe_summary=str(n)) for n in range(5))

    async def child(item: DurableWorkItem) -> dict[str, object]:
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        concurrent -= 1
        return _child_result(item)

    runtime = DurableSupervisorRuntime(checkpoint_store=store, maximum_parallel=2)
    state, _ = await runtime.execute(
        session_id="s",
        execution_id="bounded",
        goal="g",
        decompose=lambda _goal: items,
        run_child=child,
        synthesize=lambda done: str(len(done)),
    )
    # Five independent items with a width cap of two must never exceed two in flight.
    assert peak == 2
    assert state.phase == "completed"


@pytest.mark.asyncio
async def test_supervisor_runs_dependency_waves_in_topological_order() -> None:
    store = InMemoryPatternCheckpointStore()
    order: list[str] = []
    # Diamond DAG: a -> {b, c} -> d
    items = (
        DurableWorkItem(work_item_id="a", safe_summary="a"),
        DurableWorkItem(work_item_id="b", safe_summary="b", dependencies=("a",)),
        DurableWorkItem(work_item_id="c", safe_summary="c", dependencies=("a",)),
        DurableWorkItem(work_item_id="d", safe_summary="d", dependencies=("b", "c")),
    )

    async def child(item: DurableWorkItem) -> dict[str, object]:
        order.append(item.work_item_id)
        return _child_result(item)

    runtime = DurableSupervisorRuntime(checkpoint_store=store)
    state, _ = await runtime.execute(
        session_id="s",
        execution_id="diamond",
        goal="g",
        decompose=lambda _goal: items,
        run_child=child,
        synthesize=lambda done: "ok",
    )
    assert state.phase == "completed"
    # a strictly before b, c; both strictly before d.
    assert order[0] == "a"
    assert order[-1] == "d"
    assert set(order[1:3]) == {"b", "c"}
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


@pytest.mark.asyncio
async def test_supervisor_cancellation_checkpoints_partial_and_resume_finishes_rest() -> None:
    store = InMemoryPatternCheckpointStore()
    calls: list[str] = []
    cancel = asyncio.Event()
    items = (
        DurableWorkItem(work_item_id="a", safe_summary="a"),
        DurableWorkItem(work_item_id="b", safe_summary="b", dependencies=("a",)),
    )

    async def child(item: DurableWorkItem) -> dict[str, object]:
        calls.append(item.work_item_id)
        if item.work_item_id == "a":
            # Cancel after the first wave completes so the second wave is skipped.
            cancel.set()
        return _child_result(item)

    runtime = DurableSupervisorRuntime(checkpoint_store=store)
    stopped, answer = await runtime.execute(
        session_id="s",
        execution_id="resumable",
        goal="g",
        decompose=lambda _goal: items,
        run_child=child,
        synthesize=lambda done: "must-not-run",
        cancelled=cancel,
    )
    assert stopped.phase == "cancelled" and stopped.terminal_reason == "cancelled"
    assert answer is None
    assert calls == ["a"]
    # 'a' is durably completed, 'b' is still pending in the checkpoint.
    by_id = {item.work_item_id: item for item in stopped.work_items}
    assert by_id["a"].state == "completed" and by_id["b"].state == "pending"

    cancel.clear()
    resumed, answer = await runtime.execute(
        session_id="s",
        execution_id="resumable",
        goal="g",
        decompose=lambda _goal: pytest.fail("must not re-decompose after resume"),
        run_child=child,
        synthesize=lambda done: ",".join(i.work_item_id for i in done),
        cancelled=cancel,
    )
    assert resumed.phase == "completed" and answer == "a,b"
    # 'a' is not re-executed on resume; only the remaining 'b' runs.
    assert calls == ["a", "b"]
