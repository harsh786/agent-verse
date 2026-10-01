"""KB-SEARCH-TENANT: knowledge search never returns another tenant's chunks.

``hybrid_search``'s chunk legs (vector, FTS, trigram, BM25) filtered on
``collection_id`` only and relied on RLS, and a caller that passed
``embedding_dim`` skipped the collection-ownership check entirely. On a
SUPERUSER / BYPASSRLS connection a tenant could therefore search another
tenant's collection. Now the ownership check runs on every path and every leg
also requires ``tenant_id = current_setting('app.tenant_id')``.

Runs on the testcontainer SUPERUSER, so RLS does nothing here.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.rag.engine import hybrid_search

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_DIM = 1024
_VEC = [0.0] * (_DIM - 1) + [1.0]


async def _tenant(s: Any) -> str:
    tid = uuid.uuid4().hex
    await s.execute(
        text("INSERT INTO tenants (id, name, email) VALUES (:id, 'T', :e)"),
        {"id": tid, "e": f"{tid}@example.test"},
    )
    return tid


async def _collection(s: Any, tenant: str) -> str:
    cid = uuid.uuid4().hex
    await s.execute(
        text(
            "INSERT INTO knowledge_collections (id, tenant_id, name, embedding_dim) "
            "VALUES (:id, :tid, 'c', :dim)"
        ),
        {"id": cid, "tid": tenant, "dim": _DIM},
    )
    return cid


async def _chunk(s: Any, *, tenant: str, collection: str, content: str) -> None:
    await s.execute(
        text(
            f"INSERT INTO knowledge_chunks_{_DIM} "
            "(tenant_id, collection_id, document_id, chunk_index, content, content_hash, "
            " embedding) "
            "VALUES (:tid, :cid, :doc, 0, :content, :hash, CAST(:emb AS vector))"
        ),
        {
            "tid": tenant,
            "cid": collection,
            "doc": uuid.uuid4().hex,
            "content": content,
            "hash": uuid.uuid4().hex,
            "emb": str(_VEC),
        },
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world(pg_url: str) -> AsyncIterator[dict[str, Any]]:
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s, s.begin():
        a, b = await _tenant(s), await _tenant(s)
        col_a, col_b = await _collection(s, a), await _collection(s, b)
        await _chunk(s, tenant=a, collection=col_a, content="alpha retention schedule policy")
        # A row of tenant A inside B's collection (FKs ignore tenancy): only the
        # per-leg tenant predicate keeps it out of B's results.
        await _chunk(s, tenant=a, collection=col_b, content="alpha retention schedule leak")
        await _chunk(s, tenant=b, collection=col_b, content="beta retention schedule notes")
    yield {"factory": factory, "a": a, "b": b, "col_a": col_a, "col_b": col_b}
    await engine.dispose()


async def _search(world: dict[str, Any], tenant: str, collection: str, **kw: Any) -> list[str]:
    async with world["factory"]() as s, s.begin():
        await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
        results = await hybrid_search(
            s,
            query="retention schedule",
            query_embedding=_VEC,
            collection_id=collection,
            **kw,
        )
    return [r.content for r in results]


@pytest.mark.parametrize("embedding_dim", [None, _DIM])
async def test_a_second_tenant_never_gets_hits_from_a_foreign_collection(
    world: dict[str, Any], embedding_dim: int | None
) -> None:
    own = await _search(world, world["a"], world["col_a"], embedding_dim=embedding_dim)
    assert own and all("alpha" in c for c in own)
    # B searching A's collection: nothing, whether or not it passes the dimension.
    assert await _search(world, world["b"], world["col_a"], embedding_dim=embedding_dim) == []


@pytest.mark.parametrize("mode", ["hybrid", "vector", "lexical"])
async def test_every_leg_drops_rows_of_another_tenant(world: dict[str, Any], mode: str) -> None:
    hits = await _search(
        world, world["b"], world["col_b"], embedding_dim=_DIM, retrieval_mode=mode
    )
    assert hits, f"{mode}: tenant B must still find its own chunk"
    assert all("leak" not in c for c in hits), f"{mode} leaked tenant A's row: {hits}"
