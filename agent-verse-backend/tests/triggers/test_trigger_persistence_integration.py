"""Integration: trigger state is authoritative in Postgres, across replicas, under RLS.

Every "replica" below is a separate ``ScheduleStore`` on a least-privilege
NOBYPASSRLS role (the production API role); the superuser engine stands in for
the maintenance (BYPASSRLS) factory. Regressions covered:

* resume / PATCH / rotate-secret changed one process's memory only.
* webhook signing secrets had no column (a restart dropped them → unsigned
  deliveries accepted); they are now stored encrypted.
* ``sync_from_db`` ran GUC-less on the app role → loaded nothing.
* a trigger created on another replica was invisible to webhook matching.
* PLAN_MAX_TRIGGERS was never enforced (now counted in the DB).
* GET /schedules/{id}/history read a key nothing writes → always empty.
* channel_tenant_mappings had no channel_id column and the CRUD used an unset
  ``app.state.db`` → no mapping could exist, inbound channels never routed.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/triggers/test_trigger_persistence_integration.py -m integration
"""

from __future__ import annotations

import datetime
import json
import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.db.app_role import AppRoleSpec, ensure_app_role
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.quota import TriggerQuotaExceeded
from app.triggers.store import ScheduleStore

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": admin_url},
            check=True,
            capture_output=True,
            text=True,
        )
        yield admin_url


@pytest_asyncio.fixture(scope="function")
async def dbs(postgres_url: str) -> AsyncIterator[SimpleNamespace]:
    password = secrets.token_urlsafe(24)
    role = f"test_app_trig_{secrets.token_hex(4)}"
    admin_engine = create_async_engine(postgres_url)
    t1, t2 = uuid.uuid4().hex, uuid.uuid4().hex
    async with admin_engine.begin() as conn:
        # The application role comes from the SAME bootstrap production runs after
        # every migration (app/db/app_role.py), not a hand-picked table list: the
        # schedule store also reads other tenant-scoped tables (e.g. the per-tenant
        # envelope key in tenant_vault_keys for webhook secrets), as in production.
        await conn.run_sync(ensure_app_role, AppRoleSpec(role=role, password=password))
        for tid in (t1, t2):
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, :n, :e, 'free', true)"
                ),
                {"id": tid, "n": tid, "e": f"{tid}@example.test"},
            )
    app_url = (
        make_url(postgres_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, pool_size=4, max_overflow=0)
    yield SimpleNamespace(
        admin=async_sessionmaker(admin_engine, expire_on_commit=False),
        app=async_sessionmaker(app_engine, expire_on_commit=False),
        t1=t1,
        t2=t2,
    )
    await app_engine.dispose()
    await admin_engine.dispose()


def _ctx(tid: str, plan: PlanTier = PlanTier.PROFESSIONAL) -> TenantContext:
    return TenantContext(tenant_id=tid, plan=plan, api_key_id="k")


def _client(app: Any) -> Any:
    # In-loop ASGI client: TestClient runs the app on another event loop, which
    # the asyncpg pool (bound to the test's loop) cannot be used from.
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _replica(dbs: SimpleNamespace) -> ScheduleStore:
    return ScheduleStore(db_session_factory=dbs.app, system_db_session_factory=dbs.admin)


async def _row(dbs: SimpleNamespace, sid: str) -> Any:
    async with dbs.admin() as s:
        return (
            await s.execute(text("SELECT * FROM schedules WHERE id = :i"), {"i": sid})
        ).mappings().one()


@pytest.mark.asyncio
async def test_resume_patch_and_secret_persist_across_replicas(dbs: SimpleNamespace) -> None:
    ctx = _ctx(dbs.t1)
    a, b = _replica(dbs), _replica(dbs)
    spec = TriggerSpec(
        trigger_type=TriggerType.WEBHOOK,
        webhook_token="w" * 40,
        webhook_signature_secret="create-secret",
    )
    sid = await a.create_async(goal_id="", spec=spec, tenant_ctx=ctx, goal_template="go")

    # Secret is stored, encrypted (never plaintext).
    row = await _row(dbs, sid)
    assert row["webhook_signature_secret_enc"]
    assert "create-secret" not in row["webhook_signature_secret_enc"]

    # Pause on A, resume on B (B never saw the trigger in memory).
    assert await a.set_paused_async(sid, paused=True, tenant_ctx=ctx) is not None
    assert (await _row(dbs, sid))["paused"] is True
    assert await b.set_paused_async(sid, paused=False, tenant_ctx=ctx) is not None
    assert (await _row(dbs, sid))["paused"] is False

    # PATCH on B reaches the row; the omitted webhook token/secret are kept.
    new_spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK, description="edited")
    updated = await b.update_async(sid, tenant_ctx=ctx, goal_template="new goal", spec=new_spec)
    assert updated is not None
    row = await _row(dbs, sid)
    assert row["goal_id_template"] == "new goal"
    assert row["description"] == "edited"
    assert row["webhook_token"] == "w" * 40

    # Rotate on A: new + previous secrets and the grace deadline are durable.
    assert await a.update_secret_async(
        sid, new_secret="rotated-secret", tenant_id=dbs.t1, grace_period_seconds=300
    )
    row = await _row(dbs, sid)
    assert row["webhook_signature_secret_prev_enc"]
    assert row["webhook_secret_grace_until"] is not None

    # A fresh replica (restart) sees all of it — via the maintenance factory.
    c = _replica(dbs)
    assert await c.sync_from_db() >= 1
    rec = c.get(sid, tenant_ctx=ctx)
    assert rec is not None
    assert rec["paused"] is False
    assert rec["goal_template"] == "new goal"
    assert rec["spec"].webhook_signature_secret == "rotated-secret"
    assert rec["previous_webhook_secret"] == "create-secret"

    # Webhook matching on a replica that never loaded it (read-through).
    d = ScheduleStore(db_session_factory=dbs.app)
    found = await d.find_by_type_async("webhook", tenant_id=dbs.t1)
    assert [r["schedule_id"] for r in found] == [sid]


