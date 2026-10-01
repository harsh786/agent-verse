"""KB-43: a Source reindex keeps documents under legal hold (real Postgres, app role).

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/ingestion/test_reindex_legal_hold_integration.py -m integration
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from app.ingestion.scheduler import _delete_source_documents
from app.ingestion.source_config import SourceConfig, SourceFamily
from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from tests.rag._pg import admin_exec, app_engine, seed_tenant, sessions

pytestmark = pytest.mark.integration


async def test_reindex_deletes_unheld_documents_and_keeps_held_ones(pg_url: str) -> None:
    tid = str(uuid.uuid4())
    await seed_tenant(pg_url, tid)
    engine = await app_engine(pg_url)
    try:
        store = KnowledgeStore(sessions(engine), embedding_dim=768)
        ctx = TenantContext(tid, PlanTier.FREE, "k")
        cid = await store.create_collection_async(KnowledgeCollection(name="c"), tenant_ctx=ctx)
        for doc in ("doc-a", "doc-held", "doc-c"):
            await store.ingest_chunks_async(
                [
                    Chunk(
                        document_id=doc,
                        content=f"{doc} {i}",
                        embedding=[0.02 * (i + 1)] * 768,
                        chunk_index=i,
                        metadata={"source_id": "src-1"},
                    )
                    for i in range(2)
                ],
                collection_id=cid,
                tenant_ctx=ctx,
            )
        await admin_exec(
            pg_url,
            "INSERT INTO legal_holds (tenant_id, name, resource_type, resource_ids) "
            "VALUES (:t, 'matter', 'document', CAST(:r AS jsonb))",
            {"t": tid, "r": json.dumps(["doc-held"])},
        )
        assert await store.held_document_ids_async(
            cid, ["doc-a", "doc-held", "doc-c"], tenant_ctx=ctx
        ) == {"doc-held"}

        config = SourceConfig(
            source_id="src-1",
            tenant_id=tid,
            name="s",
            family=SourceFamily.WEB,
            source_type="http",
            collection_id=cid,
        )
        removed = await _delete_source_documents(SimpleNamespace(_kb=store), config)

        assert removed == 2
        rows = await admin_exec(
            pg_url,
            "SELECT DISTINCT document_id FROM knowledge_chunks_768 WHERE collection_id = :c",
            {"c": cid},
        )
        assert [r[0] for r in rows] == ["doc-held"]

        # A collection hold covers every document.
        await admin_exec(
            pg_url,
            "INSERT INTO legal_holds (tenant_id, name, resource_type, resource_ids) "
            "VALUES (:t, 'm2', 'collection', CAST(:r AS jsonb))",
            {"t": tid, "r": json.dumps([cid])},
        )
        assert await store.held_document_ids_async(cid, ["x", "y"], tenant_ctx=ctx) == {"x", "y"}
    finally:
        await engine.dispose()
