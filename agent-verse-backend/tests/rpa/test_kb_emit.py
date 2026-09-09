"""WS-13: RPA scrape → KB-ready chunks + the executor scrape_to_kb bridge.

Proves the ONE reachable RPA→KB path builds provenance-tagged chunks with a
shared ``doc_content_hash`` and that ``RPAExecutor.scrape_to_kb`` persists them
with cross-source dedup via ``exists_by_hash``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.rag.models import KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.rpa.executor import RPAExecutor
from app.rpa.kb_emit import scrape_url_to_chunks
from app.tenancy.context import PlanTier, TenantContext

_HTML = (
    "<html><head><title>Doc</title></head><body>"
    "<p>Knowledge graph convergence proof text.</p></body></html>"
)


def _ctx(tid: str) -> TenantContext:
    return TenantContext(tenant_id=tid, api_key_id="k", plan=PlanTier.FREE)


def _mock_httpx(text: str) -> Any:
    resp = MagicMock()
    resp.text = text
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=ctx)
    ctx.__aexit__ = AsyncMock(return_value=False)
    ctx.get = AsyncMock(return_value=resp)
    return MagicMock(return_value=ctx)


def _sim_executor() -> RPAExecutor:
    ex = RPAExecutor()
    ex._playwright_available = False
    return ex


@pytest.mark.asyncio
async def test_scrape_url_to_chunks_provenance() -> None:
    ex = _sim_executor()
    with patch("httpx.AsyncClient", _mock_httpx(_HTML)):
        page = await scrape_url_to_chunks(
            ex, url="https://example.com/doc", source_type="rpa-web"
        )
    assert page.content_hash
    assert "convergence proof" in page.content
    assert page.chunks
    meta = page.chunks[0]["metadata"]
    assert meta["source_type"] == "rpa-web"
    assert meta["source_url"] == "https://example.com/doc"
    assert meta["doc_content_hash"] == page.content_hash
    assert meta["ingestion_provenance"] == "rpa"


@pytest.mark.asyncio
async def test_scrape_to_kb_persists_and_dedups() -> None:
    ex = _sim_executor()
    store = KnowledgeStore()
    ctx = _ctx("t1")
    store.create_collection(
        KnowledgeCollection(name="C", collection_id="c1"), tenant_ctx=ctx
    )

    async def _fake_embed(texts: list[str], embedder: object) -> list[list[float]]:
        return [[0.1] * 768 for _ in texts]

    with (
        patch("httpx.AsyncClient", _mock_httpx(_HTML)),
        patch("app.api.knowledge._embed_texts_or_http", _fake_embed),
    ):
        first = await ex.scrape_to_kb(
            url="https://example.com/doc",
            knowledge_store=store,
            embedder=object(),
            collection_id="c1",
            tenant_ctx=ctx,
            source_type="rpa",
        )
        # Re-scrape the SAME content → deduped against the one store.
        second = await ex.scrape_to_kb(
            url="https://example.com/doc",
            knowledge_store=store,
            embedder=object(),
            collection_id="c1",
            tenant_ctx=ctx,
            source_type="rpa",
        )

    assert first["chunks_ingested"] >= 1
    assert first["deduplicated"] is False
    assert second["deduplicated"] is True
    assert second["chunks_ingested"] == 0
    # exists_by_hash confirms the content is present, RLS-scoped.
    assert await store.exists_by_hash(
        content_hash=first["content_hash"], tenant_id="t1", collection_id="c1"
    )
    assert not await store.exists_by_hash(
        content_hash=first["content_hash"], tenant_id="other"
    )
