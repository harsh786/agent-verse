"""Behavioral cross-tenant Row-Level Security proof (Coverage-Matrix row 15 / D-25 / P2-9).

Unlike the string-asserting RLS tests (``tests/db/test_rls.py`` asserts emitted
``set_config`` SQL; ``tests/integration/test_coordination_rls.py`` and
``tests/civilization/test_rls_isolation.py`` assert migration *text*), this test
proves isolation **behaviorally**: it stands up a real Postgres via testcontainers,
runs the actual ``alembic upgrade head`` schema, and connects as a **non-superuser,
non-owner** role that RLS genuinely applies to (superusers and table owners bypass
RLS, so a test that connects as the container's default owner role would prove
nothing).

It then, using the production context managers in ``app/db/rls.py``, shows that:

1. Tenant A, inside its own ``rls_context``, sees ONLY its own rows across a
   representative set of RLS-protected tables (``goals``, ``workflows``,
   ``dpdp_consents``, ``memory_records``) — tenant B's rows are invisible, and
   vice versa.
2. With NO GUC set (and with a mismatched third tenant) neither tenant's rows are
   returned — the policy fails closed.
3. An INSERT under tenant A's GUC that tries to tag a row for tenant B is rejected
   by the RLS ``WITH CHECK`` (or the ``USING``-as-``WITH CHECK`` fallback).

Run with::

    DOCKER_HOST=... TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/integration/test_rls_behavioral_isolation.py -q
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from sqlalchemy.engine import make_url

from app.db.rls import rls_context

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]

# A dedicated application role: NOT a superuser, NOT the table owner, and
# explicitly NOBYPASSRLS — this is what makes the RLS policies actually take
# effect (the container's default role owns the tables and would bypass them).
APP_ROLE = "rls_app_role"
APP_PASSWORD = "rls-app-role-password"

TENANT_A = "tenant-a-rls"
TENANT_B = "tenant-b-rls"
TENANT_C = "tenant-c-rls"  # a third tenant that owns no rows (mismatch case)


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
def _alembic(database_url: str, *arguments: str) -> None:
    subprocess.run(
        ["alembic", *arguments],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        check=True,
        capture_output=True,
        text=True,
    )


def _asyncpg_dsn(sqlalchemy_url: str) -> str:
    """Turn a ``postgresql+asyncpg://`` SQLAlchemy URL into a plain asyncpg DSN."""
    return make_url(sqlalchemy_url).set(drivername="postgresql").render_as_string(
        hide_password=False
    )


def _app_role_url(admin_url: str) -> str:
    return (
        make_url(admin_url)
        .set(username=APP_ROLE, password=APP_PASSWORD)
        .render_as_string(hide_password=False)
    )


# Representative RLS-protected tables: (table, insert-columns, per-tenant value factory).
# Each factory yields the non-id, non-tenant column *values* for a given tenant/seed.
def _goal_values(tenant: str, seed: str) -> list[Any]:
    return [f"goal text for {tenant} / {seed}"]


def _workflow_values(tenant: str, seed: str) -> list[Any]:
    return [f"workflow-{tenant}-{seed}"]


def _dpdp_values(tenant: str, seed: str) -> list[Any]:
    return [f"principal-{tenant}", "marketing", True]


def _memory_values(tenant: str, seed: str) -> list[Any]:
    return [
        "procedural",  # memory_kind
        f"ref://{tenant}/{seed}",  # content_ref
        f"summary for {tenant}",  # safe_summary
        f"goal-{seed}",  # source_goal_id
        f"exec-{seed}",  # source_execution_id
        "internal",  # classification
        5000,  # confidence (0..10000)
        "active",  # lifecycle_state
        "test-embedder",  # embedding_model
        1536,  # embedding_dimension (== 1536 check)
        "retention-default",  # retention_policy_id
        f"idem-{tenant}-{seed}",  # idempotency_key
    ]


PROTECTED_TABLES: list[tuple[str, list[str], Any]] = [
    ("goals", ["goal_text"], _goal_values),
    ("workflows", ["name"], _workflow_values),
    (
        "dpdp_consents",
        ["data_principal_id", "purpose", "consent_given"],
        _dpdp_values,
    ),
    (
        "memory_records",
        [
            "memory_kind",
            "content_ref",
            "safe_summary",
            "source_goal_id",
            "source_execution_id",
            "classification",
            "confidence",
            "lifecycle_state",
            "embedding_model",
            "embedding_dimension",
            "retention_policy_id",
            "idempotency_key",
        ],
        _memory_values,
    ),
]


def _insert_sql(table: str, extra_columns: list[str]) -> str:
    columns = ["id", "tenant_id", *extra_columns]
    placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"


def _row_id(table: str, tenant: str, seed: str) -> str:
    return f"{table[:8]}-{tenant}-{seed}"


async def _provision(admin_url: str) -> None:
    """Create the non-superuser app role, grant it DML, and seed the tenant rows.

    Done over the admin (superuser) connection, which bypasses RLS — this mirrors
    production, where the migration/owner role provisions and the application role
    is the one subject to RLS.
    """
    conn = await asyncpg.connect(_asyncpg_dsn(admin_url))
    try:
        await conn.execute(
            f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' "
            "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
        )
        await conn.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
        await conn.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
        )
        await conn.execute(
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"
        )
        # FK targets for goals/memory_records (tenants.id). Referential-integrity
        # checks bypass RLS, so these only need to exist.
        for tenant in (TENANT_A, TENANT_B):
            await conn.execute(
                "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                "VALUES ($1, $2, $3, 'free', true)",
                tenant,
                f"Tenant {tenant}",
                f"{tenant}@example.test",
            )
    finally:
        await conn.close()


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def rls_postgres() -> Iterator[tuple[str, str]]:
    """Real Postgres with the full schema applied and a non-superuser app role.

    Yields ``(admin_url, app_url)`` as ``postgresql+asyncpg://`` SQLAlchemy URLs.
    """
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        _alembic(admin_url, "upgrade", "head")
        app_url = _app_role_url(admin_url)
        asyncio.run(_provision(admin_url))
        asyncio.run(_seed_both_tenants(app_url))
        yield admin_url, app_url


