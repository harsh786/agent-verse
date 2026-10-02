"""Integration: compliance/enterprise tables under enforced RLS and a NOBYPASSRLS role.

Eight tables had NO row level security at all — isolation rested entirely on each
query remembering ``WHERE tenant_id = ...``:

    compliance_certifications, consent_records, deleted_tenants,
    enterprise_contracts, gdpr_export_jobs, golden_tasks,
    marketplace_author_accounts, org_blueprints

``PROPOSED_RLS_SQL`` below is the exact SQL the follow-up migration must apply
(migrations are written separately from this change). This test applies it on
top of ``alembic upgrade head`` and then drives the real code paths as a
least-privilege role (NOSUPERUSER NOBYPASSRLS) — the production posture, and the
only one under which RLS gaps are visible (a superuser ignores RLS entirely).

Once the migration lands the statements here are idempotent no-ops (ENABLE/FORCE
are idempotent; policies are dropped-if-exists before being created).

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/enterprise/test_compliance_tables_rls_integration.py \
        -q -m integration --no-cov
"""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "test_app_compliance_rls"

_TEXT_GUC = "current_setting('app.tenant_id', true)"


def _tenant_all(table: str) -> list[str]:
    """Plain tenant isolation for a TEXT tenant_id table (every command)."""
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}",
        f"CREATE POLICY {table}_tenant_isolation ON {table} AS PERMISSIVE FOR ALL "
        f"USING (tenant_id = {_TEXT_GUC}) WITH CHECK (tenant_id = {_TEXT_GUC})",
    ]


# The SQL the migration must apply, per table. Keep in sync with the work-group
# report (migration_sql).
PROPOSED_RLS_SQL: dict[str, list[str]] = {
    # Platform attestation: the tenant may READ its certification records; they
    # are written by platform staff through the maintenance (BYPASSRLS) role. No
    # tenant write path exists, and check_soc2 treats an 'active' row as proof of
    # certification, so a tenant must not be able to write its own.
    "compliance_certifications": [
        "ALTER TABLE compliance_certifications ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE compliance_certifications FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS compliance_certifications_tenant_read "
        "ON compliance_certifications",
        "CREATE POLICY compliance_certifications_tenant_read ON compliance_certifications "
        f"AS PERMISSIVE FOR SELECT USING (tenant_id = {_TEXT_GUC})",
    ],
    "consent_records": _tenant_all("consent_records"),
    # A tenant's own pending-erasure request: written on its request path
    # (POST /enterprise/compliance/delete), cleared by the erasure under the same
    # tenant's GUC. Any future cross-tenant "due erasures" scanner uses the
    # maintenance role.
    "deleted_tenants": _tenant_all("deleted_tenants"),
    "enterprise_contracts": _tenant_all("enterprise_contracts"),
    "gdpr_export_jobs": _tenant_all("gdpr_export_jobs"),
    "golden_tasks": _tenant_all("golden_tasks"),
    "marketplace_author_accounts": _tenant_all("marketplace_author_accounts"),
    # UUID tenant_id, NULL = global blueprint. Global rows are readable by every
    # tenant and writable only by the maintenance role; a tenant reads/writes its own.
    "org_blueprints": [
        "ALTER TABLE org_blueprints ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE org_blueprints FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS org_blueprints_read ON org_blueprints",
        "CREATE POLICY org_blueprints_read ON org_blueprints AS PERMISSIVE FOR SELECT "
        "USING (tenant_id IS NULL OR tenant_id = app_current_tenant_uuid())",
        "DROP POLICY IF EXISTS org_blueprints_tenant_insert ON org_blueprints",
        "CREATE POLICY org_blueprints_tenant_insert ON org_blueprints AS PERMISSIVE "
        "FOR INSERT WITH CHECK (tenant_id = app_current_tenant_uuid())",
        "DROP POLICY IF EXISTS org_blueprints_tenant_update ON org_blueprints",
        "CREATE POLICY org_blueprints_tenant_update ON org_blueprints AS PERMISSIVE "
        "FOR UPDATE USING (tenant_id = app_current_tenant_uuid()) "
        "WITH CHECK (tenant_id = app_current_tenant_uuid())",
        "DROP POLICY IF EXISTS org_blueprints_tenant_delete ON org_blueprints",
        "CREATE POLICY org_blueprints_tenant_delete ON org_blueprints AS PERMISSIVE "
        "FOR DELETE USING (tenant_id = app_current_tenant_uuid())",
    ],
}

