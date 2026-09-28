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

from app.tenancy.context import PlanTier, TenantContext
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.quota import TriggerQuotaExceeded
from app.triggers.store import ScheduleStore

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
_TABLES = ("schedules", "trigger_events", "goals", "channel_tenant_mappings", "tenants")


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
        quoted = (
            await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})
        ).scalar_one()
        await conn.execute(
            text(
                f"CREATE ROLE {role} LOGIN PASSWORD {quoted} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await conn.execute(text(f"GRANT CONNECT ON DATABASE test TO {role}"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        for table in _TABLES:
            await conn.execute(
                text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role}")
            )
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
    team = f"T{uuid.uuid4().hex[:10]}"
    async with _client(app) as client:
        resp = await client.post(
            "/channels/mappings", json={"channel_type": "slack", "channel_id": team}
        )
        assert resp.status_code == 200, resp.text
        assert await _resolve_tenant_from_channel("slack", team, dbs.admin) == dbs.t1
        listed = (await client.get("/channels/mappings")).json()
        assert [m["channel_id"] for m in listed] == [team]

        # A second Slack workspace for the same tenant is allowed ...
        other = f"T{uuid.uuid4().hex[:10]}"
        resp = await client.post(
            "/channels/mappings", json={"channel_type": "slack", "channel_id": other}
        )
        assert resp.status_code == 200
        # ... but another tenant cannot claim an already-mapped workspace.
        current["tid"] = dbs.t2
        resp = await client.post(
            "/channels/mappings", json={"channel_type": "slack", "channel_id": team}
        )
        assert resp.status_code == 409
        assert await _resolve_tenant_from_channel("slack", team, dbs.admin) == dbs.t1
        assert json.dumps((await client.get("/channels/mappings")).json()) == "[]"
