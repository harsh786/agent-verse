"""Tests for the KG ingestion hook (D-15).

`extract_and_store_graph` must run the real ``EntityExtractor`` over ingested
text and persist the resulting nodes/edges into ``KnowledgeGraphStore`` — the
same store whose DB tables (``knowledge_nodes`` / ``knowledge_edges``) GraphRAG's
``query_graph_evidence`` reads. These tests exercise the in-memory store path so
they are fully deterministic and require no Postgres.
"""

from __future__ import annotations

import pytest

from app.knowledge_graph.extractor import EntityExtractor
from app.knowledge_graph.ingestion_hook import KGIngestionHook, extract_and_store_graph
from app.knowledge_graph.models import NodeType
from app.knowledge_graph.store import KnowledgeGraphStore
from app.providers.fake import FakeProvider


async def test_deterministic_extraction_persists_nodes() -> None:
    """The regex/deterministic path persists queryable entity nodes."""
    store = KnowledgeGraphStore()
    extractor = EntityExtractor()
    text = "Alice Smith met Bob Jones at OpenAI to discuss `pgvector`."

    added = await extract_and_store_graph(
        text,
        tenant_id="t-1",
        source_id="chunk-1",
        extractor=extractor,
        store=store,
    )

    assert added > 0
    nodes = store.query_nodes(tenant_id="t-1")
    assert len(nodes) == added  # every added item is queryable back
    labels = {n.label for n in nodes}
    assert "Alice Smith" in labels
    assert "Bob Jones" in labels
    # Nodes carry provenance back to the originating chunk.
    assert all(n.source_id == "chunk-1" for n in nodes)
    assert all(n.node_type is NodeType.ENTITY for n in nodes)


async def test_extraction_is_deterministic() -> None:
    """Same text + tenant yields the same node ids on repeat runs (idempotent)."""
    text = "Carol Adams works at Acme Corp on `widgets`."
    store_a = KnowledgeGraphStore()
    store_b = KnowledgeGraphStore()

    await extract_and_store_graph(
        text, tenant_id="t-1", source_id="c1",
        extractor=EntityExtractor(), store=store_a,
    )
    await extract_and_store_graph(
        text, tenant_id="t-1", source_id="c1",
        extractor=EntityExtractor(), store=store_b,
    )

    ids_a = sorted(n.node_id for n in store_a.query_nodes(tenant_id="t-1"))
    ids_b = sorted(n.node_id for n in store_b.query_nodes(tenant_id="t-1"))
    assert ids_a == ids_b


async def test_tenant_scoped_isolation() -> None:
    """Nodes stored under one tenant are invisible to another (RLS boundary)."""
    store = KnowledgeGraphStore()
    await extract_and_store_graph(
        "Dave Miller joined Globex Inc.",
        tenant_id="tenant-a", source_id="doc-a",
        extractor=EntityExtractor(), store=store,
    )

    assert store.query_nodes(tenant_id="tenant-a")  # present for owner
    assert store.query_nodes(tenant_id="tenant-b") == []  # isolated from others


async def test_llm_path_persists_nodes_and_edges() -> None:
    """With a provider, both entities and relationships are stored."""
    store = KnowledgeGraphStore()
    extractor = EntityExtractor()
    provider = FakeProvider(
        responses=[
            # entity extraction response
            '[{"label": "Alice", "type": "person", "confidence": 0.9},'
            ' {"label": "Bob", "type": "person", "confidence": 0.9}]',
            # relationship extraction response
            '[{"source": "Alice", "target": "Bob", "relation": "mentions",'
            ' "evidence": "Alice knows Bob", "confidence": 0.8}]',
        ]
    )

    added = await extract_and_store_graph(
        "Alice knows Bob.",
        tenant_id="t-1", source_id="chunk-1",
        extractor=extractor, store=store, provider=provider,
    )

    nodes = store.query_nodes(tenant_id="t-1")
    assert {n.label for n in nodes} == {"Alice", "Bob"}
    # One relationship edge was persisted and is queryable via the store.
    alice = next(n for n in nodes if n.label == "Alice")
    edges = store.get_edges_for_node(alice.node_id, tenant_id="t-1")
    assert len(edges) == 1
    assert added == len(nodes) + 1  # 2 nodes + 1 edge


async def test_empty_text_returns_zero() -> None:
    store = KnowledgeGraphStore()
    added = await extract_and_store_graph(
        "   ", tenant_id="t-1", source_id="c1",
        extractor=EntityExtractor(), store=store,
    )
    assert added == 0
    assert store.query_nodes(tenant_id="t-1") == []


async def test_store_failure_surfaces_not_swallowed() -> None:
    """A persistence error must propagate — no silent bare-except over real logic."""

    class BrokenStore(KnowledgeGraphStore):
        def add_node(self, node: object) -> None:  # type: ignore[override]
            raise RuntimeError("db down")

    with pytest.raises(RuntimeError, match="db down"):
        await extract_and_store_graph(
            "Alice Smith met Bob Jones.",
            tenant_id="t-1", source_id="c1",
            extractor=EntityExtractor(), store=BrokenStore(),
        )


# ── KGIngestionHook adapter — the object the IngestionPipeline actually calls ──


async def test_kg_ingestion_hook_process_populates_store_and_splits_counts() -> None:
    """The pipeline calls hook.process(chunks=..., document_id=..., tenant_id=...)
    and reads {entities, relations}. Deterministic path: proper nouns + acronym
    become entities, no relations, and the nodes land in the injected store."""
    store = KnowledgeGraphStore()
    hook = KGIngestionHook(store=store, extractor=EntityExtractor())

    result = await hook.process(
        chunks=["Alice Johnson met Bob Smith at ACME headquarters."],
        document_id="doc-1",
        tenant_id="t-kg",
        provider=None,  # deterministic
    )

    assert result["entities"] >= 3  # "Alice Johnson", "Bob Smith", "ACME"
    assert result["relations"] == 0  # deterministic path emits no edges
    stored = store.query_nodes(tenant_id="t-kg")
    labels = {n.label for n in stored}
    assert "ACME" in labels
    assert any(n.node_type == NodeType.ENTITY for n in stored)


async def test_kg_ingestion_hook_skips_blank_chunks() -> None:
    store = KnowledgeGraphStore()
    hook = KGIngestionHook(store=store, extractor=EntityExtractor())
    result = await hook.process(
        chunks=["   ", ""], document_id="d", tenant_id="t", provider=None
    )
    assert result == {"entities": 0, "relations": 0}
    assert store.query_nodes(tenant_id="t") == []


async def test_kg_ingestion_hook_defaults_to_shared_singleton() -> None:
    """With no store injected, the hook binds the shared kg_store singleton — the
    same object the lifespan DB-upgrades — so wiring is live end-to-end."""
    from app.knowledge_graph.store import kg_store

    hook = KGIngestionHook()
    assert hook._store is kg_store
