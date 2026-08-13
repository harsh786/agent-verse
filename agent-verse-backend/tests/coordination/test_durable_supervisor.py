from __future__ import annotations

import pytest

from app.coordination.patterns.common import (
    DurableWorkItem,
    InMemoryPatternCheckpointStore,
)
from app.coordination.patterns.supervisor_adapter import DurableSupervisorRuntime


@pytest.mark.asyncio
async def test_supervisor_checkpoints_waves_and_restart_skips_completed_children() -> None:
    store = InMemoryPatternCheckpointStore()
    calls: list[str] = []
    items = (
        DurableWorkItem(work_item_id="a", safe_summary="first"),
        DurableWorkItem(work_item_id="b", safe_summary="second", dependencies=("a",)),
    )

    async def child(item: DurableWorkItem):
        calls.append(item.work_item_id)
        return {
            "child_goal_id": f"goal-{item.work_item_id}",
            "result_reference": f"result://{item.work_item_id}",
            "evidence_references": (f"evidence://{item.work_item_id}",),
        }

    runtime = DurableSupervisorRuntime(checkpoint_store=store)
    state, answer = await runtime.execute(
        session_id="session",
        execution_id="execution",
        goal="goal",
        decompose=lambda _goal: items,
        run_child=child,
        synthesize=lambda completed: ",".join(item.work_item_id for item in completed),
    )
    assert state.phase == "completed" and answer == "a,b" and calls == ["a", "b"]
    resumed, answer = await runtime.execute(
        session_id="session",
        execution_id="execution",
        goal="goal",
        decompose=lambda _goal: pytest.fail("must not decompose"),
        run_child=lambda _item: pytest.fail("must not repeat child"),
        synthesize=lambda _completed: pytest.fail("must not repeat synthesis"),
    )
    assert resumed.phase == "completed" and answer == "a,b" and calls == ["a", "b"]


@pytest.mark.asyncio
async def test_supervisor_rejects_cycle_before_child_dispatch_and_fails_child() -> None:
    runtime = DurableSupervisorRuntime(checkpoint_store=InMemoryPatternCheckpointStore())
    with pytest.raises(ValueError, match="cycle"):
        await runtime.execute(
            session_id="s",
            execution_id="cycle",
            goal="g",
            decompose=lambda _: (
                DurableWorkItem(work_item_id="a", safe_summary="a", dependencies=("b",)),
                DurableWorkItem(work_item_id="b", safe_summary="b", dependencies=("a",)),
            ),
            run_child=lambda _: pytest.fail("must not dispatch"),
            synthesize=lambda _: "x",
        )
    failed, _ = await runtime.execute(
        session_id="s",
        execution_id="failure",
        goal="g",
        decompose=lambda _: (DurableWorkItem(work_item_id="a", safe_summary="a"),),
        run_child=lambda _: (_ for _ in ()).throw(RuntimeError("child failed")),
        synthesize=lambda _: "x",
    )
    assert failed.phase == "failed" and failed.terminal_reason == "child_failed"
