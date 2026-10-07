"""conclude_stale_experiments against a real, migrated Postgres.

a10-F246-04: the sweep only bumped ``updated_at``; it now runs the promotion
decision one last time and retires the stale challengers that did not win.

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
            # id, tenant, key, control, wins, losses, age_days, runs, score_sum, score_sq
            ("pv-a-ctrl", "tenant-a", "planner", True, 0, 0, 1, 0, 0.0, 0.0),
            ("pv-b-ctrl", "tenant-b", "planner", True, 0, 0, 1, 0, 0.0, 0.0),
            ("pv-a-old-big", "tenant-a", "planner", False, 15, 10, 45, 25, 15.0, 10.0),
            ("pv-b-old-big", "tenant-b", "planner", False, 12, 8, 40, 20, 12.0, 8.0),
            ("pv-a-old-small", "tenant-a", "planner", False, 3, 2, 45, 5, 3.0, 2.0),
            ("pv-a-new-big", "tenant-a", "planner", False, 30, 30, 1, 60, 30.0, 20.0),
            # no control for this key: nothing to conclude against -> left alone
            ("pv-c-orphan", "tenant-c", "planner", False, 15, 10, 45, 25, 15.0, 10.0),
            # a stale challenger that clearly beats its control -> promoted
            ("pv-d-ctrl", "tenant-d", "executor", True, 40, 80, 1, 120, 60.0, 31.0),
            ("pv-d-win", "tenant-d", "executor", False, 110, 10, 45, 120, 108.0, 97.5),
        ]
        for pid, tenant, key, ctrl, wins, losses, age, runs, ssum, ssq in rows:
            await conn.execute(
                "INSERT INTO prompt_variants (id, tenant_id, prompt_key, variant_name, "
                "is_control, win_count, loss_count, run_count, score_sum, score_sq_sum, "
                "updated_at) VALUES ($1, $2, $3, $1, $4, $5, $6, $7, $8, $9, "
                "NOW() - make_interval(days => $10))",
                pid,
                tenant,
                key,
                ctrl,
                wins,
                losses,
                runs,
                ssum,
                ssq,
                age,
            )
    finally:
        await conn.close()


async def _states(url: str) -> dict[str, tuple[bool, bool]]:
    conn = await asyncpg.connect(_plain(url))
    try:
        rows = await conn.fetch("SELECT id, is_active, is_control FROM prompt_variants")
    finally:
        await conn.close()
    return {r["id"]: (r["is_active"], r["is_control"]) for r in rows}


_EXPECTED = {"status": "ok", "concluded": 3, "promoted": 1, "retired": 2, "skipped_no_control": 1}


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
    assert conclude_stale_experiments.run() == _EXPECTED
    st = asyncio.run(_states(pg_url))
    # Stale challengers that did not win are retired; the controls stay.
    assert st["pv-a-old-big"] == (False, False)
    assert st["pv-b-old-big"] == (False, False)
    assert st["pv-a-ctrl"] == (True, True)
    # Too few trials / still getting evidence / no control: untouched.
    assert st["pv-a-old-small"] == (True, False)
    assert st["pv-a-new-big"] == (True, False)
    assert st["pv-c-orphan"] == (True, False)
    # The clear winner became the control; the old control is archived.
    assert st["pv-d-win"] == (True, True)
    assert st["pv-d-ctrl"] == (False, False)
    # A second run has nothing left to conclude.
    assert conclude_stale_experiments.run()["concluded"] == 0


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
        assert conclude_stale_experiments.run() == _EXPECTED
    finally:
        reset_db_singletons()
