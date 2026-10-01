"""KB-DOCUMENTS-503 (RW-07): a collection's documents can be listed on real Postgres.

GET /knowledge/collections/{id}/documents selected ``source`` / ``content`` from
``knowledge_documents`` — columns that table does not have (it only stores
ingestion jobs; indexed content lives in ``knowledge_chunks_<dim>``) — so every
call was a 503. The listing now aggregates the chunk rows per document with
keyset pagination on ``document_id``.

Runs on the testcontainer SUPERUSER (RLS does nothing here), so the explicit
tenant predicate is what keeps a foreign row out.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_DIM = 768
_DOCS = 3000  # documents in the big collection
_PDF_EVERY = 10  # every 10th document is a PDF with three chunks


async def _tenant(s: Any) -> str:
    tid = uuid.uuid4().hex
    await s.execute(
        text("INSERT INTO tenants (id, name, email) VALUES (:id, 'T', :e)"),
        {"id": tid, "e": f"{tid}@example.test"},
    )
    return tid


async def _collection(s: Any, tenant: str) -> str:
    cid = uuid.uuid4().hex
    await s.execute(
        text(
            "INSERT INTO knowledge_collections (id, tenant_id, name, embedding_dim, chunk_count) "
            "VALUES (:id, :tid, 'c', :dim, 1)"
        ),
        {"id": cid, "tid": tenant, "dim": _DIM},
    )
    return cid


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world(pg_url: str) -> AsyncIterator[dict[str, Any]]:
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s, s.begin():
        a, b = await _tenant(s), await _tenant(s)
        col_a, col_b = await _collection(s, a), await _collection(s, b)
        zero = "[" + ",".join(["0"] * (_DIM - 1)) + ",1]"
        # _DOCS documents in A's collection; every _PDF_EVERY-th is a 3-chunk PDF.
        await s.execute(
            text(f"""
                INSERT INTO knowledge_chunks_{_DIM}
                    (tenant_id, collection_id, document_id, chunk_index, content,
                     content_hash, embedding, metadata)
                SELECT :tid, :cid, 'doc-' || lpad(g::text, 5, '0'), c,
                       'Body of document ' || g || ' chunk ' || c,
                       md5(g::text || '-' || c::text), CAST(:emb AS vector),
                       CASE WHEN g % {_PDF_EVERY} = 0
                            THEN jsonb_build_object('source_file', 'report-' || g || '.pdf',
                                                    'source_type', 'pdf')
                            ELSE jsonb_build_object('doc_title', 'Page ' || g,
                                                    'source_url',
                                                    'https://example.test/p/' || g,
                                                    'source_type', 'web')
                       END
                FROM generate_series(1, {_DOCS}) AS g
                CROSS JOIN LATERAL generate_series(
                    0, CASE WHEN g % {_PDF_EVERY} = 0 THEN 2 ELSE 0 END) AS c
            """),
            {"tid": a, "cid": col_a, "emb": zero},
        )
        # Tenant A's row inside B's collection (FKs ignore tenancy) and B's own doc.
        for tid, doc in ((a, "doc-leak"), (b, "doc-b-1")):
            await s.execute(
                text(
                    f"INSERT INTO knowledge_chunks_{_DIM} (tenant_id, collection_id, "
                    "document_id, chunk_index, content, content_hash, embedding, metadata) "
                    "VALUES (:tid, :cid, :doc, 0, 'x', :h, CAST(:emb AS vector), "
                    "'{\"doc_title\": \"B doc\"}')"
                ),
                {"tid": tid, "cid": col_b, "doc": doc, "h": uuid.uuid4().hex, "emb": zero},
            )
        await s.execute(text(f"ANALYZE knowledge_chunks_{_DIM}"))
    yield {
        "store": KnowledgeStore(factory),
        "factory": factory,
        "a": TenantContext(tenant_id=a, plan=PlanTier.ENTERPRISE, api_key_id="k"),
        "b": TenantContext(tenant_id=b, plan=PlanTier.ENTERPRISE, api_key_id="k"),
        "col_a": col_a,
        "col_b": col_b,
    }
    await engine.dispose()


async def test_keyset_pages_cover_every_document_once(world: dict[str, Any]) -> None:
    store: KnowledgeStore = world["store"]
    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        started = time.monotonic()
        page = await store.list_collection_documents_async(
            tenant_ctx=world["a"], collection_id=world["col_a"], limit=100, cursor=cursor
        )
        assert time.monotonic() - started < 5.0
        pages += 1
        assert page["total"] == _DOCS and page["total_capped"] is False
        seen.extend(d["id"] for d in page["documents"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages <= _DOCS // 100 + 1
    assert seen == sorted(seen) and len(seen) == len(set(seen)) == _DOCS


async def test_rows_carry_real_metadata(world: dict[str, Any]) -> None:
    page = await world["store"].list_collection_documents_async(
        tenant_ctx=world["a"], collection_id=world["col_a"], limit=10, cursor="doc-00009"
    )
    first = page["documents"][0]
    assert first["id"] == first["document_id"] == "doc-00010"
    assert first["title"] == "report-10.pdf"
    assert first["source_type"] == "pdf"
    assert first["chunk_count"] == 3
    assert first["preview"] == "Body of document 10 chunk 0"
    assert first["created_at"]
    web = page["documents"][1]
    assert web["title"] == "Page 11" and web["source_url"] == "https://example.test/p/11"
    assert web["chunk_count"] == 1


async def test_filters(world: dict[str, Any]) -> None:
    store: KnowledgeStore = world["store"]
    pdfs = await store.list_collection_documents_async(
        tenant_ctx=world["a"], collection_id=world["col_a"], limit=100, source_type="pdf"
    )
    assert pdfs["total"] == _DOCS // _PDF_EVERY
    assert {d["source_type"] for d in pdfs["documents"]} == {"pdf"}

    hit = await store.list_collection_documents_async(
        tenant_ctx=world["a"], collection_id=world["col_a"], limit=10, search="report-120.pdf"
    )
    assert [d["id"] for d in hit["documents"]] == ["doc-00120"]

    # LIKE wildcards in the search term match literally.
    none = await store.list_collection_documents_async(
        tenant_ctx=world["a"], collection_id=world["col_a"], limit=10, search="%_%"
    )
    assert none["documents"] == [] and none["total"] == 0


async def test_legacy_offset_still_pages(world: dict[str, Any]) -> None:
    page = await world["store"].list_collection_documents_async(
        tenant_ctx=world["a"], collection_id=world["col_a"], limit=5, offset=20
    )
    assert [d["id"] for d in page["documents"]] == [f"doc-{i:05d}" for i in range(21, 26)]


async def test_tenant_predicate_and_ownership(world: dict[str, Any]) -> None:
    store: KnowledgeStore = world["store"]
    page = await store.list_collection_documents_async(
        tenant_ctx=world["b"], collection_id=world["col_b"], limit=50
    )
    assert [d["id"] for d in page["documents"]] == ["doc-b-1"]
    assert page["total"] == 1
    # A's collection is not B's: refused, not listed as empty.
    with pytest.raises(KeyError):
        await store.list_collection_documents_async(
            tenant_ctx=world["b"], collection_id=world["col_a"], limit=50
        )


async def test_page_query_streams_from_the_document_index(world: dict[str, Any]) -> None:
    """The page-id query walks the (collection_id, document_id, ...) index and stops
    after the page — no sort of the whole collection."""
    async with world["factory"]() as s:
        plan = "\n".join(
            str(r[0])
            for r in (
                await s.execute(
                    text(
                        f"EXPLAIN SELECT DISTINCT document_id FROM knowledge_chunks_{_DIM} "
                        "WHERE tenant_id = :tid AND collection_id = :cid "
                        "AND (CAST(:after AS text) IS NULL OR document_id > CAST(:after AS text)) "
                        "ORDER BY document_id LIMIT 100 OFFSET 0"
                    ),
                    {"tid": world["a"].tenant_id, "cid": world["col_a"], "after": "doc-01000"},
                )
            ).fetchall()
        )
    assert "Index" in plan and "Sort" not in plan, plan
