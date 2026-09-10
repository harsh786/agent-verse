"""WS-13 e2e: RPA→KB + OCR→KB converge on ONE knowledge store (real infra).

Boots the wired app (session conftest) against real Postgres+pgvector and drives
the two standalone convergence paths over HTTP:

* **RPA** — ``POST /knowledge/ingest/rpa-url`` scrapes a page through the REAL
  ``RPAExecutor`` (the one reachable scraper; the duplicate ad-hoc Playwright
  block is gone) and indexes it with ``source_type=rpa-web`` provenance.
* **OCR** — ``POST /ocr/extract`` with ``persist_to_kb`` indexes the extracted
  text with ``source_type=ocr`` provenance.

Then proves both are retrievable from the SAME pgvector store with correct
provenance, a query returns hits from each, and re-adding the SAME content
dedups via ``exists_by_hash`` (cross-source, one store).

The raw socket fetch (httpx) and the OCR binary are stubbed to fixed content so
the test is deterministic and needs no external egress / native OCR engine —
everything downstream (executor dispatch, report assembly, chunking, embedding,
pgvector write, hybrid retrieval, dedup) runs for real.
"""

from __future__ import annotations

import base64
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.providers.fake import FakeProvider
from app.rag.models import KnowledgeCollection
from app.tenancy.context import PlanTier, TenantContext

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_EMBED_DIM = 768
_RPA_MARKER = "AgentVerse quarterly revenue reached forty two million dollars"
_OCR_MARKER = "Invoice total due one thousand two hundred thirty four dollars"


def _ocr_result() -> dict[str, Any]:
    return {
        "raw_text": f"{_OCR_MARKER}. Paid via wire transfer.",
        "document_type": "invoice",
        "fields": {},
        "engine_used": "tesseract",
        "overall_confidence": 0.9,
        "page_count": 1,
    }


async def test_kb_convergence_rpa_and_ocr_e2e(app: Any, client: Any, monkeypatch: Any) -> None:
    kb = app.state.knowledge_store
    assert kb._db is not None, "e2e must run against the DB-backed KnowledgeStore"

    # Sign up a tenant; use its api key for HTTP + tenant_id for direct queries.
    email = f"ws13-conv-{uuid.uuid4().hex[:12]}@example.com"
    signup = await client.post("/tenants/signup", json={"name": "WS13 Conv", "email": email})
    assert signup.status_code == 201, signup.text
    api_key = signup.json()["api_key"]
    tenant_id = signup.json()["tenant_id"]
    tenant_ctx = TenantContext(tenant_id=tenant_id, api_key_id="e2e", plan=PlanTier.FREE)
    headers = {"X-API-Key": api_key}

    # A single collection all sources converge on.
    collection_id = uuid.uuid4().hex
    await kb.create_collection_async(
        KnowledgeCollection(name="WS13", collection_id=collection_id, embedder="fake"),
        tenant_ctx=tenant_ctx,
    )

    # The app has no API keys in e2e → wire a deterministic FakeProvider embedder
    # so the HTTP ingest paths can embed (matches the collection's fake dim).
    # monkeypatch auto-restores app.state.embedder after this test — without it the
    # fake embedder leaks into later e2e tests in the shared session app (it ran
    # before test_org_mission_hitl and broke its goal's hybrid retrieval).
    fake = FakeProvider(embed_dim=_EMBED_DIM)
    monkeypatch.setattr(app.state, "embedder", fake, raising=False)

    # ── RPA→KB via the REAL RPAExecutor path (httpx socket fetch stubbed) ──
    with patch(
        "app.rpa.executor.RPAExecutor._http_fetch_text",
        AsyncMock(return_value=(f"{_RPA_MARKER}.", "Report")),
    ):
        rpa_resp = await client.post(
            "/knowledge/ingest/rpa-url",
            headers=headers,
            json={"collection_id": collection_id, "urls": ["https://example.com/q4"]},
        )
    assert rpa_resp.status_code == 201, rpa_resp.text
    rpa_body = rpa_resp.json()
    assert rpa_body["scraper"] == "rpa-executor"
    assert rpa_body["total_chunks_ingested"] >= 1
    rpa_hash = rpa_body["results"][0]["content_hash"]

    # ── OCR→KB via standalone /ocr/extract (OCR engine stubbed) ──
    with patch("app.api.ocr._tool.execute", AsyncMock(return_value=_ocr_result())):
        ocr_resp = await client.post(
            "/ocr/extract",
            headers=headers,
            json={
                "image_base64": base64.b64encode(b"fake-image").decode(),
                "persist_to_kb": True,
                "collection_id": collection_id,
            },
        )
    assert ocr_resp.status_code == 200, ocr_resp.text
    ocr_body = ocr_resp.json()
    assert ocr_body["kb_persisted"] is True
    assert ocr_body["kb_deduplicated"] is False
    assert ocr_body["kb_chunks_ingested"] >= 1

    # ── Both sources are retrievable from the ONE pgvector store ──
    rpa_q = (await fake.embed_batch([_RPA_MARKER]))[0]
    rpa_hits = await kb.hybrid_search_db(_RPA_MARKER, rpa_q, collection_id, tenant_ctx, top_k=5)
    assert rpa_hits, "RPA-scraped content not retrievable"
    rpa_joined = " ".join(h.content for h in rpa_hits)
    assert "forty two million" in rpa_joined
    assert any(h.metadata.get("source_type") == "rpa-web" for h in rpa_hits)
    assert any(h.metadata.get("doc_content_hash") for h in rpa_hits)

    ocr_q = (await fake.embed_batch([_OCR_MARKER]))[0]
    ocr_hits = await kb.hybrid_search_db(_OCR_MARKER, ocr_q, collection_id, tenant_ctx, top_k=5)
    assert ocr_hits, "OCR content not retrievable"
    ocr_joined = " ".join(h.content for h in ocr_hits)
    assert "one thousand two hundred" in ocr_joined
    assert any(h.metadata.get("source_type") == "ocr" for h in ocr_hits)
    assert any(h.metadata.get("ocr_used") == "true" for h in ocr_hits)

    # ── Cross-source dedup on re-add (both paths skip via exists_by_hash) ──
    assert await kb.exists_by_hash(
        content_hash=rpa_hash, tenant_id=tenant_id, collection_id=collection_id
    )
    with patch(
        "app.rpa.executor.RPAExecutor._http_fetch_text",
        AsyncMock(return_value=(f"{_RPA_MARKER}.", "Report")),
    ):
        rpa_again = await client.post(
            "/knowledge/ingest/rpa-url",
            headers=headers,
            json={"collection_id": collection_id, "urls": ["https://example.com/q4"]},
        )
    assert rpa_again.json()["results"][0]["deduplicated"] is True
    assert rpa_again.json()["total_chunks_ingested"] == 0

    with patch("app.api.ocr._tool.execute", AsyncMock(return_value=_ocr_result())):
        ocr_again = await client.post(
            "/ocr/extract",
            headers=headers,
            json={
                "image_base64": base64.b64encode(b"fake-image").decode(),
                "persist_to_kb": True,
                "collection_id": collection_id,
            },
        )
    assert ocr_again.json()["kb_deduplicated"] is True
    assert ocr_again.json()["kb_chunks_ingested"] == 0

    # ── RLS isolation: another tenant cannot see either hash ──
    assert not await kb.exists_by_hash(content_hash=rpa_hash, tenant_id="some-other-tenant")
