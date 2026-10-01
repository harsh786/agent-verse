"""CORE-33 on real Postgres: the routing pre-filter is bounded and index-backed."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.router import MAX_ROUTING_CANDIDATES, AgentRouter
from app.api.agents import _ROUTING_TSV_SQL, AgentStore
from app.tenancy.context import PlanTier, TenantContext


@pytest.mark.integration
async def test_oldest_matching_agent_is_a_candidate_and_the_index_is_used(pg_url: str) -> None:
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid.uuid4().hex
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k")

    async def _exec(sql: str, params: dict[str, Any]) -> list[Any]:
        async with factory() as s, s.begin():
            await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
            res = await s.execute(text(sql), params)
            return list(res.all()) if res.returns_rows else []

    best = uuid.uuid4().hex
    try:
        await _exec("INSERT INTO tenants (id, name, email) VALUES (:t, 'route', :e)",
                    {"t": tenant_id, "e": f"{tenant_id}@t.local"})
        await _exec(
            "INSERT INTO agents (id, tenant_id, name, goal_template, connector_ids, created_at) "
            "VALUES (:i, :t, 'Invoice reconciler', 'reconcile vendor invoices against "
            "payments', '[\"builtin-stripe\"]', now() - interval '400 days')",
            {"i": best, "t": tenant_id},
        )
        await _exec(
            "INSERT INTO agents (id, tenant_id, name, goal_template, created_at) "
            "SELECT md5(random()::text || g::text), :t, 'Helper ' || g, 'general assistance', "
            "now() - (g || ' minutes')::interval FROM generate_series(1, 199) AS g",
            {"t": tenant_id},
        )

        store = AgentStore(db_session_factory=factory)
        out = await store.routing_candidates(
            tenant_ctx=ctx, goal="reconcile the vendor invoices with stripe",
            limit=MAX_ROUTING_CANDIDATES,
        )
        assert len(out) == MAX_ROUTING_CANDIDATES
        assert out[0]["agent_id"] == best  # the oldest agent, ranked first

        decision = await AgentRouter(agent_store=store).route(
            "reconcile the vendor invoices with stripe", ctx
        )
        assert decision.agent_id == best

        async with factory() as s, s.begin():
            await s.execute(text("SET LOCAL enable_seqscan = off"))
            plan = "\n".join(
                str(r[0])
                for r in (
                    await s.execute(
                        text(
                            # The store's exact document expression is matched by
                            # the GIN index (the tenant index competes once the
                            # tenant predicate is added; the planner then picks
                            # by selectivity / BitmapAnd).
                            f"EXPLAIN SELECT id FROM agents WHERE {_ROUTING_TSV_SQL} @@ "
                            "to_tsquery('simple', 'invoices | stripe') LIMIT 50"
                        ),
                    )
                ).all()
            )
        assert "ix_agents_routing_fts" in plan, plan
    finally:
        await _exec("DELETE FROM agents WHERE tenant_id = :t", {"t": tenant_id})
        await _exec("DELETE FROM tenants WHERE id = :t", {"t": tenant_id})
        await engine.dispose()
