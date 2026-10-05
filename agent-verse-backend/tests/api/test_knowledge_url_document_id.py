"""KB-URL-DOCUMENT-ID (RW-09): a URL-ingested document has a reachable, stable id.

``POST /knowledge/ingest/url`` returned no ``document_id`` (a random uuid4 was
minted and dropped), search hits carried ``document_id: null`` (the chunk
metadata never held it), so the re-embed endpoint for a URL document was
unreachable. Now the id is derived from (tenant, collection, normalised URL),
returned by ingest, stored on every chunk, served by list / detail / search, and
re-ingesting the URL replaces that same document.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.knowledge import router as knowledge_router
from app.api.knowledge import stable_url_document_id
from app.providers.fake import FakeProvider
from app.rag.contracts import RAGCitation, RAGExecutionResult, RAGStrategy
from app.rag.models import KnowledgeCollection
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="tid-urldoc", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_KEY = "av_test_url_document_id"
_URL = "https://Example.test/docs/page#section-2"


def _client(store: KnowledgeStore, gateway: Any = None) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(knowledge_router)
    app.state.knowledge_store = store
    app.state.semantic_cache = SemanticCache()
    app.state.embedder = FakeProvider(embed_dim=8)
    if gateway is not None:
        app.state.retrieval_gateway = gateway
    return TestClient(app, raise_server_exceptions=False)


def _store() -> tuple[KnowledgeStore, str]:
    store = KnowledgeStore()
    cid = store.create_collection(KnowledgeCollection(name="web"), tenant_ctx=_CTX)
    return store, cid


def _fetch(text: str) -> Any:
    from app.ingestion.web_fetch import WebResource

    async def _fake(url: str, source_type: str) -> WebResource:
        return WebResource(url=url, final_url=url, status=200,
                           content_type="text/plain; charset=utf-8", data=text.encode())

    return patch("app.api.knowledge._fetch_url_resource", new=_fake)


def _auth() -> dict[str, str]:
    return {"X-API-Key": _KEY}


def test_stable_id_ignores_fragment_and_host_case_but_not_path() -> None:
    a = stable_url_document_id("t", "c", "https://Example.test/docs/page#x")
    assert a == stable_url_document_id("t", "c", "https://example.test/docs/page")
    assert len(a) == 32 and int(a, 16) >= 0
    assert a != stable_url_document_id("t", "c", "https://example.test/docs/other")
    assert a != stable_url_document_id("t", "c2", "https://example.test/docs/page")
    assert a != stable_url_document_id("t2", "c", "https://example.test/docs/page")


def test_ingest_url_returns_the_persisted_document_id() -> None:
    store, cid = _store()
    client = _client(store)
    with _fetch("Version one of the page. " * 20):
        resp = client.post(
            "/knowledge/ingest/url", json={"collection_id": cid, "url": _URL}, headers=_auth()
        )
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["document_id"]
    assert doc_id == stable_url_document_id(_CTX.tenant_id, cid, _URL)

    chunks = store._data[(_CTX.tenant_id, cid)].chunks
    assert chunks and {c.document_id for c in chunks} == {doc_id}
    assert all(c.metadata.get("document_id") == doc_id for c in chunks)

    listed = client.get(f"/knowledge/collections/{cid}/documents", headers=_auth()).json()
    assert [d["document_id"] for d in listed["documents"]] == [doc_id]
    detail = client.get(f"/knowledge/collections/{cid}/documents/{doc_id}", headers=_auth())
    assert detail.status_code == 200, detail.text
    assert detail.json()["document_id"] == doc_id
    assert detail.json()["source_type"] == "web"
    missing = client.get(f"/knowledge/collections/{cid}/documents/nope", headers=_auth())
    assert missing.status_code == 404


def test_reingesting_changed_content_replaces_the_same_document() -> None:
    store, cid = _store()
    client = _client(store)
    with _fetch("Version one. " * 30):
        first = client.post(
            "/knowledge/ingest/url", json={"collection_id": cid, "url": _URL}, headers=_auth()
        ).json()
    with _fetch("Version two, rewritten. " * 30):
        second = client.post(
            "/knowledge/ingest/url", json={"collection_id": cid, "url": _URL}, headers=_auth()
        ).json()
    assert second["document_id"] == first["document_id"]
    chunks = store._data[(_CTX.tenant_id, cid)].chunks
    assert chunks and all("Version two" in c.content for c in chunks)

    # Identical content again: deduplicated, and still names the document.
    with _fetch("Version two, rewritten. " * 30):
        third = client.post(
            "/knowledge/ingest/url", json={"collection_id": cid, "url": _URL}, headers=_auth()
        ).json()
    assert third["deduplicated"] is True and third["document_id"] == first["document_id"]


def test_document_reingest_keeps_the_document_id() -> None:
    store, cid = _store()
    client = _client(store)
    with _fetch("Original body. " * 30):
        doc_id = client.post(
            "/knowledge/ingest/url", json={"collection_id": cid, "url": _URL}, headers=_auth()
        ).json()["document_id"]
    with _fetch("Refetched body. " * 30):
        resp = client.post(
            f"/knowledge/collections/{cid}/documents/{doc_id}/reingest", headers=_auth()
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["document_id"] == doc_id
    chunks = store._data[(_CTX.tenant_id, cid)].chunks
    assert chunks and all("Refetched" in c.content for c in chunks)


def test_search_hits_carry_the_document_id() -> None:
    class _Gateway:
        async def execute(self, tenant_ctx: Any, **kwargs: Any) -> RAGExecutionResult:
            return RAGExecutionResult(
                requested_strategy_id="hybrid",
                resolved_strategy_id=RAGStrategy.HYBRID,
                citations=[
                    RAGCitation(
                        citation_id="c1", chunk_id="ch1", content="x", score=0.5,
                        source=_URL, metadata={"document_id": "doc-new", "source_url": _URL},
                    ),
                    # Rows written before the fix only have source_doc_id.
                    RAGCitation(
                        citation_id="c2", chunk_id="ch2", content="y", score=0.4,
                        source=_URL, metadata={"source_doc_id": "doc-legacy"},
                    ),
                ],
            )

    store, cid = _store()
    client = _client(store, gateway=_Gateway())
    hits = client.get(f"/knowledge/search?q=x&collection_id={cid}", headers=_auth()).json()
    assert [h["document_id"] for h in hits] == ["doc-new", "doc-legacy"]
