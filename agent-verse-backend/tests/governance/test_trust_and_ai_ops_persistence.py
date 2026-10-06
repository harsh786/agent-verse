"""Integration tests: AI-Ops state is DURABLE (and the retired trust tables stay).

`app/api/ai_ops.py` is a mounted, live router that kept its entire state in
module-level dicts: an AI-Ops baseline set on one pod meant drift was computed
against "no baseline" everywhere else, and everything was lost on redeploy.

These tests exercise the real store against a migrated Postgres as a
NOSUPERUSER/NOBYPASSRLS role, and assert durability by reading back through a
*second, independent* store instance that shares no in-process state with the
one that wrote (the stand-in for another replica / a restart).

`/trust/approvals` was retired (a03-F057-01: its approvals never gated
execution; /governance/approvals does). Its store is gone, but its tables are
deliberately kept (no destructive migration); the last test pins that.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/governance/test_trust_and_ai_ops_persistence.py -q -m integration
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

from app.evals.ai_ops_store import AIOpsStore

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TABLES = (
    "ai_ops_datasets",
    "ai_ops_dataset_versions",
    "ai_ops_eval_results",
    "ai_ops_judges",
    "ai_ops_baselines",
    "ai_ops_drift_alerts",
)
TENANT_A = "tenant-trust-a"
TENANT_B = "tenant-trust-b"


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
async def app_factory(postgres_url: str) -> AsyncIterator[async_sessionmaker]:
    """A NOSUPERUSER/NOBYPASSRLS role — RLS genuinely applies."""
    password = secrets.token_urlsafe(24)
    role = f"test_app_trust_{secrets.token_hex(4)}"
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
        for tbl in TABLES:
            await conn.execute(
                text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO {role}")
            )
        for tbl in TABLES:
            await conn.execute(text(f"DELETE FROM {tbl}"))
    await admin_engine.dispose()

    url = (
        make_url(postgres_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    engine = create_async_engine(url, pool_size=6, max_overflow=0)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


# ── ai ops ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ai_ops_state_survives_a_restart(app_factory: async_sessionmaker) -> None:
    writer = AIOpsStore(app_factory)
    dataset_id = secrets.token_hex(8)
    await writer.create_dataset(
        tenant_id=TENANT_A, dataset_id=dataset_id, name="golden",
        description="d", golden_tasks=[{"expected_output": "x"}],
    )
    await writer.set_baseline(tenant_id=TENANT_A, metric_name="latency_ms", value=250.0)
    await writer.add_alert(
        tenant_id=TENANT_A,
        alert={"alert_id": secrets.token_hex(8), "severity": "critical",
               "metric_name": "latency_ms", "message": "spike"},
    )

    reader = AIOpsStore(app_factory)
    ds = await reader.get_dataset(TENANT_A, dataset_id)
    assert ds is not None and ds["task_count"] == 1, ds
    assert await reader.get_baseline(TENANT_A, "latency_ms") == 250.0
    assert len(await reader.list_alerts(TENANT_A)) == 1
    assert (await reader.alert_severity_counts(TENANT_A)).get("critical") == 1


@pytest.mark.asyncio
async def test_a_zero_baseline_is_a_real_baseline(
    app_factory: async_sessionmaker,
) -> None:
    """0.0 is falsy; the old code treated it as "no baseline set".

    That made every eval run look like a first run — the regression check was
    skipped and the baseline silently re-set each time.
    """
    store = AIOpsStore(app_factory)
    await store.set_baseline(tenant_id=TENANT_A, metric_name="error_rate", value=0.0)
    assert await store.get_baseline(TENANT_A, "error_rate") == 0.0

    # An auto-baseline must not clobber the operator's value.
    await store.set_baseline_if_absent(
        tenant_id=TENANT_A, metric_name="error_rate", value=0.9
    )
    assert await store.get_baseline(TENANT_A, "error_rate") == 0.0


@pytest.mark.asyncio
async def test_ai_ops_state_is_tenant_isolated(app_factory: async_sessionmaker) -> None:
    store = AIOpsStore(app_factory)
    dataset_id = secrets.token_hex(8)
    await store.create_dataset(
        tenant_id=TENANT_A, dataset_id=dataset_id, name="private",
        description="", golden_tasks=[],
    )
    await store.set_baseline(tenant_id=TENANT_A, metric_name="m", value=1.0)
    await store.add_alert(
        tenant_id=TENANT_A,
        alert={"alert_id": secrets.token_hex(8), "severity": "critical",
               "metric_name": "m", "message": "x"},
    )

    assert await store.get_dataset(TENANT_B, dataset_id) is None
    assert await store.list_datasets(TENANT_B) == []
    assert await store.get_baseline(TENANT_B, "m") is None
    assert await store.list_alerts(TENANT_B) == []
    assert await store.count_baselines(TENANT_B) == 0


@pytest.mark.asyncio
async def test_the_retired_trust_approval_tables_are_kept(postgres_url: str) -> None:
    """Retiring /trust/approvals dropped no data: both tables still exist."""
    engine = create_async_engine(postgres_url)
    try:
        async with engine.connect() as conn:
            found = {
                row[0]
                for row in await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name LIKE 'trust_approval_%'"
                    )
                )
            }
    finally:
        await engine.dispose()
    assert found == {"trust_approval_requests", "trust_approval_votes"}
