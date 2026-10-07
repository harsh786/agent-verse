"""OI-5 on a real Postgres: the default rerank stage keeps exact matches on top.

The real lexical legs (full-text, trigram, BM25 and P2-3's exact phrase /
identifier leg) run on the migrated testcontainer; the rerank stage then blends
in a cross-encoder. The cross-encoder is a deterministic stand-in with the
ms-marco model's real failure shapes (no model download in tests):

* structured MongoDB-style records: every logit ~ +5.4 .. +6.3 and the record
  holding the asked-for identifier NOT the highest;
* a one-word proper noun ("Hosur") over notes sharing a boilerplate tail: every
  logit ~ -9 .. -10, the only chunk containing the word scored lowest.

Before OI-5 the min-max-normalised cross-encoder outvoted the retrieval
evidence and buried the exact match; now it ranks first.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.rag.engine import hybrid_search
from app.rag.rerank_stage import apply_default_rerank
from tests.rag.test_engine_lexical_legs_pg import (
    _DIM,
    _QUERY_AXIS,
    _chunk,
    _collection,
    _tenant,
    _towards,
    _unit,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_TAIL = (
    " This note is part of the Larkspur depot operations archive. It records routine "
    "observations from the shift and is kept for audit purposes only."
)
_NOTES = [
    "Port berth schedule: the Tuticorin feeder vessel Marisol Dawn berths at 04:30.",
    "The night-shift supervisor for the Bhiwandi annex is Rukmini Desai until December.",
    "Yard safety: forklifts keep 3 m from the rail siding at the Nashik yard.",
    "Gate pass scheme overview: paper passes are issued at the security desk.",
    "Contract renewal for Halvorsen Logistics is due in March.",
    "Cold chain: reefer plugs at bay 7 are being replaced this month.",
    "The Chennai yard bulletin lists the new canteen hours.",
]
_HOSUR = "Hosur yard bulletin: gate pass scheme Orchid-9 replaces paper passes from week 44."


def _record(i: int, notes: str) -> str:
    return (
        f"_id: 66f1a{i:02d}\norder_no: ORD-{i:04d}\ncustomer: Acme {i}\nstatus: shipped\n"
        f"items: widget x2, gadget x1\ntotal: 120.50\ncreated_at: 2026-09-1{i % 10}\n"
        f"notes: {notes}"
    )


_TARGET_NOTE = "customer asked for the RTO-5531 reference on the invoice"
_RECORDS = [_record(i, "leave at the back door") for i in range(7)]
_RECORDS.insert(4, _record(42, _TARGET_NOTE))


def _fake_cross_encode(query: str, documents: list[str], batch_size: int = 32) -> list[float]:
    """ms-marco-shaped logits that cannot tell these candidates apart."""
    scores = []
    for i, doc in enumerate(documents):
        if "order_no:" in doc:
            scores.append(5.36 if _TARGET_NOTE in doc else 6.27 - 0.1 * (i % 5))
        else:
            scores.append(-10.4 if "Hosur" in doc else -9.0 + 0.1 * (i % 7))
    return scores


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def corpus(pg_url: str) -> AsyncIterator[dict[str, Any]]:
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s, s.begin():
        tenant = await _tenant(s)
        orders = await _collection(s, tenant)
        target = ""
        for i, content in enumerate(_RECORDS):
            # The target record sits FARTHEST from the query vector.
            cos = 0.35 if _TARGET_NOTE in content else 0.85 - 0.02 * i
            cid = await _chunk(
                s,
                tenant=tenant,
                collection=orders,
                content=content,
                embedding=_towards(_QUERY_AXIS, 100 + i, cos),
            )
            if _TARGET_NOTE in content:
                target = cid
        bulletins = await _collection(s, tenant)
        for i, note in enumerate(_NOTES):
            await _chunk(
                s,
                tenant=tenant,
                collection=bulletins,
                content=note + _TAIL,
                embedding=_towards(_QUERY_AXIS, 200 + i, 0.8 - 0.03 * i),
            )
        hosur = await _chunk(
            s,
            tenant=tenant,
            collection=bulletins,
            content=_HOSUR + _TAIL,
            embedding=_towards(_QUERY_AXIS, 300, 0.3),
        )
    yield {
        "factory": factory,
        "tenant": tenant,
        "orders": orders,
        "target": target,
        "bulletins": bulletins,
        "hosur": hosur,
    }
    await engine.dispose()


@pytest.fixture(autouse=True)
def _cross_encoder(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _ready(_settings: Any) -> str:
        return "ready"

    async def _fake_cross_encode_async(
        query: str, documents: list[str], *, budget_seconds: float | None = None
    ) -> list[float]:
        return _fake_cross_encode(query, documents)

    monkeypatch.setattr("app.rag.rerank_stage._cross_encoder_status", _ready)
    monkeypatch.setattr("app.rag.cross_encoder.cross_encode", _fake_cross_encode)
    # The async default path awaits the bounded lane (cross_encode_async).
    monkeypatch.setattr("app.rag.cross_encoder.cross_encode_async", _fake_cross_encode_async)


_SETTINGS = SimpleNamespace(
    rag_default_rerank_enabled=True,
    rag_default_rerank_strategy="cross_encoder",
    rag_rerank_warmup_wait_seconds=0.0,
)


async def _retrieve(corpus: dict[str, Any], collection: str, query: str) -> list[Any]:
    async with corpus["factory"]() as s, s.begin():
        await s.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": corpus["tenant"]}
        )
        base = await hybrid_search(
            s,
            query=query,
            query_embedding=_unit(_QUERY_AXIS),
            collection_id=collection,
            top_k=10,
            retrieval_mode="hybrid",
            embedding_dim=_DIM,
            strict=True,
        )
    return await apply_default_rerank(
        base, query=query, query_embedding=_unit(_QUERY_AXIS), settings=_SETTINGS, top_k=10
    )


@pytest.mark.parametrize("query", ["RTO-5531", "which order mentions RTO-5531?", "rto 5531"])
async def test_identifier_record_ranks_first_after_the_cross_encoder(
    corpus: dict[str, Any], query: str
) -> None:
    results = await _retrieve(corpus, corpus["orders"], query)
    assert results[0].chunk_id == corpus["target"], [
        (r.content[-40:], r.retrieval_legs, r.source_metadata.get("ce_score")) for r in results
    ]
    assert results[0].source_metadata.get("rerank_strategy") == "cross_encoder"
    if query != "rto 5531":
        # P2-3's exact-match leg is still what found it.
        assert "phrase" in results[0].retrieval_legs


async def test_one_word_proper_noun_keeps_its_only_lexical_match_first(
    corpus: dict[str, Any],
) -> None:
    results = await _retrieve(corpus, corpus["bulletins"], "Hosur")
    assert results[0].chunk_id == corpus["hosur"], [
        (r.content[:40], r.retrieval_legs, r.score) for r in results
    ]
    assert {"phrase", "fts"} <= set(results[0].retrieval_legs)
    assert results[0].source_metadata.get("pre_rerank_score") is not None


async def test_an_ordinary_question_still_ranks_the_relevant_note_first(
    corpus: dict[str, Any],
) -> None:
    results = await _retrieve(
        corpus, corpus["bulletins"], "which yard replaces paper passes with Orchid-9?"
    )
    assert results[0].chunk_id == corpus["hosur"]
