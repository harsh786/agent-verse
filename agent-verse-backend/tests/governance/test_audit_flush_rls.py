"""Integration test: the audit trail must actually reach Postgres.

`AuditFlusher` drains the Redis WAL into `audit_events` — a FORCE ROW LEVEL
SECURITY table (migration 0057). It ran one **cross-tenant** batch INSERT with
no RLS context at all, and the flusher is started from the API lifespan
(`app/main.py`), so it runs under the API's least-privilege role — not a
maintenance role with BYPASSRLS.

Under that role the INSERT is rejected, the broad `except` routes the batch to
the Redis DLQ, and `flush()` returns 0. The audit trail — the tamper-evident
compliance record this whole subsystem exists to produce — never reaches
Postgres, permanently.

`_ensure_chain_initialized` has the same problem in reverse: its
`SELECT event_hash FROM audit_events` matches nothing under RLS, so every
process would re-seed each tenant's hash chain from "" and the verifier would
see a fresh chain rather than a continuation.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/governance/test_audit_flush_rls.py -q -m integration
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.governance.audit_v2 import WAL_DEAD_LETTER, AuditFlusher

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TENANT_A = "tenant-audit-a"
TENANT_B = "tenant-audit-b"


class _FakePipeline:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[Any, ...]]] = []

    def lpop(self, key: str) -> None:
        self._ops.append(("lpop", (key,)))

    def rpush(self, key: str, value: str) -> None:
        self._ops.append(("rpush", (key, value)))

    async def execute(self) -> list[Any]:
        out: list[Any] = []
        for op, args in self._ops:
            if op == "lpop":
                lst = self._redis.lists.get(args[0], [])
                out.append(lst.pop(0) if lst else None)
            else:
                self._redis.lists.setdefault(args[0], []).append(args[1])
                out.append(1)
        self._ops.clear()
        return out


class _FakeRedis:
    """Just enough of redis-py's async surface for AuditFlusher."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}

    async def set(self, *a: Any, **kw: Any) -> bool:
        return True

    async def delete(self, *a: Any, **kw: Any) -> int:
        return 1

    def pipeline(self, transaction: bool = False) -> _FakePipeline:
        return _FakePipeline(self)


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": admin_url},
            check=True,
            capture_output=True,
            text=True,
        )
        yield admin_url


@pytest_asyncio.fixture(scope="function")
async def factories(postgres_url: str) -> AsyncIterator[tuple]:
    """(admin_factory, api_factory) — api_factory is NOSUPERUSER/NOBYPASSRLS.

    That is the role the API process (and therefore the flusher) actually runs
    as, which is the whole point: the flusher must not need special privileges.
    """
    password = secrets.token_urlsafe(24)
    role = f"test_api_audit_{secrets.token_hex(4)}"
    admin_engine = create_async_engine(postgres_url)
    async with admin_engine.begin() as conn:
        quoted = (
            await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})
        ).scalar_one()
        await conn.execute(
            text(
                f"CREATE ROLE {role} LOGIN PASSWORD {quoted} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await conn.execute(text(f"GRANT CONNECT ON DATABASE test TO {role}"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        await conn.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON audit_events TO {role}")
        )
        await conn.execute(text("DELETE FROM audit_events WHERE tenant_id = ANY(:t)"),
                           {"t": [TENANT_A, TENANT_B]})

    api_url = (
        make_url(postgres_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    api_engine = create_async_engine(api_url, pool_size=4, max_overflow=0)
    yield (
        async_sessionmaker(admin_engine, expire_on_commit=False),
        async_sessionmaker(api_engine, expire_on_commit=False),
    )
    await api_engine.dispose()
    await admin_engine.dispose()


def _wal_event(tenant_id: str, name: str) -> str:
    return json.dumps(
        {
            "id": f"{tenant_id}-{name}",
            "tenant_id": tenant_id,
            "event_type": "tool.call",
            "action": "execute",
            "actor_type": "agent",
            "status": "success",
        }
    )


@pytest.mark.asyncio
async def test_audit_events_reach_postgres_under_the_api_role(
    factories: tuple,
) -> None:
    admin_factory, api_factory = factories
    redis = _FakeRedis()
    from app.governance.audit_v2 import WAL_KEY

    redis.lists[WAL_KEY] = [
        _wal_event(TENANT_A, "e1"),
        _wal_event(TENANT_B, "e1"),
        _wal_event(TENANT_A, "e2"),
    ]

    flushed = await AuditFlusher(redis=redis, db_factory=api_factory).flush()
    assert flushed == 3, (
        f"flush reported {flushed}; DLQ={redis.lists.get(WAL_DEAD_LETTER, [])[:1]}"
    )

    async with admin_factory() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT tenant_id, id FROM audit_events "
                    "WHERE tenant_id = ANY(:t) ORDER BY id"
                ),
                {"t": [TENANT_A, TENANT_B]},
            )
        ).fetchall()

    assert len(rows) == 3, f"audit events never reached Postgres: {rows}"
    assert {r[0] for r in rows} == {TENANT_A, TENANT_B}
    assert not redis.lists.get(WAL_DEAD_LETTER), "events were dead-lettered"


@pytest.mark.asyncio
async def test_hash_chain_continues_across_flusher_restarts(
    factories: tuple,
) -> None:
    """A fresh flusher must seed each tenant's chain tip from Postgres.

    `_ensure_chain_initialized` reads `audit_events` under RLS too; if that read
    returns nothing the chain restarts from "" and the verifier sees a break
    rather than a continuation.
    """
    _admin, api_factory = factories
    from app.governance.audit_v2 import WAL_KEY

    redis = _FakeRedis()
    redis.lists[WAL_KEY] = [_wal_event(TENANT_A, "first")]
    assert await AuditFlusher(redis=redis, db_factory=api_factory).flush() == 1

    # A brand-new flusher (process restart): no in-memory chain cache at all.
    redis.lists[WAL_KEY] = [_wal_event(TENANT_A, "second")]
    restarted = AuditFlusher(redis=redis, db_factory=api_factory)
    assert await restarted.flush() == 1
    assert restarted._chain_cache[TENANT_A], "chain tip was not seeded from Postgres"

    async with api_factory() as s:
        await s.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_A}
        )
        rows = (
            await s.execute(
                text(
                    "SELECT id, prev_hash, event_hash FROM audit_events "
                    "WHERE tenant_id = :t ORDER BY created_at"
                ),
                {"t": TENANT_A},
            )
        ).fetchall()

    assert len(rows) == 2, rows
    # The second event must chain onto the first, not restart from "".
    assert rows[1][1] == rows[0][2], f"chain broken across restart: {rows}"
