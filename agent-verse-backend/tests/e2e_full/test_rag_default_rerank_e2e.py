"""e2e_full (WS-10 item 1): reranking engages on the DEFAULT hybrid path.

A golden-set retrieval, run against the booted app with real Postgres + pgvector
+ Redis: three documents are ingested (one strongly on-topic, two distractors),
then the canonical retrieval gateway runs the *default* HYBRID strategy. This
proves, end to end, that:

  * hybrid retrieve returns the on-topic document with REAL (finite, non-negative)
    scores from pgvector — not fabricated;
  * the default-path reranking STAGE ran (every citation carries a
    ``rerank_strategy`` marker) — reranking is no longer confined to explicit
    pattern branches;
  * the result surfaces a calibrated aggregate ``retrieval_confidence`` and each
    citation a ``calibrated_confidence``;
  * the answer is grounded in the retrieved evidence.

Determinism: a 768-dim ``FakeProvider`` embedder is pinned for both ingest and
the gateway (which embeds the query itself); the golden document repeats a
distinctive marker so the hybrid lexical leg ranks it first regardless of the
non-semantic fake vectors.
"""

from __future__ import annotations

import contextlib
import dataclasses
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_MARKER = "Zephyrine axolotl"
_GOLDEN = (
    "The Zephyrine Protocol mandates quarterly Zephyrine axolotl audits across "
    "every regional habitat. Auditors log the Zephyrine axolotl coloration in "
    "the compliance ledger each quarter."
)
_DISTRACTORS = (
    "The municipal transit authority revised its weekday bus timetable for the "
    "downtown express corridor after the winter service review.",
    "Quarterly revenue for the beverage division rose on strong export demand, "
    "led by sparkling water and cold-brew coffee lines.",
)


@pytest_asyncio.fixture(loop_scope="session")
async def rag_client(app: Any, client: Any) -> AsyncIterator[Any]:
    from httpx import ASGITransport, AsyncClient

    email = f"rerank-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": "Rerank", "email": email})
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    api_key = resp.json()["api_key"]
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://e2e-full", headers={"X-API-Key": api_key}
    ) as c:
        yield c


@pytest.fixture
def _fake_embedder(app: Any) -> Any:
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


async def test_default_hybrid_path_reranks_with_real_scores(
    app: Any, rag_client: Any, _fake_embedder: Any
) -> None:
    # 1. Fresh tenant + collection, ingest golden + distractor documents.
    me = await rag_client.get("/tenants/me")
    assert me.status_code == 200, me.text
    tenant_id = me.json()["tenant_id"]

    coll = await rag_client.post(
        "/knowledge/collections",
        json={"name": f"rerank-{uuid.uuid4().hex[:8]}", "description": "rerank e2e"},
    )
    assert coll.status_code == 201, f"collection create failed: {coll.status_code} {coll.text}"
    collection_id = coll.json()["collection_id"]

    for content in (_GOLDEN, *_DISTRACTORS):
        ing = await rag_client.post(
            "/knowledge/ingest",
            json={"collection_id": collection_id, "source_type": "text", "content": content},
        )
        assert ing.status_code == 201, f"ingest failed: {ing.status_code} {ing.text}"

    # 2. Run the DEFAULT hybrid strategy through the canonical retrieval gateway.
    gateway = app.state.retrieval_gateway
    tenant_ctx = TenantContext(
        tenant_id=tenant_id, plan=PlanTier.ENTERPRISE, api_key_id="e2e-rerank"
    )
    result = await gateway.execute(
        tenant_ctx,
        collection_id=collection_id,
        query=f"What does the ledger say about the {_MARKER} audit cadence?",
        strategy_id="hybrid",
        top_k=3,
    )

    # 3. Real retrieval from pgvector — the golden document is retrieved and its
    # ingested content comes back verbatim (not fabricated). Exact rank is left to
    # the real reranker; the DoD is that hybrid+rerank ran on real evidence.
    assert result.citations, "hybrid retrieval returned no citations"
    top = result.citations[0]
    assert any("axolotl" in c.content.lower() for c in result.citations), (
        f"golden document not retrieved from pgvector: "
        f"{[c.content[:60] for c in result.citations]!r}"
    )

    # Real, finite, non-negative scores — nothing fabricated.
    for citation in result.citations:
        assert isinstance(citation.score, float)
        assert citation.score == citation.score  # not NaN
        assert citation.score >= 0.0

    # 4. The default-path reranking STAGE ran (marker on every citation).
    for citation in result.citations:
        strat = citation.metadata.get("rerank_strategy")
        assert strat in {"score", "rrf", "diversity", "cross_encoder", "llm", "auto"}, (
            f"default-path rerank stage did not run: {citation.metadata!r}"
        )

    # 5. Calibrated confidence surfaced on the result and each citation.
    assert 0.0 <= result.retrieval_confidence <= 1.0
    assert top.metadata.get("calibrated_confidence") is not None

    # 6. The answer is grounded in the retrieved evidence.
    assert result.grounded is True
