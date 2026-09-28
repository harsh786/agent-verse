"""Memory stores run their SQL under the tenant's RLS GUC.

``episodic_memories``, ``procedural_memories`` and ``tool_reliability_memory``
are FORCE ROW LEVEL SECURITY. The API connects as a NOBYPASSRLS role, so a
statement that runs without ``app.tenant_id`` set is rejected (writes) or sees
nothing (reads) — and every store here swallows DB errors, so the failure was
silent: episodes/skills/reliability were never persisted and recall quietly
degraded to the per-process cache.

These are written/read from inside a goal (the tenant is known), so they must
use the tenant GUC — never the maintenance role.
"""

from __future__ import annotations

from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.memory.episodic import EpisodicMemoryStore
from app.memory.procedural import ProceduralMemoryStore
from app.memory.tool_reliability import ToolReliabilityStore
from app.tenancy.context import PlanTier, TenantContext
from tests._rls_recorder import RlsRecordingDb, assert_tenant_scoped

TENANT = "tenant-mem-a"


def _ctx() -> TenantContext:
    return TenantContext(tenant_id=TENANT, plan=PlanTier.PROFESSIONAL, api_key_id="k1")


def _state() -> AgentState:
    state = AgentState(goal="search jira for open tickets", tenant_ctx=_ctx(), goal_id="g-1")
    state.status = GoalStatus.COMPLETE
    step = StepResult(description="search jira", output="Found 5", status=StepStatus.COMPLETE)
    step.tool_calls = [{"tool_name": "jira.search_issues", "success": True}]
    state.steps = [step]
    return state


# ── episodic_memories ──────────────────────────────────────────────────────────


async def test_episodic_record_inserts_under_tenant_guc() -> None:
    db = RlsRecordingDb()
    store = EpisodicMemoryStore(db_factory=db)

    await store.record(state=_state(), tenant_ctx=_ctx(), quality_score=0.8)

    (insert,) = assert_tenant_scoped(db, "episodic_memories", TENANT)
    assert insert.sql.startswith("INSERT INTO episodic_memories")
    assert insert.explicit_txn


async def test_episodic_recall_reads_under_tenant_guc_and_uses_db_rows() -> None:
    rows = [("e1", "g1", "search jira tickets", "summary", "success", "", 0.9, 2, "[]")]
    db = RlsRecordingDb(rows_for=lambda sql, _p: rows if "FROM episodic_memories" in sql else [])
    store = EpisodicMemoryStore(db_factory=db)

    episodes = await store.recall(goal="jira tickets", tenant_id=TENANT)

    (select,) = assert_tenant_scoped(db, "episodic_memories", TENANT)
    assert "WHERE tenant_id = :tenant_id" in select.sql
    # The rows came from the DB, not the (empty) in-process cache.
    assert [e.episode_id for e in episodes] == ["e1"]
    assert episodes[0].tenant_id == TENANT


# ── procedural_memories ────────────────────────────────────────────────────────


async def test_procedural_learn_and_recall_run_under_tenant_guc() -> None:
    rows = [("s1", "search jira for open tickets", "jira", '["jira.search_issues"]', 3, 0.9)]
    db = RlsRecordingDb(rows_for=lambda sql, _p: rows if "FROM procedural_memories" in sql else [])
    store = ProceduralMemoryStore(db_factory=db)

    await store.learn(state=_state(), tenant_ctx=_ctx(), success=True)
    skills = await store.recall(goal="search jira", tenant_id=TENANT)

    stmts = assert_tenant_scoped(db, "procedural_memories", TENANT, min_statements=2)
    assert all(s.explicit_txn for s in stmts)
    assert [s.skill_id for s in skills] == ["s1"]


# ── tool_reliability_memory ────────────────────────────────────────────────────


async def test_tool_reliability_upsert_runs_under_tenant_guc() -> None:
    db = RlsRecordingDb()
    store = ToolReliabilityStore(db_session_factory=db)

    await store.record(tenant_id=TENANT, tool_name="jira.search", success=False, latency_ms=12.0)

    (upsert,) = assert_tenant_scoped(db, "tool_reliability_memory", TENANT)
    assert upsert.params["tid"] == TENANT
    assert "ON CONFLICT (tenant_id, tool_name)" in upsert.sql


async def test_tool_reliability_reads_run_under_tenant_guc() -> None:
    def rows_for(sql: str, _p: dict) -> list:
        if "SELECT success_count, failure_count, total_latency_ms" in sql:
            return [(8, 2, 1000.0, None)]
        if "SELECT tool_name, success_count" in sql:
            return [("flaky.tool", 1, 9, 0.1)]
        return []

    db = RlsRecordingDb(rows_for=rows_for)
    store = ToolReliabilityStore(db_session_factory=db)

    rel = await store.get_reliability(tenant_id=TENANT, tool_name="jira.search")
    unreliable = await store.get_unreliable_tools(tenant_id=TENANT)

    assert_tenant_scoped(db, "tool_reliability_memory", TENANT, min_statements=2)
    assert rel["success_count"] == 8
    assert rel["success_rate"] == 0.8
    assert [u["tool_name"] for u in unreliable] == ["flaky.tool"]
