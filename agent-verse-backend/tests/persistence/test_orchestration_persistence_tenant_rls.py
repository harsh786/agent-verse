"""OrchestrationPersistence: tenant-RLS writes, and no cross-tenant startup scan.

``reflexion_lessons`` and ``tool_trust_records`` are FORCE ROW LEVEL SECURITY.
Both are written from fire-and-forget tasks spawned inside a goal, so the
tenant is known and the writes must carry that tenant's GUC.

The startup warm-up that SELECTed every tenant's ``tool_trust_records``
(``load_tool_trust_from_db("*")``) is removed rather than moved to the
maintenance role: its in-memory store has no reader that depends on it, so
running it with BYPASSRLS would be a privilege escalation for nothing.
"""

from __future__ import annotations

from pathlib import Path

from app.agent.state import AgentState, GoalStatus
from app.services.orchestration_persistence import OrchestrationPersistence
from app.state_runtime.reflexion_store import ReflexionStore
from app.tenancy.context import PlanTier, TenantContext
from app.tool_runtime.tool_trust_store import ToolTrustStore
from tests._rls_recorder import RlsRecordingDb, assert_tenant_scoped

TENANT = "tenant-orch-a"


async def test_persist_tool_outcome_writes_under_tenant_guc() -> None:
    db = RlsRecordingDb()
    persist = OrchestrationPersistence(db=db, tool_trust_store=ToolTrustStore())

    await persist.persist_tool_outcome(
        "jira.search", success=True, latency_ms=42.0, tenant_id=TENANT
    )

    (insert,) = assert_tenant_scoped(db, "tool_trust_records", TENANT)
    assert insert.explicit_txn


async def test_persist_reflexion_lesson_writes_under_tenant_guc() -> None:
    db = RlsRecordingDb()
    persist = OrchestrationPersistence(db=db, reflexion_store=ReflexionStore())
    ctx = TenantContext(tenant_id=TENANT, plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    state = AgentState(goal="delete the staging bucket", tenant_ctx=ctx, goal_id="g-9")
    state.status = GoalStatus.FAILED
    state.verification_feedback = "permission denied on bucket"

    await persist.persist_reflexion_lesson(state)

    (insert,) = assert_tenant_scoped(db, "reflexion_lessons", TENANT)
    assert insert.params["source_goal_id"] == "g-9"


async def test_load_tool_trust_is_tenant_scoped() -> None:
    rows = [("jira.search", True, 200.0), ("github.list", False, 5000.0)]
    db = RlsRecordingDb(rows_for=lambda sql, _p: rows if "FROM tool_trust_records" in sql else [])
    store = ToolTrustStore()
    persist = OrchestrationPersistence(db=db, tool_trust_store=store)

    loaded = await persist.load_tool_trust_from_db(TENANT)

    (select,) = assert_tenant_scoped(db, "tool_trust_records", TENANT)
    assert "WHERE tenant_id = :tenant_id" in select.sql
    assert loaded == 2
    assert store.has_tool("jira.search") and store.has_tool("github.list")


async def test_load_tool_trust_refuses_cross_tenant_wildcard() -> None:
    db = RlsRecordingDb(rows_for=lambda _sql, _p: [("leak", True, 1.0)])
    store = ToolTrustStore()
    persist = OrchestrationPersistence(db=db, tool_trust_store=store)

    assert await persist.load_tool_trust_from_db("*") == 0
    assert await persist.load_tool_trust_from_db("") == 0

    assert db.statements == []  # no unscoped SELECT was ever issued
    assert db.escalations == 0
    assert not store.has_tool("leak")


def test_startup_no_longer_scans_every_tenants_tool_trust() -> None:
    main_src = (Path(__file__).resolve().parents[2] / "app" / "main.py").read_text()
    assert 'load_tool_trust_from_db("*"' not in main_src
    assert "load_tool_trust_from_db('*'" not in main_src
