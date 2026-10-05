"""D3 on real Postgres (least-privilege NOBYPASSRLS role): the public A2A directory.

The card projection ``a2a_public_agents`` is written with the agent in one
transaction, holds only card fields, is readable without a tenant context (the
directory is unauthenticated) while ``agents`` stays invisible there, and its
rows can only be written by their own tenant. Only active, opted-in agents of
tenants that switched the directory on are listed, in keyset pages.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/services/test_a2a_directory_pg.py -m integration
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.api.agents import AgentStore
from app.db.rls import sqlalchemy_rls_context
from app.services.a2a_directory import A2ADirectory
from app.tenancy.context import PlanTier, TenantContext
from tests.rag._pg import admin_exec, app_engine, seed_tenant, sessions

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]


async def test_directory_lists_only_public_active_agents_of_enabled_tenants(
    pg_url: str,
) -> None:
    ta, tb = uuid.uuid4().hex, uuid.uuid4().hex
    for tid in (ta, tb):
        await seed_tenant(pg_url, tid)
    engine = await app_engine(pg_url)
    try:
        db = sessions(engine)
        ctx_a = TenantContext(ta, PlanTier.PROFESSIONAL, "ka")
        ctx_b = TenantContext(tb, PlanTier.PROFESSIONAL, "kb")
        store = AgentStore(db)
        directory = A2ADirectory(db=db, agent_store=store)

        async def agent(ctx: TenantContext, name: str, public: bool) -> str:
            agent_id = await store.create(
                {"name": name, "system_prompt": "SECRET-PROMPT", "connector_ids": []},
                tenant_ctx=ctx,
            )
            if public:
                assert await store.update_async(
                    agent_id,
                    {"a2a_public": True, "a2a_description": f"{name} card",
                     "a2a_skills": ["triage"]},
                    tenant_ctx=ctx,
                )
            return agent_id

        public_a = [await agent(ctx_a, f"A{i}", True) for i in range(5)]
        private_a = await agent(ctx_a, "A-private", False)
        public_b = await agent(ctx_b, "B-public", True)

        # Off by default for every tenant.
        assert await directory.tenant_enabled(ta) is False
        assert await directory.list_public(after=None, limit=100) == []

        await directory.set_tenant_enabled(ta, True)
        seen: list[str] = []
        after: str | None = None
        while True:
            page = await directory.list_public(after=after, limit=2)
            seen += [a.agent_id for a in page]
            if len(page) < 2:
                break
            after = page[-1].agent_id
        assert seen == sorted(public_a)
        card = await directory.get_public(public_a[0])
        assert card is not None and card.description.endswith("card")
        assert card.skills == ("triage",)
        for hidden in (private_a, public_b):
            assert await directory.get_public(hidden) is None
        assert await directory.get_public_for_tenant(tb, public_a[0]) is None
        assert await directory.get_public_for_tenant(ta, public_a[0]) is not None

        # The projection holds card fields only, and nothing private ever.
        cols = await admin_exec(
            pg_url,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'a2a_public_agents' ORDER BY column_name",
        )
        assert [c[0] for c in cols] == [
            "agent_id", "description", "name", "skills", "tenant_id", "updated_at"]
        assert private_a not in {
            r[0] for r in await admin_exec(pg_url, "SELECT agent_id FROM a2a_public_agents")}

        # Without a tenant context the app role sees the cards but no agent rows,
        # and tenant B cannot touch tenant A's cards.
        async with db() as s, s.begin():
            assert (await s.execute(text("SELECT count(*) FROM agents"))).scalar_one() == 0
            visible = (await s.execute(text(
                "SELECT count(*) FROM a2a_public_agents WHERE tenant_id = :t"), {"t": ta}
            )).scalar_one()
            assert visible == 5
        async with db() as s, s.begin(), sqlalchemy_rls_context(s, tb):
            gone = await s.execute(
                text("DELETE FROM a2a_public_agents WHERE agent_id = :a"), {"a": public_a[0]})
            assert gone.rowcount == 0

        # Opt-out, delete and the tenant switch all take effect at once.
        assert await store.update_async(public_a[0], {"a2a_public": False}, tenant_ctx=ctx_a)
        assert await store.delete_async(public_a[1], tenant_ctx=ctx_a)
        assert [a.agent_id for a in await directory.list_public(after=None, limit=100)] == (
            sorted(public_a[2:]))
        await directory.set_tenant_enabled(ta, False)
        assert await directory.list_public(after=None, limit=100) == []
    finally:
        await engine.dispose()
