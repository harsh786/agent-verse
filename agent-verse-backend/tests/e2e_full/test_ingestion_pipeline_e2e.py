"""e2e_full: document ingestion → pgvector → retrievable by query.

The plan's ingestion flagship: a document ingested through the wired knowledge
API lands in the real pgvector-backed ``KnowledgeStore`` and is retrievable by a
semantic/lexical query — proven against real Postgres+Redis with the app booted
via ``create_app(manage_pools=True)``.

A deterministic ``FakeProvider`` embedder is pinned on ``app.state`` so the vector
leg of hybrid search is exercised without any external embedding provider; the
distinctive query terms also exercise the trigram leg, so the round-trip is
proven whichever leg matches.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.providers.fake import FakeProvider

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

# Distinctive, low-collision content so a match proves *this* document was
# ingested, chunked, stored, and retrieved — not an incidental hit.
_MARKER = "Zephyrine axolotl"
_CONTENT = (
    "The Zephyrine Protocol mandates quarterly axolotl audits across every "
    "regional habitat. Auditors record water temperature, regeneration rate, "
    "and the distinctive Zephyrine axolotl coloration in the compliance ledger."
)


@pytest.fixture
def _fake_embedder(app: Any) -> Any:
    """Pin a deterministic 768-dim embedder for both ingest and search.

    Ingest reads ``app.state.embedder``; search reads the retrieval gateway's
    frozen ``dependencies.embedder``. Collections default to a 768-dim pgvector
    column, so the fake must emit 768-dim vectors.
    """
    import contextlib
    import dataclasses

    fake = FakeProvider(embed_dim=768)
    prev_embedder = getattr(app.state, "embedder", None)
    app.state.embedder = fake

    gw = getattr(app.state, "retrieval_gateway", None)
    prev_deps = None
    if gw is not None and hasattr(gw, "dependencies"):
        prev_deps = gw.dependencies
        with contextlib.suppress(Exception):
            gw.dependencies = dataclasses.replace(gw.dependencies, embedder=fake)
    try:
        yield
    finally:
        app.state.embedder = prev_embedder
        if gw is not None and prev_deps is not None:
            gw.dependencies = prev_deps


async def test_document_ingests_and_is_retrievable(
    tenant_client: Any, _fake_embedder: Any
) -> None:
    # 1. Create a collection.
    coll = await tenant_client.post(
        "/knowledge/collections",
        json={"name": f"e2e-ingest-{uuid.uuid4().hex[:8]}", "description": "ingestion e2e"},
    )
    assert coll.status_code == 201, f"collection create failed: {coll.status_code} {coll.text}"
    collection_id = coll.json()["collection_id"]
    assert collection_id

    # 2. Ingest a document through the wired pipeline.
    ing = await tenant_client.post(
        "/knowledge/ingest",
        json={
            "collection_id": collection_id,
            "source_type": "text",
            "content": _CONTENT,
            "metadata": {"source_file": "zephyrine.txt"},
        },
    )
    assert ing.status_code == 201, f"ingest failed: {ing.status_code} {ing.text}"

    # 3. The document is retrievable by query. threshold=0.0 because the
    # deterministic FakeProvider embedder is not semantically meaningful — this
    # test proves the ingest → pgvector → retrieve round-trip, not ranking
    # quality.
    got = await tenant_client.get(
        "/knowledge/search",
        params={
            "q": _MARKER,
            "collection_id": collection_id,
            "top_k": 5,
            "threshold": 0.0,
        },
    )
    assert got.status_code == 200, f"search failed: {got.status_code} {got.text}"
    hits = got.json()
    assert hits, f"no search hits for {_MARKER!r} — document not retrievable after ingest"
    assert any("axolotl" in (h.get("content") or "").lower() for h in hits), (
        f"retrieved chunks did not contain the ingested content: {hits!r}"
    )


async def test_search_isolated_by_collection(tenant_client: Any, _fake_embedder: Any) -> None:
    """A query against an empty sibling collection must not return the document."""
    a = await tenant_client.post(
        "/knowledge/collections", json={"name": f"e2e-a-{uuid.uuid4().hex[:8]}"}
    )
    b = await tenant_client.post(
        "/knowledge/collections", json={"name": f"e2e-b-{uuid.uuid4().hex[:8]}"}
    )
    coll_a = a.json()["collection_id"]
    coll_b = b.json()["collection_id"]

    ing = await tenant_client.post(
        "/knowledge/ingest",
        json={"collection_id": coll_a, "source_type": "text", "content": _CONTENT},
    )
    assert ing.status_code == 201

    # Query collection B (empty) for the marker at threshold 0.0 — a true
    # "no documents" assertion (not merely filtered out by a high threshold).
    got_b = await tenant_client.get(
        "/knowledge/search",
        params={"q": _MARKER, "collection_id": coll_b, "top_k": 5, "threshold": 0.0},
    )
    assert got_b.status_code == 200
    assert got_b.json() == [], "document leaked across collections"
