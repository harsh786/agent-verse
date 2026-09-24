"""e2e_full: knowledge graph auto-population from real document ingestion.

Proves the D-15 ``ingest → knowledge graph`` wiring end-to-end: a document run
through the *real* ``app.state.ingestion_pipeline`` — the same
``IngestionPipeline`` instance ``/sources/{id}/sync`` and ``/sources/{id}/preview``
use (see ``app/api/ingestion.py``) — triggers ``KGIngestionHook`` (wired in
``app/main.py`` at D-15), which runs the real ``EntityExtractor`` over the
indexed chunks and persists the resulting nodes into the shared ``kg_store``
singleton (``app/knowledge_graph/store.py``).

The test then reads the graph back over the real ``/knowledge-graph`` REST API
(``app/api/knowledge_graph.py``) — proving the API and the ingestion hook agree
on the same tenant-scoped store — and asserts a second tenant cannot see the
first tenant's graph (isolation).

No connector round-trips a real external system (S3/GitHub/etc. are out of
reach in this harness), so the test builds the ``RawDocument``/``SourceConfig``
directly and calls the wired pipeline's ``ingest()`` in-process — exactly the
call ``preview_source``/``_run_sync`` make, just without a live connector
fetch. Nothing about the KG extraction, storage, or read-back is mocked.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.providers.fake import FakeProvider

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
def _fake_embedder(app: Any) -> Any:
    """Swap the wired ingestion pipeline's embedder for a deterministic 768-dim fake.

    ``app.state.ingestion_pipeline`` captures its embedder at construction time
    (``app/main.py``), not re-read from ``app.state`` per call, so pinning
    ``app.state.embedder`` (as other e2e tests do for the ``/knowledge/ingest``
    REST path) has no effect here — the pipeline object's own ``_embedder``
    attribute must be swapped. Collections default to a 768-dim pgvector column
    (see ``app/api/knowledge.py::_EMBEDDING_DIM``), so the fake must match.
    """
    pipeline = app.state.ingestion_pipeline
    assert pipeline is not None, "ingestion pipeline not wired on app.state"
    fake = FakeProvider(embed_dim=768)
    prev = pipeline._embedder
    pipeline._embedder = fake
    try:
        yield fake
    finally:
        pipeline._embedder = prev


def _build_source_and_doc(tenant_id: str, collection_id: str, content: str) -> Any:
    from app.ingestion.source_config import RawDocument, SourceConfig, SourceFamily

    suffix = uuid.uuid4().hex[:8]
    source = SourceConfig(
        source_id=f"kg-e2e-src-{suffix}",
        tenant_id=tenant_id,
        name="kg-e2e-source",
        family=SourceFamily.AGENT_GENERATED,
        source_type="agent_generated",
        collection_id=collection_id,
        min_quality_score=0.0,
    )
    raw_doc = RawDocument(
        doc_id=f"kg-e2e-doc-{suffix}",
        source_id=source.source_id,
        tenant_id=tenant_id,
        content=content.encode("utf-8"),
        content_type="text/plain",
        title="kg-e2e-document",
    )
    return source, raw_doc


async def _make_collection(client: Any) -> str:
    resp = await client.post(
        "/knowledge/collections",
        json={"name": f"kg-e2e-{uuid.uuid4().hex[:8]}"},
    )
    assert resp.status_code == 201, f"collection create failed: {resp.status_code} {resp.text}"
    return str(resp.json()["collection_id"])


async def test_document_ingestion_populates_tenant_knowledge_graph(
    app: Any, tenant_client: Any, _fake_embedder: Any
) -> None:
    """Ingesting a document through the wired pipeline creates real KG nodes.

    The deterministic (regex) entity extractor recognises "First Last"-shaped
    proper nouns as ``person`` entities — the marker below is engineered to hit
    that pattern so a positive match proves *this* document's content was
    extracted, not an artifact of prior graph state.
    """
    me = await tenant_client.get("/tenants/me")
    assert me.status_code == 200, me.text
    tenant_id = me.json()["tenant_id"]

    # Fixed two-word proper noun: the deterministic extractor's person pattern
    # (``[A-Z][a-z]+ [A-Z][a-z]+``) requires plain alphabetic words, so no uuid
    # suffix is appended here — the whole graph is process-local to this test
    # session and no other test uses this name.
    person = "Zephyrine Kestrelbird"
    content = (
        f"{person} filed the quarterly compliance report for the aviary program. "
        f"{person} confirmed every falcon enclosure passed inspection this cycle."
    )
    collection_id = await _make_collection(tenant_client)
    source, raw_doc = _build_source_and_doc(tenant_id, collection_id, content)

    pipeline = app.state.ingestion_pipeline
    result = await pipeline.ingest(raw_doc, source)
    assert result.status == "indexed", f"pipeline did not index the document: {result}"
    assert result.chunks_created >= 1, result
    assert result.kg_entities > 0, f"KG hook produced no entities: {result}"

    # Read back over the real REST API — proves the API and the ingestion hook
    # share the same tenant-scoped kg_store.
    nodes_resp = await tenant_client.get("/knowledge-graph/nodes", params={"search": person})
    assert nodes_resp.status_code == 200, nodes_resp.text
    nodes_body = nodes_resp.json()
    assert nodes_body["total"] >= 1, f"no KG nodes found for {person!r}: {nodes_body}"
    matching = [n for n in nodes_body["nodes"] if n["label"] == person]
    assert matching, f"extracted entity {person!r} not present in KG nodes: {nodes_body}"
    node = matching[0]
    assert node["node_type"] == "entity"
    assert node["source_id"].startswith(f"{raw_doc.doc_id}:"), node

    # A dedicated node lookup returns the same node plus its edge list (empty
    # here — deterministic extraction produces no relations — but the endpoint
    # must resolve the node for this tenant).
    detail_resp = await tenant_client.get(f"/knowledge-graph/nodes/{node['node_id']}")
    assert detail_resp.status_code == 200, detail_resp.text
    assert detail_resp.json()["node"]["label"] == person

    stats_resp = await tenant_client.get("/knowledge-graph/stats")
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["total_nodes"] >= 1
    assert stats["node_types"].get("entity", 0) >= 1


async def test_knowledge_graph_isolated_by_tenant(
    app: Any, client: Any, tenant_client: Any, _fake_embedder: Any
) -> None:
    """A second tenant must not see the first tenant's auto-populated graph."""
    me = await tenant_client.get("/tenants/me")
    tenant_id = me.json()["tenant_id"]

    person = "Corvina Ashworthgate"
    content = (
        f"{person} authored the confidential migration ledger for tenant-only "
        f"habitats. {person} is the sole signatory on the compliance addendum."
    )
    collection_id = await _make_collection(tenant_client)
    source, raw_doc = _build_source_and_doc(tenant_id, collection_id, content)

    pipeline = app.state.ingestion_pipeline
    result = await pipeline.ingest(raw_doc, source)
    assert result.status == "indexed", result
    assert result.kg_entities > 0, result

    # Tenant A can see its own node.
    own = await tenant_client.get("/knowledge-graph/nodes", params={"search": person})
    assert own.status_code == 200
    assert own.json()["total"] >= 1

    # A brand-new tenant B must not.
    email = f"kg-e2e-b-{uuid.uuid4().hex[:12]}@example.com"
    signup = await client.post("/tenants/signup", json={"name": "KG Isolation B", "email": email})
    assert signup.status_code == 201, signup.text
    api_key_b = signup.json()["api_key"]

    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://e2e-full", headers={"X-API-Key": api_key_b}
    ) as client_b:
        leaked = await client_b.get("/knowledge-graph/nodes", params={"search": person})
        assert leaked.status_code == 200
        assert leaked.json() == {"nodes": [], "total": 0}, (
            f"tenant B saw tenant A's KG node (isolation leak): {leaked.json()}"
        )

        # Direct node lookup by id must also be denied for tenant B.
        node_id = own.json()["nodes"][0]["node_id"]
        direct = await client_b.get(f"/knowledge-graph/nodes/{node_id}")
        assert direct.status_code == 404, (
            f"tenant B fetched tenant A's KG node directly (isolation leak): {direct.text}"
        )

        stats_b = await client_b.get("/knowledge-graph/stats")
        assert stats_b.status_code == 200
        assert stats_b.json()["total_nodes"] == 0


