"""Integration test: the trigger DLQ write actually persists.

Regression for two bugs that made EVERY dead-letter write fail (so a failed
trigger fire that should be captured for retry/inspection was silently lost):
  1. failed_at was a naive TIMESTAMP column but the code binds tz-aware UTC →
     asyncpg "can't subtract offset-naive and offset-aware datetimes"
     (fixed by migration 0129 → TIMESTAMPTZ).
  2. raw_payload (a dict) was bound directly to a JSON column, but asyncpg needs
     a JSON string → "descriptor 'encode' for 'str' ... 'dict'"
     (fixed by json.dumps + CAST(:raw_payload AS json)).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


async def test_write_to_dlq_persists_row() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from testcontainers.postgres import PostgresContainer

    from app.triggers.dlq import write_to_dlq

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=_BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": url},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"alembic failed:\n{result.stderr}"

        engine = create_async_engine(url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                await write_to_dlq(
                    session,
                    tenant_id="t-dlq",
                    trigger_id="trig-1",
                    failure_type="GOAL_ENQUEUE_FAILED",
                    error_message="Concurrent goal limit reached",
                    raw_payload={"repo": "octo/hello", "n": 42},
                    retry_count=0,
                )
            async with session_factory() as session:
                rows = (
                    await session.execute(
                        text(
                            "SELECT tenant_id, failed_at, failure_type, raw_payload, retry_count "
                            "FROM trigger_dlq WHERE tenant_id = 't-dlq'"
                        )
                    )
                ).mappings().all()
        finally:
            await engine.dispose()

    assert len(rows) == 1, "the dead-letter row must persist"
    row = rows[0]
    assert row["failure_type"] == "GOAL_ENQUEUE_FAILED"
    assert row["raw_payload"] == {"repo": "octo/hello", "n": 42}
    assert row["failed_at"].tzinfo is not None, "failed_at must be timezone-aware"
