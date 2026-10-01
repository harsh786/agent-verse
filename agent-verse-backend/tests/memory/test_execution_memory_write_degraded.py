"""MEM-07: execution-memory DB write failures are counted and surfaced.

record_async / record_failure_async caught every DB error and only logged it,
so winning plans and failure patterns silently vanished from durable memory.
"""

from __future__ import annotations

from typing import Any

from app.agent.state import AgentState, StepResult, StepStatus
from app.memory.execution import ExecutionMemory
from app.observability.metrics import MEMORY_DEGRADED_TOTAL
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="em-deg", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _BrokenDb:
    def __call__(self) -> Any:
        raise ConnectionError("db down")


def _count() -> float:
    return MEMORY_DEGRADED_TOTAL.labels(store="execution", op="record")._value.get()


async def test_failed_writes_return_false_and_count() -> None:
    mem = ExecutionMemory()
    before = _count()
    ok = await mem.record_async(goal="g", plan=["a"], success=True, tenant_id=T.tenant_id,
                                db=_BrokenDb(), goal_id="goal-1")
    ok2 = await mem.record_failure_async(goal="g", error="e", tenant_id=T.tenant_id,
                                         db=_BrokenDb(), goal_id="goal-1")
    assert ok is False and ok2 is False
    assert _count() == before + 2


async def test_successful_goal_with_failing_write_sets_degraded_flag() -> None:
    from app.agent.graph import AgentGraph

    events: list[dict[str, Any]] = []

    async def _cb(e: dict[str, Any]) -> None:
        events.append(e)

    graph = AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=FakeProvider(responses=["done"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        exec_memory=ExecutionMemory(),
    )
    graph._db_session_factory = _BrokenDb()
    graph._event_callback = _cb  # type: ignore[assignment]
    st = AgentState(goal="ship it", tenant_ctx=T, goal_id="goal-2")
    st.plan = ["Step 1: ship"]
    st.steps.append(StepResult(description="ship", status=StepStatus.COMPLETE, output="shipped"))

    await graph._node_verify({"agent_state": st, "tenant_ctx": T})

    assert "execution_memory_write" in st.context.get("memory_degraded", [])
    assert any(e.get("type") == "memory_degraded" and e.get("source") == "execution_memory_write"
               for e in events)
