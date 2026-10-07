"""Integration: chat/billing tables under enforced RLS and a NOBYPASSRLS role.

After ``alembic upgrade head`` these tables were not actually isolated:

* ``chat_artifacts``, ``chat_message_usage``, ``chat_session_folders`` and
  ``cost_ledger`` had ``ENABLE`` + a USING-only policy but no ``FORCE`` — the
  table owner (the role every dev/compose stack connects as) bypassed them.
* ``budget_configs``, ``benchmark_runs`` and ``audit_wal_queue`` had no RLS at
  all (``ab_test_results`` too; that orphaned table was dropped in e5f1a9c3d7b2).

``RLS_SQL`` below is the schema change these tables need (to be shipped as an
Alembic revision; this test applies it itself so it can exercise the fixed
application code paths against it). It is idempotent so it keeps passing once
the revision lands.

It also pins the ``POST /chat/templates`` 500: ``_TemplateStore._create_db``
did ``commit()`` + ``refresh(obj)``; the refresh ran in a new transaction with
no tenant GUC, RLS hid the just-inserted ``goal_templates`` row and SQLAlchemy
raised "Could not refresh instance".

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/db/test_chat_billing_rls_integration.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.db.rls import sqlalchemy_rls_context
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TENANT_A = "tenant-rls-cb-a"
TENANT_B = "tenant-rls-cb-b"

_TEXT_GUC = "current_setting('app.tenant_id', true)"

# A minimal valid row: since ORG-42 (c7fbea9807c1) content is BYTEA with no
# default and kind is required ('snippet' rows belong to a session).
_CHAT_ARTIFACT_INSERT = (
    "INSERT INTO chat_artifacts (id, session_id, tenant_id, kind, content) "
    "VALUES (:id, :sid, :t, 'snippet', convert_to('x', 'UTF8'))"
)

_TENANT_TABLES = (
    "chat_artifacts",
    "chat_message_usage",
    "chat_session_folders",
    "cost_ledger",
    "budget_configs",
    "benchmark_runs",
    "audit_wal_queue",
)


def _isolation(table: str, policy: str) -> str:
    return (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;\n"
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;\n"
        f"DROP POLICY IF EXISTS {policy} ON {table};\n"
        f"CREATE POLICY {policy} ON {table} "
        f"USING (tenant_id = {_TEXT_GUC}) WITH CHECK (tenant_id = {_TEXT_GUC});\n"
    )


RLS_SQL = (
    _isolation("chat_artifacts", "chat_artifacts_tenant_isolation")
    + _isolation("chat_message_usage", "chat_message_usage_tenant_isolation")
    + _isolation("chat_session_folders", "chat_session_folders_tenant_isolation")
    + _isolation("cost_ledger", "cost_ledger_isolation")
    + _isolation("budget_configs", "budget_configs_tenant_isolation")
    + _isolation("benchmark_runs", "benchmark_runs_tenant_isolation")
    + "DROP POLICY IF EXISTS benchmark_runs_global_read ON benchmark_runs;\n"
    + "CREATE POLICY benchmark_runs_global_read ON benchmark_runs "
    "FOR SELECT USING (tenant_id = 'global');\n"
    + "CREATE INDEX IF NOT EXISTS ix_benchmark_runs_tenant_suite "
    "ON benchmark_runs (tenant_id, suite_name, created_at DESC);\n"
    + _isolation("audit_wal_queue", "audit_wal_queue_tenant_isolation")
)


def _ctx(tenant_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k1")


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
    password = secrets.token_urlsafe(24)
    role = f"test_app_cb_{secrets.token_hex(4)}"
    admin_engine = create_async_engine(postgres_url)
    async with admin_engine.begin() as conn:
        for stmt in filter(None, (s.strip() for s in RLS_SQL.split(";\n"))):
            await conn.execute(text(stmt))
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
        for table in (*_TENANT_TABLES, "goal_templates", "chat_sessions"):
            await conn.execute(
                text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role}")
            )
        await conn.execute(
            text(f"GRANT USAGE ON SEQUENCE audit_wal_queue_id_seq TO {role}")
        )
        for tid in (TENANT_A, TENANT_B):
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier) "
                    "VALUES (:id, 'T', :email, 'enterprise') ON CONFLICT (id) DO NOTHING"
                ),
                {"id": tid, "email": f"{tid}@example.test"},
            )

    app_url = (
        make_url(postgres_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, pool_size=4, max_overflow=0)
    yield (
        async_sessionmaker(admin_engine, expire_on_commit=False),
        async_sessionmaker(app_engine, expire_on_commit=False),
    )
    await app_engine.dispose()
    await admin_engine.dispose()


async def _count(factory, table: str, tenant_guc: str) -> int:
    async with factory() as s, s.begin(), sqlalchemy_rls_context(s, tenant_guc):
        return int((await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one())


@pytest.mark.asyncio
async def test_every_table_is_forced_with_a_write_check(factories: tuple) -> None:
    admin_factory, _ = factories
    async with admin_factory() as s:
        for table in _TENANT_TABLES:
            enabled, forced = (
                await s.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE relname = :t"
                    ),
                    {"t": table},
                )
            ).one()
            assert enabled and forced, table
            checks = (
                await s.execute(
                    text(
                        "SELECT with_check FROM pg_policies "
                        "WHERE tablename = :t AND cmd = 'ALL'"
                    ),
                    {"t": table},
                )
            ).scalars().all()
            assert checks and all(checks), (table, checks)


@pytest.mark.asyncio
async def test_chat_template_create_returns_and_is_isolated(factories: tuple) -> None:
    """POST /chat/templates' store path: no 500, row visible only to its tenant."""
    from app.api.templates import _TemplateStore
    from app.chat.router import CHAT_PERSONA_DOMAIN

    _, app_factory = factories
    store = _TemplateStore(seed_builtins=False)
    store.set_db(app_factory)

    row = await store.create(
        TENANT_A,
        name="Persona",
        description="d",
        goal_text="You are helpful",
        domain=CHAT_PERSONA_DOMAIN,
        parameters=[],
    )
    assert row["tenant_id"] == TENANT_A and row["name"] == "Persona"

    mine = await store.list(TENANT_A, domain=CHAT_PERSONA_DOMAIN)
    assert [r["id"] for r in mine] == [row["id"]]
    assert await store.list(TENANT_B, domain=CHAT_PERSONA_DOMAIN) == []

    updated = await store.update(
        TENANT_A, row["id"], "Persona 2", "d", "You are terse", CHAT_PERSONA_DOMAIN, []
    )
    assert updated is not None and updated["version"] == 2
    assert await store.delete(TENANT_B, row["id"]) is False
    assert await store.delete(TENANT_A, row["id"]) is True


