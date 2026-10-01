"""DROP-GATEWAY-CONVERSATIONS: the unused table is dropped; downgrade restores it intact.

``gateway_conversations`` (migration 0112) lost its only reader/writer when the
unused ``ConversationManager`` was deleted; channel conversations live in the
durable chat channel sessions. Migration ``d7e3a1f9b2c4`` drops the table; its
downgrade recreates it with its indexes, FORCE'd RLS and the
``app_current_tenant_uuid()`` tenant policy it had at head.

Uses its own container: it downgrades the schema, which must not disturb the
shared ``pg_url`` database other tests expect at head.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REVISION = "d7e3a1f9b2c4"


@pytest.fixture(scope="module")
def db_url() -> Iterator[str]:
    try:
        from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

        container = PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg")
        container.start()
    except Exception as exc:  # pragma: no cover - Docker down
        pytest.skip(f"could not start a Postgres testcontainer: {exc}")
    try:
        yield container.get_connection_url()
    finally:
        container.stop()


def _alembic(url: str, *args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": url, "ENVIRONMENT": "development"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-3000:]


def _state(url: str) -> dict[str, Any]:
    async def _read() -> dict[str, Any]:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                rel = (
                    await conn.execute(
                        text(
                            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                            "WHERE relname = 'gateway_conversations' AND relkind = 'r'"
                        )
                    )
                ).first()
                policies = (
                    await conn.execute(
                        text(
                            "SELECT polname, pg_get_expr(polqual, polrelid), "
                            "pg_get_expr(polwithcheck, polrelid) FROM pg_policy "
                            "WHERE polrelid = 'gateway_conversations'::regclass"
                        )
                    )
                ).all() if rel else []
                indexes = {
                    r[0]
                    for r in (
                        await conn.execute(
                            text(
                                "SELECT indexname FROM pg_indexes "
                                "WHERE tablename = 'gateway_conversations'"
                            )
                        )
                    ).all()
                }
                version = (
                    await conn.execute(text("SELECT version_num FROM alembic_version"))
                ).scalar_one()
            return {"rel": rel, "policies": policies, "indexes": indexes, "version": version}
        finally:
            await engine.dispose()

    return asyncio.run(_read())


def test_upgrade_drops_and_downgrade_restores_gateway_conversations(db_url: str) -> None:
    _alembic(db_url, "upgrade", "head")
    head = _state(db_url)
    assert head["rel"] is None, "gateway_conversations must not exist at head"

    _alembic(db_url, "downgrade", f"{REVISION}-1")
    restored = _state(db_url)
    assert restored["rel"] is not None
    assert tuple(restored["rel"]) == (True, True)  # RLS enabled AND forced
    assert len(restored["policies"]) == 1
    name, qual, check = restored["policies"][0]
    assert name == "tenant_isolation"
    assert "app_current_tenant_uuid()" in qual and "app_current_tenant_uuid()" in check
    assert {
        "gateway_conversations_pkey",
        "idx_gw_conv_tenant",
        "idx_gw_conv_org",
        "idx_gw_conv_channel",
        "idx_gw_conv_key",
        "idx_gw_conv_last_cmd",
    } <= restored["indexes"]

    _alembic(db_url, "upgrade", "head")
    again = _state(db_url)
    assert again["rel"] is None and again["version"] == head["version"]
