"""ORG-42: chat-generated documents and saved session artifacts on real Postgres.

They used to live in per-process dicts (download 404 on any other replica and after
a restart; memory unbounded). Runs as a least-privilege NOBYPASSRLS role and proves:
a document stored through replica A downloads through replica B and after a
"restart" (new engine); another tenant gets nothing (RLS, not just the WHERE); an
expired document is gone and the purge task deletes it in batches; session
artifacts are durable, session-scoped and deleted with their session; and the
migration downgrades cleanly.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/chat/test_chat_artifacts_integration.py -q -m integration --no-cov
"""

from __future__ import annotations

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

from app.chat.ownership import SYSTEM_SCOPE
from app.chat.artifact_store import ChatArtifactStore, purge_expired_chat_artifacts
from app.chat.repository import PostgresChatRepository
from app.chat.service import ChatService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_APP_ROLE = "test_app_chat_artifacts_rls"
_CHAT_ARTIFACTS_REVISION = "c7fbea9807c1"  # app/db/migrations/versions/c7fbea9807c1_chat_artifacts.py


def _alembic(url: str, *args: str) -> None:
    proc = subprocess.run(
        ["alembic", *args],
        cwd=_BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr[-3000:]


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
            for tbl in ("chat_sessions", "chat_messages", "chat_artifacts"):
                await conn.exec_driver_sql(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO {_APP_ROLE}"
                )
        await admin.dispose()
        app_url = (
            make_url(admin_url)
            .set(username=_APP_ROLE, password=pw)
            .render_as_string(hide_password=False)
        )
        yield {"admin": admin_url, "app": app_url}


def _repo(engine: AsyncEngine) -> PostgresChatRepository:
    return PostgresChatRepository(async_sessionmaker(engine, expire_on_commit=False))


def _store(engine: AsyncEngine, **kw: int) -> ChatArtifactStore:
    store = ChatArtifactStore(**kw)
    store.attach_repository(_repo(engine))
    return store


async def _scalar(admin_url: str, sql: str, **params: str) -> int:
    engine = create_async_engine(admin_url)
    try:
        async with engine.connect() as conn:
            return int((await conn.execute(text(sql), params)).scalar_one())
    finally:
        await engine.dispose()


async def test_document_crosses_replicas_and_restart_tenant_isolated(
    urls: dict[str, str],
) -> None:
    t1, t2 = f"t-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    a, b = create_async_engine(urls["app"]), create_async_engine(urls["app"])
    try:
        aid = await _store(a).put(
            tenant_id=t1, content=b"%PDF-1.4 body", mime="application/pdf", filename="r.pdf"
        )
        got = await _store(b).get(aid, t1, scope=SYSTEM_SCOPE)
        assert got is not None
        assert got.content == b"%PDF-1.4 body" and got.filename == "r.pdf"
        assert got.mime == "application/pdf"
        assert await _store(b).get(aid, t2, scope=SYSTEM_SCOPE) is None

        # Restart: a brand-new engine and store still serve it.
        await a.dispose()
        a = create_async_engine(urls["app"])
        assert (await _store(a).get(aid, t1, scope=SYSTEM_SCOPE)) is not None

        # RLS, not just the WHERE clause: under t2's GUC the row is invisible.
        async with b.connect() as conn, conn.begin():
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": t2})
            n = (
                await conn.execute(
                    text("SELECT count(*) FROM chat_artifacts WHERE id = :i"), {"i": aid}
                )
            ).scalar_one()
        assert n == 0
    finally:
        await a.dispose()
        await b.dispose()


async def test_expired_documents_are_hidden_and_purged(urls: dict[str, str]) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(urls["app"])
    try:
        expired = _store(engine, retention_days=0)
        ids = [
            await expired.put(tenant_id=tenant, content=b"x", mime="text/plain", filename="e")
            for _ in range(5)
        ]
        kept = await _store(engine).put(
            tenant_id=tenant, content=b"y", mime="text/plain", filename="k"
        )
        assert await _store(engine).get(ids[0], tenant, scope=SYSTEM_SCOPE) is None
    finally:
        await engine.dispose()

    # The purge runs cross-tenant on the maintenance (BYPASSRLS) role.
    admin = create_async_engine(urls["admin"])
    try:
        purged = await purge_expired_chat_artifacts(
            async_sessionmaker(admin, expire_on_commit=False), batch_size=2
        )
    finally:
        await admin.dispose()
    assert purged >= 5
    assert (
        await _scalar(
            urls["admin"], "SELECT count(*) FROM chat_artifacts WHERE tenant_id = :t", t=tenant
        )
        == 1
    )
    assert (
        await _scalar(urls["admin"], "SELECT count(*) FROM chat_artifacts WHERE id = :i", i=kept)
        == 1
    )


async def test_purge_under_the_app_role_fails_loudly(urls: dict[str, str]) -> None:
    engine = create_async_engine(urls["app"])
    try:
        with pytest.raises(Exception, match=r"row-level security|permission"):
            await purge_expired_chat_artifacts(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


async def test_session_artifacts_are_durable_scoped_and_cascade(urls: dict[str, str]) -> None:
    t1, t2 = f"t-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    a, b = create_async_engine(urls["app"]), create_async_engine(urls["app"])
    try:
        svc_a, svc_b = ChatService(repository=_repo(a)), ChatService(repository=_repo(b))
        session = await svc_a.acreate_session(t1, title="s")
        other = await svc_a.acreate_session(t1, title="other")
        art = await svc_a.acreate_artifact(session.id, t1, "snippet", "python", "print(1)")

        listed = await svc_b.alist_artifacts(session.id, t1)
        assert [x.id for x in listed] == [art.id]
        assert listed[0].content == "print(1)" and listed[0].language == "python"
        assert await svc_b.alist_artifacts(session.id, t2) == []

        updated = await svc_b.aupdate_artifact(art.id, session.id, t1, "print(2)")
        assert updated is not None and updated.content == "print(2)"
        # Wrong session or tenant: not found, nothing changed.
        assert await svc_b.aupdate_artifact(art.id, other.id, t1, "x") is None
        assert await svc_b.aupdate_artifact(art.id, session.id, t2, "x") is None
        assert await svc_b.adelete_artifact(art.id, other.id, t1) is False

        await svc_a.acreate_artifact(session.id, t1, "second", "text", "y")
        assert await svc_b.adelete_artifact(art.id, session.id, t1) is True
        assert len(await svc_a.alist_artifacts(session.id, t1)) == 1

        assert await svc_a.adelete_session(session.id, t1) is True
        assert (
            await _scalar(
                urls["admin"],
                "SELECT count(*) FROM chat_artifacts WHERE session_id = :s",
                s=session.id,
            )
            == 0
        )
    finally:
        await a.dispose()
        await b.dispose()


async def test_downgrade_then_upgrade(urls: dict[str, str]) -> None:
    has_kind = (
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'chat_artifacts' AND column_name IN ('kind', 'expires_at')"
    )
    # Downgrade THIS migration (and whatever sits on top of it), not "head -1":
    # head is a merge revision once other branches land, and "-1" from a merge
    # is ambiguous to alembic.
    _alembic(urls["admin"], "downgrade", f"{_CHAT_ARTIFACTS_REVISION}-1")
    assert await _scalar(urls["admin"], has_kind) == 0
    _alembic(urls["admin"], "upgrade", "head")
    assert await _scalar(urls["admin"], has_kind) == 2
