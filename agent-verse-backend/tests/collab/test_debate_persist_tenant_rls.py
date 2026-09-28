"""persist_debate writes ``debate_sessions``/``debate_proposals`` under tenant RLS.

Both tables are FORCE ROW LEVEL SECURITY; a debate belongs to one tenant, so
its session row and every proposal row are inserted under that tenant's GUC.
The ``ON CONFLICT (id) DO UPDATE`` on ``debate_sessions`` is additionally pinned
to the same tenant, so a colliding session id owned by someone else is never
updated even if a policy were missing.
"""

from __future__ import annotations

from app.collab.agent_collab import AgentCollabSession
from tests._rls_recorder import RlsRecordingDb, assert_tenant_scoped

TENANT = "tenant-debate-a"


async def test_persist_debate_writes_session_and_proposals_under_tenant_guc() -> None:
    db = RlsRecordingDb()
    sess = AgentCollabSession(goal="Pick a rollout plan")

    await sess.persist_debate(
        session_id="ds-1",
        goal_id="g-1",
        tenant_id=TENANT,
        original_goal="Pick a rollout plan",
        consensus="Canary first",
        confidence=0.8,
        rounds=2,
        proposals=[
            {"id": "p1", "role": "planner", "proposal": "Canary", "vote": "approve"},
            {"id": "p2", "role": "critic", "proposal": "Big bang", "vote": "reject"},
        ],
        db=db,
    )

    (session_insert,) = assert_tenant_scoped(db, "INSERT INTO debate_sessions", TENANT)
    assert "WHERE debate_sessions.tenant_id = EXCLUDED.tenant_id" in session_insert.sql
    proposals = assert_tenant_scoped(db, "INSERT INTO debate_proposals", TENANT, min_statements=2)
    assert {p.params["id"] for p in proposals} == {"p1", "p2"}
    assert all(s.explicit_txn for s in db.statements)
