"""ID-ONLY-LOOKUPS: the retrieval engine reads collection metadata for the caller's tenant only.

``hybrid_search`` (embedding_dim) and the binary prefilter (chunk_count) read
``knowledge_collections`` by id alone, relying on the caller's RLS context. On a
SUPERUSER / BYPASSRLS connection that returns another tenant's collection
metadata. Both now go through ``_collection_metadata``, which also requires
``tenant_id = current_setting('app.tenant_id')`` — the tenant the session is
scoped to — so an unscoped session reads nothing at all.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.rag.engine import _collection_metadata, hybrid_search

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _scoped(session: Any, tenant_id: str) -> None:
    await session.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id}
    )


async def test_collection_metadata_is_tenant_scoped_on_a_superuser(pg_url: str) -> None:
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    a, b = uuid.uuid4().hex, uuid.uuid4().hex
    cid = uuid.uuid4().hex
    try:
        async with factory() as s, s.begin():
            for tid in (a, b):
                await s.execute(
                    text("INSERT INTO tenants (id, name, email) VALUES (:id, 'T', :e)"),
                    {"id": tid, "e": f"{tid}@example.test"},
                )
            await s.execute(
                text(
                    "INSERT INTO knowledge_collections "
                    "(id, tenant_id, name, embedding_dim, chunk_count) "
                    "VALUES (:id, :tid, 'c', 1024, 77)"
                ),
                {"id": cid, "tid": a},
            )
        async with factory() as s, s.begin():
            await _scoped(s, a)
            assert await _collection_metadata(s, cid, "embedding_dim") == 1024
            assert await _collection_metadata(s, cid, "chunk_count") == 77
        async with factory() as s, s.begin():
            await _scoped(s, b)  # the superuser bypasses RLS; the predicate holds
            with pytest.raises(LookupError):
                await _collection_metadata(s, cid, "embedding_dim")
            with pytest.raises(LookupError):
                await _collection_metadata(s, cid, "chunk_count")
            # ...and hybrid_search searches nothing rather than a guessed table.
            assert (
                await hybrid_search(
                    s, query="q", query_embedding=[0.1] * 1024, collection_id=cid
                )
                == []
            )
        async with factory() as s, s.begin():  # no tenant scope at all
            with pytest.raises(LookupError):
                await _collection_metadata(s, cid, "embedding_dim")
    finally:
        await engine.dispose()


async def test_collection_metadata_rejects_unknown_columns() -> None:
    with pytest.raises(ValueError):
        await _collection_metadata(object(), "cid", "tenant_id; DROP TABLE x")  # type: ignore[arg-type]
