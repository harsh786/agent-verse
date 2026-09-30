"""DepartmentMemory.retrieve ranks in Postgres, over every entry (integration, MEM-15).

It loaded the newest 1000 entries and keyword-scored them in Python, so an SOP
older than the 1000 newest entries of a busy department was never found, and
every call pulled up to 1000 rows.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


async def test_old_matching_entry_is_found_past_the_newest_thousand() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from testcontainers.postgres import PostgresContainer

    from app.memory.dept_memory import DepartmentMemory

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        admin_url = pg.get_connection_url()
        env = {**os.environ, "DATABASE_URL": admin_url, "ENVIRONMENT": "development"}
        r = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=_BACKEND_ROOT, env=env, capture_output=True, text=True,
        )
        assert r.returncode == 0, f"alembic failed:\n{r.stderr[-1500:]}"

        engine = create_async_engine(admin_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO department_memory_entries "
                    "(entry_id, dept_id, org_id, tenant_id, content, source, confidence, "
                    " created_at) VALUES ('sop-old', 'eng', 'o1', 't1', "
                    "'Rotate the signing keys every quarter', 'ops', 0.9, "
                    "now() - interval '400 days')"
                )
            )
            await s.execute(
                text(
                    "INSERT INTO department_memory_entries "
                    "(entry_id, dept_id, org_id, tenant_id, content, source, confidence) "
                    "SELECT 'n-' || g, 'eng', 'o1', 't1', 'standup notes ' || g, 'bot', 0.9 "
                    "FROM generate_series(1, 1100) AS g"
                )
            )
            # Another tenant's matching entry must never leak in.
            await s.execute(
                text(
                    "INSERT INTO department_memory_entries "
                    "(entry_id, dept_id, org_id, tenant_id, content, source, confidence) "
                    "VALUES ('other', 'eng', 'o2', 't2', 'rotate signing keys', 'x', 1.0)"
                )
            )

        mem = DepartmentMemory()
        mem.set_db(factory)
        hits = await mem.retrieve("eng", "rotate signing keys", top_k=3, tenant_id="t1")
        assert hits and hits[0].entry_id == "sop-old"
        assert len(hits) <= 3
        assert all(h.tenant_id == "t1" for h in hits)

        # A query that matches nothing still answers (newest first), bounded by top_k.
        none = await mem.retrieve("eng", "zzz-no-such-term", top_k=2, tenant_id="t1")
        assert len(none) == 2

        # MEM-14: org-wide search (the MCP gateway fallback) spans the org's
        # departments, ranked in SQL, tenant-scoped.
        org_hits = await mem.retrieve_for_org("o1", "rotate signing keys", top_k=2, tenant_id="t1")
        assert org_hits and org_hits[0].entry_id == "sop-old"
        assert await mem.retrieve_for_org("o2", "rotate", tenant_id="t1") == []
        await engine.dispose()
