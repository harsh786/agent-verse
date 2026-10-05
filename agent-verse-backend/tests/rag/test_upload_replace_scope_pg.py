"""D1 on real Postgres: a same-name re-upload replaces only an upload.

The upload API runs against a DB-backed ``KnowledgeStore`` on a migrated
pgvector testcontainer. Documents named like the upload but stored by a
connector (``source_id``) or another tenant stay; the earlier upload is
replaced; ``replace_existing=false`` keeps both; the lookup's expression indexes
exist on every chunk table. Runs on the container SUPERUSER, so the explicit
tenant predicate is what keeps the other tenant's row out.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.knowledge import router as knowledge_router
from app.providers.fake import FakeProvider
from app.rag.semantic_cache import SemanticCache
from app.rag.store import SUPPORTED_EMBEDDING_DIMENSIONS, UPLOAD_SOURCE, KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_DIM = 768
_KEY = "av_test_d1_pg"


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
            "VALUES (:id, :tid, 'c', :dim, 0)"
        ),
        {"id": cid, "tid": tenant, "dim": _DIM},
    )
    return cid


async def _seed(s: Any, tenant: str, cid: str, doc: str, meta: str) -> None:
    zero = "[" + ",".join(["0"] * (_DIM - 1)) + ",1]"
    await s.execute(
        text(
            f"INSERT INTO knowledge_chunks_{_DIM} (tenant_id, collection_id, document_id, "
            "chunk_index, content, content_hash, embedding, metadata) VALUES "
            "(:tid, :cid, :doc, 0, :body, :h, CAST(:emb AS vector), CAST(:meta AS jsonb))"
        ),
        {"tid": tenant, "cid": cid, "doc": doc, "body": f"body of {doc}",
         "h": uuid.uuid4().hex, "emb": zero, "meta": meta},
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world(pg_url: str) -> AsyncIterator[dict[str, Any]]:
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s, s.begin():
        a, b = await _tenant(s), await _tenant(s)
        cid = await _collection(s, a)
        await _seed(s, a, cid, "conn-x", '{"source_id": "src-x", "doc_title": "scan.md"}')
        await _seed(s, a, cid, "conn-y", '{"source_id": "src-y", "doc_title": "scan.md"}')
        await _seed(s, a, cid, "repo", '{"source_file": "scan.md", "repo_url": "https://g/r"}')
        # Another tenant's upload-shaped row inside A's collection (FKs ignore tenancy).
        await _seed(s, b, cid, "foreign", '{"source_file": "scan.md", "ext": "md"}')
    ctx = TenantContext(tenant_id=a, plan=PlanTier.ENTERPRISE, api_key_id="k")
    store = KnowledgeStore(factory)
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return ctx if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(knowledge_router)
    app.state.knowledge_store = store
    app.state.semantic_cache = SemanticCache()
    app.state.embedder = FakeProvider(embed_dim=_DIM)
    app.state.llm_provider = None
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://d1", headers={"X-API-Key": _KEY}
    ) as client:
        yield {"client": client, "factory": factory, "store": store, "ctx": ctx,
               "cid": cid, "a": a, "b": b}
    await engine.dispose()


async def _upload(w: dict[str, Any], body: str, **form: str) -> dict[str, Any]:
    resp = await w["client"].post(
        "/knowledge/ingest/file",
        data={"collection_id": w["cid"], **form},
        files={"file": ("scan.md", body.encode(), "text/markdown")},
    )
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


async def _docs(w: dict[str, Any], tenant: str) -> set[str]:
    async with w["factory"]() as s:
        rows = await s.execute(
            text(f"SELECT DISTINCT document_id FROM knowledge_chunks_{_DIM} "
                 "WHERE tenant_id = :tid AND collection_id = :cid"),
            {"tid": tenant, "cid": w["cid"]},
        )
        return {str(r[0]) for r in rows}


async def test_reupload_replaces_only_the_earlier_upload(world: dict[str, Any]) -> None:
    w = world
    v1 = await _upload(w, "# Scan\n\nfirst upload text\n")
    assert v1["replaced"] is False
    v2 = await _upload(w, "# Scan\n\nsecond upload text\n")
    assert v2["replaced"] is True and v2["document_id"] == v1["document_id"]
    assert await _docs(w, w["a"]) == {"conn-x", "conn-y", "repo", v1["document_id"]}
    assert await _docs(w, w["b"]) == {"foreign"}
    async with w["factory"]() as s:
        meta = (await s.execute(
            text(f"SELECT DISTINCT metadata->>'ingest_source' FROM knowledge_chunks_{_DIM} "
                 "WHERE tenant_id = :tid AND document_id = :d"),
            {"tid": w["a"], "d": v1["document_id"]},
        )).scalars().all()
    assert meta == [UPLOAD_SOURCE]

    keep = await _upload(w, "# Scan\n\nthird text kept beside\n", replace_existing="false")
    assert keep["replaced"] is False and keep["document_id"] != v1["document_id"]
    assert await _docs(w, w["a"]) == {
        "conn-x", "conn-y", "repo", v1["document_id"], keep["document_id"]}


async def test_store_lookup_is_scoped_to_one_source(world: dict[str, Any]) -> None:
    w = world

    async def same(source: str) -> list[str]:
        ids: list[str] = await w["store"].same_name_document_ids_async(
            tenant_ctx=w["ctx"], collection_id=w["cid"], name="scan.md", source=source)
        return ids

    assert await same("src-x") == ["conn-x"]
    assert await same("src-y") == ["conn-y"]
    assert "foreign" not in await same(UPLOAD_SOURCE)
    assert not ({"conn-x", "conn-y", "repo"} & set(await same(UPLOAD_SOURCE)))


async def test_same_name_lookup_indexes_exist(world: dict[str, Any]) -> None:
    async with world["factory"]() as s:
        names = set((await s.execute(
            text("SELECT indexname FROM pg_indexes WHERE indexname LIKE 'idx_knowledge_chunks_%'")
        )).scalars().all())
    for dim in SUPPORTED_EMBEDDING_DIMENSIONS:
        assert f"idx_knowledge_chunks_{dim}_source_file" in names
        assert f"idx_knowledge_chunks_{dim}_doc_title" in names
