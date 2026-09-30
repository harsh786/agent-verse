"""KB-29: auto-populated KG nodes are stamped with the ids GraphRAG seeds from.

The ingestion hook stored ``source_id = f"{doc}:{idx}"`` (``idx`` counted over the
de-duplicated chunk list, not the stored ``chunk_index``). GraphRAG seeds its
graph lookup from the retrieved chunks' ids (and document ids), so nothing the
pipeline extracted was ever found. Nodes now carry the id of the indexed chunk
they came from.
"""

from __future__ import annotations

from typing import Any

from app.ingestion.pipeline import IngestionPipeline
from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily
from app.knowledge_graph.extractor import EntityExtractor
from app.knowledge_graph.ingestion_hook import KGIngestionHook
from app.knowledge_graph.store import KnowledgeGraphStore

_TENANT = "kb29-tenant"


class _Store:
    def __init__(self) -> None:
        self.chunks: list[Any] = []

    async def exists_by_hash(self, **_: Any) -> bool:
        return False

    async def ingest_chunks_async(self, chunks: list[Any], **_: Any) -> list[str]:
        self.chunks.extend(chunks)
        return [c.chunk_id for c in chunks]


class _Embedder:
    async def embed(self, request: Any) -> Any:
        from app.providers.base import EmbedResponse

        return EmbedResponse(embeddings=[[0.1] * 8 for _ in request.texts], model="fake")


async def test_pipeline_kg_nodes_carry_the_indexed_chunk_ids() -> None:
    kg = KnowledgeGraphStore()
    store = _Store()
    pipeline = IngestionPipeline(
        knowledge_store=store,
        embedder=_Embedder(),
        kg_hook=KGIngestionHook(store=kg, extractor=EntityExtractor()),
    )
    body = (
        "Alice Johnson leads the ACME platform group in Berlin. "
        "Bob Smith reviews every OpenAI integration the group ships."
    )
    doc = RawDocument(
        doc_id="doc-kb29",
        source_id="src",
        tenant_id=_TENANT,
        content=body.encode(),
        content_type="text/plain",
    )
    config = SourceConfig(
        source_id="src",
        tenant_id=_TENANT,
        name="n",
        family=SourceFamily.WEB,
        source_type="test",
        collection_id="col",
        min_quality_score=0.0,
    )

    result = await pipeline.ingest(doc, config)

    assert result.status == "indexed", result
    nodes = kg.query_nodes(tenant_id=_TENANT)
    assert nodes, "no entities were extracted"
    chunk_ids = {c.chunk_id for c in store.chunks}
    # Every node points at a real indexed chunk — the identifier GraphRAG seeds
    # its graph lookup with (``RetrievalResult.chunk_id``).
    assert {n.source_id for n in nodes} <= chunk_ids


async def test_hook_uses_given_chunk_ids_and_keeps_the_legacy_fallback() -> None:
    kg = KnowledgeGraphStore()
    hook = KGIngestionHook(store=kg, extractor=EntityExtractor())
    await hook.process(
        chunks=["Alice Johnson met Bob Smith at ACME headquarters."],
        chunk_ids=["chunk-abc"],
        document_id="doc-1",
        tenant_id=_TENANT,
    )
    assert {n.source_id for n in kg.query_nodes(tenant_id=_TENANT)} == {"chunk-abc"}

    kg2 = KnowledgeGraphStore()
    hook2 = KGIngestionHook(store=kg2, extractor=EntityExtractor())
    await hook2.process(
        chunks=["Alice Johnson met Bob Smith at ACME headquarters."],
        document_id="doc-1",
        tenant_id=_TENANT,
    )
    assert {n.source_id for n in kg2.query_nodes(tenant_id=_TENANT)} == {"doc-1:0"}
