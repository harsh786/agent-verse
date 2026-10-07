"""Hybrid search on a real Postgres serves a twice-indexed document once.

A collection that two Sources of one upstream MongoDB collection fed (before the
duplicate registration was refused) holds each postmortem twice: two chunk rows
with the same ``source_url`` and text, different ``source_id``. ``hybrid_search``
collapses them before its top-k cut, so the duplicate neither appears twice nor
pushes the next-best passage out of the results.

Runs on the migrated testcontainer (``pg_url``).
"""

from __future__ import annotations

import json
import math
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
_PM = (
    "PM-2026-014 settlement webhook outage. Root cause: the expired TLS certificate on "
    "the settlement webhook endpoint; payments queued for 47 minutes."
)
_RUNBOOK = (
    "Runbook: rotate the settlement webhook TLS certificate before expiry; the outage "
    "postmortem PM-2026-014 lists the follow-up actions."
)
_URL_1 = "mongodb://mongo-a.example.com:27017,mongo-b.example.com:27017/ops/postmortems/PM-2026-014"
_URL_2 = "mongodb://MONGO-B.example.com:27017,mongo-a.example.com:27017/ops/postmortems/PM-2026-014"


def _towards(own_axis: int, cos: float) -> list[float]:
    vec = [0.0] * _DIM
    vec[0] = cos
    vec[own_axis] = math.sqrt(1.0 - cos * cos)
    return vec


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world(pg_url: str) -> AsyncIterator[dict[str, Any]]:
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant, collection = uuid.uuid4().hex, uuid.uuid4().hex
    async with factory() as s, s.begin():
        await s.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:id, 'T', :e)"),
            {"id": tenant, "e": f"{tenant}@example.test"},
        )
        await s.execute(
            text(
                "INSERT INTO knowledge_collections (id, tenant_id, name, embedding_dim) "
                "VALUES (:id, :tid, :id, :dim)"
            ),
            {"id": collection, "tid": tenant, "dim": _DIM},
        )
        rows = [
            (_PM, _URL_1, "src-first", _towards(1, 0.95)),
            (_PM, _URL_2, "src-dup", _towards(2, 0.94)),  # the same passage, re-indexed
            (_RUNBOOK, "https://wiki.example.com/runbooks/tls", "src-wiki", _towards(3, 0.80)),
        ]
        for content, url, source, embedding in rows:
            await s.execute(
                text(
                    f"INSERT INTO knowledge_chunks_{_DIM} "
                    "(tenant_id, collection_id, document_id, chunk_index, content, "
                    " content_hash, embedding, metadata) "
                    "VALUES (:tid, :cid, :doc, 0, :content, :hash, CAST(:emb AS vector), "
                    " CAST(:meta AS jsonb))"
                ),
                {
                    "tid": tenant,
                    "cid": collection,
                    "doc": uuid.uuid4().hex,
                    "content": content,
                    "hash": uuid.uuid4().hex,
                    "emb": str(embedding),
                    "meta": json.dumps({"source_url": url, "source_id": source}),
                },
            )
    yield {"factory": factory, "tenant": tenant, "collection": collection}
    await engine.dispose()


async def test_duplicate_source_url_is_returned_once_and_top_k_stays_full(
    world: dict[str, Any],
) -> None:
    query_vec = [0.0] * _DIM
    query_vec[0] = 1.0
    async with world["factory"]() as s, s.begin():
        await s.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": world["tenant"]}
        )
        results = await hybrid_search(
            s,
            query="settlement webhook outage expired TLS certificate root cause",
            query_embedding=query_vec,
            collection_id=world["collection"],
            top_k=2,
            retrieval_mode="hybrid",
            embedding_dim=_DIM,
            strict=True,
        )
    urls = [r.source_metadata.get("source_url") for r in results]
    pm_hits = [r for r in results if "PM-2026-014" in str(r.source_metadata.get("source_url"))]
    assert len(pm_hits) == 1, urls
    assert pm_hits[0].source_metadata["source_id"] in {"src-first", "src-dup"}
    # The duplicate did not push the next passage out of top_k.
    assert len(results) == 2
    assert "https://wiki.example.com/runbooks/tls" in urls
