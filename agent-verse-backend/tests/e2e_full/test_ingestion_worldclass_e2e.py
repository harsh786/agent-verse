"""WS-12 — world-class ingestion e2e against real Postgres+pgvector.

Boots the wired app (session conftest) and drives the REAL ``IngestionPipeline``
against the DB-backed ``KnowledgeStore`` on ``app.state`` — the same store the
lifespan swap installs. Proves, on real infra:

* real PDF + CSV + image bytes flow through the pipeline (no container garbage),
* correct chunker + embeddings write real pgvector rows,
* indexed content is retrievable by a hybrid query,
* ``knowledge.updated`` is published per indexed document, and
* **dedup fires on re-ingest** (the WS-12 top fix — ``exists_by_hash``).

Gated behind ``e2e_full`` + Docker; collectable everywhere.
"""

from __future__ import annotations

import uuid

import pytest

from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from app.providers.fake import FakeProvider
from app.rag.models import KnowledgeCollection
from app.tenancy.context import PlanTier, TenantContext

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_EMBED_DIM = 768


class _CapturingBus:
    """Minimal event bus that records knowledge.updated publishes."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, channel: str, payload: dict) -> None:
        self.events.append((channel, payload))


def _minimal_pdf(text: str) -> bytes:
    """A tiny but valid single-page PDF with one text-showing operator."""
    body = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        b"4 0 obj<</Length 60>>stream\n"
        b"BT /F1 18 Tf 72 700 Td (" + text.encode() + b") Tj ET\n"
        b"endstream endobj\n"
        b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        b"trailer<</Root 1 0 R>>\n%%EOF"
    )
    return body


def _tiny_png() -> bytes:
    """A 1x1 transparent PNG (valid image bytes)."""
    import base64

    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )


@pytest.fixture(scope="session")
async def _seeded_tenant(client: object) -> str:
    email = f"ws12-ingest-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post(  # type: ignore[attr-defined]
        "/tenants/signup", json={"name": "WS12 Ingest", "email": email}
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["tenant_id"])


async def test_ingestion_worldclass_e2e(app: object, _seeded_tenant: str) -> None:
    tenant_id = _seeded_tenant
    tenant_ctx = TenantContext(tenant_id=tenant_id, api_key_id="e2e", plan=PlanTier.FREE)
    kb = app.state.knowledge_store  # type: ignore[attr-defined]
    assert kb._db is not None, "e2e must run against the DB-backed KnowledgeStore"

    collection_id = uuid.uuid4().hex
    await kb.create_collection_async(
        KnowledgeCollection(name="WS12", collection_id=collection_id, embedder="fake"),
        tenant_ctx=tenant_ctx,
    )

    bus = _CapturingBus()
    pipeline = IngestionPipeline(
        knowledge_store=kb, embedder=FakeProvider(embed_dim=_EMBED_DIM), event_bus=bus
    )

    def _config() -> SourceConfig:
        return SourceConfig(
            source_id="e2e-src",
            tenant_id=tenant_id,
            name="WS12 e2e",
            family=SourceFamily.DOCUMENT_STORE,
            source_type="e2e",
            collection_id=collection_id,
            min_quality_score=0.0,
        )

    csv_bytes = (
        b"name,role,city\n"
        b"Ada Lovelace,mathematician,London\n"
        b"Alan Turing,computer scientist,Manchester\n"
        b"Grace Hopper,rear admiral,New York\n"
    )
    csv_doc = RawDocument(
        doc_id="csv-1", source_id="e2e-src", tenant_id=tenant_id,
        content=csv_bytes, content_type="text/csv", title="people.csv",
    )
    csv_result = await pipeline.ingest(csv_doc, _config())
    assert csv_result.status == "indexed", f"CSV not indexed: {csv_result.error}"
    assert csv_result.chunks_created > 0

    # PDF + image flow through without leaking container garbage; degradation is
    # recorded honestly when an optional binary/OCR engine is unavailable.
    pdf_doc = RawDocument(
        doc_id="pdf-1", source_id="e2e-src", tenant_id=tenant_id,
        content=_minimal_pdf("World class ingestion proof text"),
        content_type="application/pdf", title="proof.pdf",
    )
    pdf_result = await pipeline.ingest(pdf_doc, _config())
    assert pdf_result.status in ("indexed", "skipped"), pdf_result.error
    assert "PK" not in (pipeline.last_parsed_text or "")

    img_doc = RawDocument(
        doc_id="img-1", source_id="e2e-src", tenant_id=tenant_id,
        content=_tiny_png(), content_type="image/png", title="pixel.png",
    )
    img_result = await pipeline.ingest(img_doc, _config())
    assert img_result.status in ("indexed", "skipped"), img_result.error

    # knowledge.updated published for the indexed CSV document.
    assert any(ch == "knowledge.updated" for ch, _ in bus.events)

    # Real pgvector rows are retrievable by a hybrid query.
    q_emb = (await FakeProvider(embed_dim=_EMBED_DIM).embed_batch(["Ada Lovelace"]))[0]
    hits = await kb.hybrid_search_db(
        "Ada Lovelace", q_emb, collection_id, tenant_ctx, top_k=5
    )
    assert hits, "indexed CSV content was not retrievable from pgvector"
    joined = " ".join(h.content for h in hits)
    assert "Ada Lovelace" in joined

    # Retrieved chunks carry the document-level hash + provenance for dedup.
    assert any(h.metadata.get("doc_content_hash") for h in hits)

    # ── The WS-12 top fix: re-ingesting the SAME CSV dedups (skip), not re-index.
    reingest = await pipeline.ingest(
        RawDocument(
            doc_id="csv-1-again", source_id="e2e-src", tenant_id=tenant_id,
            content=csv_bytes, content_type="text/csv", title="people.csv",
        ),
        _config(),
    )
    assert reingest.status == "skipped"
    assert reingest.skip_reason == "dedup"

    # exists_by_hash directly confirms the content hash is present, RLS-scoped.
    doc_hash = csv_doc.compute_hash()
    assert await kb.exists_by_hash(
        content_hash=doc_hash, tenant_id=tenant_id, collection_id=collection_id
    )
    assert not await kb.exists_by_hash(
        content_hash=doc_hash, tenant_id="some-other-tenant"
    )
