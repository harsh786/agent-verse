"""P2-6: the per-collection precomputed-index readiness probe on real Postgres.

RAPTOR / agentic-chunking are "available" for a collection only when it holds
their precomputed chunks (``metadata @> {"rag_strategy": ...}``), only for
the collection's own tenant, and expired chunks do not count.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.rag.contracts import RAGStrategy
from app.rag.gateway import _probe_precomputed_indexes

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_DIM = 1024
_BOTH = (RAGStrategy.RAPTOR, RAGStrategy.AGENTIC_CHUNKING)


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
            "VALUES (:id, :tid, :id, :dim)"
        ),
        {"id": cid, "tid": tenant, "dim": _DIM},
    )
    return cid


async def _chunk(
    s: Any, *, tenant: str, collection: str, metadata: dict[str, Any], expired: bool = False
) -> None:
    await s.execute(
        text(
            f"INSERT INTO knowledge_chunks_{_DIM} "
            "(tenant_id, collection_id, document_id, chunk_index, content, content_hash, "
            " embedding, metadata, expires_at) "
            "VALUES (:tid, :cid, :doc, 0, 'summary', :hash, CAST(:emb AS vector), "
            " CAST(:meta AS jsonb), CASE WHEN :expired THEN now() - interval '1 day' END)"
        ),
        {
            "tid": tenant,
            "cid": collection,
            "doc": uuid.uuid4().hex,
            "hash": uuid.uuid4().hex,
            "emb": str([0.0] * (_DIM - 1) + [1.0]),
            "meta": json.dumps(metadata),
            "expired": expired,
        },
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world(pg_url: str) -> AsyncIterator[dict[str, Any]]:
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s, s.begin():
        tenant, other = await _tenant(s), await _tenant(s)
        plain = await _collection(s, tenant)
        await _chunk(s, tenant=tenant, collection=plain, metadata={"source": "a.md"})
        raptor = await _collection(s, tenant)
        await _chunk(s, tenant=tenant, collection=raptor, metadata={"rag_strategy": "raptor"})
        both = await _collection(s, tenant)
        await _chunk(s, tenant=tenant, collection=both, metadata={"rag_strategy": "raptor"})
        await _chunk(
            s,
            tenant=tenant,
            collection=both,
            metadata={"rag_strategy": "agentic_chunking", "is_proposition": True},
        )
        expired = await _collection(s, tenant)
        await _chunk(
            s,
            tenant=tenant,
            collection=expired,
            metadata={"rag_strategy": "raptor"},
            expired=True,
        )
    yield {
        "factory": factory,
        "tenant": tenant,
        "other": other,
        "plain": plain,
        "raptor": raptor,
        "both": both,
        "expired": expired,
    }
    await engine.dispose()


async def _probe(world: dict[str, Any], collection: str, tenant: str | None = None) -> Any:
    facts = await _probe_precomputed_indexes(
        world["factory"], tenant or world["tenant"], world[collection], _BOTH
    )
    return {s: (f.available, f.reason) for s, f in facts.items()}


async def test_unindexed_collection_is_not_ready(world: dict[str, Any]) -> None:
    assert await _probe(world, "plain") == {
        RAGStrategy.RAPTOR: (False, "requires RAPTOR indexing"),
        RAGStrategy.AGENTIC_CHUNKING: (False, "requires agentic-chunking indexing"),
    }


async def test_each_strategy_needs_its_own_index(world: dict[str, Any]) -> None:
    assert await _probe(world, "raptor") == {
        RAGStrategy.RAPTOR: (True, "ready"),
        RAGStrategy.AGENTIC_CHUNKING: (False, "requires agentic-chunking indexing"),
    }
    assert await _probe(world, "both") == {
        RAGStrategy.RAPTOR: (True, "ready"),
        RAGStrategy.AGENTIC_CHUNKING: (True, "ready"),
    }


async def test_expired_index_chunks_do_not_count(world: dict[str, Any]) -> None:
    assert (await _probe(world, "expired"))[RAGStrategy.RAPTOR][0] is False


async def test_another_tenants_collection_is_not_authorized(world: dict[str, Any]) -> None:
    assert await _probe(world, "both", tenant=world["other"]) == {
        RAGStrategy.RAPTOR: (False, "collection_not_authorized"),
        RAGStrategy.AGENTIC_CHUNKING: (False, "collection_not_authorized"),
    }
