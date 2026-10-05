"""P7-3 against real Postgres: versioned datasets in AIOpsStore.

Runs as a least-privilege (NOSUPERUSER, NOBYPASSRLS) role so FORCE RLS binds.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from collections.abc import Iterator
from typing import Any

import asyncpg
import pytest

from app.evals.ai_ops_store import AIOpsStore
from app.evals.dataset_versions import (
    DatasetNotFoundError,
    NothingToPublishError,
    VersionConflictError,
    VersionNotFoundError,
)

pytestmark = pytest.mark.integration

_T = "t-ds-ver-a"
_OTHER = "t-ds-ver-b"
_V1 = [{"input": "q0", "expected_output": "a0"}]


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
def app_role_url(pg_url: str) -> Iterator[str]:
    role = f"app_dsv_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(16)
    tables = "ai_ops_datasets, ai_ops_dataset_versions"

    async def _run(*stmts: str) -> None:
        conn = await asyncpg.connect(_plain(pg_url))
        try:
            for stmt in stmts:
                await conn.execute(stmt)
        finally:
            await conn.close()

    asyncio.run(_run(
        f"CREATE ROLE {role} LOGIN PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS",
        f"GRANT SELECT, INSERT, UPDATE ON {tables} TO {role}",
    ))
    head, tail = pg_url.split("://", 1)
    yield f"{head}://{role}:{password}@{tail.split('@', 1)[1]}"
    asyncio.run(_run(f"REVOKE ALL ON {tables} FROM {role}", f"DROP ROLE {role}"))


def _store(url: str) -> AIOpsStore:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    return AIOpsStore(
        async_sessionmaker(create_async_engine(url, poolclass=NullPool), expire_on_commit=False)
    )


def test_versioned_dataset_lifecycle(app_role_url: str) -> None:
    store = _store(app_role_url)

    async def _run() -> None:
        did = str(uuid.uuid4())
        await store.create_dataset(tenant_id=_T, dataset_id=did, name="g", description="",
                                   golden_tasks=_V1)
        head = await store.get_dataset(_T, did)
        assert head is not None
        assert (head["version"], head["status"], head["published_version"]) == (1, "published", 1)

        edited = await store.edit_dataset(_T, did, ops=[
            {"op": "replace", "index": 0, "task": {"input": "q0", "expected_output": "b0"}}])
        assert (edited["version"], edited["status"]) == (2, "draft")
        v1 = await store.get_dataset_version(_T, did, 1)
        assert v1 is not None and v1["golden_tasks"] == _V1  # published = immutable

        again = await store.edit_dataset(_T, did, ops=[{"op": "add", "task": {"input": "q1"}}],
                                         name="renamed", if_version=2)
        assert again["version"] == 2 and again["task_count"] == 2 and again["name"] == "renamed"
        with pytest.raises(VersionConflictError):
            await store.edit_dataset(_T, did, ops=[], if_version=1)
        with pytest.raises(VersionNotFoundError):
            await store.edit_dataset(_T, did, ops=[{"op": "delete", "index": 5}])

        pinned = await store.pin_version_for_run(_T, did)
        assert pinned["version"] == 2 and pinned["status"] == "published"
        assert pinned["golden_tasks"][0]["expected_output"] == "b0"
        with pytest.raises(NothingToPublishError):
            await store.publish_version(_T, did)
        v3 = await store.edit_dataset(_T, did, ops=[{"op": "delete", "index": 1}])
        assert v3["version"] == 3
        assert (await store.publish_version(_T, did))["status"] == "published"
        versions = await store.list_dataset_versions(_T, did)
        assert [(v["version"], v["status"], v["task_count"]) for v in versions] == [
            (3, "published", 1), (2, "published", 2), (1, "published", 1)
        ]
        with pytest.raises(VersionNotFoundError):
            await store.pin_version_for_run(_T, did, 9)

        # Another tenant sees nothing and can edit nothing.
        assert await store.get_dataset(_OTHER, did) is None
        assert await store.get_dataset_version(_OTHER, did, 1) is None
        with pytest.raises(DatasetNotFoundError):
            await store.edit_dataset(_OTHER, did, ops=[{"op": "add", "task": {"input": "x"}}])
        listed = await store.list_datasets(_T)
        assert [(d["dataset_id"], d["version"]) for d in listed] == [(did, 3)]

    asyncio.run(_run())


def test_concurrent_edits_of_a_published_head_make_one_draft(app_role_url: str) -> None:
    store = _store(app_role_url)

    async def _run() -> list[dict[str, Any]]:
        did = str(uuid.uuid4())
        await store.create_dataset(tenant_id=_T, dataset_id=did, name="g", description="",
                                   golden_tasks=_V1)
        edits = [
            store.edit_dataset(_T, did, ops=[{"op": "add", "task": {"input": f"c{i}"}}])
            for i in range(5)
        ]
        await asyncio.gather(*edits)
        return await store.list_dataset_versions(_T, did)

    versions = asyncio.run(_run())
    assert [(v["version"], v["status"]) for v in versions] == [(2, "draft"), (1, "published")]
    assert versions[0]["task_count"] == 6  # every edit landed in the one draft
