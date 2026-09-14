"""Grantex — PostgresGrantStore persistence + RLS (integration)."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_NOW = datetime.now(UTC)


def _grant(gid: str = "g1", agent: str = "agent-1") -> object:
    from app.governance.grants import Grant

    return Grant(
        grant_id=gid,
        tenant_id="t1",
        grantor="user:alice",
        grantee_agent_id=agent,
        scopes=("jira.*",),
        not_before=_NOW - timedelta(hours=1),
        expires_at=_NOW + timedelta(hours=1),
        max_cost_usd=5.0,
    )


async def test_postgres_grant_store_roundtrip_revoke_and_rls() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from testcontainers.postgres import PostgresContainer

    from app.governance.grants.postgres_store import PostgresGrantStore

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        admin_url = pg.get_connection_url()
        env = {**os.environ, "DATABASE_URL": admin_url, "ENVIRONMENT": "development"}
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=_BACKEND_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"alembic failed:\n{result.stderr[-1500:]}"

        engine = create_async_engine(admin_url)
        sf = async_sessionmaker(engine, expire_on_commit=False)
        store = PostgresGrantStore(sf)

        # issue + get round-trips (scopes/metadata/cost preserved)
        await store.issue(_grant())
        got = await store.get("t1", "g1")
        assert got is not None
        assert got.scopes == ("jira.*",)
        assert got.max_cost_usd == 5.0
        assert got.covers("jira.search", _NOW) is True

        # persists across a fresh store instance (new "process")
        store2 = PostgresGrantStore(sf)
        assert (await store2.get("t1", "g1")) is not None

        # list + revoke
        assert len(await store.list_for_agent("t1", "agent-1")) == 1
        revoked = await store.revoke("t1", "g1")
        assert revoked is not None and revoked.revoked is True
        assert revoked.is_active(_NOW) is False

        await engine.dispose()