GROUP_TABLES = tuple(PROPOSED_RLS_SQL)
GRANT_TABLES = (
    *GROUP_TABLES,
    "tenants",
    "tenant_settings",
    "audit_events",
    "policy_versions",
    "compliance_requests",
    "goals",
    "audit_log",
    # Erasure is gated on legal_holds (fail-closed); the role must be able to read it.
    "legal_holds",
    # Golden tasks are written through EvalSuiteStore, which bumps the suite's
    # dataset version (MEM-54).
    "eval_suites",
)


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


_PASSWORD = secrets.token_urlsafe(24)


@pytest_asyncio.fixture(scope="function")
async def factories(postgres_url: str) -> AsyncIterator[tuple[Any, Any, str]]:
    """(admin_factory, app_factory, app_url). The app role is NOBYPASSRLS."""
    admin_engine = create_async_engine(postgres_url, poolclass=NullPool)
    async with admin_engine.begin() as conn:
        for stmts in PROPOSED_RLS_SQL.values():
            for stmt in stmts:
                await conn.execute(text(stmt))
        exists = (
            await conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": APP_ROLE}
            )
        ).scalar()
        if not exists:
            quoted = (
                await conn.execute(text("SELECT quote_literal(:p)"), {"p": _PASSWORD})
            ).scalar_one()
            await conn.execute(
                text(
                    f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD {quoted} "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
                )
            )
            await conn.execute(text(f"GRANT CONNECT ON DATABASE test TO {APP_ROLE}"))
            await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
            for tbl in GRANT_TABLES:
                await conn.execute(
                    text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO {APP_ROLE}")
                )
    app_url = (
        make_url(postgres_url)
        .set(username=APP_ROLE, password=_PASSWORD)
        .render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, poolclass=NullPool)
    yield (
        async_sessionmaker(admin_engine, expire_on_commit=False),
        async_sessionmaker(app_engine, expire_on_commit=False),
        app_url,
    )
    await app_engine.dispose()
    await admin_engine.dispose()


def _tid() -> str:
    # Signup creates tenant ids as uuid4().hex (32 hex chars, no dashes).
    return uuid.uuid4().hex


async def _seed_tenant(admin: Any, tenant_id: str) -> None:
    async with admin() as s, s.begin():
        await s.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:id, 'T', :email)"),
            {"id": tenant_id, "email": f"{tenant_id}@example.test"},
        )


async def _as_app(app: Any, tenant: str | None, sql: str, params: dict[str, Any]) -> Any:
    """Run one statement as the app role, optionally under a tenant GUC.

    Returns fetched rows for SELECT/RETURNING, else the rowcount.
    """
    async with app() as s, s.begin():
        if tenant is not None:
            await s.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant}
            )
        result = await s.execute(text(sql), params)
        return result.fetchall() if result.returns_rows else result.rowcount


def _rls_rejected(exc: BaseException) -> bool:
    return "row-level security" in str(exc)


# ── Catalog ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_group_table_enables_and_forces_rls_with_policies(factories: tuple) -> None:
    admin, _app, _ = factories
    async with admin() as s:
        rows = (
            await s.execute(
                text(
                    """
                    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                           (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
                    WHERE c.relname = ANY(:tables)
                    """
                ),
                {"tables": list(GROUP_TABLES)},
            )
        ).fetchall()
    found = {r[0]: r[1:] for r in rows}
    assert set(found) == set(GROUP_TABLES), f"missing tables: {set(GROUP_TABLES) - set(found)}"
    for table, (enabled, forced, policies) in found.items():
        assert enabled and forced, f"{table}: ENABLE={enabled} FORCE={forced}"
        assert policies >= 1, f"{table}: no policy"


# ── Plain tenant isolation (TEXT tenant_id) ───────────────────────────────────