@pytest.mark.asyncio
async def test_budget_update_persists_under_rls(factories: tuple) -> None:
    from app.api.costs import UpdateBudgetRequest, update_budgets
    from app.intelligence.cost_tracker import CostTracker

    _, app_factory = factories
    tracker = CostTracker(redis=None, db_factory=app_factory)
    request = SimpleNamespace(
        state=SimpleNamespace(tenant=_ctx(TENANT_A)),
        app=SimpleNamespace(state=SimpleNamespace(cost_tracker=tracker)),
    )
    await update_budgets(
        request,  # type: ignore[arg-type]
        UpdateBudgetRequest(per_goal_usd=3.5, per_tenant_daily_usd=42.0),
    )

    mine = await tracker._load_budgets(TENANT_A)
    assert (mine.per_goal_usd, mine.per_tenant_daily_usd) == (3.5, 42.0)
    theirs = await tracker._load_budgets(TENANT_B)
    assert theirs.per_goal_usd != 3.5  # defaults: A's row is invisible to B

    # WITH CHECK: a session scoped to B cannot write A's budget row.
    with pytest.raises(Exception, match="row-level security"):
        async with app_factory() as s, s.begin(), sqlalchemy_rls_context(s, TENANT_B):
            await s.execute(
                text("INSERT INTO budget_configs (tenant_id) VALUES (:t)"), {"t": TENANT_A}
            )


