"""Per-collection embedders on real Postgres + pgvector + Redis, with a real
OpenAI-compatible embeddings server.

Two knowledge collections live side by side, each bound to its own embedding
model and width:

* ``wide``   — the deployment default, ``acme/embed-2048`` (``EMBEDDING_BASE_URL``,
  ``EMBEDDING_DIM=2048``), stored in ``knowledge_chunks_2048``;
* ``narrow`` — ``acme/embed-1024``, registered ONLY in the Model Registry with its
  own ``base_url`` and vault-encrypted key, stored in ``knowledge_chunks_1024``.

Both models are served by a real HTTP server started in this test on 127.0.0.1
(``POST /v1/embeddings``; deterministic hashed bag-of-words vectors, so a query
is nearest to the document sharing its words). Through the knowledge API
(create, ingest, hybrid search) each collection is embedded — documents AND
queries — with its own model only, its chunks land only in its width's table,
and neither collection ever sees the other's vectors. A re-embed through the
API (``POST /knowledge/collections/{id}/re-embed`` + the maintenance task) then
moves ``narrow`` from 1024 to 2048: rows move to ``knowledge_chunks_2048``, the
binding flips, and later queries / ingests use the new model.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/integration/test_per_collection_embedders_pg.py -m integration
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import uuid
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from tests.rag._pg import admin_exec, seed_tenant

pytestmark = pytest.mark.integration

_ISOLATE_PROVIDER_ENV = True

_WIDE = "acme/embed-2048"
_NARROW = "acme/embed-1024"
_WIDTHS = {_WIDE: 2048, _NARROW: 1024}
_SECRET = "it-percoll-key"
_KEY = "ak_percoll_admin"
_REGISTRY_KEYS = (
    "model_registry:configured",
    "model_registry:configured:version",
    "model_registry:preferences",
    "model_registry:probed_dimensions",
)
_FREIGHT = (
    "Bramblewood Freight ships refrigerated containers from Kochi to Rotterdam every "
    "Tuesday; spoiled cargo claims need the reefer temperature log."
)
_ORCHARD = (
    "The Lindqvist orchard grafts heirloom apple scions onto dwarfing rootstock each "
    "March and prunes the espaliers in late winter."
)


def vector_for(text: str, dim: int) -> list[float]:
    """Deterministic hashed bag-of-words embedding (unit length, never zero)."""
    vec = [0.0] * dim
    vec[0] = 0.05
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        digest = hashlib.sha256(f"{dim}:{token}".encode()).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        vec[index] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class _EmbeddingsHandler(BaseHTTPRequestHandler):
    server: _EmbeddingsServer

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": m} for m in _WIDTHS]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path != "/v1/embeddings":
            self._send(404, {"error": "not found"})
            return
        if self.headers.get("Authorization") != f"Bearer {_SECRET}":
            self._send(401, {"error": "bad key"})
            return
        model = str(body.get("model") or "")
        if model not in _WIDTHS:
            self._send(404, {"error": f"model {model} not served"})
            return
        texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
        self.server.calls.append({"model": model, "input": list(texts)})
        self._send(
            200,
            {
                "object": "list",
                "model": model,
                "data": [
                    {"object": "embedding", "index": i, "embedding": vector_for(t, _WIDTHS[model])}
                    for i, t in enumerate(texts)
                ],
                "usage": {"prompt_tokens": len(texts), "total_tokens": len(texts)},
            },
        )

    def log_message(self, *_args: Any) -> None:
        return


class _EmbeddingsServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _EmbeddingsHandler)
        self.calls: list[dict[str, Any]] = []

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/v1"

    def models_for(self, needle: str) -> set[str]:
        """The models that embedded any text containing ``needle``."""
        return {c["model"] for c in self.calls if any(needle in t for t in c["input"])}


@pytest.fixture
def embed_server() -> Iterator[_EmbeddingsServer]:
    server = _EmbeddingsServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def deployment(
    test_backends: tuple[str, str],
    embed_server: _EmbeddingsServer,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[str, str]]:
    """Default embedder = acme/embed-2048 on the test server; shared registry on Redis."""
    import redis

    import app.ai_router.registry_store as rs
    import app.ai_router.selection as sel
    from app.ai_router.registry import model_registry
    from app.ai_router.registry_store import ModelRegistryStore
    from app.core.config import get_settings

    pg_url, redis_url = test_backends
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    monkeypatch.setenv("EMBEDDING_BASE_URL", embed_server.base_url)
    monkeypatch.setenv("EMBEDDING_MODEL", _WIDE)
    monkeypatch.setenv("EMBEDDING_API_KEY", _SECRET)
    monkeypatch.setenv("EMBEDDING_DIM", "2048")
    get_settings.cache_clear()
    client = redis.from_url(redis_url, decode_responses=True)
    client.delete(*_REGISTRY_KEYS)
    monkeypatch.setattr(rs, "_store", ModelRegistryStore(client))
    monkeypatch.setattr(sel, "_lazy_seeded", False)
    monkeypatch.setattr(sel, "_seeded_version", None)
    monkeypatch.setattr(sel, "_last_version_check", 0.0)
    try:
        yield pg_url, redis_url
    finally:
        client.delete(*_REGISTRY_KEYS)
        client.close()
        model_registry.clear_configured()
        model_registry.set_preferences({})
        get_settings.cache_clear()


def _app(tenant_id: str, redis_url: str) -> FastAPI:
    """The knowledge + model-registry API wired like the lifespan wires it (DB path)."""
    import redis.asyncio as aioredis

    from app.api.knowledge import router as knowledge_router
    from app.api.model_registry import router as models_router
    from app.core.config import get_settings
    from app.db.session import get_session_factory
    from app.observability.traced_provider import traced_embedder
    from app.providers.embedder_factory import resolve_embedder
    from app.rag.catalogue import RAGAdapterConfiguration
    from app.rag.collection_embedders import CollectionEmbedders
    from app.rag.gateway import (
        RetrievalDependencies,
        RetrievalGateway,
        SQLCollectionAuthorizer,
        core_strategy_capabilities,
    )
    from app.rag.semantic_cache import SemanticCache
    from app.rag.store import KnowledgeStore

    ctx = TenantContext(
        tenant_id=tenant_id, plan=PlanTier.ENTERPRISE, api_key_id="k", roles=("admin",)
    )
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return ctx if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(knowledge_router)
    app.include_router(models_router)

    settings = get_settings()
    resolution = resolve_embedder(settings)
    # A generic EMBEDDING_BASE_URL declares no width: EMBEDDING_DIM (2048) is the
    # default for new collections, the first write confirms it.
    assert resolution.model == _WIDE, resolution
    db = get_session_factory()
    app.state.settings = settings
    app.state.embedder = traced_embedder(resolution.embedder)
    app.state.embedder_resolution = resolution
    app.state.collection_embedders = CollectionEmbedders(
        lambda: app.state.embedder,
        resolution=lambda: app.state.embedder_resolution,
        settings=settings,
    )
    store = KnowledgeStore(db, embedding_dim=2048, embedder_name=_WIDE)
    store.collection_embedders = app.state.collection_embedders
    app.state.knowledge_store = store
    app.state.semantic_cache = SemanticCache()
    app.state.retrieval_gateway = RetrievalGateway(
        RetrievalDependencies(
            session_factory=db,
            embedder=app.state.embedder,
            collection_embedders=app.state.collection_embedders,
            collection_authorizer=SQLCollectionAuthorizer(),
            strategy_capabilities=core_strategy_capabilities(RAGAdapterConfiguration()),
        )
    )
    app.state._redis = aioredis.from_url(redis_url, decode_responses=True)
    return app


@pytest.fixture
async def api(deployment: tuple[str, str]) -> AsyncIterator[tuple[httpx.AsyncClient, str]]:
    pg_url, redis_url = deployment
    tenant = f"t-percoll-{uuid.uuid4().hex[:8]}"
    await seed_tenant(pg_url, tenant)
    app = _app(tenant, redis_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"X-API-Key": _KEY}
    ) as client:
        try:
            yield client, tenant
        finally:
            await app.state._redis.aclose()
            await _dispose_engines()


async def _dispose_engines() -> None:
    import app.db.session as db_session

    for name in ("_engine", "_system_engine"):
        engine = getattr(db_session, name, None)
        if engine is not None:
            await engine.dispose()


async def _register_narrow_model(client: httpx.AsyncClient, base_url: str) -> None:
    """The operator adds acme/embed-1024 in the Model Registry (own URL + key) and
    runs Test connection (which measures and records its width)."""
    body = {
        "provider": "onprem",
        "model_id": _NARROW,
        "display_name": _NARROW,
        "capabilities": ["embedding"],
        "base_url": base_url,
        "api_key": _SECRET,
    }
    saved = await client.post("/models/configured", json=body)
    assert saved.status_code == 200, saved.text
    probe_body = {k: v for k, v in body.items() if k != "api_key"}
    probe = await client.post("/models/configured/test-endpoint", json=probe_body)
    assert probe.status_code == 200 and probe.json()["dimensions"] == 1024, probe.text
    # Not the default (2048-d EMBEDDING_DIM) but fine for a 1024-d collection.
    listing = (await client.get("/models/configured")).json()
    (embedding,) = [g for g in listing["capabilities"] if g["capability"] == "embedding"]
    (row,) = [r for r in embedding["models"] if r["model_id"] == _NARROW]
    assert row["refused"] is True and row["collection_compatible"] is True
    assert row["collection_chunk_table"] == "knowledge_chunks_1024"


async def _chunks(pg_url: str, dim: int, tenant: str, collection_id: str) -> list[Any]:
    return list(
        await admin_exec(
            pg_url,
            f"SELECT content, embedding::text FROM knowledge_chunks_{dim} "
            "WHERE tenant_id = :t AND collection_id = :c ORDER BY chunk_index",
            {"t": tenant, "c": collection_id},
        )
    )


def _assert_vectors(rows: list[Any], dim: int) -> None:
    assert rows, f"no chunk in knowledge_chunks_{dim}"
    for content, embedding_text in rows:
        stored = [float(x) for x in embedding_text.strip("[]").split(",")]
        expected = vector_for(content, dim)
        assert len(stored) == dim
        assert max(abs(a - b) for a, b in zip(stored, expected, strict=True)) < 1e-5


async def _search(client: httpx.AsyncClient, collection_id: str, q: str) -> list[dict[str, Any]]:
    resp = await client.get(
        "/knowledge/search",
        params={"q": q, "collection_id": collection_id, "strategy": "hybrid", "top_k": 3},
    )
    assert resp.status_code == 200, resp.text
    return list(resp.json())


async def _create_and_fill(
    client: httpx.AsyncClient, embed_server: _EmbeddingsServer
) -> tuple[str, str]:
    await _register_narrow_model(client, embed_server.base_url)
    wide = await client.post("/knowledge/collections", json={"name": "wide"})
    narrow = await client.post(
        "/knowledge/collections", json={"name": "narrow", "embedding_model": _NARROW}
    )
    assert wide.status_code == 201, wide.text
    assert narrow.status_code == 201, narrow.text
    assert (wide.json()["embedding_model"], wide.json()["embedding_dim"]) == (_WIDE, 2048)
    assert narrow.json()["embedding_model_key"] == f"onprem/{_NARROW}"
    assert narrow.json()["chunk_table"] == "knowledge_chunks_1024"
    wide_id, narrow_id = wide.json()["collection_id"], narrow.json()["collection_id"]
    embed_server.calls.clear()
    for cid, content in ((wide_id, _FREIGHT), (narrow_id, _ORCHARD)):
        ingested = await client.post(
            "/knowledge/ingest", json={"collection_id": cid, "content": content}
        )
        assert ingested.status_code == 201, ingested.text
        assert ingested.json()["chunks_created"] >= 1
    return wide_id, narrow_id


async def test_two_collections_with_different_embedders_ingest_and_search_without_mixing(
    api: tuple[httpx.AsyncClient, str],
    deployment: tuple[str, str],
    embed_server: _EmbeddingsServer,
) -> None:
    client, tenant = api
    pg_url, _redis = deployment
    wide_id, narrow_id = await _create_and_fill(client, embed_server)

    # Documents were embedded by their collection's model only.
    assert embed_server.models_for("Bramblewood") == {_WIDE}
    assert embed_server.models_for("Lindqvist") == {_NARROW}

    # Each collection's chunks are in its width's table, with that model's vectors...
    _assert_vectors(await _chunks(pg_url, 2048, tenant, wide_id), 2048)
    _assert_vectors(await _chunks(pg_url, 1024, tenant, narrow_id), 1024)
    # ...and never in the other's.
    assert await _chunks(pg_url, 1024, tenant, wide_id) == []
    assert await _chunks(pg_url, 2048, tenant, narrow_id) == []

    # Queries are embedded with the collection's model and find its document.
    embed_server.calls.clear()
    narrow_hits = await _search(client, narrow_id, "when are heirloom apple scions grafted")
    assert narrow_hits and "Lindqvist" in narrow_hits[0]["content"]
    assert embed_server.models_for("heirloom apple scions") == {_NARROW}
    wide_hits = await _search(client, wide_id, "refrigerated containers to Rotterdam")
    assert wide_hits and "Bramblewood" in wide_hits[0]["content"]
    assert embed_server.models_for("containers to Rotterdam") == {_WIDE}
    # No collection returns the other's content.
    assert all("Bramblewood" not in h["content"] for h in narrow_hits)
    assert all("Lindqvist" not in h["content"] for h in wide_hits)

    listed = {c["name"]: c for c in (await client.get("/knowledge/collections")).json()}
    assert (listed["narrow"]["embedder"], listed["narrow"]["embedding_dim"]) == (_NARROW, 1024)
    assert (listed["wide"]["embedder"], listed["wide"]["embedding_dim"]) == (_WIDE, 2048)
    (row,) = await admin_exec(
        pg_url,
        "SELECT embedding_provider, embedding_model, embedding_dim FROM knowledge_collections "
        "WHERE id = :c",
        {"c": narrow_id},
    )
    assert tuple(row) == ("onprem", _NARROW, 1024)


async def test_re_embed_moves_a_collection_from_1024_to_2048_and_rebinds_it(
    api: tuple[httpx.AsyncClient, str],
    deployment: tuple[str, str],
    embed_server: _EmbeddingsServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.scaling.tasks as tasks

    client, tenant = api
    pg_url, _redis = deployment
    _wide_id, narrow_id = await _create_and_fill(client, embed_server)

    queued: list[dict[str, Any]] = []
    monkeypatch.setattr(
        tasks.re_embed_collection, "apply_async",
        lambda kwargs, queue: queued.append({"kwargs": kwargs, "queue": queue}),
    )
    resp = await client.post(
        f"/knowledge/collections/{narrow_id}/re-embed", json={"embedding_model": _WIDE}
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["target_embedding_dim"] == 2048
    (job,) = queued
    assert job["queue"] == "maintenance"
    assert job["kwargs"]["target"]["model"] == _WIDE

    embed_server.calls.clear()
    result = await tasks.re_embed_collection_async(**job["kwargs"])
    assert "error" not in result, result
    assert (result["previous_dimension"], result["dimension"]) == (1024, 2048)
    assert embed_server.models_for("Lindqvist") == {_WIDE}

    # The rows moved to the 2048 table with the new model's vectors; none are left.
    _assert_vectors(await _chunks(pg_url, 2048, tenant, narrow_id), 2048)
    assert await _chunks(pg_url, 1024, tenant, narrow_id) == []
    (row,) = await admin_exec(
        pg_url,
        "SELECT embedder, embedding_model, embedding_dim, embedding_provider "
        "FROM knowledge_collections WHERE id = :c",
        {"c": narrow_id},
    )
    # Re-bound to the default model (whose width this deployment does not declare,
    # so the binding is the lenient default one: provider NULL).
    assert tuple(row) == (_WIDE, _WIDE, 2048, None)
    status = await client.get(f"/knowledge/collections/{narrow_id}/re-embed")
    assert status.json()["status"] == "completed"

    # Queries and new documents now use the new model on the new table.
    embed_server.calls.clear()
    hits = await _search(client, narrow_id, "when are heirloom apple scions grafted")
    assert hits and "Lindqvist" in hits[0]["content"]
    assert embed_server.models_for("heirloom apple scions") == {_WIDE}
    more = await client.post(
        "/knowledge/ingest",
        json={"collection_id": narrow_id, "content": "Quince trees bloom after the apples."},
    )
    assert more.status_code == 201, more.text
    assert embed_server.models_for("Quince trees") == {_WIDE}
    assert len(await _chunks(pg_url, 2048, tenant, narrow_id)) == 2


async def test_an_empty_bound_collection_never_adopts_another_models_width(
    api: tuple[httpx.AsyncClient, str],
    deployment: tuple[str, str],
    embed_server: _EmbeddingsServer,
) -> None:
    """The persistence guard: vectors of another width are refused even before the
    first write of a collection bound to a model (unbound ones used to adopt them)."""
    from app.db.session import get_session_factory
    from app.rag.models import Chunk
    from app.rag.store import EmbeddingDimensionError, KnowledgeStore

    client, tenant = api
    pg_url, _redis = deployment
    await _register_narrow_model(client, embed_server.base_url)
    narrow = await client.post(
        "/knowledge/collections", json={"name": "narrow", "embedding_model": _NARROW}
    )
    narrow_id = narrow.json()["collection_id"]
    ctx = TenantContext(tenant_id=tenant, plan=PlanTier.ENTERPRISE, api_key_id="k")
    chunk = Chunk(document_id="d1", content=_FREIGHT, embedding=vector_for(_FREIGHT, 2048),
                  chunk_index=0)
    with pytest.raises(EmbeddingDimensionError, match="bound to acme/embed-1024"):
        await KnowledgeStore(get_session_factory()).ingest_chunks_async(
            [chunk], collection_id=narrow_id, tenant_ctx=ctx
        )
    for dim in (1024, 2048):
        assert await _chunks(pg_url, dim, tenant, narrow_id) == []
    (row,) = await admin_exec(
        pg_url, "SELECT embedding_dim FROM knowledge_collections WHERE id = :c", {"c": narrow_id}
    )
    assert row[0] == 1024