async def test_rebuild_purges_db_rows_not_just_in_memory_cache(
    app: Any,
    tenant_client: Any,
    _fake_embedder: Any,
    _migrated_backends: tuple[str, str],
) -> None:
    """DELETE /knowledge-graph/rebuild must delete the DB rows, not only clear
    this replica's in-memory kg_store cache.

    Before the fix, ``delete_tenant_graph`` only popped the process-local
    dicts: the ``knowledge_nodes``/``knowledge_edges`` rows survived, so (a)
    another replica (or this one after its next hydration-TTL tick, or a
    restart) would keep serving the "deleted" graph, and (b) the rows would
    accumulate forever with nothing to prune them. This asserts the rows are
    actually gone from Postgres, read back with a fresh connection/session that
    never touches the in-memory kg_store at all.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.rls import sqlalchemy_rls_context

    me = await tenant_client.get("/tenants/me")
    assert me.status_code == 200, me.text
    tenant_id = me.json()["tenant_id"]

    person = "Marisol Windthorne"
    content = (
        f"{person} signed off on the rebuild-purge verification memo. "
        f"{person} is the only reviewer listed for this audit trail."
    )
    collection_id = await _make_collection(tenant_client)
    source, raw_doc = _build_source_and_doc(tenant_id, collection_id, content)

    pipeline = app.state.ingestion_pipeline
    result = await pipeline.ingest(raw_doc, source)
    assert result.status == "indexed", result
    assert result.kg_entities > 0, result

    database_url, _ = _migrated_backends
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _count_rows() -> tuple[int, int]:
        async with session_factory() as session, sqlalchemy_rls_context(session, tenant_id):
            nodes = (
                await session.execute(
                    text("SELECT count(*) FROM knowledge_nodes WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                )
            ).scalar_one()
            edges = (
                await session.execute(
                    text("SELECT count(*) FROM knowledge_edges WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                )
            ).scalar_one()
            return int(nodes), int(edges)

    try:
        nodes_before, _ = await _count_rows()
        assert nodes_before >= 1, "expected at least the ingested entity row in the DB"

        rebuild = await tenant_client.delete("/knowledge-graph/rebuild")
        assert rebuild.status_code == 200, rebuild.text

        nodes_after, edges_after = await _count_rows()
        assert nodes_after == 0, f"knowledge_nodes rows survived rebuild: {nodes_after}"
        assert edges_after == 0, f"knowledge_edges rows survived rebuild: {edges_after}"
    finally:
        await engine.dispose()