@pytest.mark.asyncio
async def test_sync_from_db_needs_the_maintenance_role(dbs: SimpleNamespace) -> None:
    ctx = _ctx(dbs.t1)
    sid = await _replica(dbs).create_async(
        goal_id="", spec=TriggerSpec(trigger_type=TriggerType.ONCE), tenant_ctx=ctx,
        goal_template="x",
    )
    # Old behaviour: GUC-less query on the NOBYPASSRLS app role → sees nothing.
    legacy = ScheduleStore(db_session_factory=dbs.app)
    await legacy.sync_from_db()
    assert legacy.get(sid, tenant_ctx=ctx) is None
    fixed = _replica(dbs)
    await fixed.sync_from_db()
    assert fixed.get(sid, tenant_ctx=ctx) is not None


@pytest.mark.asyncio
async def test_trigger_quota_is_counted_in_the_database(dbs: SimpleNamespace) -> None:
    ctx = _ctx(dbs.t2, PlanTier.FREE)
    for _ in range(5):
        # A fresh store each time: the count must come from Postgres, not memory.
        await _replica(dbs).create_async(
            goal_id="", spec=TriggerSpec(trigger_type=TriggerType.ONCE), tenant_ctx=ctx,
            goal_template="x", quota_plan="free",
        )
    with pytest.raises(TriggerQuotaExceeded):
        await _replica(dbs).create_async(
            goal_id="", spec=TriggerSpec(trigger_type=TriggerType.ONCE), tenant_ctx=ctx,
            goal_template="x", quota_plan="free",
        )


@pytest.mark.asyncio
async def test_schedule_history_reads_trigger_events(dbs: SimpleNamespace) -> None:
    from fastapi import FastAPI, Request

    from app.api.schedules import router as schedules_router
    from app.db.models.goal import Goal
    from app.triggers.dispatcher import TriggerDispatcher

    tid = dbs.t1
    sid = await _replica(dbs).create_async(
        goal_id="", spec=TriggerSpec(trigger_type=TriggerType.WEBHOOK, webhook_token="h" * 40),
        tenant_ctx=_ctx(tid), goal_template="hist",
    )
    goal_id = uuid.uuid4().hex
    now = datetime.datetime.now(datetime.UTC)
    async with dbs.admin() as s, s.begin():
        s.add(
            Goal(
                id=goal_id, tenant_id=tid, goal_text="hist", status="complete",
                created_at=now, completed_at=now + datetime.timedelta(seconds=2),
            )
        )

    class _Goals:
        async def create_goal(self, **_: Any) -> Any:
            return SimpleNamespace(goal_id=goal_id)

    dispatcher = TriggerDispatcher(goal_service=_Goals(), db_session_factory=dbs.app)
    spec = TriggerSpec(trigger_type=TriggerType.WEBHOOK)
    spec.trigger_id = sid  # type: ignore[attr-defined]
    ctx = SimpleNamespace(tenant_id=tid, plan="professional")
    fired = await dispatcher.dispatch(spec, {"n": 1}, ctx)
    assert fired.goal_created
    replay = await dispatcher.dispatch(spec, {"n": 1}, ctx)
    assert replay.skip_reason == "dedup"

    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        request.state.tenant = _ctx(tid)
        return await call_next(request)

    app.include_router(schedules_router)
    app.state.db_session_factory = dbs.app
    async with _client(app) as client:
        resp = await client.get(f"/schedules/{sid}/history")
    assert resp.status_code == 200, resp.text
    runs = resp.json()["runs"]
    by_status = {r["status"]: r for r in runs}
    assert set(by_status) == {"success", "skipped"}
    assert by_status["success"]["goal_id"] == goal_id
    assert by_status["success"]["duration_ms"] == 2000
    assert by_status["skipped"]["skip_reason"] == "dedup"


