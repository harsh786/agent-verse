"""Agent Runtime 2.0 plans/traces: bounded, shared across replicas, tenant-scoped.

GoalService added one plan and one trace per goal to unbounded module-level
dicts (a slow memory leak), and GET /agent-runtime/traces/{id} 404'd on every
replica except the one that submitted the goal.
"""

from __future__ import annotations

from typing import Any

from app.agent_runtime.models import (
    AgentExecutionPlan,
    AgentRole,
    AgentRunTrace,
    PlanStep,
    RiskLevel,
)
from app.agent_runtime.store import AgentRuntimeStore


class _Redis:
    def __init__(self) -> None:
        self.d: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, k: str) -> str | None:
        return self.d.get(k)

    async def set(self, k: str, v: str, ex: int | None = None) -> None:
        self.d[k] = v
        if ex is not None:
            self.ttls[k] = ex


def _plan(pid: str, tenant: str = "t1") -> AgentExecutionPlan:
    return AgentExecutionPlan(
        plan_id=pid,
        goal_id="g1",
        tenant_id=tenant,
        goal_text="do it",
        steps=[
            PlanStep(
                step_id="step_1",
                description="plan",
                role=AgentRole.PLANNER,
                risk_level=RiskLevel.HIGH,
            )
        ],
    )


def _trace(tid: str, tenant: str = "t1") -> AgentRunTrace:
    return AgentRunTrace(trace_id=tid, goal_id="g1", tenant_id=tenant, plan=_plan("p-" + tid))


async def test_memory_is_bounded() -> None:
    store = AgentRuntimeStore(max_items=10)
    for i in range(50):
        await store.put_trace(_trace(f"tr{i}"))
        await store.put_plan(_plan(f"p{i}"))
    assert len(store._traces) == 10 and len(store._plans) == 10
    assert await store.get_trace("t1", "tr0") is None  # evicted
    assert await store.get_trace("t1", "tr49") is not None


async def test_memory_entries_expire() -> None:
    now = [1000.0]
    store = AgentRuntimeStore(ttl_seconds=60, clock=lambda: now[0])
    await store.put_trace(_trace("tr1"))
    now[0] += 61
    assert await store.get_trace("t1", "tr1") is None


async def test_other_replica_reads_trace_through_redis() -> None:
    redis = _Redis()
    api_a, api_b = AgentRuntimeStore(), AgentRuntimeStore()
    api_a.set_redis(redis)
    api_b.set_redis(redis)
    await api_a.put_trace(_trace("tr1"))
    await api_a.put_plan(_plan("p1"))

    trace = await api_b.get_trace("t1", "tr1")
    plan = await api_b.get_plan("t1", "p1")
    assert trace is not None and trace.goal_id == "g1"
    assert plan is not None and plan.steps[0].role is AgentRole.PLANNER
    assert plan.steps[0].risk_level is RiskLevel.HIGH
    assert all(ttl > 0 for ttl in redis.ttls.values())


async def test_trace_update_is_visible_to_other_replicas() -> None:
    redis = _Redis()
    a, b = AgentRuntimeStore(), AgentRuntimeStore()
    a.set_redis(redis)
    b.set_redis(redis)
    await a.put_trace(_trace("tr1"))
    assert await b.update_trace("t1", "tr1", success=True, duration_ms=12.5) is True
    got = await a.get_trace("t1", "tr1", prefer_shared=True)
    assert got is not None and got.success is True and got.duration_ms == 12.5


async def test_other_tenant_cannot_read() -> None:
    redis = _Redis()
    store = AgentRuntimeStore()
    store.set_redis(redis)
    await store.put_trace(_trace("tr1", tenant="t1"))
    assert await store.get_trace("t2", "tr1") is None
    fresh = AgentRuntimeStore()
    fresh.set_redis(redis)
    assert await fresh.get_trace("t2", "tr1") is None


async def test_redis_failure_falls_back_to_memory() -> None:
    class _Broken:
        async def get(self, _k: str) -> Any:
            raise ConnectionError("down")

        async def set(self, *_a: Any, **_k: Any) -> None:
            raise ConnectionError("down")

    store = AgentRuntimeStore()
    store.set_redis(_Broken())
    await store.put_trace(_trace("tr1"))
    assert await store.get_trace("t1", "tr1") is not None


def test_goal_service_no_longer_uses_unbounded_module_dicts() -> None:
    import app.api.agent_runtime as api

    assert not hasattr(api, "_traces")
    assert not hasattr(api, "_plans")
