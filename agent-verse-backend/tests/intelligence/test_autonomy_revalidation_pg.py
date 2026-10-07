"""Owner decision on a05-F095-04 against real Postgres (NOBYPASSRLS app role).

A config change demotes a fully-autonomous agent and enqueues its durable
eval-suite run (MEM-53); the run's post-run hook promotes it back when the gate
passes, leaves it bounded when it fails, and never re-promotes after an
operator changed its autonomy. When the hook never ran (worker died after
finalizing, or the run ended ``failed``), the beat sweeper's reconciliation
resolves the marker. The compare-and-set is tenant-scoped (RLS + explicit
tenant predicate).

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/intelligence/test_autonomy_revalidation_pg.py -q -m integration
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest

from tests.intelligence._eval_fakes import FakeGoals, FastSettings
from tests.intelligence._eval_pg import eval_postgres, provision_app_role

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]


async def _demoted_agent(
    admin: Any, app: Any, tenant: str
) -> tuple[Any, Any, Any, str, dict[str, Any]]:
    """A fully-autonomous agent just changed through the PUT's code path."""
    from sqlalchemy import text

    from app.api.agents import AgentStore
    from app.intelligence import autonomy_revalidation as reval
    from app.intelligence.eval_suite_store import EvalSuiteStore
    from app.tenancy.context import PlanTier, TenantContext

    ctx = TenantContext(tenant_id=tenant, plan=PlanTier.PROFESSIONAL, api_key_id="op-key")
    async with admin() as s, s.begin():
        await s.execute(
            text("INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                 "VALUES (:id, :id, :e, 'professional', true) ON CONFLICT DO NOTHING"),
            {"id": tenant, "e": f"{tenant}@example.test"},
        )
    agents = AgentStore(app)
    suite = f"s-{uuid.uuid4().hex[:6]}"
    agent_id = await agents.create(
        {"name": "a", "eval_suite_id": suite, "system_prompt": "v1"}, tenant_ctx=ctx
    )
    async with admin() as s, s.begin():
        await s.execute(text("UPDATE agents SET autonomy_mode = 'fully-autonomous' "
                             "WHERE id = :id"), {"id": agent_id})
    evals = EvalSuiteStore(app, tenant)
    await evals.create(suite, name=suite, description="")
    await evals.import_tasks(
        suite, [{"goal": f"g{i}", "expected_tools": ["t"]} for i in range(5)], replace=False
    )
    current = await agents.get_async(agent_id, tenant_ctx=ctx)
    assert current is not None and current["autonomy_mode"] == "fully-autonomous"
    update = {"system_prompt": "v2", "autonomy_mode": "bounded-autonomous"}
    marker = await reval.begin_revalidation(
        evals, agent_id=agent_id, proposed={**current, **update}, source="agent_update",
        actor="op-key", tenant_plan="professional",
    )
    assert marker["state"] == "pending"
    assert await agents.update_async(
        agent_id, {**update, "autonomy_revalidation": marker}, tenant_ctx=ctx
    )
    dispatched: list[str] = []

    async def _dispatch(_t: str, _p: str, run_id: str) -> None:
        dispatched.append(run_id)

    marker = await reval.start_revalidation(
        eval_store=evals, agent_store=agents, tenant_ctx=ctx, agent_id=agent_id,
        marker=marker, dispatch=_dispatch, tenant_plan="professional", db_factory=app,
    )
    assert dispatched == [marker["run_id"]]
    return agents, evals, ctx, agent_id, marker


async def _run(
    evals: Any, agents: Any, ctx: Any, run_id: str,
    outcome: Callable[[str, str | None], str] | None = None, *, hook: bool = True,
) -> FakeGoals:
    from app.intelligence.eval_suite_jobs import RunSettings, run_until_done
    from app.intelligence.eval_suite_post_run import on_run_completed

    goals = FakeGoals(outcome)

    async def _load(agent_id: str) -> dict[str, Any] | None:
        found: dict[str, Any] | None = await agents.get_async(agent_id, tenant_ctx=ctx)
        return found

    out = await run_until_done(
        store=evals, run_id=run_id, goal_service=goals, tenant_ctx=ctx, agent_loader=_load,
        on_completed=on_run_completed if hook else None, cfg=RunSettings(FastSettings()),
    )
    assert out["status"] == "completed"
    return goals


async def _row(admin: Any, agent_id: str) -> tuple[str, dict[str, Any] | None, str]:
    from sqlalchemy import text

    async with admin() as s:
        r = (
            await s.execute(
                text("SELECT autonomy_mode, autonomy_revalidation, system_prompt "
                     "FROM agents WHERE id = :id"),
                {"id": agent_id},
            )
        ).one()
    return str(r[0]), r[1], str(r[2])


async def _audit_outcomes(admin: Any, tenant: str) -> list[str]:
    from sqlalchemy import text

    async with admin() as s:
        rows = (
            await s.execute(
                text("SELECT outcome FROM audit_log WHERE tenant_id = :t "
                     "AND tool_name = 'agent.autonomy' ORDER BY created_at, id"),
                {"t": tenant},
            )
        ).all()
    return [str(r[0]) for r in rows]


async def test_autonomy_revalidation_on_postgres(monkeypatch: Any) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    with eval_postgres() as admin_url:
        app_url = await provision_app_role(admin_url)
        admin_engine = create_async_engine(admin_url)
        app_engine = create_async_engine(app_url, pool_size=10)
        admin = async_sessionmaker(admin_engine, expire_on_commit=False)
        app = async_sessionmaker(app_engine, expire_on_commit=False)
        try:
            await _passing_run_promotes(admin, app)
            await _failing_run_stays_bounded(admin, app)
            await _operator_change_is_never_overridden(admin, app)
            await _sweeper_reconciles_a_lost_hook(admin, app, monkeypatch)
            await _cas_is_tenant_scoped(admin, app)
        finally:
            await app_engine.dispose()
            await admin_engine.dispose()


async def _passing_run_promotes(admin: Any, app: Any) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    agents, evals, ctx, agent_id, marker = await _demoted_agent(admin, app, tenant)
    mode, stored, prompt = await _row(admin, agent_id)
    assert (mode, prompt) == ("bounded-autonomous", "v2")
    assert stored is not None and stored["state"] == "pending"
    assert stored["reason"] == "config_changed_pending_eval"
    # The agent API exposes the pending state (read back through RLS).
    rec = await agents.get_async(agent_id, tenant_ctx=ctx)
    assert rec["pending_promotion"] is True

    goals = await _run(evals, agents, ctx, marker["run_id"])
    assert {s["agent_id"] for s in goals.submits} == {agent_id}
    mode, stored, _ = await _row(admin, agent_id)
    assert mode == "fully-autonomous"
    assert stored is not None and stored["state"] == "promoted"
    assert stored["pass_rate"] == 1.0
    assert await _audit_outcomes(admin, tenant) == ["demoted", "promoted"]


async def _failing_run_stays_bounded(admin: Any, app: Any) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    agents, evals, ctx, agent_id, marker = await _demoted_agent(admin, app, tenant)
    await _run(evals, agents, ctx, marker["run_id"], outcome=lambda _g, _a: "failed")
    mode, stored, _ = await _row(admin, agent_id)
    assert mode == "bounded-autonomous"
    assert stored is not None and stored["state"] == "failed"
    assert stored["pass_rate"] == 0.0 and "below" in stored["error"]
    assert await _audit_outcomes(admin, tenant) == ["demoted", "revalidation_failed"]


async def _operator_change_is_never_overridden(admin: Any, app: Any) -> None:
    from sqlalchemy import text

    tenant = f"t-{uuid.uuid4().hex[:8]}"
    agents, evals, ctx, agent_id, marker = await _demoted_agent(admin, app, tenant)
    # Worst case: the operator's change touched only the mode, not the marker.
    async with admin() as s, s.begin():
        await s.execute(text("UPDATE agents SET autonomy_mode = 'supervised' WHERE id = :id"),
                        {"id": agent_id})
    await _run(evals, agents, ctx, marker["run_id"])
    mode, stored, _ = await _row(admin, agent_id)
    assert mode == "supervised"
    assert stored is not None and stored["state"] == "pending"  # untouched, never promoted


async def _sweeper_reconciles_a_lost_hook(admin: Any, app: Any, monkeypatch: Any) -> None:
    from app.scaling import tasks as scaling_tasks

    tenant = f"t-{uuid.uuid4().hex[:8]}"
    agents, evals, ctx, agent_id, marker = await _demoted_agent(admin, app, tenant)
    await _run(evals, agents, ctx, marker["run_id"], hook=False)  # the hook "died"
    assert (await _row(admin, agent_id))[0] == "bounded-autonomous"

    class _Task:
        def apply_async(self, *_a: Any, **_k: Any) -> None:
            raise AssertionError("no stalled run to re-dispatch")

    monkeypatch.setattr(scaling_tasks, "run_eval_suite_worker", _Task())
    with (
        patch("app.db.session.get_system_session_factory", return_value=admin),
        patch("app.db.session.get_session_factory", return_value=app),
    ):
        out = await scaling_tasks._resume_stalled_eval_suite_runs_async(3600)
        assert out["revalidations_resolved"] == 1
        again = await scaling_tasks._resume_stalled_eval_suite_runs_async(3600)
        assert again["revalidations_resolved"] == 0
    mode, stored, _ = await _row(admin, agent_id)
    assert mode == "fully-autonomous"
    assert stored is not None and stored["state"] == "promoted"


async def _cas_is_tenant_scoped(admin: Any, app: Any) -> None:
    from app.tenancy.context import PlanTier, TenantContext

    tenant = f"t-{uuid.uuid4().hex[:8]}"
    agents, _evals, _ctx, agent_id, marker = await _demoted_agent(admin, app, tenant)
    other = TenantContext(tenant_id=f"o-{uuid.uuid4().hex[:8]}", plan=PlanTier.FREE,
                          api_key_id="x")
    assert not await agents.transition_revalidation(
        agent_id, token=marker["token"], data={"autonomy_mode": "fully-autonomous"},
        tenant_ctx=other,
    )
    assert (await _row(admin, agent_id))[0] == "bounded-autonomous"
