"""Short but meaningful pages are indexed; only empty / noise / boilerplate is skipped.

The pipeline used to drop every parsed text under 50 characters as
``empty_content`` (and the quality gate scored it 0.1 < 0.3), so a one-line
policy, a product-code page or a ``tag: urgent`` note was never searchable. The
legacy GitHub / Confluence / PDF ingestors had their own 30-50 char floors.
"""

from __future__ import annotations

import pytest

from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.quality_checks import boilerplate_reason, is_meaningful_text
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from app.providers.fake import FakeProvider
from app.rag.models import KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t1", api_key_id="test", plan=PlanTier.FREE)


def _store() -> KnowledgeStore:
    store = KnowledgeStore()
    store.create_collection(KnowledgeCollection(name="C", collection_id="c1"), tenant_ctx=_CTX)
    return store


def _config() -> SourceConfig:
    return SourceConfig(
        source_id="s1", tenant_id="t1", name="T", family=SourceFamily.WEB,
        source_type="test", collection_id="c1",
    )


def _doc(text: str, doc_id: str = "d1") -> RawDocument:
    return RawDocument(
        doc_id=doc_id, source_id="s1", tenant_id="t1", content=text.encode(),
        content_type="text/plain",
    )


@pytest.mark.parametrize(
    "text",
    [
        "tag: urgent",
        "SKU TJ-5531",
        "Refunds within 30 days.",
        "No pets allowed on site.",
        "ID: A1",
        "是的我们支持退款",
    ],
)
async def test_short_meaningful_page_is_indexed(text: str) -> None:
    store = _store()
    pipeline = IngestionPipeline(knowledge_store=store, embedder=FakeProvider(embed_dim=768))
    result = await pipeline.ingest(_doc(text), _config())
    assert result.status == "indexed", (result.status, result.skip_reason)
    chunks = store._data[("t1", "c1")].chunks
    assert chunks and text in chunks[0].content


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("   \n\t ", "empty"),
        ("..... ---- ****", "noise"),
        ("Loading...", "boilerplate"),
        ("404 Not Found", "boilerplate"),
        ("© 2024 Acme Corp. All rights reserved.", "boilerplate"),
        ("JavaScript is required", "boilerplate"),
    ],
)
async def test_empty_noise_and_boilerplate_are_still_skipped(text: str, reason: str) -> None:
    store = _store()
    pipeline = IngestionPipeline(knowledge_store=store, embedder=FakeProvider(embed_dim=768))
    result = await pipeline.ingest(_doc(text), _config())
    assert result.status == "skipped"
    assert result.skip_reason == "empty_content"
    assert result.metadata["empty_reason"] == reason  # type: ignore[attr-defined]
    assert boilerplate_reason(text) == reason


def test_boilerplate_phrase_inside_real_content_is_kept() -> None:
    assert is_meaningful_text("Page not found errors are logged to the SRE channel.")
    assert is_meaningful_text(
        "All rights reserved for the archive owner; contact legal before reuse. " * 2
    )


def test_pdf_ingestor_keeps_a_one_line_page(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.knowledge.ingestors import pdf_ingestor

    monkeypatch.setattr(
        pdf_ingestor, "extract_pdf_pages",
        lambda content, filename: ["Policy: badges required.", "   ", "...."],
    )
    chunks = pdf_ingestor.PdfIngestor().extract_chunks(content=b"%PDF", filename="p.pdf")
    assert [c["content"] for c in chunks] == ["Policy: badges required."]
