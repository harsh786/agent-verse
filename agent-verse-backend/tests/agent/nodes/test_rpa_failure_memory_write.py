"""MEM-37: an RPA failure is written to execution memory awaited, and a lost
write marks the goal memory-degraded (it was a fire-and-forget task)."""

from __future__ import annotations

from typing import Any

from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.memory.execution import ExecutionMemory
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext
from tests._rls_recorder import RlsRecordingDb

T = TenantContext(tenant_id="t-rpa", plan=PlanTier.PROFESSIONAL, api_key_id="k")


def _graph(db: Any) -> AgentGraph:
    p = FakeProvider()
    graph = AgentGraph(planner=p, executor=p, verifier=p, exec_memory=ExecutionMemory())
    graph._db_session_factory = db
    return graph


async def test_rpa_failure_is_written_before_returning() -> None:
    db = RlsRecordingDb()
    state = AgentState(goal="scrape pricing page", tenant_ctx=T, goal_id="g1")
    await _graph(db)._record_rpa_failure(
        state, T, tool_name="browser.click", url="https://x.test", error="timeout"
    )
    (stmt,) = db.touching("INSERT INTO execution_memory")
    assert "browser.click" in stmt.params["plan"]
    assert "execution_memory_write" not in state.context.get("memory_degraded", [])


async def test_lost_rpa_failure_write_marks_memory_degraded() -> None:
    class _BrokenDb:
        def __call__(self) -> Any:
            raise ConnectionError("db down")

    state = AgentState(goal="scrape pricing page", tenant_ctx=T, goal_id="g1")
    await _graph(_BrokenDb())._record_rpa_failure(
        state, T, tool_name="browser.click", url="https://x.test", error="timeout"
    )
    assert "execution_memory_write" in state.context["memory_degraded"]