@pytest.mark.asyncio
async def test_benchmark_runs_tenant_rows_plus_global_read(factories: tuple) -> None:
    # The BenchmarkStore that wrote this table was dead code (MEM-33) and was
    # removed; the table's policies are still exercised directly.
    admin_factory, app_factory = factories
    async with admin_factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO benchmark_runs (id, tenant_id, suite_name, score) "
                "VALUES ('global-row', 'global', 'suite-x', 0.5) ON CONFLICT DO NOTHING"
            )
        )
    async with app_factory() as s, s.begin(), sqlalchemy_rls_context(s, TENANT_A):
        await s.execute(
            text(
                "INSERT INTO benchmark_runs (id, tenant_id, suite_name, score) "
                "VALUES ('a-row', :t, 'suite-x', 0.8)"
            ),
            {"t": TENANT_A},
        )

    async def _scores(tenant: str) -> list[float]:
        async with app_factory() as s, s.begin(), sqlalchemy_rls_context(s, tenant):
            rows = (
                await s.execute(
                    text("SELECT score FROM benchmark_runs WHERE suite_name = 'suite-x'")
                )
            ).fetchall()
        return sorted(float(r[0]) for r in rows)

    assert await _scores(TENANT_A) == [0.5, 0.8]
    assert await _scores(TENANT_B) == [0.5]

    # The global-read policy is SELECT-only: tenants cannot forge global rows.
    with pytest.raises(Exception, match="row-level security"):
        async with app_factory() as s, s.begin(), sqlalchemy_rls_context(s, TENANT_A):
            await s.execute(
                text(
                    "INSERT INTO benchmark_runs (id, tenant_id, suite_name, score) "
                    "VALUES ('forged', 'global', 'suite-x', 1.0)"
                )
            )


@pytest.mark.asyncio
async def test_cost_ledger_write_check(factories: tuple) -> None:
    from app.intelligence.cost_tracker import CostTracker

    _, app_factory = factories
    tracker = CostTracker(redis=None, db_factory=app_factory)
    await tracker.record_llm_usage(
        model="gpt-4o",
        prompt_tokens=10,
        completion_tokens=5,
        tenant_ctx=_ctx(TENANT_A),
        goal_id="goal-cb",
        agent_id="agent-cb",
    )
    assert await _count(app_factory, "cost_ledger", TENANT_A) >= 1
    assert await _count(app_factory, "cost_ledger", TENANT_B) == 0
    with pytest.raises(Exception, match="row-level security"):
        async with app_factory() as s, s.begin(), sqlalchemy_rls_context(s, TENANT_B):
            await s.execute(
                text("INSERT INTO cost_ledger (tenant_id, cost_usd) VALUES (:t, 1)"),
                {"t": TENANT_A},
            )


@pytest.mark.asyncio
async def test_chat_side_tables_and_audit_wal_are_isolated(factories: tuple) -> None:
    _, app_factory = factories
    sid = secrets.token_hex(16)
    async with app_factory() as s, s.begin(), sqlalchemy_rls_context(s, TENANT_A):
        await s.execute(
            text("INSERT INTO chat_sessions (id, tenant_id, title) VALUES (:id, :t, 'x')"),
            {"id": sid, "t": TENANT_A},
        )
        await s.execute(
            text("INSERT INTO chat_session_folders (id, tenant_id, name) VALUES (:id, :t, 'f')"),
            {"id": secrets.token_hex(16), "t": TENANT_A},
        )
        await s.execute(
            text(
                "INSERT INTO chat_message_usage (id, message_id, session_id, tenant_id) "
                "VALUES (:id, 'm1', :sid, :t)"
            ),
            {"id": secrets.token_hex(16), "sid": sid, "t": TENANT_A},
        )
        await s.execute(
            text(_CHAT_ARTIFACT_INSERT),
            {"id": secrets.token_hex(16), "sid": sid, "t": TENANT_A},
        )
        await s.execute(
            text("INSERT INTO audit_wal_queue (tenant_id, payload) VALUES (:t, '{}'::jsonb)"),
            {"t": TENANT_A},
        )

    forged = {
        "chat_session_folders": (
            "INSERT INTO chat_session_folders (id, tenant_id, name) VALUES (:id, :t, 'f')"
        ),
        "chat_message_usage": (
            "INSERT INTO chat_message_usage (id, message_id, session_id, tenant_id) "
            "VALUES (:id, 'm2', :sid, :t)"
        ),
        "chat_artifacts": _CHAT_ARTIFACT_INSERT,
        "audit_wal_queue": (
            "INSERT INTO audit_wal_queue (tenant_id, payload) VALUES (:t, '{}'::jsonb)"
        ),
    }
    for table, insert_sql in forged.items():
        assert await _count(app_factory, table, TENANT_A) >= 1, table
        assert await _count(app_factory, table, TENANT_B) == 0, table
        # WITH CHECK: a session scoped to B cannot write a row owned by A.
        with pytest.raises(Exception, match="row-level security"):
            async with app_factory() as s, s.begin(), sqlalchemy_rls_context(s, TENANT_B):
                params = {"id": secrets.token_hex(16), "sid": sid, "t": TENANT_A}
                await s.execute(
                    text(insert_sql),
                    {k: v for k, v in params.items() if f":{k}" in insert_sql},
                )
