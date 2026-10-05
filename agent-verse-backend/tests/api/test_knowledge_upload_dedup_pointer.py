"""A deduplicated upload names the document that already holds the content (P1a-5).

Live P1a (KB-UPLOAD-DUPLICATES): the same bytes uploaded under another name
(or another extension) were correctly not stored again, but the response said
``document_id: null``, so the caller could not tell where the content lives.
"""

from __future__ import annotations

import io

from fastapi.testclient import TestClient

from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from tests.api.test_knowledge_upload_replace import _H, _client

_TEXT = "# Ops bulletin 31\n\nThe Kingfisher gate closes at 23:00 every Sunday.\n"


def _upload(client: TestClient, cid: str, name: str, data: bytes) -> dict[str, object]:
    r = client.post("/knowledge/ingest/file", headers=_H, data={"collection_id": cid},
                    files={"file": (name, io.BytesIO(data), "text/plain")})
    assert r.status_code == 201, r.text
    return dict(r.json())


def test_copy_under_another_name_points_at_the_stored_document() -> None:
    _, client, cid = _client()
    first = _upload(client, cid, "ops-bulletin-31.md", _TEXT.encode())
    for name in ("ops-bulletin-31-copy.md", "ops-bulletin-31.txt", "ops-bulletin-31.md"):
        dup = _upload(client, cid, name, _TEXT.encode())
        assert dup["deduplicated"] is True and dup["chunks_created"] == 0
        assert dup["document_id"] == first["document_id"], name


async def test_store_document_id_by_hash_is_tenant_and_collection_scoped() -> None:
    store = KnowledgeStore()
    a = TenantContext(tenant_id="ta", plan=PlanTier.FREE, api_key_id="k")
    b = TenantContext(tenant_id="tb", plan=PlanTier.FREE, api_key_id="k")
    for ctx in (a, b):
        store.create_collection(
            KnowledgeCollection(name="c", collection_id=f"c-{ctx.tenant_id}"), tenant_ctx=ctx
        )
    store.ingest_chunk(Chunk(document_id="doc-a", content="x", embedding=[0.1] * 8,
                             chunk_index=0, metadata={"doc_content_hash": "h1"}),
                       collection_id="c-ta", tenant_ctx=a)
    assert await store.document_id_by_hash(content_hash="h1", tenant_id="ta",
                                           collection_id="c-ta") == "doc-a"
    assert await store.document_id_by_hash(content_hash="h1", tenant_id="tb") is None
    assert await store.document_id_by_hash(content_hash="", tenant_id="ta") is None
    assert await store.exists_by_hash(content_hash="h1", tenant_id="ta") is True
