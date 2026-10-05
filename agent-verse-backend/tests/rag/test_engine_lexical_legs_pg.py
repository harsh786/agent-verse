"""Lexical retrieval legs on a real Postgres (P2 live findings).

P2-1: the full-text leg used ``plainto_tsquery``, which ANDs every term of the
query. A long natural-language step ("calculate the total H1 diesel cost in
INR ...") never has every term in one chunk, so the leg returned 0 hits on
every retrieval of GOAL-MULTISTEP-RAG and the fact was never retrieved.

P2-3: a PDF tariff table chunk holding "Out-of-gauge lift per lift 18,600"
ranked 4th of 6 even for the exact phrase, and an exact job code "TJ-5531"
lost to a near-identical code: the trigram leg compared the query with the
WHOLE chunk (``similarity`` < 0.3 for any long chunk), the BM25 tokenizer split
codes into parts only, and nothing rewarded an exact phrase / identifier match.

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


# pdf-tables (P1a open item): one page mixing prose with an 11-row table.
_TARIFF_PAGE = (
    "Saltmarsh Terminal - Schedule of Tariffs 2027. The terminal operator publishes these "
    "rates for the calendar year; they apply to every vessel call and every consignee and "
    "supersede earlier circulars. Rates exclude taxes. Charges accrue from gate-in until "
    "gate-out and are billed per call. Disputes go to the terminal commercial desk within "
    "seven days of the invoice. Service | Unit | Rate (INR) | Free period | Notes\n"
    "Container handling 20ft | per box | 6,450 | - | laden\n"
    "Container handling 40ft | per box | 9,800 | - | laden\n"
    "Reefer plug-in | per day | 2,150 | 1 day | includes monitoring\n"
    "Storage 20ft | per day | 310 | 5 days | after free period\n"
    "Storage 40ft | per day | 540 | 5 days | after free period\n"
    "Hazardous surcharge | per box | 3,900 | - | IMDG class 1-9\n"
    "Weighment (VGM) | per box | 720 | - | SOLAS\n"
    "Customs examination | per box | 1,850 | - | on request\n"
    "Out-of-gauge lift | per lift | 18,600 | - | pre-booked\n"
    "Shut-out cancellation | per box | 4,200 | - | within 24 hours"
)
# Shorter chunks that sit closer to the query vector than the table page.
_TARIFF_DISTRACTORS = [
    "Heavy lift operations need a crane plan; the lift supervisor signs off every lift.",
    "Gauge readings on the quay crane are checked daily; how much load a crane takes "
    "depends on the outreach.",
    "The cost of berth delays is set out in Table 2 of the tariff.",
    "Out of hours gate service costs extra and must be booked in advance.",
    "Rail gauge at the inland depot is standard; wagons are loaded out of the yard.",
]
# Low-quality receipt (P1a): the right job code and a near-identical one.
_RECEIPTS = {
    "TJ-5531": "RECEIPT - HERON TUGS. Towage job: TJ-5531. Amount: INR 1,86,000. Paid.",
    "TJ-5534": "RECEIPT - HERON TUGS. Towage job: TJ-5534. Amount: INR 7,86,000. Paid.",
    "TJ-5513": "Heron Tugs towage job TJ-5513 was cancelled; no receipt was issued.",
}


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
        tariff = await _collection(s, tenant)
        for i, content in enumerate(_TARIFF_DISTRACTORS):
            await _chunk(
                s,
                tenant=tenant,
                collection=tariff,
                content=content,
                embedding=_towards(_QUERY_AXIS, 40 + i, 0.8 - 0.05 * i),
            )
        # Ranked 4th of 6 by the vector leg, as in the live probe.
        tariff_page = await _chunk(
            s,
            tenant=tenant,
            collection=tariff,
            content=_TARIFF_PAGE,
            embedding=_towards(_QUERY_AXIS, 50, 0.68),
        )
        receipts = await _collection(s, tenant)
        receipt_ids: dict[str, str] = {}
        # The wrong codes sit closer to the query vector than the right one.
        for i, (code, content) in enumerate(sorted(_RECEIPTS.items(), reverse=True)):
            receipt_ids[code] = await _chunk(
                s,
                tenant=tenant,
                collection=receipts,
                content=content,
                embedding=_towards(_QUERY_AXIS, 60 + i, 0.9 - 0.1 * i),
            )
    yield {
        "factory": factory,
        "tenant": tenant,
        "fuel": fuel,
        "fuel_total": fuel_total,
        "tariff": tariff,
        "tariff_page": tariff_page,
        "receipts": receipts,
        "receipt_ids": receipt_ids,
    }
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


@pytest.mark.parametrize(
    "query",
    [
        "out-of-gauge lift",
        "Out-of-gauge lift per lift",
        "How much does an out-of-gauge lift cost?",
    ],
)
async def test_table_row_query_ranks_the_table_chunk_first(
    world: dict[str, Any], query: str
) -> None:
    results = await _search(world, world["tariff"], query, top_k=5)
    assert results[0].chunk_id == world["tariff_page"], [
        (r.content[:30], r.retrieval_legs) for r in results
    ]


async def test_trigram_leg_scores_a_phrase_inside_a_long_chunk(world: dict[str, Any]) -> None:
    evidence: list[dict[str, Any]] = []
    await _search(world, world["tariff"], "out-of-gauge lift", mode="lexical", evidence=evidence)
    trigram = _leg(evidence, "trigram")["component_scores"]
    assert trigram.get(world["tariff_page"], 0.0) > 0.5, trigram


@pytest.mark.parametrize("query", ["TJ-5531", "receipt for towage job TJ-5531", "tj-5531"])
async def test_exact_identifier_ranks_its_chunk_first(world: dict[str, Any], query: str) -> None:
    results = await _search(world, world["receipts"], query, top_k=3)
    assert results[0].chunk_id == world["receipt_ids"]["TJ-5531"], [
        (r.content[:50], r.retrieval_legs, r.component_scores) for r in results
    ]
    assert "phrase" in results[0].retrieval_legs


async def test_identifier_spelled_without_the_hyphen_still_matches(
    world: dict[str, Any],
) -> None:
    results = await _search(world, world["receipts"], "TJ 5531", mode="lexical", top_k=3)
    assert results[0].chunk_id == world["receipt_ids"]["TJ-5531"]


async def test_like_and_regex_metacharacters_in_a_query_are_inert(world: dict[str, Any]) -> None:
    # Strict mode: a leg error would raise. Metacharacters are matched literally.
    for query in ("100%_sure", "a.b*c (x) [y] \\ TJ-55.31", "%%", "_"):
        await _search(world, world["receipts"], query, mode="lexical")


async def test_identifier_match_is_word_bounded(world: dict[str, Any]) -> None:
    evidence: list[dict[str, Any]] = []
    await _search(world, world["receipts"], "TJ-553", mode="lexical", evidence=evidence)
    assert _leg(evidence, "phrase")["result_count"] == 0, "TJ-553 must not match TJ-5531"
