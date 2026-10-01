"""e2e_full: the Knowledge "Documents" API on the booted app (real Postgres + Redis).

KB-DOCUMENTS-503 (RW-07): GET /knowledge/collections/{id}/documents was a 503
on every call (it selected columns ``knowledge_documents`` does not have), so
uploaded documents could never be listed. Uploads go through the real
``/knowledge/ingest/file`` endpoint with a deterministic 768-d fake embedder.
"""

from __future__ import annotations

import contextlib
import dataclasses
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.providers.fake import FakeProvider

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture
def fake_embedder(app: Any) -> Any:
    fake = FakeProvider(embed_dim=768)
    prev = getattr(app.state, "embedder", None)
    app.state.embedder = fake
    gw = getattr(app.state, "retrieval_gateway", None)
    prev_deps = getattr(gw, "dependencies", None) if gw is not None else None
    if prev_deps is not None:
        with contextlib.suppress(Exception):
            gw.dependencies = dataclasses.replace(prev_deps, embedder=fake)
    try:
        yield fake
    finally:
        app.state.embedder = prev
        if prev_deps is not None:
            gw.dependencies = prev_deps


async def _tenant_client(app: Any, client: Any, label: str) -> AsyncIterator[Any]:
    from httpx import ASGITransport, AsyncClient

    email = f"{label}-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post("/tenants/signup", json={"name": label, "email": email})
    assert resp.status_code == 201, resp.text
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://e2e-full",
        headers={"X-API-Key": resp.json()["api_key"]},
    ) as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def kb_client(app: Any, client: Any) -> AsyncIterator[Any]:
    async for c in _tenant_client(app, client, "kbdocs"):
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def other_client(app: Any, client: Any) -> AsyncIterator[Any]:
    async for c in _tenant_client(app, client, "kbother"):
        yield c


async def _collection(c: Any) -> str:
    resp = await c.post("/knowledge/collections", json={"name": f"docs-{uuid.uuid4().hex[:6]}"})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["collection_id"])


async def _upload(c: Any, collection_id: str, name: str, body: str) -> dict[str, Any]:
    resp = await c.post(
        "/knowledge/ingest/file",
        data={"collection_id": collection_id},
        files={"file": (name, body.encode(), "text/plain")},
    )
    assert resp.status_code in (200, 201), resp.text
    return dict(resp.json())


async def test_uploaded_documents_are_listed_paged_and_tenant_scoped(
    kb_client: Any, other_client: Any, fake_embedder: Any
) -> None:
    col = await _collection(kb_client)
    uploaded = {}
    for i, name in enumerate(("alpha.txt", "bravo.txt", "charlie.txt")):
        body = f"{name} unique content {uuid.uuid4().hex} " + "lorem ipsum " * (40 + i)
        result = await _upload(kb_client, col, name, body)
        assert result.get("document_id"), result
        uploaded[result["document_id"]] = name

    listed = await kb_client.get(f"/knowledge/collections/{col}/documents")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 3, body
    assert {d["id"]: d["title"] for d in body["documents"]} == uploaded
    assert all(d["chunk_count"] >= 1 and d["preview"] for d in body["documents"])

    page1 = (await kb_client.get(f"/knowledge/collections/{col}/documents?limit=2")).json()
    assert len(page1["documents"]) == 2 and page1["next_cursor"]
    page2 = (
        await kb_client.get(
            f"/knowledge/collections/{col}/documents?limit=2&cursor={page1['next_cursor']}"
        )
    ).json()
    ids = [d["id"] for d in page1["documents"] + page2["documents"]]
    assert sorted(ids) == sorted(uploaded) and len(set(ids)) == 3

    found = (
        await kb_client.get(f"/knowledge/collections/{col}/documents?search=bravo")
    ).json()
    assert [d["title"] for d in found["documents"]] == ["bravo.txt"]

    victim = next(iter(uploaded))
    deleted = await kb_client.delete(f"/knowledge/collections/{col}/documents/{victim}")
    assert deleted.status_code == 200, deleted.text
    after = (await kb_client.get(f"/knowledge/collections/{col}/documents")).json()
    assert after["total"] == 2 and victim not in {d["id"] for d in after["documents"]}

    # Another tenant cannot list (or learn about) this collection.
    foreign = await other_client.get(f"/knowledge/collections/{col}/documents")
    assert foreign.status_code == 404, foreign.text


async def test_url_document_has_a_stable_reachable_id(
    kb_client: Any, fake_embedder: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """KB-URL-DOCUMENT-ID (RW-09): ingest/url returns the persisted id; list, detail
    and search serve it; re-ingesting the URL (and the reingest endpoint) replace
    that same document."""
    import app.api.knowledge as knowledge_api

    marker = f"Quokkaberry{uuid.uuid4().hex[:6]}"
    body = {"text": f"{marker} version one. " * 30}

    async def _fake_fetch(url: str, source_type: str) -> tuple[str, dict[str, Any]]:
        return body["text"], {"source_url": url, "title": "Zen page"}

    monkeypatch.setattr(knowledge_api, "_fetch_url_content", _fake_fetch)
    col = await _collection(kb_client)
    url = "https://peps.example.test/pep-0020/"

    first = await kb_client.post("/knowledge/ingest/url", json={"collection_id": col, "url": url})
    assert first.status_code == 201, first.text
    doc_id = first.json()["document_id"]
    assert doc_id and first.json()["chunks_ingested"] >= 1

    detail = await kb_client.get(f"/knowledge/collections/{col}/documents/{doc_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["source_url"] == url

    hits = (
        await kb_client.get("/knowledge/search", params={"q": marker, "collection_id": col})
    ).json()
    assert hits and {h["document_id"] for h in hits} == {doc_id}, hits

    body["text"] = f"{marker} version two, rewritten. " * 30
    second = await kb_client.post("/knowledge/ingest/url", json={"collection_id": col, "url": url})
    assert second.status_code == 201, second.text
    assert second.json()["document_id"] == doc_id
    listed = (await kb_client.get(f"/knowledge/collections/{col}/documents")).json()
    assert [d["id"] for d in listed["documents"]] == [doc_id]
    assert "version two" in listed["documents"][0]["preview"]

    body["text"] = f"{marker} version three. " * 30
    reingested = await kb_client.post(
        f"/knowledge/collections/{col}/documents/{doc_id}/reingest"
    )
    assert reingested.status_code == 200, reingested.text
    assert reingested.json()["document_id"] == doc_id
    listed = (await kb_client.get(f"/knowledge/collections/{col}/documents")).json()
    assert [d["id"] for d in listed["documents"]] == [doc_id]
    assert "version three" in listed["documents"][0]["preview"]
