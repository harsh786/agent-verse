"""Integration: billing + data-subject-rights fixes against real Postgres roles.

Runs ``alembic upgrade head`` (incl. c7e1a9d4b3f2) and drives the fixed code as
a NOSUPERUSER/NOBYPASSRLS app role, with a separate BYPASSRLS maintenance role
for the cross-tenant beat scans — the production posture, under which the
original bugs (RLS-hidden scans, swallowed DB errors) are visible.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/integration/test_billing_dsr_rls_integration.py -m integration --no-cov
"""

from __future__ import annotations

import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": url},
            check=True,
            capture_output=True,
            text=True,
        )
        yield url


@pytest_asyncio.fixture
async def dbs(postgres_url: str) -> AsyncIterator[tuple[Any, Any, Any]]:
    """(admin, app [NOBYPASSRLS], maintenance [BYPASSRLS]) session factories."""
    pw = secrets.token_urlsafe(16)
    suffix = secrets.token_hex(3)
    app_role, maint_role = f"it_app_{suffix}", f"it_maint_{suffix}"
    admin_engine = create_async_engine(postgres_url, poolclass=NullPool)
    async with admin_engine.begin() as conn:
        q = (await conn.execute(text("SELECT quote_literal(:p)"), {"p": pw})).scalar_one()
        await conn.execute(text(f"CREATE ROLE {app_role} LOGIN PASSWORD {q} NOBYPASSRLS"))
        await conn.execute(text(f"CREATE ROLE {maint_role} LOGIN PASSWORD {q} BYPASSRLS"))
        for role in (app_role, maint_role):
            await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            await conn.execute(
                text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
            )

    def _factory(role: str) -> Any:
        url = make_url(postgres_url).set(username=role, password=pw)
        eng = create_async_engine(url.render_as_string(hide_password=False), poolclass=NullPool)
        return async_sessionmaker(eng, expire_on_commit=False), eng

    admin = async_sessionmaker(admin_engine, expire_on_commit=False)
    app, app_eng = _factory(app_role)
    maint, maint_eng = _factory(maint_role)
    yield admin, app, maint
    await app_eng.dispose()
    await maint_eng.dispose()
    await admin_engine.dispose()


def _tid() -> str:
    return uuid.uuid4().hex


async def _seed_tenant(admin: Any, tid: str) -> str:
    key_hash = secrets.token_hex(32)
    async with admin() as s, s.begin():
        await s.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:id, 'T', :e)"),
            {"id": tid, "e": f"{tid}@example.test"},
        )
        await s.execute(
            text(
                "INSERT INTO api_keys (id, tenant_id, name, key_hash) "
                "VALUES (:id, :t, 'k', :h)"
            ),
            {"id": _tid(), "t": tid, "h": key_hash},
        )
    return key_hash


async def _scalar(admin: Any, sql: str, params: dict[str, Any]) -> Any:
    async with admin() as s:
        return (await s.execute(text(sql), params)).scalar()


# ── 1. billing ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_plan_and_order_record_are_durable_under_rls(dbs: tuple) -> None:
    from app.api import billing
    from app.services.tenant_service import TenantService

    admin, app, _ = dbs
    tid = _tid()
    await _seed_tenant(admin, tid)

    await TenantService(db_session_factory=app).update_plan(tid, "professional")
    assert await _scalar(admin, "SELECT plan_tier FROM tenants WHERE id = :t", {"t": tid}) == (
        "professional"
    )

    class _Req:
        class app:
            class state:
                db_session_factory = app

    order = billing._Order(
        order_id=f"order_{_tid()}", tenant_id=tid, plan="starter", cycle="monthly",
        amount=2900, currency="INR", is_mock=False,
    )
    await billing._save_order(_Req, order)  # type: ignore[arg-type]
    loaded = await billing._load_order(_Req, tid, order.order_id)  # type: ignore[arg-type]
    assert loaded is not None and loaded.plan == "starter"
    # Another tenant cannot see (or pay) it.
    assert await billing._load_order(_Req, _tid(), order.order_id) is None  # type: ignore[arg-type]
    await billing._mark_order_paid(_Req, loaded, "pay_1")  # type: ignore[arg-type]
    assert await _scalar(
        admin, "SELECT status FROM billing_orders WHERE order_id = :o", {"o": order.order_id}
    ) == "paid"