@pytest.mark.asyncio
async def test_channel_mapping_created_and_resolved_under_rls(dbs: SimpleNamespace) -> None:
    from fastapi import FastAPI, Request

    from app.api.channels.ingestion import _resolve_tenant_from_channel
    from app.api.channels.ingestion import router as channels_router

    current = {"tid": dbs.t1}
    app = FastAPI()

    @app.middleware("http")
    async def _tenant(request: Request, call_next: Any) -> Any:
        request.state.tenant = SimpleNamespace(tenant_id=current["tid"], plan="free")
        return await call_next(request)

    app.include_router(channels_router)
    app.state.channel_gateway = None
    app.state.db_session_factory = dbs.app
    app.state.system_db_session_factory = dbs.admin
    team = f"T{uuid.uuid4().hex[:10]}"
    async with _client(app) as client:
        resp = await client.post(
            "/channels/mappings", json={"channel_type": "slack", "channel_id": team}
        )
        assert resp.status_code == 200, resp.text
        # TRG-03: pending until the one-time code arrives on the channel itself.
        assert await _resolve_tenant_from_channel("slack", team, dbs.admin) is None
        from app.api.channels.verification import verify_from_inbound

        code = resp.json()["verification_code"]
        assert await verify_from_inbound(dbs.admin, "slack", team, {"text": code}) == dbs.t1
        assert await _resolve_tenant_from_channel("slack", team, dbs.admin) == dbs.t1
        listed = (await client.get("/channels/mappings")).json()
        assert [m["channel_id"] for m in listed] == [team]

        # A second Slack workspace for the same tenant is allowed ...
        other = f"T{uuid.uuid4().hex[:10]}"
        resp = await client.post(
            "/channels/mappings", json={"channel_type": "slack", "channel_id": other}
        )
        assert resp.status_code == 200
        # ... but another tenant cannot claim an already-verified workspace.
        current["tid"] = dbs.t2
        resp = await client.post(
            "/channels/mappings", json={"channel_type": "slack", "channel_id": team}
        )
        assert resp.status_code == 409
        assert await _resolve_tenant_from_channel("slack", team, dbs.admin) == dbs.t1
        assert json.dumps((await client.get("/channels/mappings")).json()) == "[]"


@pytest.mark.asyncio
async def test_agent_schedules_deleted_across_replicas(dbs: SimpleNamespace) -> None:
    """TRG-31: a schedule created on replica A is deleted when the agent is
    deleted on replica B (B never cached it). Only that agent's rows, only in
    that tenant, go — before the agent row (agent_id is ON DELETE SET NULL)."""
    agent_a, agent_other = uuid.uuid4().hex, uuid.uuid4().hex
    async with dbs.admin() as s, s.begin():
        for aid in (agent_a, agent_other):
            await s.execute(
                text("INSERT INTO agents (id, tenant_id, name) VALUES (:i, :t, 'a')"),
                {"i": aid, "t": dbs.t1},
            )
    ctx = _ctx(dbs.t1)
    replica_a, replica_b = _replica(dbs), _replica(dbs)

    def _spec() -> TriggerSpec:
        return TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=3600)

    doomed = await replica_a.create_async(
        goal_id="", spec=_spec(), tenant_ctx=ctx, agent_id=agent_a, goal_template="x"
    )
    kept = await replica_a.create_async(
        goal_id="", spec=_spec(), tenant_ctx=ctx, agent_id=agent_other, goal_template="x"
    )
    assert replica_b.get(doomed, tenant_ctx=ctx) is None  # B never saw it
    # TRG-30: SQL pagination runs on Postgres (stable created_at, id order).
    page = await replica_b.list_all_async(tenant_ctx=ctx, strict=True, limit=1, offset=1)
    assert [r["schedule_id"] for r in page] == [kept]

    assert await replica_b.delete_for_agent_async(agent_a, tenant_ctx=ctx) == [doomed]

    async with dbs.admin() as s:
        left = (await s.execute(text("SELECT id FROM schedules"))).scalars().all()
    assert doomed not in left and kept in left
    # Another tenant's context deletes nothing (RLS).
    assert await replica_b.delete_for_agent_async(agent_other, tenant_ctx=_ctx(dbs.t2)) == []


