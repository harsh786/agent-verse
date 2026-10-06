"""a10-F231-01 / a10-F229-02 on real Postgres: ids kept in a goal's execution_context.

``GoalService.find_goals_by_context`` resolves a batch id / builder project id
from the persisted goals as the least-privilege (NOBYPASSRLS) app role: only the
caller tenant's goals, only that id, oldest first; the partial indexes of
migration c4e8a2f6b1d3 exist.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/services/test_find_goals_by_context_pg.py -q -m integration
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.governance.audit import AuditLog
from app.governance.hitl import HITLGateway
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext
from tests.memory._pg import app_role_engine, sessionmaker_for

pytestmark = pytest.mark.integration


async def test_context_lookup_is_tenant_scoped_on_the_app_role(pg_url: str) -> None:
    t1, t2 = f"ctx-a-{uuid.uuid4().hex[:8]}", f"ctx-b-{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(pg_url)
    async with admin.begin() as c:
        for t in (t1, t2):
            await c.execute(
                text("INSERT INTO tenants (id, name, email) VALUES (:t, :t, :e)"),
                {"t": t, "e": f"{t}@example.test"},
            )
        indexes = set(
            (
                await c.execute(
                    text("SELECT indexname FROM pg_indexes WHERE tablename = 'goals'")
                )
            ).scalars()
        )
    assert {"ix_goals_batch_id", "ix_goals_builder_project_id"} <= indexes

    app_eng = await app_role_engine(pg_url, ["goals"])
    sessions = sessionmaker_for(app_eng)
    rows = [
        (t1, "g-1", "complete", {"batch_id": "batch_1"}),
        (t1, "g-2", "executing", {"batch_id": "batch_1", "other": 1}),
        (t1, "g-3", "planning", {"batch_id": "batch_2"}),
        (t1, "g-4", "planning", {"builder_project_id": "p-1"}),
        (t2, "g-5", "planning", {"batch_id": "batch_1"}),  # same id, other tenant
    ]
    from app.db.rls import sqlalchemy_rls_context

    for i, (tenant, gid, status, ctx) in enumerate(rows):
        async with sessions() as s, s.begin(), sqlalchemy_rls_context(s, tenant):
            await s.execute(
                text(
                    "INSERT INTO goals (id, tenant_id, goal_text, status, priority, "
                    "execution_context, created_at) VALUES (:id, :t, 'x', :st, 'normal', "
                    "CAST(:ctx AS json), now() + make_interval(secs => :i))"
                ),
                {"id": f"{gid}-{t1}", "t": tenant, "st": status, "ctx": json.dumps(ctx), "i": i},
            )

    svc = GoalService(audit_log=AuditLog(), hitl=HITLGateway(), db_session_factory=sessions)
    ctx1 = TenantContext(tenant_id=t1, plan=PlanTier.PROFESSIONAL, api_key_id="k")
    try:
        batch = await svc.find_goals_by_context(ctx1, "batch_id", "batch_1")
        assert [(g["goal_id"], g["status"]) for g in batch] == [
            (f"g-1-{t1}", "complete"),
            (f"g-2-{t1}", "executing"),
        ]
        project = await svc.find_goals_by_context(ctx1, "builder_project_id", "p-1")
        assert [g["goal_id"] for g in project] == [f"g-4-{t1}"]
        assert await svc.find_goals_by_context(ctx1, "batch_id", "batch_missing") == []
        with pytest.raises(ValueError):
            await svc.find_goals_by_context(ctx1, "goal_text", "x")
    finally:
        await app_eng.dispose()
        await admin.dispose()
