"""Lexical retrieval legs on a real Postgres (P2 live findings).

P2-1: the full-text leg used ``plainto_tsquery``, which ANDs every term of the
query. A long natural-language step ("calculate the total H1 diesel cost in
INR ...") never has every term in one chunk, so the leg returned 0 hits on
every retrieval of GOAL-MULTISTEP-RAG and the fact was never retrieved.

Runs on the migrated testcontainer (real ``to_tsvector`` / GIN / pg_trgm).
"""

from __future__ import annotations

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


def _unit(i: int) -> list[float]:
    """A unit vector along axis ``i`` (orthogonal to every other axis)."""
    vec = [0.0] * _DIM
    vec[i] = 1.0
    return vec


def _towards(query_axis: int, own_axis: int, cos: float) -> list[float]:
    """A unit vector whose cosine similarity to ``_unit(query_axis)`` is ``cos``."""
    vec = [0.0] * _DIM
    vec[query_axis] = cos
    vec[own_axis] = math.sqrt(1.0 - cos * cos)
    return vec


_QUERY_AXIS = 0

# GOAL-MULTISTEP-RAG: the fleet-and-fuel workbook row that holds the answer,
# among shipment-ledger rows that crowd the vector leg.
_FUEL_TOTAL = (
    "Sheet H1 summary | period=Total H1 | vessels=14 | diesel_litres=268600 | "
    "diesel_cost_inr=24979830 | avg_price_inr_per_litre=93.0"
)
_LEDGER = [
    f"Shipment ledger row {i} | consignee=Harbour Freight {i} | status=delivered | "
    f"container=MSKU{4100 + i} | weight_t={10 + i}"
    for i in range(6)
] + ["Port cost centre allocation for berth maintenance, reviewed quarterly by finance"]
_LONG_STEP = (
    "Step 2: Using the fleet and fuel workbook, calculate the total H1 diesel cost in INR "
    "across all vessels and report the exact figure with its source"
)


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


async def _chunk(
    s: Any, *, tenant: str, collection: str, content: str, embedding: list[float]
) -> str:
    row = await s.execute(
        text(
            f"INSERT INTO knowledge_chunks_{_DIM} "
            "(tenant_id, collection_id, document_id, chunk_index, content, content_hash, "
            " embedding) "
            "VALUES (:tid, :cid, :doc, 0, :content, :hash, CAST(:emb AS vector)) "
            "RETURNING id"
        ),
        {
            "tid": tenant,
            "cid": collection,
            "doc": uuid.uuid4().hex,
            "content": content,
            "hash": uuid.uuid4().hex,
            "emb": str(embedding),
        },
    )
    return str(row.scalar_one())


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world(pg_url: str) -> AsyncIterator[dict[str, Any]]:
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s, s.begin():
        tenant = await _tenant(s)
        fuel = await _collection(s, tenant)
        # The ledger rows sit closer to the query vector than the answer row.
        for i, content in enumerate(_LEDGER):
            await _chunk(
                s,
                tenant=tenant,
                collection=fuel,
                content=content,
                embedding=_towards(_QUERY_AXIS, 10 + i, 0.9 - 0.01 * i),
            )
        fuel_total = await _chunk(
            s,
            tenant=tenant,
            collection=fuel,
            content=_FUEL_TOTAL,
            embedding=_towards(_QUERY_AXIS, 30, 0.5),
        )
    yield {"factory": factory, "tenant": tenant, "fuel": fuel, "fuel_total": fuel_total}
    await engine.dispose()


async def _search(
    world: dict[str, Any],
    collection: str,
    query: str,
    *,
    mode: str = "hybrid",
    top_k: int = 3,
    evidence: list[dict[str, Any]] | None = None,
) -> list[Any]:
    async with world["factory"]() as s, s.begin():
        await s.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": world["tenant"]}
        )
        return await hybrid_search(
            s,
            query=query,
            query_embedding=_unit(_QUERY_AXIS),
            collection_id=collection,
            top_k=top_k,
            retrieval_mode=mode,
            embedding_dim=_DIM,
            strict=True,
            evidence=evidence,
        )


def _leg(evidence: list[dict[str, Any]], component: str) -> dict[str, Any]:
    return next(item for item in evidence if item["component"] == component)


async def test_fts_leg_returns_hits_for_a_long_natural_language_step(
    world: dict[str, Any],
) -> None:
    evidence: list[dict[str, Any]] = []
    await _search(world, world["fuel"], _LONG_STEP, mode="lexical", evidence=evidence)
    fts = _leg(evidence, "fts")
    assert fts["result_count"] > 0, "a long query must not AND every term into 0 FTS hits"
    best = max(fts["component_scores"].items(), key=lambda item: item[1])[0]
    assert best == world["fuel_total"], fts["component_scores"]


async def test_long_step_retrieves_the_answer_row_in_top_3_despite_vector_crowding(
    world: dict[str, Any],
) -> None:
    results = await _search(world, world["fuel"], _LONG_STEP, top_k=3)
    ids = [r.chunk_id for r in results]
    assert world["fuel_total"] in ids, [r.content[:40] for r in results]
    hit = next(r for r in results if r.chunk_id == world["fuel_total"])
    assert "fts" in hit.retrieval_legs


async def test_short_query_still_prefers_chunks_matching_every_term(
    world: dict[str, Any],
) -> None:
    evidence: list[dict[str, Any]] = []
    await _search(world, world["fuel"], "diesel cost", mode="lexical", evidence=evidence)
    scores = _leg(evidence, "fts")["component_scores"]
    assert len(scores) == 2, "OR semantics: the cost-only chunk is a candidate too"
    best = max(scores.items(), key=lambda item: item[1])[0]
    assert best == world["fuel_total"], "a chunk with every term outranks a partial match"
