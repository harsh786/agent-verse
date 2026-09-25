"""Integration test: re-installing a marketplace template must not orphan agents.

``marketplace_installs`` carries ``UNIQUE (template_id, installer_tenant_id)``
(migration 0059), and ``MarketplaceV2.install`` upserts it with
``ON CONFLICT ... DO UPDATE SET agent_id = EXCLUDED.agent_id``. But the agent
row itself is created with a *fresh* ``uuid4`` on every call and nothing ever
deletes the one the install record previously pointed at — and there is no
``uninstall`` method anywhere in the class.

So a tenant re-installing the same template (a double-clicked "Install", a
retried request, or simply re-deploying with new parameters) silently leaks a
dead ``agents`` row per attempt: still listed by ``GET /agents``, still
counting against the tenant's agent quota, referenced by nothing.

Migration 0059's own docstring claims this table "fixes ghost-agent bug" — it
fixed the duplicate *install record*, not the orphaned *agent row*.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/enterprise/test_marketplace_reinstall_orphans.py -q -m integration
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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.enterprise.marketplace_v2 import MarketplaceV2
from app.tenancy.context import PlanTier, TenantContext

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
async def factory(postgres_url: str) -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(postgres_url, echo=False)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _publish(svc: MarketplaceV2, ctx: TenantContext) -> str:
    record = await svc.publish_template(
        data={
            "name": "Incident Triage",
            "slug": f"tpl-{secrets.token_hex(4)}",
            "description": "triage",
            "template_config": {"goal_template": "triage {{incident}}"},
        },
        tenant_ctx=ctx,
        run_security_review=False,
    )
    return str(record["id"])


@pytest.mark.asyncio
async def test_reinstalling_a_template_does_not_orphan_the_previous_agent(
    factory: async_sessionmaker,
) -> None:
    svc = MarketplaceV2(db_factory=factory)
    tenant_id = f"tenant-{secrets.token_hex(6)}"
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k1")

    # agents.tenant_id is a FK to tenants.id — seed the tenant row.
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, email) "
                "VALUES (:id, :name, :email)"
            ),
            {"id": tenant_id, "name": "T", "email": f"{tenant_id}@example.test"},
        )
        await s.commit()

    template_id = await _publish(svc, ctx)

    first = await svc.install(template_id=template_id, params={}, tenant_ctx=ctx)
    assert first["success"] is True, first
    second = await svc.install(template_id=template_id, params={}, tenant_ctx=ctx)
    assert second["success"] is True, second

    async with factory() as s:
        agent_rows = (
            await s.execute(
                text("SELECT id FROM agents WHERE tenant_id = :t"), {"t": tenant_id}
            )
        ).fetchall()
        referenced = (
            await s.execute(
                text(
                    "SELECT agent_id FROM marketplace_installs "
                    "WHERE template_id = :tpl AND installer_tenant_id = :t"
                ),
                {"tpl": template_id, "t": tenant_id},
            )
        ).scalar_one()

    agent_ids = {r[0] for r in agent_rows}
    orphans = agent_ids - {referenced}
    assert not orphans, (
        f"re-install left {len(orphans)} orphaned agent row(s) {orphans} — the install "
        f"record now points only at {referenced!r}, and nothing ever deletes the rest"
    )


@pytest.mark.asyncio
async def test_concurrent_installs_converge_on_one_agent(
    factory: async_sessionmaker,
) -> None:
    """Two installs racing (double-clicked "Install", a retried request) must not
    each leak an agent. ``UNIQUE (template_id, installer_tenant_id)`` serialises
    them; the loser's ``ON CONFLICT DO UPDATE`` now returns the winner's
    ``agent_id`` instead of overwriting it with its own freshly-minted one.
    """
    import asyncio

    svc = MarketplaceV2(db_factory=factory)
    tenant_id = f"tenant-{secrets.token_hex(6)}"
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k1")

    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:id, :name, :email)"),
            {"id": tenant_id, "name": "T", "email": f"{tenant_id}@example.test"},
        )
        await s.commit()

    template_id = await _publish(svc, ctx)

    results = await asyncio.gather(
        svc.install(template_id=template_id, params={}, tenant_ctx=ctx),
        svc.install(template_id=template_id, params={}, tenant_ctx=ctx),
    )
    assert all(r["success"] is True for r in results), results
    assert results[0]["agent_id"] == results[1]["agent_id"], (
        "racing installs returned two different agent ids — one of them is orphaned"
    )

    async with factory() as s:
        count = (
            await s.execute(
                text("SELECT count(*) FROM agents WHERE tenant_id = :t"), {"t": tenant_id}
            )
        ).scalar_one()
    assert count == 1, f"racing installs created {count} agent rows, expected 1"
