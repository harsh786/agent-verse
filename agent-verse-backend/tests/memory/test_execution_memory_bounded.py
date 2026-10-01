"""MEM-08: the process-wide ExecutionMemory cache is bounded and single-appended."""

from __future__ import annotations

from app.agent.state import AgentState, StepResult, StepStatus
from app.memory.execution import MAX_ENTRIES_PER_TENANT, MAX_TENANTS, ExecutionMemory
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext


def _ctx(tid: str) -> TenantContext:
    return TenantContext(tenant_id=tid, plan=PlanTier.PROFESSIONAL, api_key_id="k")


async def test_500_goals_leave_at_most_the_cap() -> None:
    mem = ExecutionMemory()
    for i in range(500):
        mem.record(goal=f"g{i}", plan=["a"], tenant_ctx=_ctx("t1"))
        await mem.record_async(goal=f"h{i}", plan=["a"], success=True, tenant_id="t1")
        mem.record_failure(goal=f"f{i}", failed_step="s", error="e", tenant_ctx=_ctx("t1"))
    assert len(mem._plans["t1"]) <= MAX_ENTRIES_PER_TENANT
    assert len(mem._failures["t1"]) <= MAX_ENTRIES_PER_TENANT
    assert len(mem._memories["t1"]) <= MAX_ENTRIES_PER_TENANT
    # Newest kept.
    assert mem._plans["t1"][-1]["goal"] == "h499"


def test_tenant_count_is_bounded_lru() -> None:
    mem = ExecutionMemory()
    for i in range(MAX_TENANTS + 50):
        mem.record(goal="g", plan=["a"], tenant_ctx=_ctx(f"t{i}"))
    assert len(mem._plans) <= MAX_TENANTS
    assert f"t{MAX_TENANTS + 49}" in mem._plans and "t0" not in mem._plans


async def test_successful_goal_is_appended_once() -> None:
    from app.agent.graph import AgentGraph

    class _OkDb:
        """A DB that accepts the INSERT."""

        def __call__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *a):  # type: ignore[no-untyped-def]
            return None

        def begin(self):  # type: ignore[no-untyped-def]
            return self

        async def execute(self, *a, **k):  # type: ignore[no-untyped-def]
            return None

    t = _ctx("t-once")
    mem = ExecutionMemory()
    graph = AgentGraph(
        planner=FakeProvider(responses=["plan"]),
        executor=FakeProvider(responses=["done"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        exec_memory=mem,
    )
    graph._db_session_factory = _OkDb()
    st = AgentState(goal="ship it", tenant_ctx=t, goal_id="g1")
    st.plan = ["Step 1: ship"]
    st.steps.append(StepResult(description="ship", status=StepStatus.COMPLETE, output="shipped"))
    await graph._node_verify({"agent_state": st, "tenant_ctx": t})
    assert [p["goal"] for p in mem._plans["t-once"]] == ["ship it"]
