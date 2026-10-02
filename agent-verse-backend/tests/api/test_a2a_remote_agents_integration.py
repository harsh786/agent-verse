"""a2a_remote_agents on real Postgres, as a least-privilege (NOBYPASSRLS) role.

The migrated testcontainer (``pg_url``) proves migration ``5ea3dc985432`` applies;
the store functions then run as a NOSUPERUSER NOBYPASSRLS login role — the only
configuration under which a missing ``app.tenant_id`` or a broken policy shows
(a superuser bypasses RLS entirely).

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/api/test_a2a_remote_agents_integration.py -q -m integration --no-cov
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.api.a2a_remote_agents as mod

pytestmark = pytest.mark.integration

_ROLE = "test_app_a2a_remote"
_PASSWORD = "test-a2a-remote-pw"
_CARD = {"name": "Research Bot", "version": "1.0.0"}


@pytest_asyncio.fixture
async def app_db(pg_url: str) -> AsyncIterator[Any]:
    """A session factory connected as a NOBYPASSRLS role with DML on the table."""
    owner = create_async_engine(pg_url)
    async with owner.begin() as conn:
        await conn.execute(
            text(
                "DO $$ BEGIN IF NOT EXISTS "
                f"(SELECT 1 FROM pg_roles WHERE rolname = '{_ROLE}') THEN "
                f"CREATE ROLE {_ROLE} LOGIN PASSWORD '{_PASSWORD}' "
                "NOSUPERUSER NOBYPASSRLS; END IF; END $$"
            )
        )
        await conn.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON a2a_remote_agents TO {_ROLE}")
        )
        await conn.execute(text("DELETE FROM a2a_remote_agents"))
    await owner.dispose()

    url = make_url(pg_url).set(username=_ROLE, password=_PASSWORD)
    engine = create_async_engine(url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_rows_are_isolated_per_tenant_under_rls(app_db: Any) -> None:
    tid_a, tid_b = str(uuid.uuid4()), str(uuid.uuid4())
    url = "https://agents.example.com/.well-known/agent.json"

    row = await mod._insert(app_db, tid_a, "Research Bot", url, _CARD)
    assert row is not None
    assert row["card"] == _CARD

    # Same URL for the same tenant: conflict (None); another tenant may register it.
    assert await mod._insert(app_db, tid_a, "Dup", url, _CARD) is None
    other = await mod._insert(app_db, tid_b, "Theirs", url, _CARD)
    assert other is not None

    a_rows = await mod._list(app_db, tid_a)
    assert [r["id"] for r in a_rows] == [row["id"]]
    assert [r["id"] for r in await mod._list(app_db, tid_b)] == [other["id"]]

    # Tenant B can neither read, update nor delete tenant A's row.
    assert await mod._get(app_db, tid_b, row["id"]) is None
    assert await mod._record_check(app_db, tid_b, row["id"], None, "x") is None
    assert await mod._delete(app_db, tid_b, row["id"]) is False

    # A failed ping keeps the last good card and records the error.
    checked = await mod._record_check(app_db, tid_a, row["id"], None, "unreachable")
    assert checked is not None
    assert checked["card"] == _CARD
    assert checked["last_error"] == "unreachable"
    refreshed = await mod._record_check(
        app_db, tid_a, row["id"], {**_CARD, "version": "2"}, None
    )
    assert refreshed is not None
    assert refreshed["card"]["version"] == "2"
    assert refreshed["last_error"] is None

    assert await mod._delete(app_db, tid_a, row["id"]) is True
    assert await mod._list(app_db, tid_a) == []


async def test_a_row_cannot_be_written_for_another_tenant(app_db: Any) -> None:
    """WITH CHECK: the GUC tenant must match the row's tenant_id."""
    tid_a, tid_b = str(uuid.uuid4()), str(uuid.uuid4())
    from app.db.rls import sqlalchemy_rls_context

    with pytest.raises(Exception, match="row-level security"):
        async with app_db() as s, s.begin(), sqlalchemy_rls_context(s, tid_a):
            await s.execute(
                text(
                    "INSERT INTO a2a_remote_agents (id, tenant_id, name, url, card) "
                    "VALUES ('x', CAST(:tid AS uuid), 'n', 'https://e.example', '{}'::jsonb)"
                ),
                {"tid": tid_b},
            )