@pytest.mark.asyncio
async def test_outcome_circuit_opens_from_failed_goals_on_the_app_role(
    dbs: SimpleNamespace,
) -> None:
    """TRG-13: the circuit reads trigger_events ⋈ goals under RLS on the
    least-privilege role, so every worker sees the same state."""
    from app.triggers.outcome_circuit import FAILURE_THRESHOLD, read_outcome_circuit

    trigger_id = uuid.uuid4().hex
    async with dbs.admin() as s, s.begin():
        for i in range(FAILURE_THRESHOLD):
            gid = uuid.uuid4().hex
            await s.execute(
                text(
                    "INSERT INTO goals (id, tenant_id, goal_text, status, priority, "
                    "autonomy_mode, dry_run, iterations) "
                    "VALUES (:g, :t, 'x', 'failed', 'normal', 'bounded-autonomous', false, 0)"
                ),
                {"g": gid, "t": dbs.t1},
            )
            await s.execute(
                text(
                    "INSERT INTO trigger_events (id, tenant_id, trigger_id, trigger_type, "
                    "idempotency_key, fired_at, goal_created, goal_id) VALUES "
                    "(:id, :t, :tid, 'cron', :k, NOW() - make_interval(mins => :m), true, :g)"
                ),
                {"id": str(uuid.uuid4()), "t": dbs.t1, "tid": trigger_id, "k": gid, "m": i + 1,
                 "g": gid},
            )

    assert (await read_outcome_circuit(dbs.app, dbs.t1, trigger_id)).state == "open"
    # Another tenant sees none of those rows.
    assert (await read_outcome_circuit(dbs.app, dbs.t2, trigger_id)).state == "closed"


