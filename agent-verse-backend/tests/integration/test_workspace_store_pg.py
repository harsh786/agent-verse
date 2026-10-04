"""NATIVE-01 against real Postgres: the tenant workspace is shared and durable.

Two store instances on separate engines stand in for two replicas (or an API
replica and a worker): a file written through one is read through the other and
survives both being discarded. Runs as a NOSUPERUSER/NOBYPASSRLS role so the
forced RLS policy binds; another tenant sees nothing.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Iterator
from typing import Any

import asyncpg
import pytest

from app.tools.workspace_store import (
    PostgresWorkspaceStore,
    WorkspaceConflictError,
    WorkspaceFileTooLargeError,
    WorkspaceLimits,
    WorkspaceQuotaExceededError,
)

pytestmark = pytest.mark.integration


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
def app_role_url(pg_url: str) -> Iterator[str]:
    role = f"app_ws_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(16)

    async def _run(*stmts: str) -> None:
        conn = await asyncpg.connect(_plain(pg_url))
        try:
            for stmt in stmts:
                await conn.execute(stmt)
        finally:
            await conn.close()

    asyncio.run(
        _run(
            f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS",
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON workspace_files, workspace_usage TO {role}",
        )
    )
    head, tail = pg_url.split("://", 1)
    yield f"{head}://{role}:{password}@{tail.split('@', 1)[1]}"
    asyncio.run(
        _run(
            f"REVOKE ALL ON workspace_files, workspace_usage FROM {role}",
            f"DROP ROLE IF EXISTS {role}",
        )
    )


def _factory(url: str) -> Any:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    return async_sessionmaker(create_async_engine(url, poolclass=NullPool), expire_on_commit=False)


def test_workspace_is_shared_durable_and_tenant_scoped(app_role_url: str, pg_url: str) -> None:
    tid, other = f"t-{secrets.token_hex(4)}", f"o-{secrets.token_hex(4)}"

    async def _scenario() -> None:
        replica_a = PostgresWorkspaceStore(_factory(app_role_url))
        replica_b = PostgresWorkspaceStore(_factory(app_role_url))

        assert await replica_a.write(tid, "reports/q3/summary.md", "# Q3 ✓") == len(
            "# Q3 ✓".encode()
        )
        await replica_a.write(tid, "reports/notes.txt", "n")
        await replica_a.write(tid, "reports/notes.txt", "n2")  # overwrite
        assert await replica_b.read(tid, "reports/q3/summary.md") == "# Q3 ✓"
        assert await replica_b.read(tid, "reports/notes.txt") == "n2"

        listing = await replica_b.list(tid, "reports")
        assert [(e["name"], e["type"]) for e in listing] == [
            ("notes.txt", "file"),
            ("q3", "directory"),
        ]
        assert [e["name"] for e in await replica_b.list(tid, ".")] == ["reports"]
        page = await replica_b.list(tid, "reports", limit=1, after="notes.txt")
        assert [e["name"] for e in page] == ["q3"]

        # Tenant isolation (RLS + explicit predicate).
        with pytest.raises(FileNotFoundError):
            await replica_b.read(other, "reports/notes.txt")
        assert await replica_b.list(other, ".") == []
        assert await replica_b.delete(other, "reports") is False

        # File/directory conflicts.
        with pytest.raises(WorkspaceConflictError):
            await replica_a.write(tid, "reports/notes.txt/x", "x")
        with pytest.raises(WorkspaceConflictError):
            await replica_a.write(tid, "reports/q3", "x")

        # Concurrent writers on separate connections never corrupt the tree.
        await asyncio.gather(
            *(replica_a.write(tid, f"bulk/f{i}.txt", str(i)) for i in range(10)),
            *(replica_b.write(tid, f"bulk/g{i}.txt", str(i)) for i in range(10)),
        )
        assert len(await replica_a.list(tid, "bulk")) == 20

        # Subtree delete through one replica is seen by the other.
        assert await replica_b.delete(tid, "reports") is True
        with pytest.raises(FileNotFoundError):
            await replica_a.read(tid, "reports/q3/summary.md")
        assert [e["name"] for e in await replica_a.list(tid, ".")] == ["bulk"]

        # A "restart": brand-new store objects still see the data.
        fresh = PostgresWorkspaceStore(_factory(app_role_url))
        assert await fresh.read(tid, "bulk/f3.txt") == "3"

    asyncio.run(_scenario())


def test_quota_is_exact_under_concurrent_replicas(app_role_url: str, pg_url: str) -> None:
    """NATIVE-04: concurrent writers through two replicas never overshoot the quota."""
    tid = f"q-{secrets.token_hex(4)}"
    limits = WorkspaceLimits(max_file_bytes=10, max_tenant_bytes=50, max_entries=100)

    async def _scenario() -> None:
        a = PostgresWorkspaceStore(_factory(app_role_url), limits=limits)
        b = PostgresWorkspaceStore(_factory(app_role_url), limits=limits)
        with pytest.raises(WorkspaceFileTooLargeError):
            await a.write(tid, "big.txt", "x" * 11)
        results = await asyncio.gather(
            *(a.write(tid, f"f{i}.txt", "x" * 10) for i in range(8)),
            *(b.write(tid, f"g{i}.txt", "x" * 10) for i in range(8)),
            return_exceptions=True,
        )
        ok = [r for r in results if r == 10]
        refused = [r for r in results if isinstance(r, WorkspaceQuotaExceededError)]
        assert len(ok) == 5 and len(refused) == 11, results
        usage = await b.usage(tid)
        assert usage["bytes_used"] == 50 and usage["entries"] == 5
        assert len(await a.list(tid, ".")) == 5
        # Overwrite counts only the delta; deleting gives the space back.
        name = (await a.list(tid, "."))[0]["name"]
        await a.write(tid, name, "x")
        assert (await a.usage(tid))["bytes_used"] == 41
        await a.write(tid, "d/e.txt", "x" * 9)
        assert (await a.usage(tid))["entries"] == 7
        assert await b.delete(tid, "d") is True
        assert await b.usage(tid) == {"bytes_used": 41, "entries": 5, **limits.as_dict()}

    asyncio.run(_scenario())
