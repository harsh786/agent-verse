"""Persistent tamper-evident audit chain (integration)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


async def test_persistent_chain_appends_verifies_and_detects_tamper() -> None:
    import asyncpg
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from testcontainers.postgres import PostgresContainer

    from app.governance.audit_chain_store import PersistentAuditChain

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        admin_url = pg.get_connection_url()
        env = {**os.environ, "DATABASE_URL": admin_url, "ENVIRONMENT": "development"}
        r = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=_BACKEND_ROOT, env=env, capture_output=True, text=True,
        )
        assert r.returncode == 0, f"alembic failed:\n{r.stderr[-1500:]}"

        engine = create_async_engine(admin_url)
        sf = async_sessionmaker(engine, expire_on_commit=False)
        chain = PersistentAuditChain(sf)

        for i in range(4):
            await chain.append("t1", {"action": "tool_call", "tool": f"jira.op{i}"})

        ok, broken = await chain.verify("t1")
        assert ok is True and broken is None

        # persists across a fresh instance ("restart")
        chain2 = PersistentAuditChain(sf)
        ok2, _ = await chain2.verify("t1")
        assert ok2 is True

        # tamper with a middle record's payload directly in the DB → verify fails
        raw = admin_url.replace("postgresql+asyncpg://", "")
        conn = await asyncpg.connect(f"postgresql://{raw}")
        await conn.execute(
            "UPDATE audit_chain SET payload = '{\"action\":\"hacked\"}'::jsonb "
            "WHERE tenant_id='t1' AND seq=1"
        )
        await conn.close()

        ok3, broken3 = await chain.verify("t1")
        assert ok3 is False
        assert broken3 == 1

        # tenant isolation: other tenant has an empty (valid) chain
        assert await chain.verify("t2") == (True, None)
        await engine.dispose()
