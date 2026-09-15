"""Phase 0.3d — PostgresChatRepository against real Postgres (testcontainers).

Proves chat sessions + messages persist, list in order, update, and delete, and
that a second tenant's rows are not returned (explicit tenant filter + RLS).
"""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.chat.repository import PostgresChatRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def repo() -> AsyncIterator[PostgresChatRepository]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        admin_url = pg.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=_BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": admin_url},
            check=True,
            capture_output=True,
            text=True,
        )
        engine = create_async_engine(admin_url)
        yield PostgresChatRepository(async_sessionmaker(engine, expire_on_commit=False))
        await engine.dispose()


async def test_session_and_message_round_trip(repo: PostgresChatRepository) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    sid = uuid.uuid4().hex

    await repo.create_session(session_id=sid, tenant_id=tenant, title="Planning", agent_id="a1")
    got = await repo.get_session(sid, tenant)
    assert got is not None and got["title"] == "Planning" and got["agent_id"] == "a1"

    await repo.save_message(
        message_id=uuid.uuid4().hex, session_id=sid, tenant_id=tenant, role="user", content="hi"
    )
    await repo.save_message(
        message_id=uuid.uuid4().hex,
        session_id=sid,
        tenant_id=tenant,
        role="assistant",
        content="hello there",
        intent="QA",
    )
    msgs = await repo.list_messages(sid, tenant)
    assert [m["role"] for m in msgs] == ["user", "assistant"]  # created_at order
    assert msgs[1]["content"] == "hello there" and msgs[1]["intent"] == "QA"

    assert await repo.update_session(sid, tenant, title="Renamed", pinned=True) is True
    assert (await repo.get_session(sid, tenant))["title"] == "Renamed"

    assert await repo.delete_session(sid, tenant) is True
    assert await repo.get_session(sid, tenant) is None
    assert await repo.list_messages(sid, tenant) == []


async def test_edit_and_branch_prune_round_trip(repo: PostgresChatRepository) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    sid = uuid.uuid4().hex
    await repo.create_session(session_id=sid, tenant_id=tenant, title="Editing")

    u1 = uuid.uuid4().hex
    await repo.save_message(
        message_id=u1, session_id=sid, tenant_id=tenant, role="user", content="q1"
    )
    await repo.save_message(
        message_id=uuid.uuid4().hex, session_id=sid, tenant_id=tenant,
        role="assistant", content="a1",
    )
    await repo.save_message(
        message_id=uuid.uuid4().hex, session_id=sid, tenant_id=tenant, role="user", content="q2"
    )

    row = await repo.get_message(u1, tenant)
    assert row is not None and row["role"] == "user"

    assert await repo.update_message_content(u1, tenant, "q1-edited") is True
    pruned = await repo.delete_messages_after(sid, tenant, row["created_at"])
    assert len(pruned) == 2

    msgs = await repo.list_messages(sid, tenant)
    assert [m["content"] for m in msgs] == ["q1-edited"]

    # Cross-tenant: another tenant cannot read or mutate this message.
    other = f"t-{uuid.uuid4().hex[:8]}"
    assert await repo.get_message(u1, other) is None
    assert await repo.update_message_content(u1, other, "hacked") is False

    await repo.delete_session(sid, tenant)


async def test_sessions_are_tenant_scoped(repo: PostgresChatRepository) -> None:
    t1, t2 = f"t-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    s1 = uuid.uuid4().hex
    await repo.create_session(session_id=s1, tenant_id=t1, title="tenant-1 only")

    # Same session id must be invisible to another tenant.
    assert await repo.get_session(s1, t2) is None
    assert all(s["id"] != s1 for s in await repo.list_sessions(t2))
    assert any(s["id"] == s1 for s in await repo.list_sessions(t1))