# --------------------------------------------------------------------------- #
# tests                                                                        #
# --------------------------------------------------------------------------- #
async def _seed_both_tenants(app_url: str) -> None:
    """Insert one row per tenant into every protected table, each write inside the
    writing tenant's own ``rls_context`` (exactly as production does)."""
    conn = await asyncpg.connect(_asyncpg_dsn(app_url))
    try:
        for tenant in (TENANT_A, TENANT_B):
            for table, extra_columns, factory in PROTECTED_TABLES:
                async with conn.transaction(), rls_context(conn, tenant):
                    await conn.execute(
                        _insert_sql(table, extra_columns),
                        _row_id(table, tenant, "seed"),
                        tenant,
                        *factory(tenant, "seed"),
                    )
    finally:
        await conn.close()


async def test_cross_tenant_reads_are_isolated(rls_postgres: tuple[str, str]) -> None:
    """Inside tenant A's GUC, only A's rows are visible — and symmetrically for B."""
    _admin_url, app_url = rls_postgres

    conn = await asyncpg.connect(_asyncpg_dsn(app_url))
    try:
        for reader, other in ((TENANT_A, TENANT_B), (TENANT_B, TENANT_A)):
            for table, _cols, _factory in PROTECTED_TABLES:
                async with conn.transaction(), rls_context(conn, reader):
                    rows = await conn.fetch(
                        f"SELECT id, tenant_id FROM {table}"
                    )
                visible_tenants = {r["tenant_id"] for r in rows}
                visible_ids = {r["id"] for r in rows}
                assert visible_tenants == {reader}, (
                    f"{table}: tenant {reader} saw tenants {visible_tenants}, "
                    f"expected only {{{reader}}}"
                )
                assert _row_id(table, reader, "seed") in visible_ids
                assert _row_id(table, other, "seed") not in visible_ids, (
                    f"{table}: tenant {reader} could see tenant {other}'s row — "
                    "RLS cross-tenant leak!"
                )
    finally:
        await conn.close()


async def test_reads_fail_closed_without_or_with_mismatched_guc(
    rls_postgres: tuple[str, str],
) -> None:
    """With no GUC set, or a mismatched tenant, no tenant's rows are returned."""
    _admin_url, app_url = rls_postgres

    conn = await asyncpg.connect(_asyncpg_dsn(app_url))
    try:
        for table, _cols, _factory in PROTECTED_TABLES:
            # No GUC set at all → current_setting(..., true) is NULL → zero rows.
            async with conn.transaction():
                rows_no_guc = await conn.fetch(f"SELECT id FROM {table}")
            assert rows_no_guc == [], (
                f"{table}: rows leaked with NO app.tenant_id set — must fail closed"
            )

            # A third tenant that owns nothing → sees nothing.
            async with conn.transaction(), rls_context(conn, TENANT_C):
                rows_mismatch = await conn.fetch(f"SELECT id FROM {table}")
            assert rows_mismatch == [], (
                f"{table}: mismatched tenant {TENANT_C} saw rows — RLS leak"
            )
    finally:
        await conn.close()


async def test_insert_check_blocks_cross_tenant_write(
    rls_postgres: tuple[str, str],
) -> None:
    """An INSERT under tenant A's GUC cannot tag a row for tenant B (WITH CHECK)."""
    _admin_url, app_url = rls_postgres

    conn = await asyncpg.connect(_asyncpg_dsn(app_url))
    try:
        for table, extra_columns, factory in PROTECTED_TABLES:
            # Outer transaction holds the tenant-A GUC; the forged INSERT runs in an
            # inner savepoint so its RLS rejection rolls back only the savepoint,
            # leaving the outer transaction (and rls_context cleanup) intact — and
            # letting us capture the ACTUAL policy-violation error, not a masked
            # "transaction is aborted" follow-on error.
            async with conn.transaction(), rls_context(conn, TENANT_A):
                with pytest.raises(asyncpg.PostgresError) as exc_info:
                    async with conn.transaction():
                        # tenant_id deliberately set to TENANT_B while the GUC
                        # says TENANT_A.
                        await conn.execute(
                            _insert_sql(table, extra_columns),
                            _row_id(table, TENANT_B, "forged"),
                            TENANT_B,
                            *factory(TENANT_B, "forged"),
                        )
            assert "row-level security" in str(exc_info.value).lower(), (
                f"{table}: cross-tenant INSERT was not blocked by RLS WITH CHECK "
                f"(got: {exc_info.value!r})"
            )

        # And confirm the forged rows never landed (verified as tenant B).
        for table, _cols, _factory in PROTECTED_TABLES:
            async with conn.transaction(), rls_context(conn, TENANT_B):
                forged = await conn.fetch(
                    f"SELECT id FROM {table} WHERE id = $1",
                    _row_id(table, TENANT_B, "forged"),
                )
            assert forged == [], f"{table}: a forged cross-tenant row persisted"
    finally:
        await conn.close()