_INSERTS: dict[str, tuple[str, bool]] = {
    # table -> (INSERT sql with :id/:tid, needs a tenants row for the FK)
    "consent_records": (
        "INSERT INTO consent_records (id, tenant_id, purpose) VALUES (:id, :tid, 'analytics')",
        False,
    ),
    "deleted_tenants": ("INSERT INTO deleted_tenants (tenant_id) VALUES (:tid)", False),
    "enterprise_contracts": (
        "INSERT INTO enterprise_contracts (id, tenant_id, contract_type) "
        "VALUES (:id, :tid, 'dpa')",
        True,
    ),
    "gdpr_export_jobs": ("INSERT INTO gdpr_export_jobs (id, tenant_id) VALUES (:id, :tid)", False),
    "golden_tasks": (
        "INSERT INTO golden_tasks (id, eval_suite_id, tenant_id, goal) "
        "VALUES (:id, 's1', :tid, 'g')",
        False,
    ),
    "marketplace_author_accounts": (
        "INSERT INTO marketplace_author_accounts (id, tenant_id) VALUES (:id, :tid)",
        False,
    ),
}


@pytest.mark.parametrize("table", sorted(_INSERTS))
@pytest.mark.asyncio
async def test_text_tenant_table_isolates_tenants(factories: tuple, table: str) -> None:
    admin, app, _ = factories
    insert_sql, needs_tenant_row = _INSERTS[table]
    a, b = _tid(), _tid()
    if needs_tenant_row:
        await _seed_tenant(admin, a)
        await _seed_tenant(admin, b)

    # No tenant context: a write is rejected outright.
    with pytest.raises(DBAPIError) as no_ctx:
        await _as_app(app, None, insert_sql, {"id": _tid(), "tid": a})
    assert _rls_rejected(no_ctx.value), no_ctx.value

    # Tenant B cannot write a row owned by tenant A.
    with pytest.raises(DBAPIError) as cross:
        await _as_app(app, b, insert_sql, {"id": _tid(), "tid": a})
    assert _rls_rejected(cross.value), cross.value

    # Tenant A writes its own row.
    assert await _as_app(app, a, insert_sql, {"id": _tid(), "tid": a}) == 1

    count_sql = f"SELECT count(*) FROM {table} WHERE tenant_id = :tid"
    assert (await _as_app(app, a, count_sql, {"tid": a}))[0][0] == 1
    assert (await _as_app(app, b, count_sql, {"tid": a}))[0][0] == 0, "B saw A's row"
    assert (await _as_app(app, None, count_sql, {"tid": a}))[0][0] == 0, "unscoped read saw it"

    # Tenant B can neither modify nor delete A's row.
    assert await _as_app(app, b, f"DELETE FROM {table} WHERE tenant_id = :tid", {"tid": a}) == 0
    async with admin() as s:
        left = (
            await s.execute(text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": a})
        ).scalar()
    assert left == 1


# ── compliance_certifications: tenant-readable, platform-written ─────────────


@pytest.mark.asyncio
async def test_certifications_are_readable_by_owner_only_and_not_tenant_writable(
    factories: tuple,
) -> None:
    admin, app, _ = factories
    a, b = _tid(), _tid()
    for t in (a, b):
        await _seed_tenant(admin, t)
    async with admin() as s, s.begin():  # platform (maintenance-role) write
        await s.execute(
            text(
                "INSERT INTO compliance_certifications (tenant_id, certification_type, status) "
                "VALUES (:t, 'soc2_type2', 'active')"
            ),
            {"t": a},
        )

    sel = "SELECT status FROM compliance_certifications WHERE tenant_id = :tid"
    assert await _as_app(app, a, sel, {"tid": a}) == [("active",)]
    assert await _as_app(app, b, sel, {"tid": a}) == []

    # A tenant cannot self-certify, nor edit/delete its certification.
    with pytest.raises(DBAPIError) as ins:
        await _as_app(
            app,
            b,
            "INSERT INTO compliance_certifications (tenant_id, certification_type, status) "
            "VALUES (:tid, 'soc2_type2', 'active')",
            {"tid": b},
        )
    assert _rls_rejected(ins.value), ins.value
    upd = "UPDATE compliance_certifications SET status = 'revoked' WHERE tenant_id = :tid"
    assert await _as_app(app, a, upd, {"tid": a}) == 0
    assert (
        await _as_app(app, a, "DELETE FROM compliance_certifications WHERE tenant_id = :tid",
                      {"tid": a})
        == 0
    )

    # The SOC2 control still reads it under the tenant's context.
    from app.enterprise.compliance_v2 import ComplianceChecker

    report = await ComplianceChecker(db_factory=app).check_soc2(a)
    assert report["controls"]["certification_on_file"]["pass"] is True


# ── org_blueprints: global readable, owner writable ───────────────────────────


@pytest.mark.asyncio
async def test_org_blueprints_global_readable_owner_writable(factories: tuple) -> None:
    admin, app, _ = factories
    a, b = _tid(), _tid()
    ids = {k: uuid.uuid4() for k in ("global", "a", "b")}
    tag = secrets.token_hex(4)
    async with admin() as s, s.begin():  # global template seeded by the platform
        for key, owner in (("global", None), ("a", a), ("b", b)):
            await s.execute(
                text(
                    "INSERT INTO org_blueprints (id, tenant_id, name, slug, domain) "
                    "VALUES (:id, CAST(:t AS uuid), :name, :slug, :domain)"
                ),
                {"id": ids[key], "t": owner, "name": f"{key}-{tag}", "slug": f"{key}-{tag}",
                 "domain": tag},
            )

    sel = "SELECT name FROM org_blueprints WHERE domain = :d ORDER BY name"
    assert await _as_app(app, a, sel, {"d": tag}) == [(f"a-{tag}",), (f"global-{tag}",)]
    assert await _as_app(app, None, sel, {"d": tag}) == [(f"global-{tag}",)]

    ins = (
        "INSERT INTO org_blueprints (id, tenant_id, name, slug) "
        "VALUES (:id, CAST(:owner AS uuid), 'x', :slug)"
    )
    # A tenant cannot create a global blueprint, nor one for another tenant.
    for owner in (None, b):
        with pytest.raises(DBAPIError) as rejected:
            await _as_app(app, a, ins, {"id": uuid.uuid4(), "owner": owner,
                                        "slug": f"x-{secrets.token_hex(4)}"})
        assert _rls_rejected(rejected.value), rejected.value
    assert await _as_app(app, a, ins, {"id": uuid.uuid4(), "owner": a,
                                       "slug": f"x-{secrets.token_hex(4)}"}) == 1

    # Global rows are read-only to tenants; B's rows are invisible to A.
    upd = "UPDATE org_blueprints SET description = 'pwned' WHERE id = :id"
    assert await _as_app(app, a, upd, {"id": ids["global"]}) == 0
    assert await _as_app(app, a, upd, {"id": ids["b"]}) == 0
    assert await _as_app(app, a, "DELETE FROM org_blueprints WHERE id = :id",
                         {"id": ids["global"]}) == 0
    assert await _as_app(app, a, upd, {"id": ids["a"]}) == 1

    # OrgService under the tenant's RLS scope (the org router's dependency shape).
    from app.db.rls import sqlalchemy_rls_context
    from app.org.service import OrgService

    async with app() as s, s.begin(), sqlalchemy_rls_context(s, a):
        svc = OrgService(session=s, tenant_id=a)
        listed = {bp.name for bp in await svc.list_blueprints(domain=tag)}
        foreign = await svc.get_blueprint(str(ids["b"]))
        glob = await svc.get_blueprint_by_slug(f"global-{tag}")
    assert listed == {f"a-{tag}", f"global-{tag}"}
    assert foreign is None
    assert glob is not None


# ── golden_tasks: a tenant cannot edit another tenant's golden task ─────────


@pytest.mark.asyncio
async def test_golden_task_edit_cannot_touch_another_tenants_task(
    factories: tuple,
) -> None:
    # MEM-54: golden tasks are revision rows written by EvalSuiteStore; task ids
    # are scoped to (tenant, suite), so the same id in another tenant is a
    # different task and an edit through tenant B never reaches A's row.
    admin, app, _ = factories
    from app.intelligence.eval_suite_store import EvalSuiteStore

    a, b = _tid(), _tid()
    store_a, store_b = EvalSuiteStore(app, a), EvalSuiteStore(app, b)
    await store_a.create("s1", name="s1", description="")
    await store_b.create("s1", name="s1", description="")
    task_id = _tid()
    await store_a.import_tasks(
        "s1", [{"task_id": task_id, "goal": "original", "expected_tools": ["t"]}],
        replace=False,
    )
    assert await store_b.update_task("s1", task_id, {"goal": "hijacked"}) is None
    await store_b.import_tasks(
        "s1", [{"task_id": task_id, "goal": "b's own", "expected_tools": ["t"]}],
        replace=False,
    )
    async with admin() as s:
        rows = (
            await s.execute(
                text("SELECT tenant_id, goal FROM golden_tasks WHERE task_id = :id "
                     "ORDER BY tenant_id = :a DESC"),
                {"id": task_id, "a": a},
            )
        ).all()
    assert [tuple(r) for r in rows] == [(a, "original"), (b, "b's own")]
    assert [t["goal"] for t in await store_a.list_tasks("s1")] == ["original"]
    edited = await store_a.update_task("s1", task_id, {"goal": "edited"})
    assert edited is not None
    assert [t["goal"] for t in await store_a.list_tasks("s1")] == ["edited"]
    assert [t["goal"] for t in await store_b.list_tasks("s1")] == ["b's own"]


# ── Request paths end to end, as the app role ─────────────────────────────────


def _api(app_factory: Any, tenants: dict[str, str]) -> Any:
    from fastapi import FastAPI

    from app.api.enterprise import compliance_router
    from app.api.enterprise import router as enterprise_router
    from app.enterprise.compliance import ComplianceController
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.middleware import TenantMiddleware

    api = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        tid = tenants.get(key)
        return (
            TenantContext(
                tenant_id=tid, plan=PlanTier.ENTERPRISE, api_key_id=key, roles=("admin",)
            )
            if tid
            else None
        )  # admin: signing a contract is admin-only (3bae0a371)

    api.add_middleware(TenantMiddleware, key_resolver=_resolve)
    api.include_router(enterprise_router)
    api.include_router(compliance_router)
    api.state.db_session_factory = app_factory
    controller = ComplianceController()
    controller.configure_services(db=app_factory)
    api.state.compliance_controller = controller
    return api


@pytest.mark.asyncio
async def test_compliance_request_paths_work_and_isolate_under_nobypassrls(
    factories: tuple,
) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.enterprise.compliance_v2 import ComplianceChecker

    admin, app, _ = factories
    a, b = _tid(), _tid()
    for t in (a, b):
        await _seed_tenant(admin, t)
    api = _api(app, {"key-a": a, "key-b": b})
    ha, hb = {"X-API-Key": "key-a"}, {"X-API-Key": "key-b"}

    with patch("app.scaling.tasks.run_gdpr_export") as task:
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://t") as c:
            assert (await c.post("/compliance/consent", json={"purpose": "analytics"},
                                 headers=ha)).status_code == 200
            assert (await c.post("/compliance/consent", json={"purpose": "marketing"},
                                 headers=ha)).status_code == 200
            assert (await c.delete("/compliance/consent/marketing", headers=ha)).status_code == 200
            job_id = (await c.post("/compliance/export/start", headers=ha)).json()["job_id"]
            signed = await c.post(
                "/enterprise/contracts/dpa/sign",
                json={"signer_name": "Ada", "signer_email": "ada@example.test"},
                headers=ha,
            )
            assert signed.status_code == 201, signed.text

            polled = await c.get(f"/compliance/export/jobs/{job_id}", headers=ha)
            assert polled.status_code == 200, polled.text
            assert polled.json()["status"] == "pending"
            contracts = (await c.get("/enterprise/contracts", headers=ha)).json()
            assert [k["contract_type"] for k in contracts] == ["dpa"]

            # Tenant B sees none of it.
            assert (await c.get("/enterprise/contracts", headers=hb)).json() == []
            assert (await c.get(f"/compliance/export/jobs/{job_id}", headers=hb)).status_code == 404
            # ...and B revoking "analytics" does not touch A's consent: B has no
            # active consent for it, so the revoke is a 404 (never a fake 'revoked').
            assert (await c.delete("/compliance/consent/analytics", headers=hb)).status_code == 404
    task.delay.assert_called_once_with(job_id, a)

    async with admin() as s:
        consents = (
            await s.execute(
                text(
                    "SELECT purpose, revoked_at IS NOT NULL FROM consent_records "
                    "WHERE tenant_id = :t ORDER BY purpose"
                ),
                {"t": a},
            )
        ).fetchall()
        job = (
            await s.execute(
                text("SELECT tenant_id, status FROM gdpr_export_jobs WHERE id = :id"),
                {"id": job_id},
            )
        ).one()
    assert [tuple(r) for r in consents] == [("analytics", False), ("marketing", True)]
    assert tuple(job) == (a, "pending")

    # The GDPR control reads all three tables under A's context. Mark the job the
    # way the export worker does ('complete') — the control used to miss it.
    async with admin() as s, s.begin():
        await s.execute(
            text(
                "UPDATE gdpr_export_jobs SET status = 'complete', completed_at = NOW() "
                "WHERE id = :id"
            ),
            {"id": job_id},
        )
    report = await ComplianceChecker(db_factory=app).check_gdpr(a)
    for control in ("dpa_signed", "data_portability", "consent_management"):
        assert report["controls"][control]["pass"] is True, (control, report["controls"])
    assert all(
        not report_b["pass"]
        for name, report_b in (await ComplianceChecker(db_factory=app).check_gdpr(b))[
            "controls"
        ].items()
        if name in ("dpa_signed", "data_portability", "consent_management")
    )


@pytest.mark.asyncio
async def test_erasure_request_and_execution_manage_deleted_tenants_row(
    factories: tuple,
) -> None:
    from app.enterprise.compliance import ComplianceController
    from app.tenancy.context import PlanTier, TenantContext

    admin, app, _ = factories
    victim, other = _tid(), _tid()
    for t in (victim, other):
        await _seed_tenant(admin, t)
    ctx = TenantContext(tenant_id=victim, plan=PlanTier.ENTERPRISE, api_key_id="k")

    cc = ComplianceController()
    cc.configure_services(db=app)
    await cc.request_data_deletion(tenant_ctx=ctx)

    async def _pending(tid: str) -> int:
        async with admin() as s:
            return int(
                (
                    await s.execute(
                        text("SELECT count(*) FROM deleted_tenants WHERE tenant_id = :t"),
                        {"t": tid},
                    )
                ).scalar_one()
            )

    assert await _pending(victim) == 1, "erasure request was never recorded"
    sel = "SELECT count(*) FROM deleted_tenants WHERE tenant_id = :tid"
    assert (await _as_app(app, other, sel, {"tid": victim}))[0][0] == 0

    await cc.execute_data_deletion_async(tenant_ctx=ctx, db=app)
    # The job row is kept as the durable record of the erasure (status/result).
    assert await _pending(victim) == 1, "erasure job record was lost"


@pytest.mark.asyncio
async def test_gdpr_export_worker_completes_its_job_under_rls(factories: tuple) -> None:
    admin, _app, app_url = factories
    tenant = _tid()
    job_id = _tid()
    await _seed_tenant(admin, tenant)
    async with admin() as s, s.begin():
        await s.execute(
            text("INSERT INTO gdpr_export_jobs (id, tenant_id) VALUES (:id, :t)"),
            {"id": job_id, "t": tenant},
        )

    from app.scaling.tasks import run_gdpr_export

    def _fresh_factory() -> Any:
        return async_sessionmaker(
            create_async_engine(app_url, poolclass=NullPool), expire_on_commit=False
        )

    with patch("app.db.session.get_session_factory", side_effect=_fresh_factory):
        result = await asyncio.to_thread(run_gdpr_export.run, job_id=job_id, tenant_id=tenant)
    assert result["status"] == "complete"

    async with admin() as s:
        row = (
            await s.execute(
                text("SELECT status, download_url FROM gdpr_export_jobs WHERE id = :id"),
                {"id": job_id},
            )
        ).one()
    assert row[0] == "complete", "job stayed pending: the completion UPDATE matched no row"
    assert row[1] == result["download_url"]
