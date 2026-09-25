"""Integration test: the HIPAA framework check must be *passable*.

``ComplianceChecker`` (app/enterprise/compliance_v2.py) never sets the
``app.tenant_id`` GUC anywhere — it contains zero ``set_config`` calls — yet two
of the five HIPAA controls read FORCE-ROW-LEVEL-SECURITY tables:

  * ``_check_audit_active``   → ``audit_events``   (migration 0057, FORCE RLS)
  * ``_check_phi_hitl_policy``→ ``policy_versions``(migration 0056, FORCE RLS)

Under any real least-privilege (non-BYPASSRLS) role — the kind provisioned in
production — both queries match zero rows and the controls report failure.

A third control, ``_check_encryption_tier``, ran ``SELECT plan FROM tenants``,
but that column is ``plan_tier`` (migration 0002). Every call raised
UndefinedColumn, swallowed by a bare ``except Exception: return False``.

Net effect: 3 of 5 controls could never pass, so ``check_hipaa`` returned
``non_compliant`` for even a perfectly configured tenant (4 of 5 are needed for
"partial", 5 for "compliant") — the framework was unusable, and every failure
was reported as a genuine compliance gap with an actionable-sounding note.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/enterprise/test_compliance_v2_hipaa_rls.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.enterprise.compliance_v2 import ComplianceChecker

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "test_app_hipaa"
GRANT_TABLES = (
    "tenants",
    "tenant_settings",
    "enterprise_contracts",
    "audit_events",
    "policy_versions",
    "compliance_certifications",
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


@pytest_asyncio.fixture(scope="function")
async def factories(postgres_url: str) -> AsyncIterator[tuple]:
    """(admin_factory, app_factory) — app_factory is NOSUPERUSER/NOBYPASSRLS, the
    only configuration under which the RLS gap is visible."""
    password = secrets.token_urlsafe(24)
    admin_engine = create_async_engine(postgres_url)
    async with admin_engine.begin() as conn:
        quoted = (
            await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})
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
        .set(username=APP_ROLE, password=password)
        .render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, pool_size=4, max_overflow=0, echo=False)
    admin_factory = async_sessionmaker(admin_engine, expire_on_commit=False)
    yield (admin_factory, async_sessionmaker(app_engine, expire_on_commit=False))
    await app_engine.dispose()
    await admin_engine.dispose()


async def _seed_fully_compliant_hipaa_tenant(admin_factory, tenant_id: str) -> None:
    """Satisfy all five HIPAA controls check_hipaa looks for."""
    async with admin_factory() as s:
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, email, plan_tier) "
                "VALUES (:id, 'T', :email, 'enterprise')"
            ),
            {"id": tenant_id, "email": f"{tenant_id}@example.test"},
        )
        # 1. signed BAA
        await s.execute(
            text(
                "INSERT INTO enterprise_contracts "
                "(tenant_id, contract_type, status, signed_by_name, signed_at) "
                "VALUES (:t, 'baa', 'signed', 'Jo Officer', NOW())"
            ),
            {"t": tenant_id},
        )
        # 2. PHI access logging active
        await s.execute(
            text(
                "INSERT INTO audit_events (tenant_id, event_type, action) "
                "VALUES (:t, 'phi.read', 'read')"
            ),
            {"t": tenant_id},
        )
        # 3. active HITL policy covering PHI
        await s.execute(
            text(
                "INSERT INTO policy_versions "
                "(tenant_id, policy_id, version_number, name, rules, is_active) "
                "VALUES (:t, 'phi-hitl', 1, 'PHI HITL', "
                "CAST(:rules AS jsonb), TRUE)"
            ),
            {"t": tenant_id, "rules": '[{"match": "patient", "require": "hitl"}]'},
        )
        # 4 + 5. training tracked; plan tier already 'enterprise' above
        await s.execute(
            text(
                "INSERT INTO tenant_settings (tenant_id, settings) "
                "VALUES (:t, CAST(:cfg AS jsonb))"
            ),
            {
                "t": tenant_id,
                "cfg": '{"hipaa_training_enabled": "true", "retention_days": "365"}',
            },
        )
        await s.commit()


@pytest.mark.asyncio
async def test_fully_configured_tenant_passes_hipaa_under_nobypassrls_role(
    factories: tuple,
) -> None:
    admin_factory, app_factory = factories
    tenant_id = f"tenant-{secrets.token_hex(6)}"
    await _seed_fully_compliant_hipaa_tenant(admin_factory, tenant_id)

    report = await ComplianceChecker(db_factory=app_factory).check_hipaa(tenant_id)

    failed = {k: v for k, v in report["controls"].items() if not v["pass"]}
    assert not failed, (
        f"controls failed for a fully-configured HIPAA tenant: {failed}. "
        f"Report: {report['passed_count']}/{report['total_count']} -> {report['status']}"
    )
    assert report["status"] == "compliant", report
