"""e2e_full: cost breakdowns and simulation runs are durable, RLS-isolated Postgres rows.

Regression: both lived only in process memory — the per-goal cost breakdown in
``app/observability/cost_breakdown._goal_breakdowns`` and simulation runs in
``SimulationRunner._runs`` / ``_run_tenant``. With several API replicas (or a Celery
worker running the goal) a read on any other process 404'd / came back empty, and a
restart lost everything. Migration ``f7a8b9c0d1e2`` adds ``goal_cost_breakdowns`` and
``simulation_runs`` (ENABLE + FORCE RLS, ``app_current_tenant_uuid()`` policy).

Run with ``E2E_LEAST_PRIVILEGE=1`` so the app connects as a NOBYPASSRLS role: any
store path that forgot ``sqlalchemy_rls_context`` then reads/writes nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def _tenant(app: Any, client: Any) -> tuple[Any, str]:
    from httpx import ASGITransport, AsyncClient

    email = f"rt-{uuid.uuid4().hex[:12]}@example.com"
    r = await client.post("/tenants/signup", json={"name": "Runtime", "email": email})
    assert r.status_code == 201, r.text
    body = r.json()
    c = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://e2e-full",
        headers={"X-API-Key": body["api_key"]},
    )
    return c, str(body["tenant_id"])


async def _count_rows(app: Any, table: str, tenant_id: str, where: str, params: dict) -> int:
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with (
        app.state.db_session_factory() as s,
        s.begin(),
        sqlalchemy_rls_context(s, tenant_id),
    ):
        return int(
            (await s.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), params))
            .scalar_one()
        )


async def test_simulation_run_survives_a_fresh_replica_and_is_tenant_isolated(
    app: Any, client: Any, _reset_signup_rate_limit: None
) -> None:
    from app.enterprise.simulation import SimulationRunner
    from app.tenancy.context import PlanTier, TenantContext

    owner, owner_tid = await _tenant(app, client)
    other, other_tid = await _tenant(app, client)
    async with owner, other:
        created = await owner.post(
            "/enterprise/simulation", json={"goal": "notify the team", "mock_tools": {}}
        )
        assert created.status_code == 201, created.text
        run_id = created.json()["run_id"]

        # Not held in this replica's memory: the DB row is authoritative.
        assert run_id not in app.state.simulation_runner._runs

        got = await owner.get(f"/enterprise/simulation/{run_id}")
        assert got.status_code == 200, got.text
        assert got.json()["run_id"] == run_id

        # Another tenant cannot see it — at the API and at the database.
        assert (await other.get(f"/enterprise/simulation/{run_id}")).status_code == 404

    # A brand-new runner (another replica / after restart) reads it from Postgres.
    fresh = SimulationRunner()
    fresh.set_db(app.state.db_session_factory)
    ctx = TenantContext(tenant_id=owner_tid, plan=PlanTier.FREE, api_key_id="k")
    run = await fresh.aget(run_id=run_id, tenant_ctx=ctx)
    assert run is not None and run.goal == "notify the team"

    q = "run_id = :rid"
    assert await _count_rows(app, "simulation_runs", owner_tid, q, {"rid": run_id}) == 1
    assert await _count_rows(app, "simulation_runs", other_tid, q, {"rid": run_id}) == 0


async def test_cost_breakdown_recorded_elsewhere_is_served_and_tenant_isolated(
    app: Any, client: Any, _reset_signup_rate_limit: None
) -> None:
    from app.observability import cost_breakdown as cb

    owner, owner_tid = await _tenant(app, client)
    other, other_tid = await _tenant(app, client)
    async with owner, other:
        submit = await owner.post("/goals", json={"goal": "Summarize the quarterly report"})
        assert submit.status_code == 202, submit.text
        goal_id = submit.json()["goal_id"]

        # The worker (another process) records costs through the DB-bound store.
        model = f"e2e-planner-{uuid.uuid4().hex[:6]}"
        await cb.arecord_role_cost(goal_id, "planner", model, 120, 30, 0.5, tenant_id=owner_tid)
        await cb.arecord_role_cost(goal_id, "planner", model, 80, 10, 0.25, tenant_id=owner_tid)
        cb._goal_breakdowns.clear()  # this API process holds nothing for the goal

        resp = await owner.get(f"/goals/{goal_id}/cost-metrics")
        assert resp.status_code == 200, resp.text
        mine = [r for r in resp.json()["roles"] if r["model"] == model]
        assert mine == [
            {
                "role": "planner",
                "model": model,
                "input_tokens": 200,
                "output_tokens": 40,
                "cost_usd": 0.75,
                "calls": 2,
            }
        ]

        assert (await other.get(f"/goals/{goal_id}/cost-metrics")).status_code == 404

    q = "goal_id = :gid"
    assert await _count_rows(app, "goal_cost_breakdowns", owner_tid, q, {"gid": goal_id}) >= 1
    assert await _count_rows(app, "goal_cost_breakdowns", other_tid, q, {"gid": goal_id}) == 0