@pytest.mark.asyncio
async def test_beat_loads_only_due_schedules(
    dbs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TRG-15: 20k schedules, 10 due -> the beat's Postgres discovery returns
    exactly those 10 (bounded, indexed next_fire_at predicate) on the
    least-privilege role, and a next_fire_at written back is honoured."""
    from app.scaling import tasks

    insert = (
        "INSERT INTO schedules (id, tenant_id, goal_id_template, trigger_type, "
        "cron_expression, interval_seconds, config, paused, next_fire_at) "
    )
    async with dbs.admin() as s, s.begin():
        await s.execute(
            text(
                insert + "SELECT md5(random()::text || g), :t, 'x', 'cron', '0 9 * * *', 0, "
                "'{}'::jsonb, false, NOW() + interval '1 hour' FROM generate_series(1, 20000) g"
            ),
            {"t": dbs.t1},
        )
        await s.execute(
            text(
                insert + "SELECT 'due' || lpad(g::text, 29, '0'), :t, 'x', 'cron', "
                "'* * * * *', 0, '{}'::jsonb, false, NOW() - interval '1 minute' "
                "FROM generate_series(1, 10) g"
            ),
            {"t": dbs.t1},
        )
        # A webhook row (never a beat type) and a paused row are not loaded.
        await s.execute(
            text(
                insert + "VALUES ('hook1', :t, 'x', 'webhook', '', 0, '{}'::jsonb, false, NULL), "
                "('paused1', :t, 'x', 'cron', '* * * * *', 0, '{}'::jsonb, true, NULL)"
            ),
            {"t": dbs.t1},
        )
    # TRG-15: discovery is one cross-tenant claim on the maintenance session.
    monkeypatch.setattr("app.db.session.get_system_session_factory", lambda: dbs.admin)

    def _mine(found: dict[str, Any]) -> list[str]:
        prefix = f"schedule:{dbs.t1}:"
        return sorted(k.removeprefix(prefix) for k in found if k.startswith(prefix))

    due = await tasks._load_db_schedules()
    assert due is not None
    assert _mine(due) == [f"due{i:029d}" for i in range(1, 11)]
    # Claimed rows are leased: an overlapping claim does not see them again.
    leased = await tasks._load_db_schedules()
    assert leased is not None and _mine(leased) == []

    # The beat records each claimed row's real next time (one statement): one
    # moves an hour ahead, the rest are due again.
    now = datetime.datetime.now(datetime.UTC)
    later = now + datetime.timedelta(hours=1)
    past = now - datetime.timedelta(seconds=1)
    await tasks._persist_next_evaluations(
        [(dbs.t1, f"due{1:029d}", later)]
        + [(dbs.t1, f"due{i:029d}", past) for i in range(2, 11)]
    )
    again = await tasks._load_db_schedules()
    assert again is not None
    assert _mine(again) == [f"due{i:029d}" for i in range(2, 11)]


@pytest.mark.asyncio
async def test_beat_claims_due_rows_of_a_thousand_tenants_in_one_query(
    dbs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TRG-15: 10k schedules across 1k tenants (1 due per tenant) are found by
    ONE statement - no per-tenant loop - and an inactive tenant's are not."""
    from sqlalchemy import event

    from app.scaling import tasks

    async with dbs.admin() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "SELECT 'bt' || lpad(g::text, 6, '0'), 'n', 'bt' || g || '@x.test', 'free', "
                "g <> 1000 FROM generate_series(1, 1000) g"
            )
        )
        await s.execute(
            text(
                "INSERT INTO schedules (id, tenant_id, goal_id_template, trigger_type, "
                "cron_expression, interval_seconds, config, paused, next_fire_at) "
                "SELECT 'bs' || lpad(g::text, 7, '0'), 'bt' || lpad(((g - 1) / 10 + 1)::text, 6, '0'), "
                "'x', 'cron', '* * * * *', 0, '{}'::jsonb, false, "
                "CASE WHEN g % 10 = 0 THEN NOW() - interval '1 minute' "
                "ELSE NOW() + interval '1 hour' END "
                "FROM generate_series(1, 10000) g"
            )
        )
    statements: list[str] = []
    engine = dbs.admin.kw["bind"].sync_engine

    def _count(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if "schedules" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", _count)
    monkeypatch.setattr("app.db.session.get_system_session_factory", lambda: dbs.admin)
    try:
        found = await tasks._load_db_schedules(limit=5000)
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    assert found is not None
    mine = {k for k in found if k.startswith("schedule:bt")}
    assert len(mine) == 999  # one due row per ACTIVE tenant
    assert not any(k.startswith("schedule:bt001000:") for k in mine)
    assert len(statements) == 1


@pytest.mark.asyncio
async def test_resume_and_edit_rearm_the_schedule(
    dbs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B1-1: resume and a spec edit set ``armed_at`` (the beat's slot floor) on
    the row; a goal-template-only edit does not, and the beat's payload falls
    back to ``created_at`` for a never-re-armed schedule."""
    from app.scaling import tasks

    ctx = _ctx(dbs.t1)
    store = _replica(dbs)
    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="*/5 * * * *")
    sid = await store.create_async(goal_id="", spec=spec, tenant_ctx=ctx, goal_template="go")
    row = await _row(dbs, sid)
    assert row["armed_at"] is None
    monkeypatch.setattr("app.db.session.get_system_session_factory", lambda: dbs.admin)
    claimed = await tasks._load_db_schedules()
    assert claimed is not None
    payload = claimed[f"schedule:{dbs.t1}:{sid}"]
    assert tasks._schedule_datetime(payload["armed_at"]) == tasks._schedule_datetime(
        row["created_at"]
    )

    before = datetime.datetime.now(datetime.UTC)
    await store.set_paused_async(sid, paused=True, tenant_ctx=ctx)
    assert (await _row(dbs, sid))["armed_at"] is None
    await store.set_paused_async(sid, paused=False, tenant_ctx=ctx)
    resumed = (await _row(dbs, sid))["armed_at"]
    assert resumed is not None and resumed >= before

    await store.update_async(sid, tenant_ctx=ctx, goal_template="new text")
    assert (await _row(dbs, sid))["armed_at"] == resumed
    await store.update_async(
        sid,
        tenant_ctx=ctx,
        spec=TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="*/10 * * * *"),
    )
    edited = (await _row(dbs, sid))["armed_at"]
    assert edited is not None and edited > resumed
