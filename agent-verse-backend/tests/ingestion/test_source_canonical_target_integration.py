"""Integration: canonical-target back-fill (a8c2e4f6b1d3) and the race guard.

* The migration back-fills ``source_configs.canonical_target_hash`` from each
  row's config — opening vault-encrypted endpoint fields (a MongoDB ``uri``) —
  and creates the partial unique index. Existing duplicates do not fail the
  upgrade: the oldest row keeps the key, the others stay NULL (logged). A row
  whose secret cannot be opened and a connector without a comparable identity
  stay NULL too.
* ``SourceConfigStore.create`` / ``update`` refuse a duplicate target
  (``DuplicateSourceError`` naming the existing Source), also against a legacy
  row whose key is still NULL, and when two creates of the same target race
  past the application check, the unique index lets exactly one win.

Uses its own container: it migrates from the previous head, which must not
disturb the shared ``pg_url`` database.

Run with::

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/ingestion/test_source_canonical_target_integration.py \\
        -q -m integration
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.ingestion.source_config import SourceConfig, SourceFamily
from app.ingestion.source_identity import (
    CANONICAL_TARGET_INDEX,
    DuplicateSourceError,
    canonical_target_hash,
)
from app.ingestion.source_secrets import encrypt_connection_config
from app.ingestion.source_store import SourceConfigStore

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = "a4c6e8f0b2d1"
TENANT = "tenant-canon-target"
_URI = "mongodb://reader:reader-pw@mongo-a.example.com:27017,mongo-b.example.com:27017/"


def _mongo(collections: list[str], uri: str = _URI) -> dict[str, Any]:
    return {"uri": uri, "database": "rw_source", "collections": collections}


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


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": url, "ENVIRONMENT": "development"}
    for key in ("MIGRATION_DATABASE_URL", "APP_DB_USER", "APP_DB_PASSWORD"):
        env.pop(key, None)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


_ROWS: dict[str, dict[str, Any]] = {
    # oldest first: the first one of a duplicate pair keeps the key
    "src-first": {"type": "mongodb", "kb": "kb-1", "cc": _mongo(["postmortems"])},
    # the same target, spelled differently, in the same KB: an existing duplicate
    "src-dup": {
        "type": "mongodb",
        "kb": "kb-1",
        "cc": _mongo(
            ["postmortems"],
            uri="mongodb://other:pw@MONGO-B.example.com,mongo-a.example.com/?replicaSet=rs0",
        ),
    },
    # the same target into another KB: allowed, keyed
    "src-other-kb": {"type": "mongodb", "kb": "kb-2", "cc": _mongo(["postmortems"])},
    # a different collection into the same KB: allowed, keyed
    "src-other-coll": {"type": "mongodb", "kb": "kb-1", "cc": _mongo(["runbooks"])},
    # an account named only by its token: no comparable identity
    "src-hubspot": {
        "type": "hubspot",
        "kb": "kb-1",
        "cc": {"access_token": "tok", "object_types": ["contacts"]},
    },
    # a secret that cannot be opened here (wrong key / tampered): left NULL
    "src-unreadable": {
        "type": "mongodb",
        "kb": "kb-3",
        "cc": {"uri": "enc:v1:not-a-fernet-token", "database": "x", "collections": ["c"]},
    },
}


@pytest.fixture(scope="module")
def migrated(db_url: str) -> str:
    """Schema at the previous head, legacy rows inserted, then upgraded to head."""
    result = _alembic(db_url, "upgrade", PREVIOUS)
    assert result.returncode == 0, result.stderr[-3000:]

    async def _seed() -> None:
        engine = create_async_engine(db_url)
        try:
            async with engine.begin() as conn:
                for i, (sid, row) in enumerate(_ROWS.items()):
                    cc = row["cc"]
                    if sid != "src-unreadable":
                        cc = encrypt_connection_config(cc)  # how the store persists it
                    await conn.execute(
                        text(
                            "INSERT INTO source_configs (id, tenant_id, name, family, "
                            "source_type, connection_config, collection_id, created_at) "
                            "VALUES (:id, :tid, :name, 'nosql_database', :type, "
                            "CAST(:cc AS jsonb), :kb, now() - make_interval(mins => :age))"
                        ),
                        {
                            "id": sid,
                            "name": sid,
                            "tid": TENANT,
                            "type": row["type"],
                            "cc": json.dumps(cc),
                            "kb": row["kb"],
                            "age": 100 - i,
                        },
                    )
        finally:
            await engine.dispose()

    asyncio.run(_seed())
    result = _alembic(db_url, "upgrade", "head")
    assert result.returncode == 0, result.stderr[-3000:]
    # Existing duplicates are reported, never failing the upgrade.
    assert "src-dup" in result.stderr and "src-first" in result.stderr
    return db_url


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def factory(migrated: str) -> AsyncIterator[Any]:
    engine = create_async_engine(migrated)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _hashes(factory: Any) -> dict[str, str | None]:
    async with factory() as s:
        rows = (
            await s.execute(text("SELECT id, canonical_target_hash FROM source_configs"))
        ).all()
    return {str(r[0]): r[1] for r in rows}


async def test_backfill_keys_rows_and_leaves_duplicates_and_unreadable_null(factory: Any) -> None:
    hashes = await _hashes(factory)
    expected = canonical_target_hash("mongodb", _mongo(["postmortems"]))
    assert hashes["src-first"] == expected
    assert hashes["src-dup"] is None  # existing duplicate: left in place, unkeyed
    assert hashes["src-other-kb"] == expected  # same target, other KB: allowed
    assert hashes["src-other-coll"] == canonical_target_hash("mongodb", _mongo(["runbooks"]))
    assert hashes["src-hubspot"] is None
    assert hashes["src-unreadable"] is None
    # The rows themselves are untouched (nothing deleted, secrets still sealed).
    async with factory() as s:
        n = (await s.execute(text("SELECT count(*) FROM source_configs"))).scalar_one()
        forced = (
            await s.execute(
                text(
                    "SELECT relforcerowsecurity FROM pg_class "
                    "WHERE oid = 'source_configs'::regclass"
                )
            )
        ).scalar_one()
        index = (
            await s.execute(
                text(
                    "SELECT i.indisunique, i.indisvalid, pg_get_indexdef(i.indexrelid) "
                    "FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                    "WHERE c.relname = :n"
                ),
                {"n": CANONICAL_TARGET_INDEX},
            )
        ).one()
    assert n == len(_ROWS)
    assert forced is True  # FORCE RLS restored after the back-fill
    assert index[0] is True and index[1] is True
    assert "canonical_target_hash IS NOT NULL" in index[2]


def _config(sid: str, kb: str, collections: list[str], **cc: Any) -> SourceConfig:
    return SourceConfig(
        source_id=sid,
        tenant_id=TENANT,
        name=sid,
        family=SourceFamily.NOSQL_DATABASE,
        source_type="mongodb",
        connection_config={**_mongo(collections), **cc},
        collection_id=kb,
    )


async def test_create_refuses_a_backfilled_duplicate(factory: Any) -> None:
    store = SourceConfigStore(db=factory)
    with pytest.raises(DuplicateSourceError) as err:
        await store.create(_config(uuid.uuid4().hex, "kb-1", ["postmortems"]))
    assert err.value.existing_source_id == "src-first"
    # A different collection or another KB is fine.
    await store.create(_config(uuid.uuid4().hex, "kb-1", ["incidents"]))
    await store.create(_config(uuid.uuid4().hex, "kb-9", ["postmortems"]))


async def test_create_refuses_a_duplicate_of_a_legacy_unkeyed_row(factory: Any) -> None:
    """A row the back-fill could not key is still compared (decrypted on read)."""
    legacy = "src-legacy-null"
    async with factory() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO source_configs (id, tenant_id, name, family, source_type, "
                "connection_config, collection_id) VALUES (:id, :tid, :name, "
                "'nosql_database', 'mongodb', CAST(:cc AS jsonb), 'kb-legacy')"
            ),
            {
                "id": legacy,
                "name": legacy,
                "tid": TENANT,
                "cc": json.dumps(encrypt_connection_config(_mongo(["audit"]))),
            },
        )
    store = SourceConfigStore(db=factory)
    with pytest.raises(DuplicateSourceError) as err:
        await store.create(_config(uuid.uuid4().hex, "kb-legacy", ["audit"]))
    assert err.value.existing_source_id == legacy


async def test_concurrent_creates_of_one_target_exactly_one_wins(factory: Any) -> None:
    store = SourceConfigStore(db=factory)
    configs = [_config(f"race-{i}-{uuid.uuid4().hex[:8]}", "kb-race", ["orders"]) for i in range(4)]
    # Both pass the application check (the race window): only the index decides.
    with patch.object(SourceConfigStore, "find_duplicate", AsyncMock(return_value=None)):
        outcomes = await asyncio.gather(
            *(store.create(c) for c in configs), return_exceptions=True
        )
    winners = [c for c, o in zip(configs, outcomes, strict=True) if not isinstance(o, Exception)]
    losers = [o for o in outcomes if isinstance(o, Exception)]
    assert len(winners) == 1, outcomes
    assert all(isinstance(o, DuplicateSourceError) for o in losers), losers
    assert {o.existing_source_id for o in losers} == {winners[0].source_id}  # type: ignore[union-attr]
    async with factory() as s:
        n = (
            await s.execute(
                text("SELECT count(*) FROM source_configs WHERE collection_id = 'kb-race'")
            )
        ).scalar_one()
    assert n == 1


async def test_concurrent_creates_without_patching_exactly_one_wins(factory: Any) -> None:
    store = SourceConfigStore(db=factory)
    configs = [_config(f"race2-{i}", "kb-race-2", ["orders"]) for i in range(6)]
    outcomes = await asyncio.gather(*(store.create(c) for c in configs), return_exceptions=True)
    assert sum(not isinstance(o, Exception) for o in outcomes) == 1, outcomes
    assert all(isinstance(o, DuplicateSourceError) for o in outcomes if isinstance(o, Exception))


async def test_update_into_a_duplicate_is_refused_by_check_and_by_index(factory: Any) -> None:
    store = SourceConfigStore(db=factory)
    first = await store.create(_config("upd-first", "kb-upd", ["x"]))
    second = await store.create(_config("upd-second", "kb-upd", ["y"]))
    with pytest.raises(DuplicateSourceError) as err:
        await store.update(second.source_id, TENANT, connection_config=_mongo(["x"]))
    assert err.value.existing_source_id == first.source_id
    with (
        patch.object(SourceConfigStore, "find_duplicate", AsyncMock(return_value=None)),
        pytest.raises(DuplicateSourceError) as err2,
    ):
        await store.update(second.source_id, TENANT, connection_config=_mongo(["x"]))
    assert err2.value.existing_source_id == first.source_id
    unchanged = await store.get(second.source_id, TENANT)
    assert unchanged is not None and unchanged.connection_config["collections"] == ["y"]
    # A legitimate re-point keeps the key current.
    await store.update(second.source_id, TENANT, connection_config=_mongo(["z"]))
    assert (await _hashes(factory))[second.source_id] == canonical_target_hash(
        "mongodb", _mongo(["z"])
    )
    # Deleting the first frees its target.
    assert await store.delete(first.source_id, TENANT)
    await store.update(second.source_id, TENANT, connection_config=_mongo(["x"]))
