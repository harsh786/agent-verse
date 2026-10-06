"""CHAT-SEC-3: chat ``ttl_days`` is enforced, on a real Postgres.

The purge (app.chat.retention) runs as the maintenance role and:

* deletes a session idle longer than its ``ttl_days`` with all its messages
  (deleted in bounded batches), its artifacts and usage rows;
* keeps a recent session, a pinned one, one with no ttl, and every session an
  active legal hold covers (tenant-wide, by session id, by owner);
* asks for an owned session's transcript to be purged from the index BEFORE
  deleting it, and keeps the session when that fails;
* is bounded per run (``truncated``) and the next run continues.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/chat/test_session_retention_integration.py -m integration
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.chat.retention import purge_expired_chat_sessions
from tests.rag._pg import admin_exec, seed_tenant

pytestmark = pytest.mark.integration

NOW = datetime.now(UTC)


async def _session(
    pg: str, tid: str, *, idle_days: float, ttl: int | None, pinned: bool = False,
    owner: str | None = None, messages: int = 2,
) -> str:
    sid = uuid.uuid4().hex
    at = NOW - timedelta(days=idle_days)
    await admin_exec(
        pg,
        "INSERT INTO chat_sessions (id, tenant_id, title, ttl_days, pinned, owner_user_id, "
        "owner_principal, created_at, updated_at) VALUES (:id, :t, 'x', :ttl, :p, :o, :op, "
        ":at, :at)",
        {"id": sid, "t": tid, "ttl": ttl, "p": pinned, "o": owner,
         "op": f"user:{owner}" if owner else None, "at": at},
    )
    if messages:
        await admin_exec(
            pg,
            "INSERT INTO chat_messages (id, session_id, tenant_id, role, content, created_at) "
            "SELECT md5(random()::text || g::text), :s, :t, 'user', 'm' || g, :at "
            "FROM generate_series(1, :n) g",
            {"s": sid, "t": tid, "n": messages, "at": at},
        )
    await admin_exec(
        pg,
        "INSERT INTO chat_artifacts (id, tenant_id, kind, session_id, title, mime, content, "
        "size_bytes) VALUES (:id, :t, 'snippet', :s, 'a', 'text/plain', '\\x00', 1)",
        {"id": uuid.uuid4().hex, "t": tid, "s": sid},
    )
    return sid


async def _hold(pg: str, tid: str, *, kind: str = "chat", resources: list[str] | None = None,
                users: list[str] | None = None) -> None:
    await admin_exec(
        pg,
        "INSERT INTO legal_holds (tenant_id, name, resource_type, resource_ids, user_ids, "
        "status) VALUES (:t, 'matter', :k, CAST(:r AS jsonb), CAST(:u AS jsonb), 'active')",
        {"t": tid, "k": kind, "r": json.dumps(resources or []), "u": json.dumps(users or [])},
    )


async def _left(pg: str, sids: list[str]) -> dict[str, tuple[int, int]]:
    rows = await admin_exec(
        pg,
        "SELECT s.id, (SELECT count(*) FROM chat_messages m WHERE m.session_id = s.id), "
        "(SELECT count(*) FROM chat_artifacts a WHERE a.session_id = s.id) "
        "FROM chat_sessions s WHERE s.id = ANY(CAST(:ids AS text[]))",
        {"ids": sids},
    )
    return {str(r[0]): (int(r[1]), int(r[2])) for r in rows}


async def test_expired_sessions_are_purged_and_everything_else_is_kept(pg_url: str) -> None:
    t, held_t = "t-" + uuid.uuid4().hex[:10], "h-" + uuid.uuid4().hex[:10]
    for tid in (t, held_t):
        await seed_tenant(pg_url, tid)
    expired = await _session(pg_url, t, idle_days=10, ttl=7, owner="user-a", messages=23)
    expired_unowned = await _session(pg_url, t, idle_days=40, ttl=30)
    recent = await _session(pg_url, t, idle_days=2, ttl=7, owner="user-a")
    pinned = await _session(pg_url, t, idle_days=100, ttl=7, pinned=True)
    no_ttl = await _session(pg_url, t, idle_days=900, ttl=None)
    held_by_id = await _session(pg_url, t, idle_days=10, ttl=7)
    held_by_owner = await _session(pg_url, t, idle_days=10, ttl=7, owner="user-held")
    held_tenant = await _session(pg_url, held_t, idle_days=10, ttl=7)
    await _hold(pg_url, t, resources=[held_by_id])
    await _hold(pg_url, t, users=["user-held"])
    await _hold(pg_url, held_t, kind="tenant")

    purged: list[tuple[str, str, list[str]]] = []

    async def hook(tid: str, owner: str, sids: list[str]) -> None:
        purged.append((tid, owner, sorted(sids)))

    engine = create_async_engine(pg_url)  # the maintenance (RLS-bypassing) role
    try:
        report = await purge_expired_chat_sessions(
            async_sessionmaker(engine, expire_on_commit=False),
            on_owned_sessions=hook, message_batch=5,
        )
    finally:
        await engine.dispose()

    every = [expired, expired_unowned, recent, pinned, no_ttl, held_by_id, held_by_owner,
             held_tenant]
    left = await _left(pg_url, every)
    assert expired not in left and expired_unowned not in left
    assert set(left) == {recent, pinned, no_ttl, held_by_id, held_by_owner, held_tenant}
    assert all(v == (2, 1) for v in left.values())  # kept sessions keep everything
    orphans = await admin_exec(
        pg_url,
        "SELECT count(*) FROM chat_messages WHERE session_id = ANY(CAST(:ids AS text[]))",
        {"ids": [expired, expired_unowned]},
    )
    assert orphans[0][0] == 0
    assert purged == [(t, "user-a", [expired])]
    assert report.sessions_deleted >= 2 and report.messages_deleted >= 25
    assert report.artifacts_deleted >= 2 and report.truncated is False


async def test_a_failed_transcript_purge_keeps_the_session(pg_url: str) -> None:
    t = "t-" + uuid.uuid4().hex[:10]
    await seed_tenant(pg_url, t)
    sid = await _session(pg_url, t, idle_days=10, ttl=5, owner="user-x")

    async def broken(*_a: Any) -> None:
        raise ConnectionError("broker down")

    engine = create_async_engine(pg_url)
    try:
        with pytest.raises(ConnectionError):
            await purge_expired_chat_sessions(
                async_sessionmaker(engine, expire_on_commit=False), on_owned_sessions=broken
            )
    finally:
        await engine.dispose()
    assert (await _left(pg_url, [sid]))[sid] == (2, 1)
    await admin_exec(pg_url, "DELETE FROM chat_messages WHERE session_id = :s", {"s": sid})
    await admin_exec(pg_url, "DELETE FROM chat_sessions WHERE id = :s", {"s": sid})


async def test_the_purge_is_bounded_and_the_next_run_continues(pg_url: str) -> None:
    t = "t-" + uuid.uuid4().hex[:10]
    await seed_tenant(pg_url, t)
    sids = [await _session(pg_url, t, idle_days=20, ttl=11, messages=1) for _ in range(5)]
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        first = await purge_expired_chat_sessions(factory, batch_size=2, max_batches=1)
        assert first.truncated is True and first.sessions_deleted == 2
        assert len(await _left(pg_url, sids)) == 3
        while (await purge_expired_chat_sessions(factory, batch_size=2, max_batches=1)).truncated:
            pass
    finally:
        await engine.dispose()
    assert await _left(pg_url, sids) == {}


async def test_the_app_role_cannot_run_it_silently(pg_url: str) -> None:
    """Under the NOBYPASSRLS app role the purge fails loudly (never 'deleted 0')."""
    from tests.rag._pg import app_engine, sessions

    t = "t-" + uuid.uuid4().hex[:10]
    await seed_tenant(pg_url, t)
    await _session(pg_url, t, idle_days=10, ttl=3)
    engine = await app_engine(pg_url)
    try:
        with pytest.raises(Exception, match=r"row-level security|row_security"):
            await purge_expired_chat_sessions(sessions(engine))
    finally:
        await engine.dispose()