# ── 2. GDPR tenant erasure job ────────────────────────────────────────────────


class _FakeRedis:
    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, *keys: str) -> int:
        self.deleted.extend(keys)
        return len(keys)


@pytest.mark.asyncio
async def test_tenant_erasure_job_respects_legal_hold_then_runs(dbs: tuple) -> None:
    from app.enterprise.compliance import ComplianceController
    from app.scaling.tasks import _process_tenant_erasures_async

    admin, app, maint = dbs
    tid = _tid()
    key_hash = await _seed_tenant(admin, tid)
    gid = _tid()
    hold_id = _tid()
    async with admin() as s, s.begin():
        await s.execute(
            text("INSERT INTO goals (id, tenant_id, goal_text) VALUES (:g, :t, 'x')"),
            {"g": gid, "t": tid},
        )
        await s.execute(
            text(
                "INSERT INTO legal_holds (id, tenant_id, name, resource_type, status) "
                "VALUES (:h, :t, 'litigation', 'tenant', 'active')"
            ),
            {"h": hold_id, "t": tid},
        )
    ctx = TenantContext(tenant_id=tid, plan=PlanTier.ENTERPRISE, api_key_id="k")
    cc = ComplianceController()
    cc.configure_services(db=app)
    req = await cc.request_data_deletion(tenant_ctx=ctx)
    assert req["status"] == "pending" and req["deletion_scheduled"] is True
    # Fast-forward past the grace period.
    async with admin() as s, s.begin():
        await s.execute(
            text("UPDATE deleted_tenants SET scheduled_for = now() - interval '1 minute' "
                 "WHERE tenant_id = :t"),
            {"t": tid},
        )

    redis = _FakeRedis()
    out = await _process_tenant_erasures_async(db=app, system_db=maint, redis=redis)
    assert out["on_hold"] >= 1
    assert (await cc.get_deletion_status(tenant_ctx=ctx) or {})["status"] == "on_hold"
    assert await _scalar(admin, "SELECT count(*) FROM goals WHERE id = :g", {"g": gid}) == 1

    async with admin() as s, s.begin():
        await s.execute(
            text("UPDATE legal_holds SET status = 'released' WHERE id = :h"), {"h": hold_id}
        )
    out = await _process_tenant_erasures_async(db=app, system_db=maint, redis=redis)
    status = await cc.get_deletion_status(tenant_ctx=ctx) or {}
    assert status["status"] == "completed", status
    assert await _scalar(admin, "SELECT count(*) FROM goals WHERE id = :g", {"g": gid}) == 0
    assert await _scalar(admin, "SELECT count(*) FROM tenants WHERE id = :t", {"t": tid}) == 0
    assert f"api_key:{key_hash}" in redis.deleted


# ── 3. DPDP erasures + feedback batch (maintenance scan, tenant RLS work) ─────


@pytest.mark.asyncio
async def test_dpdp_erasure_scan_finds_force_rls_rows(dbs: tuple) -> None:
    from app.scaling.tasks import _process_dpdp_erasures_async

    admin, app, maint = dbs
    tid = _tid()
    await _seed_tenant(admin, tid)
    rid = _tid()
    async with admin() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO dpdp_erasure_requests (id, tenant_id, data_principal_id, status) "
                "VALUES (:r, :t, 'subject-123', 'pending')"
            ),
            {"r": rid, "t": tid},
        )
    out = await _process_dpdp_erasures_async(db=app, system_db=maint)
    assert out["processed"] >= 1, out
    st = await _scalar(admin, "SELECT status FROM dpdp_erasure_requests WHERE id = :r", {"r": rid})
    assert st in ("completed", "suspended", "verification_failed"), st


