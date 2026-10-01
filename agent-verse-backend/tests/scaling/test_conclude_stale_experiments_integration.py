"""conclude_stale_experiments against a real, migrated Postgres.

The Postgres log showed it failing every run: ``column "wins" does not exist``
— the table (migration 0029) has ``win_count`` / ``loss_count``. And
``prompt_variants`` is ENABLE + FORCE ROW LEVEL SECURITY (f2a3b4c5d6e7), so a
cross-tenant maintenance UPDATE on the plain application session matches no
rows under a NOBYPASSRLS role; it must run on the maintenance (system) factory.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Iterator

import asyncpg
import pytest

pytestmark = pytest.mark.integration


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _seed(url: str) -> None:
    conn = await asyncpg.connect(_plain(url))
    try:
        await conn.execute("DELETE FROM prompt_variants")
        rows = [
            # id, tenant, wins, losses, age_days
            ("pv-a-old-big", "tenant-a", 15, 10, 45),  # eligible
            ("pv-b-old-big", "tenant-b", 12, 8, 40),  # eligible (other tenant)
            ("pv-a-old-small", "tenant-a", 3, 2, 45),  # too few trials
            ("pv-a-new-big", "tenant-a", 30, 30, 1),  # too recent
        ]
        for pid, tenant, wins, losses, age in rows:
            await conn.execute(
                "INSERT INTO prompt_variants (id, tenant_id, prompt_key, variant_name, "
                "win_count, loss_count, updated_at) "
                "VALUES ($1, $2, 'planner', $1, $3, $4, NOW() - make_interval(days => $5))",
                pid,
                tenant,
                wins,
                losses,
                age,
            )
    finally:
        await conn.close()


@pytest.fixture
def app_role_url(pg_url: str) -> Iterator[str]:
    """A least-privilege (NOBYPASSRLS) application role on the test database."""
    role = f"app_ro_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(16)

    async def _create() -> None:
        conn = await asyncpg.connect(_plain(pg_url))
        try:
            await conn.execute(
                f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS"
            )
            await conn.execute(f"GRANT SELECT, UPDATE ON prompt_variants TO {role}")
        finally:
            await conn.close()

    async def _drop() -> None:
        conn = await asyncpg.connect(_plain(pg_url))
        try:
            await conn.execute(f"REVOKE ALL ON prompt_variants FROM {role}")
            await conn.execute(f"DROP ROLE IF EXISTS {role}")
        finally:
            await conn.close()

    asyncio.run(_create())
    head, tail = pg_url.split("://", 1)
    yield f"{head}://{role}:{password}@{tail.split('@', 1)[1]}"
    asyncio.run(_drop())


def test_concludes_eligible_variants_across_tenants(test_backends: tuple[str, str]) -> None:
    from app.scaling.tasks import conclude_stale_experiments

    pg_url, _ = test_backends
    asyncio.run(_seed(pg_url))
    assert conclude_stale_experiments.run() == {"status": "ok", "concluded": 2}


def test_runs_on_the_maintenance_role_under_a_nobypassrls_app_role(
    test_backends: tuple[str, str], app_role_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.scaling.tasks import conclude_stale_experiments
    from tests._test_backends import reset_db_singletons

    pg_url, _ = test_backends
    asyncio.run(_seed(pg_url))
    monkeypatch.setenv("DATABASE_URL", app_role_url)  # request role: RLS applies
    monkeypatch.setenv("MAINTENANCE_DATABASE_URL", pg_url)  # BYPASSRLS maintenance
    reset_db_singletons()
    try:
        assert conclude_stale_experiments.run() == {"status": "ok", "concluded": 2}
    finally:
        reset_db_singletons()
