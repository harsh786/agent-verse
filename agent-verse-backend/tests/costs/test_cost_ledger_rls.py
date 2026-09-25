"""Integration test: LLM cost records must actually reach the ledger.

`cost_ledger` is FORCE ROW LEVEL SECURITY, and `CostTracker` opened plain
sessions with no RLS context on all seven of its DB paths. Under a real
least-privilege role every `record_llm_usage` INSERT was rejected and swallowed,
so the cost ledger stayed empty — and every read built on it
(`get_cost_by_model`, per-agent summaries, trends, projected monthly cost, and
the budget loader) returned nothing.

That matters beyond reporting: an earlier session fixed double-billing and the
budget auto-downgrade tier logic, but those corrections operate on a ledger that
was never being written.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/costs/test_cost_ledger_rls.py -q -m integration
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

from app.intelligence.cost_tracker import CostTracker
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TENANT_A = "tenant-cost-a"
TENANT_B = "tenant-cost-b"


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
    role = f"test_app_cost_{secrets.token_hex(4)}"
    admin_engine = create_async_engine(postgres_url)
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
        await conn.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON cost_ledger TO {role}")
        )
        await conn.execute(
            text("DELETE FROM cost_ledger WHERE tenant_id = ANY(:t)"),
            {"t": [TENANT_A, TENANT_B]},
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


def _ctx(tenant_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k1")


@pytest.mark.asyncio
async def test_llm_usage_reaches_the_cost_ledger(factories: tuple) -> None:
    admin_factory, app_factory = factories
    tracker = CostTracker(redis=None, db_factory=app_factory)

    cost = await tracker.record_llm_usage(
        model="gpt-4o",
        prompt_tokens=1000,
        completion_tokens=500,
        tenant_ctx=_ctx(TENANT_A),
        goal_id="goal-1",
        agent_id="agent-1",
    )
    assert cost > 0, cost

    async with admin_factory() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT tenant_id, model, prompt_tokens, completion_tokens "
                    "FROM cost_ledger WHERE tenant_id = :t"
                ),
                {"t": TENANT_A},
            )
        ).fetchall()
    assert len(rows) == 1, f"cost record never reached the ledger: {rows}"
    assert rows[0][1] == "gpt-4o"
    assert rows[0][2] == 1000


@pytest.mark.asyncio
async def test_cost_reads_see_the_ledger_and_are_tenant_scoped(
    factories: tuple,
) -> None:
    _admin, app_factory = factories
    tracker = CostTracker(redis=None, db_factory=app_factory)

    await tracker.record_llm_usage(
        model="gpt-4o",
        prompt_tokens=100,
        completion_tokens=50,
        tenant_ctx=_ctx(TENANT_A),
        goal_id="goal-a",
    )

    by_model = await tracker.get_cost_by_model(TENANT_A, days=30)
    assert by_model, "get_cost_by_model saw an empty ledger under RLS"
    assert any(r.get("model") == "gpt-4o" for r in by_model), by_model

    # Tenant B must see none of tenant A's spend.
    assert await tracker.get_cost_by_model(TENANT_B, days=30) == []