@pytest.mark.asyncio
async def test_feedback_batch_scan_finds_force_rls_rows(dbs: tuple) -> None:
    from app.scaling.tasks import _process_feedback_batch_async

    admin, app, maint = dbs
    tid = _tid()
    await _seed_tenant(admin, tid)
    gid, fid = _tid(), _tid()
    async with admin() as s, s.begin():
        await s.execute(
            text("INSERT INTO goals (id, tenant_id, goal_text) VALUES (:g, :t, 'x')"),
            {"g": gid, "t": tid},
        )
        await s.execute(
            text(
                "INSERT INTO goal_feedback (id, tenant_id, goal_id, rating) "
                "VALUES (:f, :t, :g, 5)"
            ),
            {"f": fid, "t": tid, "g": gid},
        )
    out = await _process_feedback_batch_async(db=app, system_db=maint)
    assert out["processed"] >= 1, out
    assert await _scalar(
        admin, "SELECT processed_at IS NOT NULL FROM goal_feedback WHERE id = :f", {"f": fid}
    )


# ── 4. Memory 2.0 is DB-authoritative ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_v2_roundtrip_under_rls(dbs: tuple) -> None:
    import httpx
    from fastapi import FastAPI

    import app.api.memory_v2 as memory_v2

    admin, app_db, _ = dbs
    tid = _tid()
    await _seed_tenant(admin, tid)
    api = FastAPI()
    api.include_router(memory_v2.router)
    api.state.db_session_factory = app_db

    @api.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = TenantContext(tenant_id=tid, plan=PlanTier.FREE, api_key_id="k")
        return await call_next(request)

    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        mid = (await c.post("/memory-v2", json={"content": "fact"})).json()["memory_id"]
        assert await _scalar(
            admin, "SELECT count(*) FROM long_term_memory WHERE id = :m", {"m": mid}
        ) == 1
        assert (await c.get(f"/memory-v2/{mid}")).status_code == 200
        assert (await c.patch(f"/memory-v2/{mid}", json={"confidence": 0.5})).status_code == 200
        assert (await c.delete(f"/memory-v2/{mid}")).status_code == 200
        assert (await c.get(f"/memory-v2/{mid}")).status_code == 404
        assert (await c.patch(f"/memory-v2/{mid}", json={"content": "x"})).status_code == 404


# ── 5. training export reads goals + durable evaluations ──────────────────────


@pytest.mark.asyncio
async def test_training_export_db_query(dbs: tuple) -> None:
    from app.api.training_export import _collect_training_examples_db

    admin, app, _ = dbs
    tid = _tid()
    await _seed_tenant(admin, tid)
    gid = _tid()
    async with admin() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO goals (id, tenant_id, goal_text, status) "
                "VALUES (:g, :t, 'ship it', 'complete')"
            ),
            {"g": gid, "t": tid},
        )
        await s.execute(
            text(
                "INSERT INTO evaluations (id, goal_id, tenant_id, scores, average_score, passed, "
                "strategy_execution_id) VALUES (:i, :g, :t, '{}', 0.91, true, 'se-1')"
            ),
            {"i": _tid(), "g": gid, "t": tid},
        )
        await s.execute(
            text(
                "INSERT INTO goal_steps (id, goal_id, tenant_id, step_index, description, output) "
                "VALUES (:i, :g, :t, 0, 'do', 'done')"
            ),
            {"i": _tid(), "g": gid, "t": tid},
        )
    rows = await _collect_training_examples_db(app, 0.8, 10, tid)
    assert len(rows) == 1 and rows[0]["eval_score"] == pytest.approx(0.91)
    assert rows[0]["result"] == "done"
    assert await _collect_training_examples_db(app, 0.8, 10, _tid()) == []
