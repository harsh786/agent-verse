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

from app.tools.workspace_store import PostgresWorkspaceStore, WorkspaceConflictError

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
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON workspace_files TO {role}",
        )
    )
    head, tail = pg_url.split("://", 1)
    yield f"{head}://{role}:{password}@{tail.split('@', 1)[1]}"
    asyncio.run(_run(f"REVOKE ALL ON workspace_files FROM {role}", f"DROP ROLE IF EXISTS {role}"))


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
