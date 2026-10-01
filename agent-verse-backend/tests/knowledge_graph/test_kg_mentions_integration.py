"""KB-53 on real Postgres (app role, RLS): entity mentions are reference counts.

A node kept only its first mention's source_id: deleting a document removed an
entity another document still mentioned (or left the deleted document's edges),
GraphRAG seeded only from the first mention, and random edge ids duplicated
every edge on each re-extraction.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/knowledge_graph/test_kg_mentions_integration.py -m integration
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.db.rls import sqlalchemy_rls_context
from app.knowledge_graph.extractor import edge_id_for
from app.knowledge_graph.ingestion_hook import KGIngestionHook
from app.knowledge_graph.models import EdgeType, GraphEdge, GraphNode, NodeType
from app.knowledge_graph.store import KnowledgeGraphStore
from app.rag.agentic.patterns.graph import GraphEvidenceQuery, query_graph_evidence
from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from tests.rag._pg import admin_exec, app_engine, seed_tenant, sessions

pytestmark = pytest.mark.integration


class _Extractor:
    """Every chunk mentions the shared entity "Acme" and one entity of its own."""

    def set_provider(self, provider: Any) -> None:
        del provider

    @staticmethod
    def _node(tenant_id: str, label: str, source_id: str | None) -> GraphNode:
        return GraphNode(
            node_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{tenant_id}:{label}")),
            tenant_id=tenant_id,
            node_type=NodeType.ENTITY,
            label=label,
            content=f"org: {label}",
            source_id=source_id,
        )

    async def extract_entities_llm(
        self, text: str, tenant_id: str, source_id: str | None = None
    ) -> list[GraphNode]:
        own = text.split()[0]
        return [self._node(tenant_id, "Acme", source_id), self._node(tenant_id, own, source_id)]

    async def extract_relationships_llm(
        self, text: str, entities: list[GraphNode], tenant_id: str, source_id: str | None = None
    ) -> list[GraphEdge]:
        acme, own = entities
        return [
            GraphEdge(
                edge_id=edge_id_for(
                    tenant_id, own.node_id, acme.node_id, EdgeType.MENTIONS.value, source_id or ""
                ),
                tenant_id=tenant_id,
                source_node_id=own.node_id,
                target_node_id=acme.node_id,
                edge_type=EdgeType.MENTIONS,
                provenance=source_id or "",
            )
        ]


async def _count(pg_url: str, sql: str, tid: str) -> int:
    rows = await admin_exec(pg_url, sql, {"t": tid})
    return int(rows[0][0])


async def test_mentions_reference_count_entities_across_documents(pg_url: str) -> None:
    tid = str(uuid.uuid4())
    await seed_tenant(pg_url, tid)
    engine = await app_engine(pg_url)
    try:
        factory = sessions(engine)
        store = KnowledgeStore(factory, embedding_dim=768)
        graph = KnowledgeGraphStore()
        graph.set_db(factory)
        hook = KGIngestionHook(store=graph, extractor=_Extractor())
        ctx = TenantContext(tid, PlanTier.FREE, "k")
        cid = await store.create_collection_async(KnowledgeCollection(name="c"), tenant_ctx=ctx)

        async def _ingest(doc: str, texts: list[str]) -> list[str]:
            chunks = [
                Chunk(document_id=doc, content=t, embedding=[0.1 * (i + 1)] * 768, chunk_index=i)
                for i, t in enumerate(texts)
            ]
            await store.ingest_chunks_async(chunks, collection_id=cid, tenant_ctx=ctx)
            ids = [c.chunk_id for c in chunks]
            await hook.process(
                chunks=texts, document_id=doc, tenant_id=tid, provider=object(), chunk_ids=ids
            )
            return ids

        a_chunks = await _ingest("doc-a", ["Alpha works with Acme", "Apex partners with Acme"])
        b_chunks = await _ingest("doc-b", ["Beta buys from Acme"])
        acme = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{tid}:Acme"))

        edges_sql = "SELECT count(*) FROM knowledge_edges WHERE tenant_id = :t"
        assert await _count(pg_url, edges_sql, tid) == 3
        mentions_sql = (
            f"SELECT count(*) FROM knowledge_node_mentions WHERE tenant_id = :t "
            f"AND node_id = '{acme}'"
        )
        assert await _count(pg_url, mentions_sql, tid) == 3

        # Re-extracting the same document adds nothing (deterministic edge ids).
        await hook.process(
            chunks=["Alpha works with Acme", "Apex partners with Acme"],
            document_id="doc-a",
            tenant_id=tid,
            provider=object(),
            chunk_ids=a_chunks,
        )
        assert await _count(pg_url, edges_sql, tid) == 3
        assert await _count(pg_url, mentions_sql, tid) == 3

        # Delete doc A: Acme survives (doc B mentions it), A's edges and A-only
        # entities are gone, and Acme now points at B's chunk.
        await store.delete_document_async("doc-a", collection_id=cid, tenant_ctx=ctx)
        nodes = await admin_exec(
            pg_url,
            "SELECT label, source_id FROM knowledge_nodes WHERE tenant_id = :t ORDER BY label",
            {"t": tid},
        )
        assert [(r[0], r[1]) for r in nodes] == [("Acme", b_chunks[0]), ("Beta", b_chunks[0])]
        assert await _count(pg_url, edges_sql, tid) == 1
        assert await _count(pg_url, mentions_sql, tid) == 1

        # GraphRAG seeds Acme from B's chunk (a later mention).
        async with factory() as session, session.begin(), sqlalchemy_rls_context(session, tid):
            evidence = await query_graph_evidence(
                session,
                GraphEvidenceQuery(tenant_id=tid, query="zzzz", seed_chunk_ids=(b_chunks[0],)),
            )
        labels = {e.provenance.get("label") for e in evidence if e.evidence_type == "entity"}
        assert "Acme" in labels

        # Delete doc B: Acme and every edge go.
        await store.delete_document_async("doc-b", collection_id=cid, tenant_ctx=ctx)
        assert (
            await _count(pg_url, "SELECT count(*) FROM knowledge_nodes WHERE tenant_id = :t", tid)
            == 0
        )
        assert await _count(pg_url, edges_sql, tid) == 0
        assert (
            await _count(
                pg_url, "SELECT count(*) FROM knowledge_node_mentions WHERE tenant_id = :t", tid
            )
            == 0
        )
    finally:
        await engine.dispose()
