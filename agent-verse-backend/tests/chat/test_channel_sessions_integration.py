"""Durable gateway conversation mappings against real Postgres (testcontainers).

Runs as a least-privilege NOBYPASSRLS role (a superuser bypasses RLS, hiding a
missing tenant GUC) and proves: two independent "replicas" (separate engines /
connection pools / ChatService instances) racing on a channel user's first
message converge on ONE session with no orphan; a restart resolves the same
session; tenants are isolated by RLS; deleting a session cascades its mappings;
and the migration downgrades cleanly.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/chat/test_channel_sessions_integration.py -q -m integration --no-cov
"""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.chat.repository import PostgresChatRepository
from app.chat.service import ChatService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_APP_ROLE = "test_app_chat_channel_rls"
_TABLES = ("chat_sessions", "chat_channel_sessions", "chat_principal_sessions")


def _alembic(url: str, *args: str) -> None:
    subprocess.run(
        ["alembic", *args],
        cwd=_BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        check=True,
        capture_output=True,
        text=True,
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def urls() -> AsyncIterator[dict[str, str]]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        admin_url = pg.get_connection_url()
        _alembic(admin_url, "upgrade", "head")
        pw = secrets.token_hex(12)
        admin = create_async_engine(admin_url)
        async with admin.begin() as conn:
            await conn.exec_driver_sql(
                f"CREATE ROLE {_APP_ROLE} LOGIN PASSWORD '{pw}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
            await conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}")
            for tbl in _TABLES:
                await conn.exec_driver_sql(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO {_APP_ROLE}"
                )
            await conn.exec_driver_sql(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON chat_messages TO {_APP_ROLE}"
            )
        await admin.dispose()
        app_url = make_url(admin_url).set(username=_APP_ROLE, password=pw).render_as_string(
            hide_password=False
        )
        yield {"admin": admin_url, "app": app_url}


def _repo(engine: AsyncEngine) -> PostgresChatRepository:
    return PostgresChatRepository(async_sessionmaker(engine, expire_on_commit=False))


async def _count(admin_url: str, sql: str, **params: str) -> int:
    engine = create_async_engine(admin_url)
    try:
        async with engine.connect() as conn:
            return int((await conn.execute(text(sql), params)).scalar_one())
    finally:
        await engine.dispose()


async def test_replicas_racing_converge_and_restart_resolves_same(urls: dict[str, str]) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    engines = [create_async_engine(urls["app"]) for _ in range(3)]
    try:
        replicas = [ChatService(repository=_repo(e)) for e in engines]
        results = await asyncio.gather(
            *[
                r.aget_or_create_channel_session(
                    tenant_id=tenant, channel="telegram", channel_user_id="chat-42"
                )
                for r in replicas
                for _ in range(3)
            ]
        )
        assert len({s.id for s in results}) == 1
        # Losers deleted their own session: exactly one row, no orphan.
        assert await _count(
            urls["admin"], "SELECT count(*) FROM chat_sessions WHERE tenant_id = :t", t=tenant
        ) == 1

        restarted = ChatService(repository=_repo(engines[0]))
        again = await restarted.aget_or_create_channel_session(
            tenant_id=tenant, channel="telegram", channel_user_id="chat-42"
        )
        assert again.id == results[0].id
    finally:
        for e in engines:
            await e.dispose()


async def test_tenant_isolation_and_cascade(urls: dict[str, str]) -> None:
    t1, t2 = f"t-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(urls["app"])
    try:
        repo = _repo(engine)
        svc = ChatService(repository=repo)
        a = await svc.aget_or_create_channel_session(
            tenant_id=t1, channel="whatsapp", channel_user_id="+1"
        )
        b = await svc.aget_or_create_channel_session(
            tenant_id=t2, channel="whatsapp", channel_user_id="+1"
        )
        assert a.id != b.id

        # Under t2's GUC, t1's mapping is invisible (RLS, not just the WHERE).
        async with engine.connect() as conn, conn.begin():
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": t2})
            rows = (await conn.execute(text("SELECT tenant_id FROM chat_channel_sessions"))).all()
        assert {r[0] for r in rows} == {t2}

        await repo.claim_principal_session(tenant_id=t1, principal_id="p1", session_id=a.id)
        assert await repo.delete_session(a.id, t1) is True
        assert await _count(
            urls["admin"],
            "SELECT count(*) FROM chat_channel_sessions WHERE chat_session_id = :s", s=a.id,
        ) == 0
        assert await repo.get_principal_session(t1, "p1") is None

        # The next message after deletion starts a fresh (durable) conversation.
        fresh = await svc.aget_or_create_channel_session(
            tenant_id=t1, channel="whatsapp", channel_user_id="+1"
        )
        assert fresh.id != a.id
    finally:
        await engine.dispose()


async def test_downgrade_then_upgrade(urls: dict[str, str]) -> None:
    exists = (
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_name IN ('chat_channel_sessions', 'chat_principal_sessions')"
    )
    _alembic(urls["admin"], "downgrade", "a9c4e2f7b1d3")
    assert await _count(urls["admin"], exists) == 0
    _alembic(urls["admin"], "upgrade", "head")
    assert await _count(urls["admin"], exists) == 2
